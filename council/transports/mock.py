"""Mock transport - a self-contained market so the desk can be demonstrated
with the exchange closed and with no API keys at all.

It is not a toy stub: bars come from a seeded geometric Brownian motion, the
chain is priced with the same Black-Scholes module the live path uses, complete
with a volatility smile, an implied-vol level set above realised vol (so there
is a real variance risk premium to find), open interest and realistic bid/ask
width. Orders fill at the limit and mutate a position book.

    python -m council once --mock

...runs the entire five-agent pipeline end to end, offline, in about a second.
"""
from __future__ import annotations

import datetime as _dt
import math
import random

from .. import blackscholes as bs
from ..util import log
from .base import Transport, Unsupported, norm_account, norm_bar, norm_position

SEEDS = {"SPY": 11, "QQQ": 23, "IWM": 31, "AAPL": 47, "MSFT": 53, "NVDA": 61,
         "AMD": 71, "META": 83, "GOOGL": 97, "AMZN": 101}
SPOTS = {"SPY": 642.0, "QQQ": 573.0, "IWM": 233.0, "AAPL": 238.0, "MSFT": 512.0,
         "NVDA": 178.0, "AMD": 164.0, "META": 748.0, "GOOGL": 213.0, "AMZN": 232.0}
VOLS = {"SPY": 0.13, "QQQ": 0.17, "IWM": 0.20, "AAPL": 0.24, "MSFT": 0.22,
        "NVDA": 0.42, "AMD": 0.45, "META": 0.31, "GOOGL": 0.27, "AMZN": 0.29}
DRIFTS = {"SPY": 0.10, "QQQ": 0.13, "IWM": 0.03, "AAPL": 0.07, "MSFT": 0.08,
          "NVDA": 0.20, "AMD": -0.06, "META": 0.11, "GOOGL": 0.09, "AMZN": 0.05}


