"""Alpaca MCP server transport - stdio JSON-RPC with runtime tool discovery.

Most projects that "use MCP" hard-code a tool name and hope. This one boots the
Alpaca MCP server, calls `tools/list`, reads the 60-odd advertised tools *and
their JSON schemas*, and then binds its own intents onto whatever the server
actually offers:

    intent SUBMIT_ORDER -> discovered tool `place_option_order`
                        -> arguments coerced to that tool's declared schema

So a rename upstream (`qty` -> `quantity`, `place_option_order` ->
`submit_option_order`) does not break the desk: the binding is late, not baked.
Run `python -m council mcp-doctor` to print the full discovered map.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time

from ..util import log
from .base import (ACCOUNT, CALENDAR, CANCEL_ORDER, CLOCK, CLOSE_POSITION,
                   OPTION_CHAIN, OPTION_CONTRACTS, ORDERS, POSITIONS,
                   STOCK_BARS, SUBMIT_ORDER, Transport, TransportError,
                   Unsupported, norm_account, norm_bar, norm_position)

PROTOCOL = "2025-06-18"
BOOT_TIMEOUT = 90
CALL_TIMEOUT = 45

# Preferred tool names per intent, best first. Anything not found falls through
# to token-overlap scoring against the live tool list.
PREFERRED = {
    CLOCK: ["get_clock"],
    CALENDAR: ["get_calendar"],
    ACCOUNT: ["get_account_info", "get_account"],
    POSITIONS: ["get_all_positions", "get_positions"],
    ORDERS: ["get_orders"],
    SUBMIT_ORDER: ["place_option_order", "submit_option_order", "place_order"],
    CANCEL_ORDER: ["cancel_order_by_id", "cancel_order"],
    CLOSE_POSITION: ["close_position"],
    STOCK_BARS: ["get_stock_bars"],
    OPTION_CONTRACTS: ["get_option_contracts"],
    OPTION_CHAIN: ["get_option_chain", "get_option_snapshot"],
}

# canonical argument name -> aliases a server might declare instead
ALIASES = {
    # Verified against a live `tools/list` on alpaca-mcp-server: the chain tool
    # names its underlying `root_symbol`, and close_position uses
    # `symbol_or_asset_id`. Discovery finds the tool; these find the parameter.
    "symbol": ["symbol", "symbols", "root_symbol", "underlying_symbol",
               "underlying_symbols", "symbol_or_asset_id", "ticker",
               "symbol_or_symbols", "underlying"],
    "qty": ["qty", "quantity", "shares", "contracts"],
    "side": ["side", "order_side"],
    "type": ["type", "order_type"],
    "limit_price": ["limit_price", "limitPrice", "price"],
    "time_in_force": ["time_in_force", "timeInForce", "tif"],
    "order_class": ["order_class", "orderClass", "class"],
    "legs": ["legs", "order_legs", "option_legs"],
    "order_id": ["order_id", "orderId", "id"],
    "timeframe": ["timeframe", "time_frame", "granularity"],
    "start": ["start", "start_date", "from"],
    "end": ["end", "end_date", "to"],
    "limit": ["limit", "max_results", "page_size"],
    "expiration_date_gte": ["expiration_date_gte", "expiration_gte", "exp_gte"],
    "expiration_date_lte": ["expiration_date_lte", "expiration_lte", "exp_lte"],
    "strike_price_gte": ["strike_price_gte", "strike_gte"],
    "strike_price_lte": ["strike_price_lte", "strike_lte"],
    "type_option": ["type", "option_type", "contract_type"],
    "percentage": ["percentage"],
    "feed": ["feed"],
}


class McpTransport(Transport):
    name = "mcp"

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.tools: dict = {}
        self.bindings: dict = {}
        self._id = 0
        self._lock = threading.Lock()
        self._boot_error = None
        self._booted = False

    # ── stdio JSON-RPC ───────────────────────────────────────────────────────

    def _spawn(self):
        cmd = shlex.split(self.cfg.mcp_cmd, posix=(os.name != "nt"))
        env = dict(os.environ)
        env["ALPACA_API_KEY"] = self.cfg.api_key
        env["ALPACA_SECRET_KEY"] = self.cfg.secret_key
        env["ALPACA_PAPER_TRADE"] = "true" if self.cfg.paper else "false"
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )

    def _send(self, msg: dict):
        if not self.proc or self.proc.stdin is None:
            raise TransportError("mcp server not running")
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read_until(self, want_id, timeout: float):
        """MCP stdio frames are newline-delimited JSON; skip notifications."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is None or self.proc.stdout is None:
                raise TransportError("mcp stdout closed")
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    raise TransportError(f"mcp server exited rc={self.proc.returncode}")
                continue
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise TransportError(f"mcp error: {msg['error']}")
                return msg.get("result", {})
        raise TransportError(f"mcp timeout waiting for id={want_id}")

    def _rpc(self, method: str, params: dict = None, timeout: float = CALL_TIMEOUT):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
            return self._read_until(rid, timeout)

    # ── discovery ────────────────────────────────────────────────────────────

    def boot(self) -> bool:
        if self._booted:
            return self.proc is not None and self.proc.poll() is None
        self._booted = True
        if not self.cfg.has_keys:
            self._boot_error = "no api keys"
            return False
        try:
            self._spawn()
            self._rpc("initialize", {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "theta-council", "version": "1.0.0"},
            }, timeout=BOOT_TIMEOUT)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized",
                        "params": {}})
            listed = self._rpc("tools/list", {}, timeout=BOOT_TIMEOUT)
            for t in listed.get("tools", []):
                self.tools[t["name"]] = t
            self._bind()
            log("INFO", "mcp connected", tools=len(self.tools),
                bound=len(self.bindings))
            return bool(self.tools)
        except Exception as exc:  # noqa: BLE001
            self._boot_error = str(exc)[:200]
            log("WARN", "mcp unavailable, router will fail over",
                reason=self._boot_error)
            self.close()
            return False

    def _bind(self):
        names = list(self.tools)
        for intent, prefs in PREFERRED.items():
            chosen = next((p for p in prefs if p in self.tools), None)
            if chosen is None:
                chosen = self._fuzzy(prefs[0], names)
            if chosen:
                self.bindings[intent] = chosen

    @staticmethod
    def _fuzzy(target: str, names: list):
        want = set(target.split("_"))
        best, score = None, 0.0
        for n in names:
            have = set(n.split("_"))
            s = len(want & have) / max(1, len(want | have))
            if s > score:
                best, score = n, s
        return best if score >= 0.55 else None

    def _schema_props(self, tool_name: str) -> dict:
        schema = (self.tools.get(tool_name) or {}).get("inputSchema") or {}
        return schema.get("properties") or {}

    def _required(self, tool_name: str) -> list:
        schema = (self.tools.get(tool_name) or {}).get("inputSchema") or {}
        return schema.get("required") or []

    def _coerce(self, tool_name: str, canonical: dict) -> dict:
        """Project canonical arg names onto whatever this tool declares."""
        props = self._schema_props(tool_name)
        if not props:
            return {k: v for k, v in canonical.items() if v is not None}
        out = {}
        for key, val in canonical.items():
            if val is None:
                continue
            for alias in ALIASES.get(key, [key]):
                if alias in props:
                    spec = props[alias] or {}
                    if spec.get("type") == "array" and not isinstance(val, list):
                        val = [val]
                    elif spec.get("type") == "string" and not isinstance(val, str):
                        val = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
                    out[alias] = val
                    break
        missing = [r for r in self._required(tool_name) if r not in out]
        if missing:
            raise Unsupported(f"{tool_name} requires {missing}, cannot satisfy")
        return out

    # ── result parsing ───────────────────────────────────────────────────────

    @staticmethod
    def _payload(result: dict):
        """MCP tools return prose, JSON, or structured content. Prefer structure."""
        if isinstance(result.get("structuredContent"), (dict, list)):
            sc = result["structuredContent"]
            if isinstance(sc, dict) and "result" in sc and len(sc) == 1:
                return sc["result"]
            return sc
        chunks = []
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(part.get("text") or "")
        text = "\n".join(chunks).strip()
        if not text:
            return {}
        for i, ch in enumerate(text):
            if ch in "[{":
                try:
                    return json.loads(text[i:])
                except json.JSONDecodeError:
                    break
        return {"_text": text}

    # ── contract ─────────────────────────────────────────────────────────────

    def available(self) -> bool:
        return self.boot()

    def supports(self, intent: str) -> bool:
        return intent in self.bindings

    def call(self, intent: str, **kw):
        if not self.boot():
            raise Unsupported(f"mcp not available ({self._boot_error})")
        tool = self.bindings.get(intent)
        if not tool:
            raise Unsupported(f"mcp exposes no tool for {intent}")
        fn = getattr(self, f"_i_{intent}", None)
        if fn is None:
            raise Unsupported(f"mcp cannot serve {intent}")
        return fn(tool, **kw)

    def close(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    # ── intents ──────────────────────────────────────────────────────────────

    def _one(self, tool, canonical=None):
        return self._payload(self._rpc("tools/call", {
            "name": tool, "arguments": self._coerce(tool, canonical or {})}))

    def _i_clock(self, tool):
        r = self._one(tool)
        if not isinstance(r, dict) or "_text" in r:
            raise TransportError("mcp clock not machine-readable")
        return {"timestamp": r.get("timestamp", ""), "is_open": bool(r.get("is_open")),
                "next_open": r.get("next_open", ""), "next_close": r.get("next_close", "")}

    def _i_account(self, tool):
        r = self._one(tool)
        if not isinstance(r, dict) or "_text" in r:
            raise TransportError("mcp account not machine-readable")
        return norm_account(r)

    def _i_positions(self, tool):
        r = self._one(tool)
        if isinstance(r, dict):
            r = r.get("positions") or ([] if "_text" in r else [r])
        if not isinstance(r, list):
            raise TransportError("mcp positions not machine-readable")
        return [norm_position(p) for p in r if isinstance(p, dict)]

    def _i_orders(self, tool, status: str = "open", limit: int = 200, after: str = None):
        r = self._one(tool, {"status": status, "limit": limit})
        if isinstance(r, dict):
            r = r.get("orders") or ([] if "_text" in r else [r])
        return r if isinstance(r, list) else []

    def _i_cancel_order(self, tool, order_id: str):
        self._one(tool, {"order_id": order_id})
        return {"cancelled": order_id}

    def _i_close_position(self, tool, symbol: str, qty=None, percentage=None):
        return self._one(tool, {"symbol": symbol, "qty": qty})

    def _i_stock_bars(self, tool, symbol: str, timeframe: str = "1Day",
                      start: str = None, limit: int = 260):
        r = self._one(tool, {"symbol": symbol, "timeframe": timeframe,
                             "start": start, "limit": limit})
        bars = r
        if isinstance(r, dict):
            bars = (r.get("bars") or {}).get(symbol) or r.get("bars") or []
        if not isinstance(bars, list) or not bars:
            raise TransportError("mcp bars not machine-readable")
        return [norm_bar(b) for b in bars if isinstance(b, dict)]

    def _i_option_contracts(self, tool, underlying: str, exp_gte: str = None,
                            exp_lte: str = None, kind: str = None,
                            strike_gte: float = None, strike_lte: float = None,
                            limit: int = 1000):
        r = self._one(tool, {"symbol": underlying, "expiration_date_gte": exp_gte,
                             "expiration_date_lte": exp_lte, "type_option": kind,
                             "strike_price_gte": strike_gte,
                             "strike_price_lte": strike_lte, "limit": limit})
        if isinstance(r, dict):
            r = r.get("option_contracts") or r.get("contracts") or []
        if not isinstance(r, list):
            raise TransportError("mcp contracts not machine-readable")
        return r

    def _i_option_chain(self, tool, underlying: str, exp_gte: str = None,
                        exp_lte: str = None, strike_gte: float = None,
                        strike_lte: float = None, kind: str = None,
                        limit: int = 1000):
        r = self._one(tool, {"symbol": underlying, "expiration_date_gte": exp_gte,
                             "expiration_date_lte": exp_lte,
                             "strike_price_gte": strike_gte,
                             "strike_price_lte": strike_lte, "limit": limit})
        snaps = r.get("snapshots") if isinstance(r, dict) else None
        if isinstance(snaps, dict):
            return snaps
        if isinstance(r, dict) and r and "_text" not in r and all(
                isinstance(v, dict) for v in r.values()):
            return r
        raise TransportError("mcp chain not machine-readable")

    def _i_submit_order(self, tool, payload: dict):
        canonical = {
            "symbol": payload.get("symbol"),
            "qty": payload.get("qty"),
            "side": payload.get("side"),
            "type": payload.get("type", "limit"),
            "limit_price": payload.get("limit_price"),
            "time_in_force": payload.get("time_in_force", "day"),
            "order_class": payload.get("order_class"),
            "legs": payload.get("legs"),
        }
        if payload.get("order_class") == "mleg" and "legs" not in self._schema_props(tool):
            raise Unsupported(f"{tool} declares no legs parameter")
        return self._one(tool, canonical)
