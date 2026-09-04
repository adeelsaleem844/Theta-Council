"""Agent 1 - the Market Analyst.

Answers one question per symbol: which way is this thing leaning, and how much
should we believe it? Nothing here is clever. It is deliberately boring, because
the Analyst's job is to be a stable frame the rest of the council argues inside.

Trend is measured in ATR units rather than percent, so a 0.4% separation of the
moving averages means something different in SPY than it does in NVDA.
"""
from __future__ import annotations

from ..util import clamp
from .types import BEAR, BULL, CHOP, VOLATILE, Regime


class Analyst:
    name = "analyst"

    def __init__(self, cfg):
        self.cfg = cfg

    def read(self, sb) -> Regime:
        r = Regime(symbol=sb.symbol)
        if sb.error or sb.spot <= 0:
            r.notes.append(f"no data: {sb.error or 'empty'}")
            return r

        # Separation of the moving averages, expressed in daily ATRs.
        atr = sb.atr14 or (sb.spot * 0.01)
        sep_atr = (sb.ema20 - sb.ema50) / atr if atr else 0.0
        # 20-day regression slope, also in ATRs per day.
        slope_atr = (sb.slope20 * sb.spot) / atr if atr else 0.0

        trend = clamp(0.55 * clamp(sep_atr / 2.5, -1, 1) +
                      0.45 * clamp(slope_atr / 0.35, -1, 1), -1, 1)
        r.trend_score = trend

        # A volatility shock invalidates trend reading altogether: gaps break
        # short-gamma structures long before the moving averages notice.
        if sb.rv_percentile >= 90 or (sb.rv20 > 0 and sb.rv10 / max(sb.rv20, 1e-9) > 1.6):
            r.label = VOLATILE
            r.conviction = clamp(sb.rv_percentile / 100.0, 0.4, 1.0)
            r.notes.append(
                f"vol shock: rv20 at {sb.rv_percentile:.0f}th pct, "
                f"rv10/rv20={sb.rv10 / max(sb.rv20, 1e-9):.2f}")
            return r

        if trend > 0.30:
            r.label = BULL
        elif trend < -0.30:
            r.label = BEAR
        else:
            r.label = CHOP
        r.conviction = clamp(abs(trend), 0.0, 1.0)

        r.notes.append(f"ema20-ema50 = {sep_atr:+.2f} ATR, slope = {slope_atr:+.2f} ATR/day")

        # Exhaustion guards. These do not change the label - they attach a
        # warning the Architect reads when it picks which side to sell.
        if sb.rsi14 <= 25:
            r.notes.append(f"RSI {sb.rsi14:.0f}: capitulation, do not sell puts into it")
            r.conviction *= 0.5
        elif sb.rsi14 >= 78:
            r.notes.append(f"RSI {sb.rsi14:.0f}: extended, do not sell calls into it")
            r.conviction *= 0.7

        if abs(sb.zscore20) > 2.2:
            r.notes.append(f"price {sb.zscore20:+.1f} sd from its 20d mean, mean-reversion risk")
            r.conviction *= 0.75

        return r

    def portfolio_regime(self, regimes: dict) -> str:
        """The index complex sets the house view; single names cannot outvote it."""
        core = [regimes[s] for s in ("SPY", "QQQ", "IWM") if s in regimes]
        pool = core or list(regimes.values())
        if not pool:
            return CHOP
        if any(r.label == VOLATILE for r in core):
            return VOLATILE
        avg = sum(r.trend_score for r in pool) / len(pool)
        if avg > 0.25:
            return BULL
        if avg < -0.25:
            return BEAR
        return CHOP