class MockTransport(Transport):
    name = "mock"

    def __init__(self, cfg):
        self.cfg = cfg
        self._bars: dict = {}
        self.equity = 100_000.0
        self.start_equity = 100_000.0
        self.positions: dict = {}
        self.orders: list = []
        self._seq = 0

    # ── contract ─────────────────────────────────────────────────────────────

    def available(self) -> bool:
        return True

    def supports(self, intent: str) -> bool:
        return hasattr(self, f"_i_{intent}")

    def call(self, intent: str, **kw):
        fn = getattr(self, f"_i_{intent}", None)
        if fn is None:
            raise Unsupported(f"mock cannot serve {intent}")
        return fn(**kw)

    # ── synthetic tape ───────────────────────────────────────────────────────

    def _series(self, symbol: str, n: int = 260):
        if symbol in self._bars:
            return self._bars[symbol]
        rnd = random.Random(SEEDS.get(symbol, abs(hash(symbol)) % 9999))
        spot = SPOTS.get(symbol, 100.0)
        vol = VOLS.get(symbol, 0.28)
        drift = DRIFTS.get(symbol, 0.05)
        dt = 1.0 / 252.0
        # Walk backwards from today's spot so the last close equals the spot.
        path = [spot]
        for _ in range(n):
            shock = rnd.gauss(0.0, 1.0)
            step = math.exp((drift - 0.5 * vol * vol) * dt + vol * math.sqrt(dt) * shock)
            path.append(path[-1] / step)
        path.reverse()
        today = _dt.date.today()
        bars = []
        for i, close in enumerate(path):
            day = today - _dt.timedelta(days=(len(path) - 1 - i))
            wiggle = close * vol * 0.02
            bars.append(norm_bar({
                "t": day.isoformat(),
                "o": round(close - rnd.uniform(-wiggle, wiggle), 2),
                "h": round(close + abs(rnd.uniform(0, wiggle * 1.6)), 2),
                "l": round(close - abs(rnd.uniform(0, wiggle * 1.6)), 2),
                "c": round(close, 2),
                "v": rnd.randint(2_000_000, 60_000_000),
            }))
        self._bars[symbol] = bars
        return bars

    def spot(self, symbol: str) -> float:
        return self._series(symbol)[-1]["c"]

    # ── intents ──────────────────────────────────────────────────────────────

    def _i_clock(self):
        # A simulated mid-session moment, with three hours left on the clock so
        # the Risk Officer's end-of-day gate behaves as it would intraday.
        now = _dt.datetime.now(_dt.timezone.utc)
        return {"timestamp": now.isoformat(), "is_open": True,
                "next_open": (now + _dt.timedelta(hours=20)).isoformat(),
                "next_close": (now + _dt.timedelta(hours=3)).isoformat()}

    def _i_calendar(self, start=None, end=None):
        return []

    def _i_account(self):
        opt_value = sum(p["market_value"] for p in self.positions.values())
        return norm_account({
            "id": "MOCK-PAPER-0000-0000",
            "account_number": "MOCKACCT",
            "equity": self.equity + opt_value,
            "last_equity": self.start_equity,
            "cash": self.equity,
            "buying_power": self.equity * 2,
            "options_buying_power": self.equity,
            "options_trading_level": 3,
            "status": "ACTIVE",
        })

    def _i_positions(self):
        return [norm_position(p["raw"]) for p in self.positions.values()]

    def _i_orders(self, status="open", limit=200, after=None):
        return self.orders[-limit:]

    def _i_cancel_order(self, order_id: str):
        return {"cancelled": order_id}

    def _i_close_position(self, symbol: str, qty=None, percentage=None):
        pos = self.positions.pop(symbol, None)
        if pos:
            self.equity += pos["market_value"]
        return {"closed": symbol}

    def _i_stock_bars(self, symbol: str, timeframe="1Day", start=None, limit=260):
        return self._series(symbol, 260)[-limit:]

    def _i_option_contracts(self, underlying: str, **kw):
        return []

    def _i_option_chain(self, underlying: str, exp_gte=None, exp_lte=None,
                        strike_gte=None, strike_lte=None, kind=None, limit=1000):
        spot = self.spot(underlying)
        rv = VOLS.get(underlying, 0.28)
        # Implied vol is set above realised so the Vol Scout finds a real edge -
        # exactly the regime premium-selling is designed for.
        base_iv = rv * 1.28
        rnd = random.Random(SEEDS.get(underlying, 7) * 3)
        today = _dt.date.today()
        snaps = {}
        # Weeklies plus monthlies, so short-dated tenor buckets have
        # something to land on, exactly like a real listed chain.
        for dte in (2, 5, 9, 16, 23, 30, 37, 44):
            expiry = today + _dt.timedelta(days=dte)
            if exp_gte and expiry.isoformat() < exp_gte[:10]:
                continue
            if exp_lte and expiry.isoformat() > exp_lte[:10]:
                continue
            t = bs.years_to_expiry(dte)
            step = max(1.0, round(spot * 0.01))
            lo = int((spot * 0.80) // step) * step
            hi = int((spot * 1.20) // step) * step
            strike = lo
            while strike <= hi:
                if strike_gte and strike < strike_gte:
                    strike += step
                    continue
                if strike_lte and strike > strike_lte:
                    break
                moneyness = math.log(max(strike, 0.01) / spot)
                iv = base_iv * (1.0 + 1.5 * moneyness * moneyness - 0.55 * moneyness)
                iv = max(0.05, iv)
                for k in ("call", "put"):
                    if kind and kind != k:
                        continue
                    mid = bs.price(spot, strike, t, iv, k)
                    if mid < 0.03:
                        continue
                    width = max(0.02, mid * rnd.uniform(0.012, 0.045))
                    snaps[occ_symbol(underlying, expiry, k, strike)] = {
                        "impliedVolatility": round(iv, 4),
                        "greeks": {
                            "delta": round(bs.delta(spot, strike, t, iv, k), 4),
                            "gamma": round(bs.gamma(spot, strike, t, iv), 6),
                            "theta": round(bs.theta(spot, strike, t, iv, k), 4),
                            "vega": round(bs.vega(spot, strike, t, iv), 4),
                        },
                        "latestQuote": {
                            "bp": round(max(0.01, mid - width), 2),
                            "ap": round(mid + width, 2),
                            "bs": rnd.randint(3, 90),
                            "as": rnd.randint(3, 90),
                        },
                        "latestTrade": {"p": round(mid, 2)},
                        "dailyBar": {"c": round(mid, 2),
                                     "v": rnd.randint(50, 9000)},
                        "openInterest": rnd.randint(300, 24000),
                    }
                strike += step
        return snaps

    def _i_submit_order(self, payload: dict):
        self._seq += 1
        oid = f"mock-{self._seq:04d}"
        legs = payload.get("legs") or [{
            "symbol": payload.get("symbol"), "side": payload.get("side"),
            "ratio_qty": "1"}]
        qty = float(payload.get("qty") or 1)
        net = float(payload.get("limit_price") or 0)
        self.equity -= net * 100.0 * qty
        for leg in legs:
            sym = leg.get("symbol")
            ratio = float(leg.get("ratio_qty") or 1) * qty
            signed = ratio if leg.get("side") == "buy" else -ratio
            prev = self.positions.get(sym)
            base = prev["qty"] if prev else 0.0
            newq = base + signed
            if abs(newq) < 1e-9:
                self.positions.pop(sym, None)
                continue
            self.positions[sym] = {
                "qty": newq,
                "market_value": 0.0,
                "raw": {"symbol": sym, "qty": str(newq), "asset_class": "us_option",
                        "side": "long" if newq > 0 else "short",
                        "avg_entry_price": "1.00", "current_price": "1.00",
                        "market_value": "0", "cost_basis": "0",
                        "unrealized_pl": "0", "unrealized_plpc": "0"},
            }
        order = {"id": oid, "status": "filled", "order_class":
                 payload.get("order_class", "simple"), "qty": str(qty),
                 "limit_price": str(net), "filled_avg_price": str(net),
                 "legs": legs, "symbol": payload.get("symbol", "")}
        self.orders.append(order)
        log("INFO", "mock fill", order=oid, net=net, qty=qty)
        return order


def occ_symbol(underlying: str, expiry, kind: str, strike: float) -> str:
    """Build an OCC option symbol: SPY  260918 P 00600000."""
    d = expiry.strftime("%y%m%d")
    c = "C" if kind == "call" else "P"
    return f"{underlying}{d}{c}{int(round(strike * 1000)):08d}"
