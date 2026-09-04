"""The orchestrator: one cycle of the desk.

Order of operations matters and is deliberate:

  1. intake        - one brief, shared by every agent, journalled verbatim
  2. read          - Analyst and Vol Scout form views
  3. MANAGE FIRST  - exits are processed before entries, every single cycle.
                     Closing a winner returns risk budget and buying power that
                     a new entry can then use, and a desk that opens before it
                     closes will always drift to its position cap.
  4. portfolio gates - kill switches, delta band, buying power, clock
  5. design        - Architect builds executable structures
  6. adjudicate    - the LLM may veto and may shrink. Nothing else.
  7. review        - Risk Officer gates each survivor and sets the size
  8. execute       - mleg orders, then journal everything including refusals
"""
from __future__ import annotations

from . import orders
from .agents import (Adjudicator, Analyst, Architect, Bandit, PositionManager,
                     RiskOfficer, VolScout)
from .agents.types import STRUCTURE_LABEL
from .journal import Journal
from .market import MarketIntake
from .transports import Router, TransportError
from .util import banner, log


class CycleReport:
    def __init__(self):
        self.cycle_id = ""
        self.equity = 0.0
        self.market_open = False
        self.portfolio_regime = ""
        self.posture = ""
        self.posture_reason = ""
        self.used_llm = False
        self.model = ""
        self.regimes = {}
        self.vols = {}
        self.candidates = []
        self.verdicts = []
        self.submitted = []
        self.closed = []
        self.held = []
        self.portfolio_gates = []
        self.errors = []


