"""Agent 3 - the Structure Architect.

Turns an opinion into an executable, defined-risk options structure with real
strikes pulled from the live chain, and prices its own edge.

How the edge is priced (this is the heart of the desk)
------------------------------------------------------
A vertical spread has a model value. Price it twice:

    V_iv  - using the market's implied volatility  -> what we get paid
    V_rv  - using the underlying's realised vol    -> what we think it is worth

For a spread we SELL:      EV per share = credit_received - V_rv
For a spread we BUY:       EV per share = V_rv - debit_paid

Both legs are valued under the same measure and the same horizon, so the
difference is not a directional bet - it is the variance risk premium, expressed
in dollars. When implied is 1.2x realised, EV is positive and the desk sells.
When implied sits under realised, EV on a short spread is negative no matter how
pretty the chart looks, and the Architect refuses to build it.

`credit_received` is haircut for the bid/ask we will actually cross, so the
edge is quoted after friction rather than before it.
"""
from __future__ import annotations

from .. import blackscholes as bs
from ..util import clamp, log
from .types import (BEAR, BEAR_CALL_SPREAD, BULL, BULL_PUT_SPREAD, CHOP,
                    IRON_CONDOR, LONG_CALL_SPREAD, LONG_PUT_SPREAD, VOLATILE,
                    Candidate, Leg)

# Fraction of the quoted bid/ask we assume we give up per leg on entry.
FILL_HAIRCUT = 0.25


