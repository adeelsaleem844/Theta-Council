"""Indicators, hand-rolled so the desk carries zero third-party dependencies."""
from __future__ import annotations

import math

TRADING_DAYS = 252


def sma(xs, n: int):
    if len(xs) < n or n <= 0:
        return None
    return sum(xs[-n:]) / n


def ema(xs, n: int):
    if len(xs) < n or n <= 0:
        return None
    k = 2.0 / (n + 1.0)
    val = sum(xs[:n]) / n
    for x in xs[n:]:
        val = x * k + val * (1 - k)
    return val


def rsi(xs, n: int = 14):
    if len(xs) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def atr(highs, lows, closes, n: int = 14):
    if min(len(highs), len(lows), len(closes)) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    val = sum(trs[:n]) / n
    for tr in trs[n:]:
        val = (val * (n - 1) + tr) / n
    return val


def log_returns(closes):
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def realized_vol(closes, n: int = 20):
    """Annualised close-to-close volatility as a decimal (0.18 == 18%)."""
    rets = log_returns(closes)
    if len(rets) < n or n < 2:
        return None
    w = rets[-n:]
    mean = sum(w) / n
    var = sum((r - mean) ** 2 for r in w) / (n - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def percentile_rank(history, value: float):
    """Where `value` sits inside `history`, on a 0..100 scale."""
    vals = [v for v in history if v is not None]
    if len(vals) < 5:
        return None
    below = sum(1 for v in vals if v <= value)
    return 100.0 * below / len(vals)


def linreg_slope(xs):
    """Least-squares slope per bar, normalised by mean level (i.e. fraction/bar)."""
    n = len(xs)
    if n < 3:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(xs) / n
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(xs))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0 or mean_y == 0:
        return None
    return (num / den) / mean_y


def zscore(xs, n: int = 20):
    if len(xs) < n or n < 2:
        return None
    w = xs[-n:]
    mean = sum(w) / n
    var = sum((x - mean) ** 2 for x in w) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (xs[-1] - mean) / sd


def max_drawdown(equity):
    peak, mdd = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak)
    return mdd * 100.0