class Desk:
    def __init__(self, cfg):
        self.cfg = cfg
        self.journal = Journal(cfg)
        self.router = Router(cfg)
        self.intake = MarketIntake(cfg, self.router, self.journal)
        self.analyst = Analyst(cfg)
        self.scout = VolScout(cfg)
        self.architect = Architect(cfg)
        self.adjudicator = Adjudicator(cfg)
        self.risk = RiskOfficer(cfg, self.journal)
        self.manager = PositionManager(cfg)
        self.bandit = Bandit(self.journal)

    # ── one cycle ────────────────────────────────────────────────────────────

    def cycle(self) -> CycleReport:
        rep = CycleReport()
        rep.cycle_id = self.journal.start_cycle()
        r = self.cfg.risk

        banner(f"cycle {rep.cycle_id}")
        brief = self.intake.build()
        rep.equity = brief.equity
        rep.market_open = brief.is_open
        rep.errors = list(brief.errors)
        self.journal.record_equity(brief.equity)
        self.bandit.refresh()

        # 2. views
        for sym, sb in brief.symbols.items():
            rep.regimes[sym] = self.analyst.read(sb)
            rep.vols[sym] = self.scout.read(sb)
        rep.portfolio_regime = self.analyst.portfolio_regime(rep.regimes)

        # 3. exits before entries
        rep.closed, rep.held = self._manage(brief)

        # Re-read the account after closing so sizing sees the freed capital.
        if rep.closed and not self.cfg.dry_run:
            try:
                brief.account = self.router.call("account")
            except TransportError as exc:
                rep.errors.append(f"account refresh: {exc}")

        # 4. portfolio gates
        rep.portfolio_gates = self.risk.portfolio_gates(brief)
        portfolio_ok = all(g.passed for g in rep.portfolio_gates)
        if not portfolio_ok:
            for g in rep.portfolio_gates:
                if not g.passed:
                    log("WARN", f"portfolio gate blocked: {g.name}", detail=g.detail)

        # 5. design
        for sym, sb in brief.symbols.items():
            rep.candidates += self.architect.design(sb, rep.regimes[sym],
                                                    rep.vols[sym], self.bandit)
        rep.candidates.sort(key=lambda c: c.score, reverse=True)
        log("INFO", "designed candidates", n=len(rep.candidates))

        # 6. adjudicate
        open_book = [{"underlying": t["underlying"], "structure": t["structure"],
                      "expiry": t["expiry"], "qty": t["qty"],
                      "risk": round(float(t["max_loss"]) * int(t["qty"]), 2)}
                     for t in self.journal.open_trades()]
        verdict = self.adjudicator.adjudicate(brief, rep.candidates, rep.regimes,
                                              rep.portfolio_regime, open_book)
        rep.posture = verdict.get("posture", "normal")
        rep.posture_reason = verdict.get("posture_reason", "")
        rep.used_llm = bool(verdict.get("used_llm"))
        rep.model = verdict.get("model", "")

        # 7 + 8. gate, size, execute
        live = [c for c in rep.candidates if c.conviction > 0.0]
        live.sort(key=lambda c: (c.score * c.conviction), reverse=True)
        pending = []
        for cand in live:
            if len(rep.submitted) >= r.max_new_entries_per_cycle:
                break
            v = self.risk.review(cand, brief, portfolio_ok, pending)
            rep.verdicts.append(v)
            self.journal.record_decision(rep.cycle_id, v)
            if not v.approved:
                log("INFO", f"blocked {cand.label()}", why=v.reason[:160])
                continue
            sent = self._execute(rep.cycle_id, cand)
            if sent:
                rep.submitted.append(sent)
                pending.append(cand)

        # Candidates the Risk Officer never got to still deserve a record.
        for cand in rep.candidates:
            if all(v.candidate.cid != cand.cid for v in rep.verdicts):
                from .agents.types import Gate, Verdict
                why = ("model declined" if cand.conviction <= 0
                       else "cycle entry cap reached")
                v = Verdict(candidate=cand, approved=False,
                            gates=[Gate("adjudicator", cand.conviction > 0, why)],
                            reason=why)
                rep.verdicts.append(v)
                self.journal.record_decision(rep.cycle_id, v)

        self.journal.record_cycle(rep.cycle_id, brief, rep.portfolio_regime,
                                  verdict, len(rep.candidates), len(rep.submitted),
                                  self.router.summary(), rep.portfolio_gates)
        self.journal.emit("cycle", {
            "cycle_id": rep.cycle_id, "equity": rep.equity,
            "portfolio_regime": rep.portfolio_regime, "posture": rep.posture,
            "used_llm": rep.used_llm, "model": rep.model,
            "candidates": [c.compact() for c in rep.candidates],
            "closed": [a.__dict__ for a in rep.closed],
            "submitted": rep.submitted,
            "llm_raw": verdict.get("raw", ""),
        })
        return rep

    # ── steps ────────────────────────────────────────────────────────────────

    def _manage(self, brief):
        open_trades = self.journal.open_trades()
        if not open_trades:
            return [], []
        actions = self.manager.review(brief, open_trades)
        closed, held = [], []
        for a in actions:
            if a.action != "close":
                held.append(a)
                continue
            trade = next((t for t in open_trades if t["trade_id"] == a.trade_id), None)
            if trade is None:
                continue
            urgency = "urgent" if a.rule in ("STOP", "TIME", "DEFEND") else "normal"
            payload = orders.build_exit(trade, a.close_value, urgency)
            try:
                order = orders.submit(self.router, payload,
                                      credit=bool(trade.get("is_credit", True)),
                                      dry_run=self.cfg.dry_run)
            except TransportError as exc:
                log("ERROR", "exit order rejected", trade=a.trade_id,
                    error=str(exc)[:200])
                self.journal.emit("exit_rejected", {"trade_id": a.trade_id,
                                                    "error": str(exc)[:400],
                                                    "payload": payload})
                continue
            if not self.cfg.dry_run:
                self.journal.close_trade(a.trade_id, a.close_value, a.pl_dollars,
                                         a.rule, order)
            log("INFO", f"CLOSED {a.underlying} {a.structure}", rule=a.rule,
                pl=round(a.pl_dollars, 2))
            closed.append(a)
        return closed, held

    def _execute(self, cycle_id: str, cand):
        coid = f"tc-{cand.cid}"[:48].replace(" ", "")
        payload = orders.build_entry(cand, coid)
        log("INFO", f"SEND {cand.label()} {cand.strikes()}",
            qty=cand.qty, net=payload["limit_price"], dte=cand.dte,
            ev=round(cand.ev_dollars, 2))
        try:
            order = orders.submit(self.router, payload, credit=cand.is_credit,
                                  dry_run=self.cfg.dry_run)
        except TransportError as exc:
            log("ERROR", "entry order rejected", cid=cand.cid, error=str(exc)[:240])
            self.journal.emit("entry_rejected", {"cid": cand.cid,
                                                 "error": str(exc)[:400],
                                                 "payload": payload})
            return None
        trade_id = "dry-run"
        if not self.cfg.dry_run:
            trade_id = self.journal.open_trade(cycle_id, cand, order)
        return {"trade_id": trade_id, "cid": cand.cid, "label": cand.label(),
                "strikes": cand.strikes(), "qty": cand.qty,
                "limit": payload["limit_price"], "dte": cand.dte,
                "max_loss": round(cand.max_loss * cand.qty, 2),
                "ev": round(cand.ev_dollars * cand.qty, 2),
                "order_id": (order or {}).get("id", ""),
                "status": (order or {}).get("status", "")}

    # ── console report ───────────────────────────────────────────────────────

    def print_report(self, rep: CycleReport) -> None:
        banner("desk state")
        print(f"  equity            ${rep.equity:,.2f}")
        print(f"  market            {'OPEN' if rep.market_open else 'CLOSED'}")
        print(f"  house view        {rep.portfolio_regime}")
        print(f"  posture           {rep.posture}"
              + (f"  ({rep.posture_reason})" if rep.posture_reason else ""))
        print(f"  adjudicator       "
              + (f"{rep.model}" if rep.used_llm else "deterministic (no LLM)"))

        banner("symbol reads")
        print(f"  {'sym':<6}{'regime':<10}{'trend':>7}{'IV':>8}{'RV':>8}"
              f"{'VRP':>7}{'rank':>6}  edge")
        for sym, reg in rep.regimes.items():
            v = rep.vols[sym]
            print(f"  {sym:<6}{reg.label:<10}{reg.trend_score:>+7.2f}"
                  f"{v.iv * 100:>7.1f}%{v.rv * 100:>7.1f}%"
                  f"{v.vrp_ratio:>7.2f}{v.iv_rank:>6.0f}  {v.edge}")

        banner("risk officer - portfolio gates")
        for g in rep.portfolio_gates:
            print(f"  {g.line()}")

        if rep.closed or rep.held:
            banner("position management")
            for a in rep.closed:
                print(f"  CLOSE {a.underlying:<6} {STRUCTURE_LABEL.get(a.structure, a.structure):<18}"
                      f" [{a.rule}] {a.reason}")
            for a in rep.held:
                print(f"  hold  {a.underlying:<6} {STRUCTURE_LABEL.get(a.structure, a.structure):<18}"
                      f" {a.reason}")

        banner(f"candidates ({len(rep.candidates)})")
        if not rep.candidates:
            print("  none - no symbol currently offers a variance premium worth "
                  "trading. Standing aside is a decision.")
        for c in rep.candidates[:10]:
            print(f"  {c.cid:<26} {c.strikes():<26} {c.dte:>3}d "
                  f"net {c.net_price:>5.2f} risk ${c.max_loss:>7,.0f} "
                  f"EV ${c.ev_dollars:>+7,.0f} edge {c.edge_ratio * 100:>+5.1f}% "
                  f"pop {c.pop_rv * 100:>4.0f}% score {c.score:.2f} "
                  f"conv {c.conviction:.2f}")

        blocked = [v for v in rep.verdicts if not v.approved]
        if blocked:
            banner(f"refused ({len(blocked)})")
            for v in blocked[:10]:
                print(f"  {v.candidate.cid:<26} {', '.join(v.blocked_by()) or 'n/a'}")
                print(f"    -> {v.reason[:150]}")

        banner(f"orders sent ({len(rep.submitted)})")
        if not rep.submitted:
            print("  no orders this cycle")
        for s in rep.submitted:
            print(f"  {s['label']:<30} {s['strikes']:<26} x{s['qty']} "
                  f"@ {s['limit']}  risk ${s['max_loss']:,.0f} "
                  f"EV ${s['ev']:+,.0f}  {s['status']}")

        self.router.print_summary()

        banner("running totals")
        st = self.journal.stats()
        print(f"  cycles {st['cycles']}   closed {st['closed']}   "
              f"win rate {st['win_rate']}%   realised P&L ${st['realized_pl']:+,.2f}")
        print(f"  open structures {st['open_structures']}   "
              f"capital at risk ${st['open_risk']:,.0f}   "
              f"refusals logged {st['decisions_blocked']}")
        rows = self.bandit.table()
        if rows:
            print("  track record by regime x structure:")
            for row in rows:
                print(f"    {row['regime']:<9}{row['structure']:<20}"
                      f"{row['wins']}W/{row['losses']}L  prior {row['prior']:.2f}")

    def close(self) -> None:
        self.router.close()
        self.journal.close()
