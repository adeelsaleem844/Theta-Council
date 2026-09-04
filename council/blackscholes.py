"""Black-Scholes-Merton, used two ways.

1. Alpaca's option snapshots carry greeks and implied vol on the OPRA feed. On
   the free `indicative` feed they can be missing, so we solve for them here.
2. The Risk Officer needs probability-of-touch and probability-ITM to size and
   to defend positions - neither is served by any API.
"""
from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)
DAYS_PER_YEAR = 365.0


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(s: float, k: float, t: float, v: float, r: float, q: float):
    vt = v * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * v * v) * t) / vt
    return d1, d1 - vt


def price(s: float, k: float, t: float, v: float, kind: str,
          r: float = 0.045, q: float = 0.0):
    """Option premium. `t` in years, `v` decimal vol, `kind` in {call, put}."""
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        intrinsic = max(s - k, 0.0) if kind == "call" else max(k - s, 0.0)
        return intrinsic
    d1, d2 = _d1_d2(s, k, t, v, r, q)
    df, dq = math.exp(-r * t), math.exp(-q * t)
    if kind == "call":
        return s * dq * norm_cdf(d1) - k * df * norm_cdf(d2)
    return k * df * norm_cdf(-d2) - s * dq * norm_cdf(-d1)


def delta(s: float, k: float, t: float, v: float, kind: str,
          r: float = 0.045, q: float = 0.0):
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        if kind == "call":
            return 1.0 if s > k else 0.0
        return -1.0 if s < k else 0.0
    d1, _ = _d1_d2(s, k, t, v, r, q)
    dq = math.exp(-q * t)
    return dq * norm_cdf(d1) if kind == "call" else -dq * norm_cdf(-d1)


def vega(s: float, k: float, t: float, v: float, r: float = 0.045, q: float = 0.0):
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        return 0.0
    d1, _ = _d1_d2(s, k, t, v, r, q)
    return s * math.exp(-q * t) * norm_pdf(d1) * math.sqrt(t)


def theta(s: float, k: float, t: float, v: float, kind: str,
          r: float = 0.045, q: float = 0.0):
    """Per-calendar-day theta."""
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        return 0.0
    d1, d2 = _d1_d2(s, k, t, v, r, q)
    df, dq = math.exp(-r * t), math.exp(-q * t)
    term = -(s * dq * norm_pdf(d1) * v) / (2.0 * math.sqrt(t))
    if kind == "call":
        annual = term - r * k * df * norm_cdf(d2) + q * s * dq * norm_cdf(d1)
    else:
        annual = term + r * k * df * norm_cdf(-d2) - q * s * dq * norm_cdf(-d1)
    return annual / DAYS_PER_YEAR


def gamma(s: float, k: float, t: float, v: float, r: float = 0.045, q: float = 0.0):
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        return 0.0
    d1, _ = _d1_d2(s, k, t, v, r, q)
    return math.exp(-q * t) * norm_pdf(d1) / (s * v * math.sqrt(t))


def implied_vol(target: float, s: float, k: float, t: float, kind: str,
                r: float = 0.045, q: float = 0.0):
    """Newton-Raphson with a bisection safety net. Returns None if no solution."""
    if target <= 0 or s <= 0 or k <= 0 or t <= 0:
        return None
    intrinsic = max(s - k, 0.0) if kind == "call" else max(k - s, 0.0)
    if target < intrinsic - 1e-6:
        return None

    v = 0.30
    for _ in range(60):
        diff = price(s, k, t, v, kind, r, q) - target
        if abs(diff) < 1e-6:
            return v
        dv = vega(s, k, t, v, r, q)
        if dv < 1e-8:
            break
        step = diff / dv
        v -= max(-0.5, min(0.5, step))
        if v <= 1e-4 or v > 6.0:
            break

    lo, hi = 1e-4, 6.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if price(s, k, t, mid, kind, r, q) > target:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            return 0.5 * (lo + hi)
    return None


def prob_itm(s: float, k: float, t: float, v: float, kind: str,
             r: float = 0.045, q: float = 0.0):
    """Risk-neutral probability of finishing in the money."""
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        return 1.0 if (s > k if kind == "call" else s < k) else 0.0
    _, d2 = _d1_d2(s, k, t, v, r, q)
    return norm_cdf(d2) if kind == "call" else norm_cdf(-d2)


def prob_touch(s: float, k: float, t: float, v: float):
    """Rule-of-thumb probability the underlying touches `k` before expiry.

    For a driftless GBM the probability of touching a barrier is roughly twice
    the probability of finishing beyond it. The Risk Officer uses this to reject
    short strikes that are statistically likely to be tested.
    """
    if s <= 0 or k <= 0 or t <= 0 or v <= 0:
        return 1.0
    kind = "call" if k > s else "put"
    return min(1.0, 2.0 * prob_itm(s, k, t, v, kind))


def years_to_expiry(dte: int) -> float:
    return max(dte, 0) / DAYS_PER_YEAR
