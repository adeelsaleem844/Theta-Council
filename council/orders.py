"""Order construction and submission.

Alpaca expresses a spread as a single `mleg` order: one order_class, one net
limit price, and a legs array where each leg carries a `position_intent`. That
matters for a credit spread - sending the legs separately would momentarily
leave a naked short option, which is both a different risk profile and a higher
options level than this desk runs at.

One live API nuance is handled defensively. Alpaca reports credits as negative
and debits as positive, so a credit spread's net `limit_price` is negative. The
convention has moved between releases, so the first credit order of a session
is submitted with the documented sign and, if the broker rejects it as a price
error, retried once with the sign flipped. Whichever works is remembered for the
rest of the run and written to the journal. Guessing silently is how order bugs
survive to production; this guesses once, out loud, and then stops guessing.
"""
from __future__ import annotations

from .transports import SUBMIT_ORDER, TransportError
from .util import log

# Learned once per process: -1 means "credits are sent negative".
_CREDIT_SIGN = [-1.0]
_SIGN_LEARNED = [False]

PRICE_ERROR_HINTS = ("limit_price", "limit price", "price must", "invalid price",
                     "sub-penny", "42210000", "must be positive", "negative")


def tick_for(premium: float) -> float:
    """US option quoting increments: a penny under $3, a nickel above."""
    return 0.01 if abs(premium) < 3.0 else 0.05


def round_to_tick(px: float, *, favour: str) -> float:
    """`favour='us'` rounds in the direction that improves our fill odds."""
    t = tick_for(px)
    n = px / t
    import math
    n = math.floor(n) if favour == "us" else math.ceil(n)
    return round(n * t, 2)


def entry_limit(cand) -> float:
    """The price we underwrote. We do not chase past our own edge calculation."""
    if cand.is_credit:
        # Ask for slightly less than mid so the order actually fills; never less
        # than the haircut price the EV was computed at.
        return max(0.01, round_to_tick(cand.net_price, favour="us"))
    return round_to_tick(cand.net_price, favour="them")


def build_entry(cand, client_order_id: str = None) -> dict:
    legs = [{
        "symbol": lg.symbol,
        "ratio_qty": str(int(lg.ratio)),
        "side": lg.side,
        "position_intent": lg.position_intent,
    } for lg in cand.legs]

    net = entry_limit(cand)
    signed = net * (_CREDIT_SIGN[0] if cand.is_credit else 1.0)
    payload = {
        "order_class": "mleg",
        "qty": str(int(cand.qty)),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": f"{signed:.2f}",
        "legs": legs,
    }
    if client_order_id:
        payload["client_order_id"] = client_order_id[:48]
    return payload


def build_exit(trade: dict, close_value: float, urgency: str = "normal") -> dict:
    """Close every leg in one order, reversing each side."""
    legs = []
    for lg in trade.get("legs") or []:
        opened_buy = lg.get("side") == "buy"
        legs.append({
            "symbol": lg["symbol"],
            "ratio_qty": str(int(lg.get("ratio", 1) or 1)),
            "side": "sell" if opened_buy else "buy",
            "position_intent": "sell_to_close" if opened_buy else "buy_to_close",
        })

    # Closing a credit structure means paying `close_value`. Pay up when the
    # exit is mandatory (time cutoff, stop, delta breach) - a stop that does not
    # fill is not a stop.
    pad = {"urgent": 0.18, "normal": 0.08}.get(urgency, 0.08)
    is_credit = bool(trade.get("is_credit", True))
    if is_credit:
        px = max(0.01, abs(close_value) * (1.0 + pad)) + (0.05 if urgency == "urgent" else 0.0)
        signed = round_to_tick(px, favour="them")           # a debit to close
    else:
        px = max(0.01, abs(close_value) * (1.0 - pad))
        signed = -round_to_tick(px, favour="us") if _CREDIT_SIGN[0] < 0 else round_to_tick(px, favour="us")

    return {
        "order_class": "mleg",
        "qty": str(int(trade.get("qty") or 1)),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": f"{signed:.2f}",
        "legs": legs,
    }


def _flip_sign(payload: dict) -> dict:
    out = dict(payload)
    try:
        out["limit_price"] = f"{-float(payload['limit_price']):.2f}"
    except (KeyError, TypeError, ValueError):
        return out
    return out


def submit(router, payload: dict, *, credit: bool, dry_run: bool = False) -> dict:
    """Submit, and learn the broker's credit-sign convention exactly once."""
    if dry_run:
        # Prefer the Alpaca CLI's own --dry-run: the broker validates and echoes
        # the request body it would have accepted. That is real evidence the
        # order is well formed, rather than the desk approving its own homework.
        cli = router.transport("cli") if hasattr(router, "transport") else None
        if cli is not None:
            try:
                echo = cli.call(SUBMIT_ORDER, payload=payload, cli_dry_run=True)
                log("INFO", "DRY RUN - validated by the broker, not sent",
                    limit=payload.get("limit_price"),
                    legs=len(payload.get("legs") or []), qty=payload.get("qty"))
                return {"dry_run": True, "broker_validated": True,
                        "echo": echo, "payload": payload,
                        "status": "validated_not_sent"}
            except Exception as exc:  # noqa: BLE001
                log("DEBUG", "cli --dry-run unavailable, printing locally",
                    error=str(exc)[:160])
        log("INFO", "DRY RUN - order not sent", limit=payload.get("limit_price"),
            legs=len(payload.get("legs") or []), qty=payload.get("qty"))
        return {"dry_run": True, "payload": payload, "status": "not_sent"}

    try:
        order = router.call(SUBMIT_ORDER, payload=payload)
        if credit and not _SIGN_LEARNED[0]:
            _SIGN_LEARNED[0] = True
            log("INFO", "credit sign convention confirmed", sign=_CREDIT_SIGN[0])
        return order
    except TransportError as exc:
        msg = str(exc).lower()
        if not credit or _SIGN_LEARNED[0] or not any(h in msg for h in PRICE_ERROR_HINTS):
            raise
        flipped = _flip_sign(payload)
        log("WARN", "broker rejected the net credit price, retrying with the "
                    "opposite sign convention",
            first=payload.get("limit_price"), retry=flipped.get("limit_price"))
        order = router.call(SUBMIT_ORDER, payload=flipped)
        _CREDIT_SIGN[0] *= -1.0
        _SIGN_LEARNED[0] = True
        log("INFO", "credit sign convention learned", sign=_CREDIT_SIGN[0])
        return order


def credit_sign() -> float:
    return _CREDIT_SIGN[0]


def describe(payload: dict) -> str:
    legs = payload.get("legs") or []
    parts = [f"{'+' if lg['side'] == 'buy' else '-'}{lg['symbol']}" for lg in legs]
    return (f"mleg qty={payload.get('qty')} net={payload.get('limit_price')} "
            f"[{' '.join(parts)}]")
