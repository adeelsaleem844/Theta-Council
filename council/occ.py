"""OCC option symbol handling.

    NVDA260918P00170000
    ^root ^yymmdd ^kind ^strike x 1000, zero padded to 8

Alpaca uses unpadded roots, so parse from the right.
"""
from __future__ import annotations

import datetime as _dt


def build(underlying: str, expiry, kind: str, strike: float) -> str:
    if isinstance(expiry, str):
        expiry = _dt.date.fromisoformat(expiry[:10])
    c = "C" if kind.lower().startswith("c") else "P"
    return f"{underlying.upper()}{expiry.strftime('%y%m%d')}{c}{int(round(strike * 1000)):08d}"


def parse(symbol: str) -> dict:
    """Return root / expiry / kind / strike, or {} if this isn't an OCC symbol."""
    s = (symbol or "").strip().upper()
    if len(s) < 16:
        return {}
    strike_part, kind_part, date_part, root = s[-8:], s[-9], s[-15:-9], s[:-15]
    if not strike_part.isdigit() or not date_part.isdigit() or kind_part not in "CP":
        return {}
    if not root or not root.isalpha():
        return {}
    try:
        expiry = _dt.datetime.strptime(date_part, "%y%m%d").date()
    except ValueError:
        return {}
    return {
        "root": root,
        "expiry": expiry.isoformat(),
        "kind": "call" if kind_part == "C" else "put",
        "strike": int(strike_part) / 1000.0,
    }


def is_option(symbol: str) -> bool:
    return bool(parse(symbol))


def underlying_of(symbol: str) -> str:
    return parse(symbol).get("root", symbol)


def dte(symbol: str, today=None) -> int:
    p = parse(symbol)
    if not p:
        return 0
    ref = today or _dt.date.today()
    if isinstance(ref, str):
        ref = _dt.date.fromisoformat(ref[:10])
    return (_dt.date.fromisoformat(p["expiry"]) - ref).days


def describe(symbol: str) -> str:
    p = parse(symbol)
    if not p:
        return symbol
    return f"{p['root']} {p['expiry']} {p['strike']:g}{p['kind'][0].upper()}"
