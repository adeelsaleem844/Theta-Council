"""Market intake: turn Alpaca's raw payloads into the brief the council reads.

One object, `MarketBrief`, is the single source of truth for a cycle. Every
agent - including the LLM - sees exactly this and nothing else, which is what
makes a decision reproducible from the journal.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from . import blackscholes as bs
from . import indicators as ind
from . import occ
from .transports import (ACCOUNT, CLOCK, OPTION_CHAIN, POSITIONS, STOCK_BARS)
from .util import log, safe_float, utcnow


@dataclass
class Contract:
    """One option, with everything the desk needs to price and gate it."""
    symbol: str
    underlying: str
    kind: str
    strike: float
    expiry: str
    dte: int
    bid: float
    ask: float
    mid: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: float

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 999.0
        return 100.0 * (self.ask - self.bid) / self.mid

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)

    def liquid(self, min_oi: int, max_spread_pct: float) -> bool:
        if self.bid <= 0 or self.ask <= 0 or self.mid <= 0:
            return False
        if self.open_interest and self.open_interest < min_oi:
            return False
        # A 4c-wide market on a 30c option is fine even though it is 13% of mid.
        return self.spread_pct <= max_spread_pct or (self.ask - self.bid) <= 0.06


@dataclass
class SymbolBrief:
    symbol: str
    spot: float = 0.0
    ema20: float = 0.0
    ema50: float = 0.0
    rsi14: float = 50.0
    atr14: float = 0.0
    atr_pct: float = 0.0
    rv10: float = 0.0
    rv20: float = 0.0
    rv60: float = 0.0
    rv_percentile: float = 50.0
    slope20: float = 0.0
    zscore20: float = 0.0
    atm_iv: float = 0.0
    vrp_ratio: float = 0.0        # atm_iv / rv20 - the core edge signal
    iv_rank: float = 50.0         # percentile of atm_iv in our own history
    chain: list = field(default_factory=list)
    expiries: list = field(default_factory=list)
    error: str = ""

    def compact(self) -> dict:
        """The trimmed view handed to the LLM - numbers only, no chain dump."""
        return {
            "symbol": self.symbol,
            "spot": round(self.spot, 2),
            "trend_ema20_over_50": round(self.ema20 / self.ema50 - 1, 4) if self.ema50 else 0,
            "rsi14": round(self.rsi14, 1),
            "atr_pct": round(self.atr_pct, 2),
            "rv20_pct": round(self.rv20 * 100, 1),
            "rv_percentile": round(self.rv_percentile, 0),
            "atm_iv_pct": round(self.atm_iv * 100, 1),
            "vrp_ratio": round(self.vrp_ratio, 3),
            "iv_rank": round(self.iv_rank, 0),
            "slope20": round(self.slope20 * 100, 3),
            "contracts_liquid": len(self.chain),
        }


@dataclass
class MarketBrief:
    asof: str
    is_open: bool
    account: dict
    positions: list
    symbols: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    @property
    def equity(self) -> float:
        return safe_float(self.account.get("equity"), 0.0)

    def option_positions(self) -> list:
        return [p for p in self.positions if occ.is_option(p["symbol"])]

    def compact(self) -> dict:
        return {
            "asof": self.asof,
            "market_open": self.is_open,
            "equity": round(self.equity, 2),
            "cash": round(safe_float(self.account.get("cash")), 2),
            "options_buying_power": round(safe_float(self.account.get("options_buying_power")), 2),
            "options_level": self.account.get("options_trading_level"),
            "open_option_legs": len(self.option_positions()),
            "symbols": [s.compact() for s in self.symbols.values() if not s.error],
        }


# ── chain parsing ────────────────────────────────────────────────────────────

def _quote(snap: dict):
    q = snap.get("latestQuote") or snap.get("latest_quote") or {}
    bid = safe_float(q.get("bp", q.get("bid_price")))
    ask = safe_float(q.get("ap", q.get("ask_price")))
    return bid, ask


def contract_from_snapshot(symbol: str, snap: dict, spot: float,
                           today: _dt.date) -> Contract | None:
    meta = occ.parse(symbol)
    if not meta:
        return None
    bid, ask = _quote(snap)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else safe_float(
        (snap.get("latestTrade") or {}).get("p"))
    dte = (_dt.date.fromisoformat(meta["expiry"]) - today).days
    greeks = snap.get("greeks") or {}
    iv = safe_float(snap.get("impliedVolatility", snap.get("implied_volatility")))

    # The free `indicative` feed can omit greeks and IV. Solve for them rather
    # than dropping the contract - a missing delta must never silently become 0.
    t = bs.years_to_expiry(dte)
    if iv <= 0 and mid > 0 and t > 0:
        iv = bs.implied_vol(mid, spot, meta["strike"], t, meta["kind"]) or 0.0
    d = safe_float(greeks.get("delta"))
    if d == 0 and iv > 0 and t > 0:
        d = bs.delta(spot, meta["strike"], t, iv, meta["kind"])
    g = safe_float(greeks.get("gamma")) or (
        bs.gamma(spot, meta["strike"], t, iv) if iv > 0 else 0.0)
    th = safe_float(greeks.get("theta")) or (
        bs.theta(spot, meta["strike"], t, iv, meta["kind"]) if iv > 0 else 0.0)
    vg = safe_float(greeks.get("vega")) or (
        bs.vega(spot, meta["strike"], t, iv) if iv > 0 else 0.0)

    daily = snap.get("dailyBar") or {}
    oi = int(safe_float(snap.get("openInterest", snap.get("open_interest"))))
    return Contract(
        symbol=symbol, underlying=meta["root"], kind=meta["kind"],
        strike=meta["strike"], expiry=meta["expiry"], dte=dte,
        bid=bid, ask=ask, mid=round(mid, 4), iv=iv, delta=d, gamma=g,
        theta=th, vega=vg, open_interest=oi, volume=safe_float(daily.get("v")),
    )


# ── intake ───────────────────────────────────────────────────────────────────

class MarketIntake:
    def __init__(self, cfg, router, journal=None):
        self.cfg = cfg
        self.router = router
        self.journal = journal

    def build(self) -> MarketBrief:
        risk = self.cfg.risk
        today = _dt.date.today()
        errors = []

        try:
            clock = self.router.call(CLOCK)
        except Exception as exc:  # noqa: BLE001
            clock = {"is_open": False}
            errors.append(f"clock: {exc}")

        account = self.router.call(ACCOUNT)
        try:
            positions = self.router.call(POSITIONS)
        except Exception as exc:  # noqa: BLE001
            positions, _ = [], errors.append(f"positions: {exc}")

        brief = MarketBrief(
            asof=utcnow().isoformat(timespec="seconds"),
            is_open=bool(clock.get("is_open")),
            account=account, positions=positions or [], clock=clock, errors=errors,
        )

        span_lo = min(lo for lo, _ in risk.tenors)
        span_hi = max(hi for _, hi in risk.tenors)
        exp_gte = (today + _dt.timedelta(days=max(0, span_lo - 2))).isoformat()
        exp_lte = (today + _dt.timedelta(days=span_hi + 6)).isoformat()

        for sym in self.cfg.universe:
            sb = SymbolBrief(symbol=sym)
            try:
                bars = self.router.call(STOCK_BARS, symbol=sym, timeframe="1Day",
                                        start=(today - _dt.timedelta(days=400)).isoformat(),
                                        limit=260)
                closes = [b["c"] for b in bars if b["c"] > 0]
                if len(closes) < 60:
                    raise ValueError(f"only {len(closes)} daily bars")
                highs = [b["h"] for b in bars]
                lows = [b["l"] for b in bars]

                sb.spot = closes[-1]
                sb.ema20 = ind.ema(closes, 20) or sb.spot
                sb.ema50 = ind.ema(closes, 50) or sb.spot
                sb.rsi14 = ind.rsi(closes, 14) or 50.0
                sb.atr14 = ind.atr(highs, lows, closes, 14) or 0.0
                sb.atr_pct = 100.0 * sb.atr14 / sb.spot if sb.spot else 0.0
                sb.rv10 = ind.realized_vol(closes, 10) or 0.0
                sb.rv20 = ind.realized_vol(closes, 20) or 0.0
                sb.rv60 = ind.realized_vol(closes, 60) or 0.0
                sb.slope20 = ind.linreg_slope(closes[-20:]) or 0.0
                sb.zscore20 = ind.zscore(closes, 20) or 0.0

                rv_hist = []
                for i in range(60, len(closes)):
                    v = ind.realized_vol(closes[: i + 1], 20)
                    if v:
                        rv_hist.append(v)
                sb.rv_percentile = ind.percentile_rank(rv_hist, sb.rv20) or 50.0

                snaps = self.router.call(
                    OPTION_CHAIN, underlying=sym, exp_gte=exp_gte, exp_lte=exp_lte,
                    strike_gte=round(sb.spot * 0.80, 2),
                    strike_lte=round(sb.spot * 1.20, 2))
                chain = []
                for osym, snap in (snaps or {}).items():
                    c = contract_from_snapshot(osym, snap, sb.spot, today)
                    if c is None or c.dte < 1:
                        continue
                    if c.liquid(risk.min_open_interest, risk.max_spread_pct_of_mid):
                        chain.append(c)
                sb.chain = chain
                sb.expiries = sorted({c.expiry for c in chain})
                sb.atm_iv = atm_iv(chain, sb.spot,
                                   target_dte=max(14, min(30, span_hi)))
                sb.vrp_ratio = (sb.atm_iv / sb.rv20) if sb.rv20 > 0 else 0.0

                if self.journal is not None:
                    self.journal.record_iv(sym, sb.atm_iv, sb.rv20)
                    hist = self.journal.iv_history(sym, lookback=120)
                    sb.iv_rank = ind.percentile_rank(hist, sb.atm_iv) or 50.0

                log("INFO", f"intake {sym}", spot=round(sb.spot, 2),
                    rv=round(sb.rv20 * 100, 1), iv=round(sb.atm_iv * 100, 1),
                    vrp=round(sb.vrp_ratio, 2), liquid=len(chain))
            except Exception as exc:  # noqa: BLE001
                sb.error = str(exc)[:200]
                log("WARN", f"intake {sym} failed", error=sb.error)
            brief.symbols[sym] = sb

        return brief


def atm_iv(chain: list, spot: float, target_dte: int = 30) -> float:
    """Average the call and put IV nearest the money at the nearest expiry to
    `target_dte`. Averaging the two sides cancels most of the skew bias."""
    if not chain or spot <= 0:
        return 0.0
    expiries = sorted({c.expiry for c in chain})
    if not expiries:
        return 0.0
    by_dte = {}
    for c in chain:
        by_dte.setdefault(c.expiry, c.dte)
    target_exp = min(expiries, key=lambda e: abs(by_dte.get(e, 0) - target_dte))
    slice_ = [c for c in chain if c.expiry == target_exp and c.iv > 0]
    if not slice_:
        return 0.0
    picks = []
    for kind in ("call", "put"):
        side = [c for c in slice_ if c.kind == kind]
        if side:
            picks.append(min(side, key=lambda c: abs(c.strike - spot)).iv)
    return sum(picks) / len(picks) if picks else 0.0