class Architect:
    name = "architect"

    def __init__(self, cfg):
        self.cfg = cfg

    # ── chain helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _slice(sb, kind: str, expiry: str) -> list:
        return sorted((c for c in sb.chain if c.kind == kind and c.expiry == expiry),
                      key=lambda c: c.strike)

    def _expiries(self, sb) -> list:
        """One listed expiry per configured tenor bucket.

        Each bucket aims at its own midpoint, so a 21-45 day bucket lands near
        33 days and a 2-9 day bucket lands near 5 - the same strategy expressed
        at two very different theta-per-day rates. Duplicate expiries across
        buckets are collapsed.
        """
        out, taken = [], set()
        for lo, hi in self.cfg.risk.tenors:
            pool = {c.expiry: c.dte for c in sb.chain if lo <= c.dte <= hi}
            if not pool:
                continue
            aim = (lo + hi) / 2.0
            expiry = min(pool, key=lambda e: abs(pool[e] - aim))
            if expiry in taken:
                continue
            taken.add(expiry)
            out.append((expiry, pool[expiry]))
        return out

    def _widths(self, spot: float, credit: bool = True) -> list:
        """Vertical widths worth trying, scaled to the underlying's price.

        Credit spreads are tried NARROWEST first. A wider short spread collects
        more absolute premium but a worse credit-to-width ratio and far more
        risk per contract - and since size is capped by dollars at risk, the
        narrow version lets the desk hold more contracts for the same risk with
        a better payoff ratio. Debit spreads go the other way: extra width is
        what buys the payoff ratio.
        """
        if spot < 40:
            ladder = [1.0, 2.0]
        elif spot < 120:
            ladder = [2.0, 2.5, 5.0]
        elif spot < 300:
            ladder = [2.5, 5.0, 10.0]
        else:
            ladder = [5.0, 10.0, 15.0]
        return ladder if credit else list(reversed(ladder))

    @staticmethod
    def _near_strike(pool: list, target: float, tol: float):
        """Closest listed strike to `target`, within `tol`.

        Real chains are not evenly spaced - $1 increments near the money, $5
        further out, and gaps where a strike was never listed. Demanding an
        exact match silently pushes the desk into whatever width happens to
        line up, so we take the nearest strike inside a tolerance and recompute
        the true width from the contract we actually found.
        """
        if not pool:
            return None
        hit = min(pool, key=lambda c: abs(c.strike - target))
        return hit if abs(hit.strike - target) <= tol else None

    def _short_leg(self, pool: list, target: float):
        """Contract whose |delta| is closest to `target`, inside the allowed band."""
        r = self.cfg.risk
        ok = [c for c in pool if r.min_short_delta <= c.abs_delta <= r.max_short_delta]
        if not ok:
            return None
        return min(ok, key=lambda c: abs(c.abs_delta - target))

    def _long_leg_by_delta(self, pool: list, target: float):
        if not pool:
            return None
        return min(pool, key=lambda c: abs(c.abs_delta - target))

    # ── pricing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _value_at(vol: float, spot: float, legs: list, dte: int) -> float:
        """Model value of the structure per share, from the seller's side.

        Positive means the structure is a net liability to the seller (i.e. what
        a buyer should pay for it).
        """
        t = bs.years_to_expiry(dte)
        total = 0.0
        for lg in legs:
            px = bs.price(spot, lg.strike, t, max(vol, 1e-4), lg.kind)
            total += px * lg.ratio * (-1.0 if lg.side == "buy" else 1.0)
        return total

    @staticmethod
    def _quoted_net(legs: list, credit: bool) -> tuple:
        """(mid net premium, haircut net premium) per share, always positive."""
        mid = 0.0
        spread_sum = 0.0
        for lg in legs:
            sign = 1.0 if lg.side == "sell" else -1.0
            mid += sign * lg.mid * lg.ratio
            spread_sum += max(0.0, lg.ask - lg.bid) * lg.ratio
        haircut = FILL_HAIRCUT * spread_sum
        if credit:
            return mid, mid - haircut
        return -mid, -mid + haircut

    def _finalise(self, cid: str, sb, regime, vol, structure: str, legs: list,
                  expiry: str, dte: int, width: float, is_credit: bool,
                  prior: float) -> Candidate | None:
        if any(lg is None for lg in legs) or width <= 0:
            return None

        mid_net, exec_net = self._quoted_net(legs, is_credit)
        if exec_net <= 0.01:
            return None

        v_rv = self._value_at(sb.rv20, sb.spot, legs, dte)
        if is_credit:
            ev_share = exec_net - v_rv
            max_loss = (width - exec_net) * 100.0
            max_gain = exec_net * 100.0
        else:
            ev_share = v_rv - exec_net
            max_loss = exec_net * 100.0
            max_gain = (width - exec_net) * 100.0
        if max_loss <= 0:
            return None

        shorts = [lg for lg in legs if lg.side == "sell"]
        short_delta = max((abs(lg.delta) for lg in shorts), default=0.0) if is_credit \
            else max((abs(lg.delta) for lg in legs if lg.side == "buy"), default=0.0)

        # Probability of finishing on the right side of the tested strike, under
        # the market's vol and then under ours.
        t = bs.years_to_expiry(dte)
        tested = shorts if is_credit else [lg for lg in legs if lg.side == "buy"]
        pop_iv = pop_rv = 0.0
        if tested:
            worst = max(tested, key=lambda lg: abs(lg.delta))
            iv = worst.iv or sb.atm_iv
            itm_iv = bs.prob_itm(sb.spot, worst.strike, t, max(iv, 1e-4), worst.kind)
            itm_rv = bs.prob_itm(sb.spot, worst.strike, t, max(sb.rv20, 1e-4), worst.kind)
            if is_credit:
                pop_iv, pop_rv = 1.0 - itm_iv, 1.0 - itm_rv
            else:
                pop_iv, pop_rv = itm_iv, itm_rv
        if structure == IRON_CONDOR and shorts:
            # Both wings can be tested; multiply the two survival probabilities.
            pop_iv = pop_rv = 1.0
            for lg in shorts:
                iv = lg.iv or sb.atm_iv
                pop_iv *= 1.0 - bs.prob_itm(sb.spot, lg.strike, t, max(iv, 1e-4), lg.kind)
                pop_rv *= 1.0 - bs.prob_itm(sb.spot, lg.strike, t, max(sb.rv20, 1e-4), lg.kind)

        liq = self._liquidity(legs)
        ev_dollars = ev_share * 100.0
        edge_ratio = ev_dollars / max_loss

        cand = Candidate(
            cid=cid, underlying=sb.symbol, structure=structure, legs=legs,
            expiry=expiry, dte=dte, is_credit=is_credit,
            net_price=round(exec_net, 2), width=width,
            max_loss=max_loss, max_gain=max_gain, short_delta=short_delta,
            pop_iv=pop_iv, pop_rv=pop_rv, ev_dollars=ev_dollars,
            edge_ratio=edge_ratio, vrp_ratio=sb.vrp_ratio, liquidity=liq,
            prior=prior, regime=regime.label,
        )
        cand.score = self._score(cand, regime, vol)
        cand.thesis = self._thesis(cand, sb, regime, vol)
        cand.invalidators = self._invalidators(cand, sb)
        return cand

    @staticmethod
    def _liquidity(legs: list) -> float:
        scores = []
        for lg in legs:
            if lg.mid <= 0:
                return 0.0
            rel = (lg.ask - lg.bid) / lg.mid
            tight = clamp(1.0 - rel / 0.20, 0.0, 1.0)
            depth = clamp((lg.open_interest or 0) / 4000.0, 0.0, 1.0)
            scores.append(0.65 * tight + 0.35 * depth)
        return sum(scores) / len(scores)

    def _score(self, c: Candidate, regime, vol) -> float:
        edge = clamp(c.edge_ratio / 0.15, 0.0, 1.0)     # 15% of risk as EV == top marks
        return round(0.45 * edge + 0.20 * vol.score + 0.15 * regime.conviction
                     + 0.10 * c.liquidity + 0.10 * c.prior, 4)

    @staticmethod
    def _thesis(c: Candidate, sb, regime, vol) -> str:
        side = "collecting" if c.is_credit else "paying"
        return (
            f"{c.label()} {c.strikes()} at {c.dte}d. {regime.label} regime "
            f"(trend {regime.trend_score:+.2f}); implied {sb.atm_iv * 100:.1f}% vs "
            f"realised {sb.rv20 * 100:.1f}% = {sb.vrp_ratio:.2f}x. "
            f"{side} ${c.net_price:.2f} on a {c.width:g}-wide, risking "
            f"${c.max_loss:,.0f} per contract; EV ${c.ev_dollars:+,.0f} "
            f"({c.edge_ratio * 100:+.1f}% of risk) with {c.pop_rv * 100:.0f}% "
            f"forecast win rate."
        )

    @staticmethod
    def _invalidators(c: Candidate, sb) -> list:
        out = [f"short strike delta above {0.40:.2f}",
               f"realised vol rising through implied ({sb.atm_iv * 100:.0f}%)",
               f"{c.dte - 7} more days then time-exit at 7 DTE"]
        if c.structure in (BULL_PUT_SPREAD, IRON_CONDOR):
            lowest = min((lg.strike for lg in c.legs if lg.kind == "put"), default=0)
            out.append(f"spot below {lowest:g}")
        if c.structure in (BEAR_CALL_SPREAD, IRON_CONDOR):
            highest = max((lg.strike for lg in c.legs if lg.kind == "call"), default=0)
            out.append(f"spot above {highest:g}")
        return out

    # ── builders ─────────────────────────────────────────────────────────────

    def _vertical(self, sb, regime, vol, structure, kind, short_target,
                  expiry, dte, prior, cid):
        """Credit vertical: sell near the money, buy the wing `width` away."""
        pool = self._slice(sb, kind, expiry)
        short = self._short_leg(pool, short_target)
        if short is None:
            return None
        for want in self._widths(sb.spot, credit=True):
            target = short.strike - want if kind == "put" else short.strike + want
            long_c = self._near_strike(pool, target, want * 0.5)
            if long_c is None:
                continue
            width = abs(short.strike - long_c.strike)
            if width <= 0:
                continue
            legs = [self._leg(short, "sell"), self._leg(long_c, "buy")]
            cand = self._finalise(cid, sb, regime, vol, structure, legs, expiry,
                                  dte, width, True, prior)
            if cand:
                return cand
        return None

    def _debit_vertical(self, sb, regime, vol, structure, kind, expiry, dte,
                        prior, cid):
        """Debit vertical: buy ~0.55 delta, sell ~0.28 delta above/below it."""
        pool = self._slice(sb, kind, expiry)
        long_c = self._long_leg_by_delta(pool, 0.55)
        if long_c is None:
            return None
        for want in self._widths(sb.spot, credit=False):
            target = long_c.strike + want if kind == "call" else long_c.strike - want
            short = self._near_strike(pool, target, want * 0.5)
            if short is None:
                continue
            width = abs(long_c.strike - short.strike)
            if width <= 0:
                continue
            legs = [self._leg(long_c, "buy"), self._leg(short, "sell")]
            cand = self._finalise(cid, sb, regime, vol, structure, legs, expiry,
                                  dte, width, False, prior)
            if cand:
                return cand
        return None

    def _condor(self, sb, regime, vol, expiry, dte, prior, cid):
        puts = self._slice(sb, "put", expiry)
        calls = self._slice(sb, "call", expiry)
        sp = self._short_leg(puts, 0.16)
        sc = self._short_leg(calls, 0.16)
        if sp is None or sc is None:
            return None
        for want in self._widths(sb.spot, credit=True):
            lp = self._near_strike(puts, sp.strike - want, want * 0.5)
            lc = self._near_strike(calls, sc.strike + want, want * 0.5)
            if lp is None or lc is None:
                continue
            # Only one wing of a condor can finish in the money, so the risk is
            # the wider of the two sides, not their sum.
            width = max(abs(sp.strike - lp.strike), abs(lc.strike - sc.strike))
            if width <= 0:
                continue
            legs = [self._leg(sp, "sell"), self._leg(lp, "buy"),
                    self._leg(sc, "sell"), self._leg(lc, "buy")]
            cand = self._finalise(cid, sb, regime, vol, IRON_CONDOR, legs, expiry,
                                  dte, width, True, prior)
            if cand:
                return cand
        return None

    @staticmethod
    def _leg(c, side: str) -> Leg:
        return Leg(symbol=c.symbol, side=side, ratio=1, kind=c.kind,
                   strike=c.strike, expiry=c.expiry, mid=c.mid, bid=c.bid,
                   ask=c.ask, delta=c.delta, iv=c.iv,
                   open_interest=c.open_interest)

    # ── entry point ──────────────────────────────────────────────────────────

    def design(self, sb, regime, vol, bandit=None) -> list:
        """Every structure this symbol currently justifies, best score first."""
        if sb.error or not sb.chain or vol.edge == "none":
            return []
        tenors = self._expiries(sb)
        if not tenors:
            return []

        notes = " ".join(regime.notes).lower()
        no_short_puts = "do not sell puts" in notes
        no_short_calls = "do not sell calls" in notes

        def prior_for(structure: str) -> float:
            return bandit.prior(regime.label, structure) if bandit else 0.5

        out = []
        for expiry, dte in tenors:
            tag = f"{dte}d"
            if vol.edge == "sell":
                if regime.label == VOLATILE:
                    continue   # never sell fresh gamma into a vol shock
                if regime.label == BULL and not no_short_puts:
                    out.append(self._vertical(sb, regime, vol, BULL_PUT_SPREAD, "put",
                                              0.20, expiry, dte,
                                              prior_for(BULL_PUT_SPREAD),
                                              f"{sb.symbol}-BPS-{tag}-{expiry}"))
                if regime.label == BEAR and not no_short_calls:
                    out.append(self._vertical(sb, regime, vol, BEAR_CALL_SPREAD, "call",
                                              0.20, expiry, dte,
                                              prior_for(BEAR_CALL_SPREAD),
                                              f"{sb.symbol}-BCS-{tag}-{expiry}"))
                if regime.label == CHOP and not (no_short_puts or no_short_calls):
                    out.append(self._condor(sb, regime, vol, expiry, dte,
                                            prior_for(IRON_CONDOR),
                                            f"{sb.symbol}-IC-{tag}-{expiry}"))
                # In chop with a bullish tilt a put spread is often better than a
                # condor: one tested strike instead of two, for similar credit.
                if regime.label == CHOP and regime.trend_score > 0.05 and not no_short_puts:
                    out.append(self._vertical(sb, regime, vol, BULL_PUT_SPREAD, "put",
                                              0.16, expiry, dte,
                                              prior_for(BULL_PUT_SPREAD),
                                              f"{sb.symbol}-BPS2-{tag}-{expiry}"))
            elif vol.edge == "buy":
                if regime.label == BULL:
                    out.append(self._debit_vertical(sb, regime, vol, LONG_CALL_SPREAD,
                                                    "call", expiry, dte,
                                                    prior_for(LONG_CALL_SPREAD),
                                                    f"{sb.symbol}-LCS-{tag}-{expiry}"))
                elif regime.label == BEAR:
                    out.append(self._debit_vertical(sb, regime, vol, LONG_PUT_SPREAD,
                                                    "put", expiry, dte,
                                                    prior_for(LONG_PUT_SPREAD),
                                                    f"{sb.symbol}-LPS-{tag}-{expiry}"))

        built = [c for c in out if c is not None]
        for c in built:
            log("DEBUG", "designed", cid=c.cid, score=c.score,
                edge=round(c.edge_ratio, 3), credit=c.net_price)
        return sorted(built, key=lambda c: c.score, reverse=True)
