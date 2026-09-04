"""REST transport - the guaranteed fallback, stdlib urllib only.

This is the pipe of last resort. It exists so the desk never stalls because a
CLI binary is missing or an MCP server failed to boot on a judge's machine.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..util import log
from .base import (ACCOUNT, CALENDAR, CANCEL_ORDER, CLOCK, CLOSE_POSITION,
                   OPTION_CHAIN, OPTION_CONTRACTS, ORDERS, POSITIONS,
                   STOCK_BARS, SUBMIT_ORDER, Transport, TransportError,
                   Unsupported, norm_account, norm_bar, norm_position)

TIMEOUT = 25


class RestTransport(Transport):
    name = "rest"

    def __init__(self, cfg):
        self.cfg = cfg

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.cfg.api_key,
            "APCA-API-SECRET-KEY": self.cfg.secret_key,
            "accept": "application/json",
            "content-type": "application/json",
            "User-Agent": "theta-council/1.0",
        }

    def _request(self, method: str, url: str, params: dict = None, body: dict = None):
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise TransportError(f"HTTP {exc.code} {method} {url} :: {detail}") from exc
        except Exception as exc:
            raise TransportError(f"{method} {url} :: {exc}") from exc
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise TransportError(f"bad JSON from {url}: {text[:200]}") from exc

    def _paged(self, url: str, params: dict, key: str):
        """Walk Alpaca's page_token pagination and merge `key` from each page."""
        out, token, pages = None, None, 0
        while pages < 20:
            page = dict(params)
            if token:
                page["page_token"] = token
            payload = self._request("GET", url, params=page)
            chunk = payload.get(key)
            if out is None:
                out = chunk
            elif isinstance(out, dict) and isinstance(chunk, dict):
                out.update(chunk)
            elif isinstance(out, list) and isinstance(chunk, list):
                out.extend(chunk)
            token = payload.get("next_page_token")
            pages += 1
            if not token:
                break
        return out if out is not None else ({} if key == "snapshots" else [])

    # ── contract ─────────────────────────────────────────────────────────────

    def available(self) -> bool:
        return self.cfg.has_keys

    def supports(self, intent: str) -> bool:
        return True

    def call(self, intent: str, **kw):
        fn = getattr(self, f"_i_{intent}", None)
        if fn is None:
            raise Unsupported(f"rest cannot serve {intent}")
        return fn(**kw)

    # ── trading api ──────────────────────────────────────────────────────────

    def _i_clock(self):
        r = self._request("GET", f"{self.cfg.trading_base}/v2/clock")
        return {
            "timestamp": r.get("timestamp", ""),
            "is_open": bool(r.get("is_open")),
            "next_open": r.get("next_open", ""),
            "next_close": r.get("next_close", ""),
        }

    def _i_calendar(self, start: str = None, end: str = None):
        return self._request("GET", f"{self.cfg.trading_base}/v2/calendar",
                             params={"start": start, "end": end})

    def _i_account(self):
        return norm_account(self._request("GET", f"{self.cfg.trading_base}/v2/account"))

    def _i_positions(self):
        raw = self._request("GET", f"{self.cfg.trading_base}/v2/positions")
        return [norm_position(p) for p in (raw or [])]

    def _i_orders(self, status: str = "open", limit: int = 200, after: str = None):
        raw = self._request("GET", f"{self.cfg.trading_base}/v2/orders",
                            params={"status": status, "limit": limit,
                                    "nested": "true", "after": after})
        return raw or []

    def _i_submit_order(self, payload: dict):
        return self._request("POST", f"{self.cfg.trading_base}/v2/orders", body=payload)

    def _i_cancel_order(self, order_id: str):
        self._request("DELETE", f"{self.cfg.trading_base}/v2/orders/{order_id}")
        return {"cancelled": order_id}

    def _i_close_position(self, symbol: str, qty=None, percentage=None):
        params = {}
        if qty is not None:
            params["qty"] = qty
        elif percentage is not None:
            params["percentage"] = percentage
        sym = urllib.parse.quote(symbol, safe="")
        return self._request("DELETE", f"{self.cfg.trading_base}/v2/positions/{sym}",
                             params=params)

    def _i_option_contracts(self, underlying: str, exp_gte: str = None,
                            exp_lte: str = None, kind: str = None,
                            strike_gte: float = None, strike_lte: float = None,
                            limit: int = 1000):
        raw = self._paged(f"{self.cfg.trading_base}/v2/options/contracts",
                          {"underlying_symbols": underlying, "status": "active",
                           "expiration_date_gte": exp_gte,
                           "expiration_date_lte": exp_lte,
                           "type": kind, "strike_price_gte": strike_gte,
                           "strike_price_lte": strike_lte, "limit": limit},
                          key="option_contracts")
        return raw or []

    # ── market data api ──────────────────────────────────────────────────────

    def _i_stock_bars(self, symbol: str, timeframe: str = "1Day",
                      start: str = None, limit: int = 260):
        payload = self._request("GET", f"{self.cfg.data_base}/v2/stocks/bars",
                                params={"symbols": symbol, "timeframe": timeframe,
                                        "start": start, "limit": limit,
                                        "adjustment": "all", "sort": "asc",
                                        "feed": self.cfg.stock_feed})
        bars = (payload.get("bars") or {}).get(symbol) or []
        return [norm_bar(b) for b in bars]

    def _i_option_chain(self, underlying: str, exp_gte: str = None,
                        exp_lte: str = None, strike_gte: float = None,
                        strike_lte: float = None, kind: str = None,
                        limit: int = 1000):
        url = f"{self.cfg.data_base}/v1beta1/options/snapshots/{underlying}"
        snaps = self._paged(url, {"feed": self.cfg.option_feed, "limit": limit,
                                  "expiration_date_gte": exp_gte,
                                  "expiration_date_lte": exp_lte,
                                  "strike_price_gte": strike_gte,
                                  "strike_price_lte": strike_lte,
                                  "type": kind}, key="snapshots")
        return snaps or {}
