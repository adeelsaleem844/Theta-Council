"""The transport contract.

Theta Council never talks to Alpaca directly. It states an *intent* - "give me
the option chain", "submit this spread" - and the router picks a transport that
can serve it: the Alpaca CLI, the Alpaca MCP server, or the REST API.

Every reply is normalised into the same shape, so an agent cannot tell (and does
not care) which pipe served it. The journal records the pipe anyway, which is
what makes the CLI/MCP usage auditable rather than merely claimed.
"""
from __future__ import annotations

# ── intents ──────────────────────────────────────────────────────────────────
CLOCK = "clock"
CALENDAR = "calendar"
ACCOUNT = "account"
POSITIONS = "positions"
ORDERS = "orders"
SUBMIT_ORDER = "submit_order"
CANCEL_ORDER = "cancel_order"
CLOSE_POSITION = "close_position"
STOCK_BARS = "stock_bars"
OPTION_CONTRACTS = "option_contracts"
OPTION_CHAIN = "option_chain"

ALL_INTENTS = (
    CLOCK, CALENDAR, ACCOUNT, POSITIONS, ORDERS, SUBMIT_ORDER, CANCEL_ORDER,
    CLOSE_POSITION, STOCK_BARS, OPTION_CONTRACTS, OPTION_CHAIN,
)


class TransportError(RuntimeError):
    """The transport tried and failed. The router may fail over."""


class Unsupported(TransportError):
    """This transport cannot serve this intent at all. Fail over immediately."""


class Transport:
    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def supports(self, intent: str) -> bool:
        raise NotImplementedError

    def call(self, intent: str, **kw):
        raise NotImplementedError

    def close(self) -> None:
        pass


# ── normalisers ──────────────────────────────────────────────────────────────

def norm_account(raw: dict) -> dict:
    """Coerce an Alpaca account payload into the fields the desk reads."""
    def f(*keys, default=0.0):
        for k in keys:
            if raw.get(k) not in (None, ""):
                try:
                    return float(raw[k])
                except (TypeError, ValueError):
                    pass
        return default

    equity = f("equity", "portfolio_value")
    return {
        "id": str(raw.get("id") or raw.get("account_number") or ""),
        "account_number": str(raw.get("account_number") or ""),
        "equity": equity,
        "last_equity": f("last_equity", default=equity),
        "cash": f("cash"),
        "buying_power": f("buying_power"),
        "options_buying_power": f("options_buying_power", "buying_power"),
        "options_trading_level": int(f("options_trading_level", default=0)),
        "status": str(raw.get("status") or ""),
        "pattern_day_trader": bool(raw.get("pattern_day_trader")),
        "trading_blocked": bool(raw.get("trading_blocked")),
        "raw": raw,
    }


def norm_position(raw: dict) -> dict:
    def f(key, default=0.0):
        try:
            return float(raw.get(key) or default)
        except (TypeError, ValueError):
            return default

    symbol = str(raw.get("symbol") or "")
    return {
        "symbol": symbol,
        "asset_class": str(raw.get("asset_class") or ""),
        "is_option": len(symbol) > 12 and symbol[-9] in "CP",
        "qty": f("qty"),
        "side": str(raw.get("side") or ""),
        "avg_entry_price": f("avg_entry_price"),
        "current_price": f("current_price"),
        "market_value": f("market_value"),
        "cost_basis": f("cost_basis"),
        "unrealized_pl": f("unrealized_pl"),
        "unrealized_plpc": f("unrealized_plpc"),
        "raw": raw,
    }


def norm_bar(raw: dict) -> dict:
    return {
        "t": str(raw.get("t") or raw.get("timestamp") or ""),
        "o": float(raw.get("o", raw.get("open", 0)) or 0),
        "h": float(raw.get("h", raw.get("high", 0)) or 0),
        "l": float(raw.get("l", raw.get("low", 0)) or 0),
        "c": float(raw.get("c", raw.get("close", 0)) or 0),
        "v": float(raw.get("v", raw.get("volume", 0)) or 0),
    }
