"""Agent 4 - the Risk Officer. The only agent with a veto.

Design rule for the whole desk: **the language model has a veto and a dial,
never the wheel.** It can reject a trade, and it can size one down. Everything
that could actually hurt the account - position count, dollars at risk, net
delta, buying-power reserve, kill switches - is decided here, in deterministic
Python, and it runs *after* the model has spoken. There is no prompt that can
talk the Risk Officer into a bigger position, because the Risk Officer does not
read prompts.

Every gate returns a reason string with the numbers in it, and all of them are
written to the journal whether they passed or failed. A blocked trade is a
recorded decision, not a silent absence.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os

from .. import blackscholes as bs
from .. import occ
from ..util import clamp, log, safe_float
from .types import (BULL_PUT_SPREAD, CREDIT_STRUCTURES, DEBIT_STRUCTURES,
                    IRON_CONDOR, Candidate, Gate, Verdict)

ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "EEM", "TLT", "GLD",
        "SMH", "ARKK", "VTI", "VOO", "IVV", "RSP", "MDY"}

# Structures each Alpaca options level can express.
LEVEL_ALLOWS = {
    0: set(),
    1: set(),
    2: set(),
    3: set(CREDIT_STRUCTURES) | set(DEBIT_STRUCTURES),
}


class RiskOfficer:
    name = "risk_officer"

    def __init__(self, cfg, journal=None):
        self.cfg = cfg
        self.journal = journal
        self.earnings = self._load_earnings()
        self.stand_down_reasons: list = []

    @staticmethod
    def _load_earnings() -> dict:
        for path in ("fixtures/earnings.json", "earnings.json"):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception:
                    return {}
        return {}

    # ── portfolio state ──────────────────────────────────────────────────────

    def portfolio_delta(self, brief) -> float:
        """Net share-equivalent delta across every open option leg.

        Re-derived from the current chain where possible rather than trusted
        from entry, because a spread that has gone against us has a very
        different delta than the one we opened.
        """
        total = 0.0
        today = _dt.date.today()
        for pos in brief.option_positions():
            meta = occ.parse(pos["symbol"])
            if not meta:
                continue
            sb = brief.symbols.get(meta["root"])
            if sb is None or sb.spot <= 0:
                continue
            live = next((c for c in sb.chain if c.symbol == pos["symbol"]), None)
            if live is not None and live.delta:
                d = live.delta
            else:
                dte = max(0, (_dt.date.fromisoformat(meta["expiry"]) - today).days)
                vol = sb.atm_iv or sb.rv20 or 0.25
                d = bs.delta(sb.spot, meta["strike"], bs.years_to_expiry(dte),
                             vol, meta["kind"])
            total += d * pos["qty"] * 100.0
        return total

    def deployed_risk(self, brief) -> float:
        """Dollars still at risk in open structures, from the trade journal."""
        if self.journal is None:
            return 0.0
        return self.journal.open_risk()

    # ── portfolio gates ──────────────────────────────────────────────────────

    def portfolio_gates(self, brief) -> list:
        r = self.cfg.risk
        gates = []
        eq = brief.equity
        acct = brief.account

        gates.append(Gate("market_open", brief.is_open,
                          "market open" if brief.is_open else "market closed"))

        blocked = bool(acct.get("trading_blocked"))
        gates.append(Gate("account_tradable", not blocked,
                          f"status={acct.get('status') or 'unknown'}"))

        level = int(safe_float(acct.get("options_trading_level"), 0))
        gates.append(Gate("options_level", level >= 3,
                          f"level {level} (need 3 for multi-leg spreads)"))

        # Day P&L kill switch.
        last_eq = safe_float(acct.get("last_equity"), eq)
        day_pl_pct = 100.0 * (eq - last_eq) / last_eq if last_eq > 0 else 0.0
        ok = day_pl_pct > -r.daily_loss_kill_pct
        gates.append(Gate("daily_loss_kill", ok,
                          f"day P&L {day_pl_pct:+.2f}% vs limit "
                          f"-{r.daily_loss_kill_pct:.2f}%"))

        # Peak-to-trough kill switch, measured on our own equity curve.
        peak = self.journal.equity_peak(eq) if self.journal else eq
        dd = 100.0 * (eq - peak) / peak if peak > 0 else 0.0
        ok = dd > -r.drawdown_kill_pct
        gates.append(Gate("drawdown_kill", ok,
                          f"drawdown {dd:+.2f}% from peak ${peak:,.0f} vs limit "
                          f"-{r.drawdown_kill_pct:.2f}%"))

        obp = safe_float(acct.get("options_buying_power"))
        reserve_ok = eq <= 0 or (100.0 * obp / eq) >= r.min_bp_reserve_pct
        gates.append(Gate("bp_reserve", reserve_ok,
                          f"options BP ${obp:,.0f} = {(100.0 * obp / eq) if eq else 0:.0f}% "
                          f"of equity, floor {r.min_bp_reserve_pct:.0f}%"))

        open_structs = self.journal.open_trade_count() if self.journal else 0
        gates.append(Gate("position_count", open_structs < r.max_concurrent_positions,
                          f"{open_structs} open structures, cap {r.max_concurrent_positions}"))

        net_delta = self.portfolio_delta(brief)
        cap = r.max_net_delta_per_100k * max(eq, 1.0) / 100_000.0
        gates.append(Gate("net_delta_band", abs(net_delta) <= cap,
                          f"net delta {net_delta:+.0f} vs band +/-{cap:.0f}"))

        deployed_pct = 100.0 * self.deployed_risk(brief) / eq if eq > 0 else 0.0
        gates.append(Gate("deployed_risk", deployed_pct < r.max_deployed_risk_pct,
                          f"{deployed_pct:.1f}% of equity at risk, cap "
                          f"{r.max_deployed_risk_pct:.0f}%"))

        gates.append(self._time_of_day(brief))

        self.stand_down_reasons = [g.name for g in gates if not g.passed]
        return gates

    def _time_of_day(self, brief) -> Gate:
        r = self.cfg.risk
        if not brief.is_open:
            return Gate("time_of_day", False, "market closed")
        nc = brief.clock.get("next_close") or ""
        try:
            close = _dt.datetime.fromisoformat(nc.replace("Z", "+00:00"))
            now = _dt.datetime.now(_dt.timezone.utc)
            mins_left = (close - now).total_seconds() / 60.0
        except Exception:
            return Gate("time_of_day", True, "close time unavailable, allowing")
        if mins_left < r.no_entry_last_minutes:
            return Gate("time_of_day", False,
                        f"{mins_left:.0f} min to the bell, no fresh short gamma")
        return Gate("time_of_day", True, f"{mins_left:.0f} min of session left")

    # ── per-candidate gates ──────────────────────────────────────────────────

    def review(self, cand: Candidate, brief, portfolio_ok: bool,
               pending: list = None) -> Verdict:
        r = self.cfg.risk
        eq = brief.equity
        gates = []
        pending = pending or []

        gates.append(Gate("portfolio_clear", portfolio_ok,
                          "portfolio gates clear" if portfolio_ok
                          else f"stood down: {', '.join(self.stand_down_reasons)}"))

        level = int(safe_float(brief.account.get("options_trading_level"), 0))
        allowed = LEVEL_ALLOWS.get(min(level, 3), set())
        gates.append(Gate("structure_permitted", cand.structure in allowed,
                          f"{cand.structure} at options level {level}"))

        gates.append(Gate("positive_expectancy", cand.edge_ratio > 0.0,
                          f"EV ${cand.ev_dollars:+,.0f} = {cand.edge_ratio * 100:+.1f}% "
                          f"of max loss"))

        if cand.is_credit:
            ratio = cand.net_price / cand.width if cand.width else 0.0
            gates.append(Gate("credit_to_width", ratio >= r.min_credit_to_width,
                              f"credit ${cand.net_price:.2f} on {cand.width:g} wide "
                              f"= {ratio:.2f}, floor {r.min_credit_to_width:.2f}"))
            gates.append(Gate("short_delta_band",
                              r.min_short_delta <= cand.short_delta <= r.max_short_delta,
                              f"short delta {cand.short_delta:.3f} in "
                              f"[{r.min_short_delta:.2f}, {r.max_short_delta:.2f}]"))
            t = bs.years_to_expiry(cand.dte)
            shorts = [lg for lg in cand.legs if lg.side == "sell"]
            sb = brief.symbols.get(cand.underlying)
            touch = 0.0
            if sb and shorts:
                touch = max(bs.prob_touch(sb.spot, lg.strike, t,
                                          max(sb.rv20, 1e-4)) for lg in shorts)
            gates.append(Gate("prob_touch", touch <= 0.62,
                              f"{touch * 100:.0f}% chance a short strike is tested, cap 62%"))
        else:
            reward = cand.max_gain / cand.max_loss if cand.max_loss else 0.0
            gates.append(Gate("debit_reward", reward >= r.min_debit_reward_ratio,
                              f"payoff {reward:.2f}:1, floor "
                              f"{r.min_debit_reward_ratio:.2f}:1"))

        in_tenor = any(lo <= cand.dte <= hi for lo, hi in r.tenors)
        buckets = ", ".join(f"{lo}-{hi}" for lo, hi in r.tenors)
        gates.append(Gate("dte_window", in_tenor,
                          f"{cand.dte} DTE against tenor buckets [{buckets}]"))

        bad = [lg.symbol for lg in cand.legs
               if lg.bid <= 0 or lg.ask <= 0
               or (lg.open_interest and lg.open_interest < r.min_open_interest)]
        gates.append(Gate("leg_liquidity", not bad,
                          "all legs quoted with depth" if not bad
                          else f"thin legs: {', '.join(occ.describe(s) for s in bad)}"))
        gates.append(Gate("liquidity_score", cand.liquidity >= 0.25,
                          f"liquidity {cand.liquidity:.2f}, floor 0.25"))

        # Concentration, counting anything we are about to send this cycle.
        held = self.journal.open_count_for(cand.underlying) if self.journal else 0
        held += sum(1 for p in pending if p.underlying == cand.underlying)
        gates.append(Gate("per_underlying_cap", held < r.max_positions_per_underlying,
                          f"{held} open in {cand.underlying}, cap "
                          f"{r.max_positions_per_underlying}"))

        dupe = self.journal.has_open(cand.underlying, cand.expiry, cand.structure) \
            if self.journal else False
        dupe = dupe or any(p.underlying == cand.underlying and p.expiry == cand.expiry
                           and p.structure == cand.structure for p in pending)
        gates.append(Gate("not_duplicate", not dupe,
                          f"no open {cand.structure} in {cand.underlying} {cand.expiry}"
                          if not dupe else "already holding this structure"))

        gates.append(self._earnings_gate(cand))

        # Sizing is a gate, not an afterthought: if one contract already breaks
        # the per-trade risk limit, the trade does not happen.
        qty, size_note = self._size(cand, brief, pending)
        cand.qty = qty
        gates.append(Gate("sizing", qty >= 1, size_note))

        approved = all(g.passed for g in gates)
        reason = "cleared" if approved else "; ".join(
            g.detail for g in gates if not g.passed)
        return Verdict(candidate=cand, approved=approved, gates=gates, reason=reason)

    def _earnings_gate(self, cand: Candidate) -> Gate:
        if cand.underlying in ETFS:
            return Gate("earnings_blackout", True, "ETF, no single-name event risk")
        date = self.earnings.get(cand.underlying)
        if not date:
            return Gate("earnings_blackout", True,
                        "no earnings date on file for this name")
        try:
            ed = _dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            return Gate("earnings_blackout", True, f"unparseable earnings date {date}")
        today = _dt.date.today()
        expiry = _dt.date.fromisoformat(cand.expiry)
        if today <= ed <= expiry:
            return Gate("earnings_blackout", False,
                        f"earnings {ed.isoformat()} falls before {cand.expiry} expiry")
        return Gate("earnings_blackout", True, f"earnings {ed.isoformat()} outside the trade")

    def _size(self, cand: Candidate, brief, pending: list) -> tuple:
        r = self.cfg.risk
        eq = brief.equity
        if eq <= 0 or cand.max_loss <= 0:
            return 0, "no equity or undefined risk"

        per_trade_budget = eq * r.max_risk_per_trade_pct / 100.0
        already = self.deployed_risk(brief) + sum(
            p.max_loss * max(p.qty, 1) for p in pending)
        book_budget = max(0.0, eq * r.max_deployed_risk_pct / 100.0 - already)
        budget = min(per_trade_budget, book_budget)

        # The LLM's dial: it may shrink the position, never grow it.
        budget *= clamp(cand.conviction, 0.0, 1.0)

        qty = int(math.floor(budget / cand.max_loss))
        obp = safe_float(brief.account.get("options_buying_power"))
        if cand.is_credit:
            # A defined-risk credit spread reserves (width - credit) x 100.
            per_contract_bp = cand.max_loss
        else:
            per_contract_bp = cand.net_price * 100.0
        if per_contract_bp > 0:
            usable = max(0.0, obp - eq * r.min_bp_reserve_pct / 100.0)
            qty = min(qty, int(math.floor(usable / per_contract_bp)))
        qty = max(0, min(qty, 25))

        note = (f"{qty} contract(s): budget ${budget:,.0f} "
                f"(per-trade ${per_trade_budget:,.0f} x conviction "
                f"{cand.conviction:.2f}, book headroom ${book_budget:,.0f}) "
                f"/ ${cand.max_loss:,.0f} risk each")
        if qty < 1:
            note = ("one contract risks $%.0f which exceeds the available budget $%.0f"
                    % (cand.max_loss, budget))
        return qty, note
