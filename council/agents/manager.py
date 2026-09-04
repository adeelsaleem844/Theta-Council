"""The Position Manager - the agent that actually decides the P&L.

Entries get all the attention; exits get all the money. Four rules, checked in
priority order on every cycle, applied to every open structure:

  1. TIME     - a short vertical stops being a premium trade and becomes a
                coin flip with leverage once most of its life is gone. The
                cutoff is the LAST QUARTER of the trade's original life, capped
                at 7 days: a 32-day spread exits at 7 DTE, a 5-day spread exits
                at 1. A fixed 7-day rule would close a short-dated trade the
                moment it opened.
  2. TARGET   - take 55% of maximum profit. The last 45% of a credit spread's
                value takes most of the remaining time to earn and carries all
                of the remaining gamma. Selling it back early raises annualised
                return per unit of risk, which is the only return that matters.
  3. STOP     - close at 2x the credit received, before max loss.
  4. DEFEND   - if the short strike's delta pushes through 0.40 the structure is
                no longer the trade that was approved. Exit.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .. import blackscholes as bs
from .. import occ
from ..util import log


@dataclass
class Action:
    trade_id: str
    underlying: str
    structure: str
    action: str            # close | hold
    rule: str
    reason: str
    pl_dollars: float = 0.0
    pl_pct_of_max: float = 0.0
    close_value: float = 0.0
    legs: list = field(default_factory=list)
    qty: int = 1


class PositionManager:
    name = "manager"

    def __init__(self, cfg):
        self.cfg = cfg

    # ── valuation ────────────────────────────────────────────────────────────

    @staticmethod
    def _live_mid(brief, symbol: str) -> float:
        """Current mid for one leg: live quote first, model price as backstop."""
        meta = occ.parse(symbol)
        if not meta:
            return 0.0
        sb = brief.symbols.get(meta["root"])
        if sb is None:
            return 0.0
        hit = next((c for c in sb.chain if c.symbol == symbol), None)
        if hit is not None and hit.mid > 0:
            return hit.mid
        dte = max(0, (_dt.date.fromisoformat(meta["expiry"]) - _dt.date.today()).days)
        vol = sb.atm_iv or sb.rv20 or 0.25
        return bs.price(sb.spot, meta["strike"], bs.years_to_expiry(dte), vol,
                        meta["kind"])

    def structure_value(self, brief, legs: list) -> float:
        """What it costs to close, per share. Positive = we pay to get out."""
        total = 0.0
        for lg in legs:
            mid = self._live_mid(brief, lg["symbol"])
            ratio = float(lg.get("ratio", 1) or 1)
            # We are buying back what we sold and selling what we bought.
            total += mid * ratio * (1.0 if lg.get("side") == "sell" else -1.0)
        return total

    # ── decisions ────────────────────────────────────────────────────────────

    def review(self, brief, open_trades: list) -> list:
        r = self.cfg.risk
        out = []
        today = _dt.date.today()

        for tr in open_trades:
            legs = tr.get("legs") or []
            if not legs:
                continue
            qty = int(tr.get("qty") or 1)
            entry = float(tr.get("net_price") or 0.0)
            is_credit = bool(tr.get("is_credit", True))
            width = float(tr.get("width") or 0.0)
            expiry = str(tr.get("expiry") or "")
            dte = (_dt.date.fromisoformat(expiry) - today).days if expiry else 0

            opened = str(tr.get("opened_ts") or "")[:10]
            try:
                original = (_dt.date.fromisoformat(expiry)
                            - _dt.date.fromisoformat(opened)).days
            except Exception:
                original = max(dte, r.time_exit_dte * 4)
            time_cut = max(1, min(r.time_exit_dte, round(original * 0.25)))

            now = self.structure_value(brief, legs)
            if is_credit:
                pl_share = entry - now
                max_profit = entry
            else:
                pl_share = now - entry
                max_profit = max(width - entry, 0.01)
            pl_dollars = pl_share * 100.0 * qty
            pct_of_max = 100.0 * pl_share / max_profit if max_profit else 0.0

            base = dict(trade_id=str(tr.get("trade_id")),
                        underlying=str(tr.get("underlying")),
                        structure=str(tr.get("structure")),
                        pl_dollars=pl_dollars, pl_pct_of_max=pct_of_max,
                        close_value=now, legs=legs, qty=qty)

            if dte <= time_cut:
                out.append(Action(action="close", rule="TIME",
                                  reason=f"{dte} DTE at or under the {time_cut}-day gamma "
                                         f"cutoff for a {original}-day trade; "
                                         f"P&L ${pl_dollars:+,.0f}",
                                  **base))
                continue

            if pct_of_max >= r.take_profit_pct:
                out.append(Action(action="close", rule="TARGET",
                                  reason=f"captured {pct_of_max:.0f}% of max profit "
                                         f"(target {r.take_profit_pct:.0f}%), "
                                         f"${pl_dollars:+,.0f} with {dte}d left",
                                  **base))
                continue

            if is_credit and now >= entry * r.stop_loss_mult:
                out.append(Action(action="close", rule="STOP",
                                  reason=f"cost to close ${now:.2f} reached "
                                         f"{r.stop_loss_mult:.1f}x the ${entry:.2f} credit; "
                                         f"cutting at ${pl_dollars:+,.0f}",
                                  **base))
                continue
            if not is_credit and pl_share <= -entry * 0.6:
                out.append(Action(action="close", rule="STOP",
                                  reason=f"lost 60% of the ${entry:.2f} debit, "
                                         f"${pl_dollars:+,.0f}",
                                  **base))
                continue

            breach = self._delta_breach(brief, legs, is_credit)
            if breach is not None:
                sym, d = breach
                out.append(Action(action="close", rule="DEFEND",
                                  reason=f"{occ.describe(sym)} delta {d:.2f} through the "
                                         f"{r.delta_breach:.2f} breach line; "
                                         f"${pl_dollars:+,.0f}",
                                  **base))
                continue

            out.append(Action(action="hold", rule="HOLD",
                              reason=f"{pct_of_max:.0f}% of max, {dte}d left, "
                                     f"${pl_dollars:+,.0f} open",
                              **base))

        for a in out:
            log("INFO" if a.action == "close" else "DEBUG",
                f"manage {a.underlying} {a.structure}", rule=a.rule,
                action=a.action, pl=round(a.pl_dollars, 2))
        return out

    def _delta_breach(self, brief, legs: list, is_credit: bool):
        if not is_credit:
            return None
        r = self.cfg.risk
        worst = None
        for lg in legs:
            if lg.get("side") != "sell":
                continue
            meta = occ.parse(lg["symbol"])
            if not meta:
                continue
            sb = brief.symbols.get(meta["root"])
            if sb is None or sb.spot <= 0:
                continue
            hit = next((c for c in sb.chain if c.symbol == lg["symbol"]), None)
            if hit is not None and hit.delta:
                d = abs(hit.delta)
            else:
                dte = max(0, (_dt.date.fromisoformat(meta["expiry"])
                              - _dt.date.today()).days)
                vol = sb.atm_iv or sb.rv20 or 0.25
                d = abs(bs.delta(sb.spot, meta["strike"],
                                 bs.years_to_expiry(dte), vol, meta["kind"]))
            if d >= r.delta_breach and (worst is None or d > worst[1]):
                worst = (lg["symbol"], d)
        return worst
