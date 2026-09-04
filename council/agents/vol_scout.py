"""Agent 2 - the Volatility Scout.

This is where the desk's actual edge is decided, so it is worth being precise
about the claim being made.

Selling an option is selling a forecast of movement. The market prices that
forecast at implied volatility; the underlying then delivers realised
volatility. Across liquid US equity index options the first number is, on
average, larger than the second - the variance risk premium. That gap is not a
prediction about direction. It is a fee paid to whoever is willing to carry
gamma risk.

So the Scout does not ask "will this go up?". It asks:

    is the market currently paying more for movement than this underlying has
    recently been delivering?          vrp_ratio = ATM implied / 20d realised

Above ~1.12 we are being paid a real premium and the desk wants to be short
options. Below ~0.98 options are cheap relative to delivered movement and the
desk would rather own them. In between, we stand aside - which is most of the
time, and is the point.
"""
from __future__ import annotations

from ..util import clamp
from .types import VolView


class VolScout:
    name = "vol_scout"

    def __init__(self, cfg):
        self.cfg = cfg

    def read(self, sb) -> VolView:
        r = self.cfg.risk
        v = VolView(symbol=sb.symbol, vrp_ratio=sb.vrp_ratio, iv=sb.atm_iv,
                    rv=sb.rv20, iv_rank=sb.iv_rank)

        if sb.error or sb.atm_iv <= 0 or sb.rv20 <= 0:
            v.notes.append("no usable implied/realised pair")
            return v

        if not sb.chain:
            v.notes.append("chain failed liquidity screen entirely")
            return v

        if sb.vrp_ratio >= r.min_vrp_ratio:
            v.edge = "sell"
            # Scale to 1.0 at a ratio of 1.45 - beyond that the extra premium is
            # usually compensation for a real event, not a gift.
            v.score = clamp((sb.vrp_ratio - 1.0) / 0.45, 0.0, 1.0)
            v.notes.append(
                f"IV {sb.atm_iv * 100:.1f}% vs RV {sb.rv20 * 100:.1f}% "
                f"= {sb.vrp_ratio:.2f}x: paid to be short movement")
        elif sb.vrp_ratio <= r.max_vrp_ratio_for_debit:
            v.edge = "buy"
            v.score = clamp((1.0 - sb.vrp_ratio) / 0.25, 0.0, 1.0)
            v.notes.append(
                f"IV {sb.atm_iv * 100:.1f}% under RV {sb.rv20 * 100:.1f}%: "
                f"movement is on sale, prefer long premium")
        else:
            v.notes.append(
                f"VRP {sb.vrp_ratio:.2f}x sits in the no-trade band "
                f"({r.max_vrp_ratio_for_debit:.2f}-{r.min_vrp_ratio:.2f})")
            return v

        # An extreme IV rank on top of a high VRP is usually an event, not an
        # edge. Haircut the score rather than blocking - the Risk Officer owns
        # the veto, the Scout only owns the opinion.
        if v.edge == "sell" and sb.iv_rank >= 92:
            v.score *= 0.6
            v.notes.append(f"IV rank {sb.iv_rank:.0f}: possible event premium, size down")
        if v.edge == "sell" and sb.rv_percentile >= 85:
            v.score *= 0.7
            v.notes.append(f"realised vol at {sb.rv_percentile:.0f}th pct: gamma is expensive")

        # Term-structure sanity: if 10d realised is running far above 20d, the
        # delivered vol is accelerating and today's IV is probably not enough.
        if v.edge == "sell" and sb.rv10 > sb.rv20 * 1.35:
            v.score *= 0.55
            v.notes.append("short-window realised vol accelerating, discount the premium")

        return v
