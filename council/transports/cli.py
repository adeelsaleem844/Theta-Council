"""Alpaca CLI transport - the desk's preferred pipe.

`alpaca` prints JSON on stdout by default and returns exit 2 on auth errors and
1 on API errors, which makes it a perfectly good machine interface. It is the
pipe this desk reaches for first, for one reason: a shell command is the most
inspectable thing in the stack. The journal stores the exact argv of every call,
so any decision the desk made can be replayed by hand in a terminal.

Crucially, `alpaca order submit` accepts `--order-class mleg` with a `--legs`
JSON array, so full multi-leg spreads go over the CLI - not just reads. It also
has its own `--dry-run`, which the desk uses in dry-run mode so the *broker*
echoes back the request body it would have accepted, rather than the desk
printing its own guess.

Every command and flag below was verified against alpaca CLI v1.x by reading
`--help`. Commands are still probed at startup rather than trusted, so a rename
upstream degrades to a clean failover instead of a crash.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from ..util import log
from .base import (ACCOUNT, CALENDAR, CANCEL_ORDER, CLOCK, CLOSE_POSITION,
                   OPTION_CHAIN, OPTION_CONTRACTS, ORDERS, POSITIONS,
                   STOCK_BARS, SUBMIT_ORDER, Transport, TransportError,
                   Unsupported, norm_account, norm_bar, norm_position)

TIMEOUT = 45

# intent -> candidate command heads, tried in order. First entry is the one
# verified against the current CLI; the rest cover older/newer spellings.
CANDIDATES = {
    CLOCK: [["clock"], ["clock", "markets"], ["clock", "get"]],
    CALENDAR: [["calendar"], ["calendar", "market"]],
    ACCOUNT: [["account", "get"], ["account"]],
    POSITIONS: [["position", "list"], ["positions", "list"]],
    ORDERS: [["order", "list"], ["orders", "list"]],
    SUBMIT_ORDER: [["order", "submit"], ["order", "create"]],
    CANCEL_ORDER: [["order", "cancel"]],
    CLOSE_POSITION: [["position", "close"], ["positions", "close"]],
    STOCK_BARS: [["data", "bars"], ["data", "stock", "bars"]],
    OPTION_CHAIN: [["data", "option", "chain"], ["data", "option", "snapshot"]],
    OPTION_CONTRACTS: [["option", "contracts"]],
}


class CliTransport(Transport):
    name = "cli"

    def __init__(self, cfg):
        self.cfg = cfg
        found = shutil.which(cfg.cli_bin)
        self.bin = found or cfg.cli_bin
        self._ok = bool(found)
        self._probed: dict = {}
        self.last_argv: list = []

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _env(self) -> dict:
        e = dict(os.environ)
        e["ALPACA_API_KEY"] = self.cfg.api_key
        e["ALPACA_SECRET_KEY"] = self.cfg.secret_key
        e.pop("ALPACA_LIVE_TRADE", None)
        if not self.cfg.paper:
            e["ALPACA_LIVE_TRADE"] = "true"
        return e

    def _run(self, args: list, expect_json: bool = True):
        argv = [self.bin] + args + ["--quiet"]
        self.last_argv = argv
        log("DEBUG", "cli", argv=" ".join(argv))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=TIMEOUT, env=self._env())
        except FileNotFoundError as exc:
            raise Unsupported(f"alpaca CLI not on PATH: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportError(f"alpaca CLI timed out: {' '.join(args)}") from exc

        if proc.returncode == 2:
            raise TransportError(f"alpaca CLI auth error: {proc.stderr.strip()[:300]}")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()[:400]
            low = err.lower()
            if "unknown command" in low or "unknown flag" in low:
                raise Unsupported(f"alpaca CLI rejected `{' '.join(args)}`: {err}")
            raise TransportError(f"alpaca CLI exit {proc.returncode}: {err}")

        out = proc.stdout.strip()
        if not expect_json:
            return out
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            for i, ch in enumerate(out):
                if ch in "[{":
                    try:
                        return json.loads(out[i:])
                    except json.JSONDecodeError:
                        break
            raise TransportError(f"alpaca CLI gave non-JSON: {out[:200]}")

    def _head(self, intent: str) -> list:
        """Resolve and cache the working command head for an intent."""
        if intent in self._probed:
            head = self._probed[intent]
            if head is None:
                raise Unsupported(f"cli has no command for {intent}")
            return head
        for cand in CANDIDATES.get(intent, []):
            try:
                proc = subprocess.run([self.bin] + cand + ["--help"],
                                      capture_output=True, text=True, timeout=20,
                                      env=self._env())
            except Exception:
                continue
            if proc.returncode == 0 and "unknown" not in proc.stderr.lower()[:200]:
                self._probed[intent] = cand
                log("DEBUG", "cli probe ok", intent=intent, cmd=" ".join(cand))
                return cand
        self._probed[intent] = None
        raise Unsupported(f"cli has no command for {intent}")

    def _paged(self, head: list, flags: list, key: str):
        """Follow the CLI's --page-token pagination, merging `key`."""
        out, token, pages = None, None, 0
        while pages < 20:
            args = head + list(flags)
            if token:
                args += ["--page-token", token]
            payload = self._run(args)
            if not isinstance(payload, dict):
                return payload
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
        return self._ok and self.cfg.has_keys

    def supports(self, intent: str) -> bool:
        return intent in CANDIDATES

    def call(self, intent: str, **kw):
        fn = getattr(self, f"_i_{intent}", None)
        if fn is None:
            raise Unsupported(f"cli cannot serve {intent}")
        return fn(**kw)

    # ── reads ────────────────────────────────────────────────────────────────

    def _i_clock(self):
        r = self._run(self._head(CLOCK))
        if isinstance(r, list):
            r = r[0] if r else {}
        return {"timestamp": r.get("timestamp", ""),
                "is_open": bool(r.get("is_open")),
                "next_open": r.get("next_open", ""),
                "next_close": r.get("next_close", "")}

    def _i_calendar(self, start: str = None, end: str = None):
        args = self._head(CALENDAR)
        if start:
            args += ["--start", start[:10]]
        if end:
            args += ["--end", end[:10]]
        return self._run(args)

    def _i_account(self):
        r = self._run(self._head(ACCOUNT))
        if isinstance(r, list):
            r = r[0] if r else {}
        return norm_account(r)

    def _i_positions(self):
        r = self._run(self._head(POSITIONS))
        if isinstance(r, dict):
            r = r.get("positions") or []
        return [norm_position(p) for p in (r or []) if isinstance(p, dict)]

    def _i_orders(self, status: str = "open", limit: int = 200, after: str = None):
        args = self._head(ORDERS) + ["--status", status, "--limit", str(min(limit, 500)),
                                     "--nested"]
        if after:
            args += ["--after", after]
        r = self._run(args)
        if isinstance(r, dict):
            r = r.get("orders") or []
        return r or []

    def _i_stock_bars(self, symbol: str, timeframe: str = "1Day",
                      start: str = None, limit: int = 260):
        args = self._head(STOCK_BARS) + [
            "--symbol", symbol, "--timeframe", timeframe,
            "--limit", str(limit), "--sort", "asc",
            "--adjustment", "split", "--feed", self.cfg.stock_feed,
        ]
        if start:
            args += ["--start", start[:10]]
        payload = self._run(args)
        bars = payload
        if isinstance(payload, dict):
            bars = payload.get("bars")
            if isinstance(bars, dict):
                bars = bars.get(symbol) or []
        return [norm_bar(b) for b in (bars or []) if isinstance(b, dict)]

    def _i_option_chain(self, underlying: str, exp_gte: str = None,
                        exp_lte: str = None, strike_gte: float = None,
                        strike_lte: float = None, kind: str = None,
                        limit: int = 1000):
        flags = ["--underlying-symbol", underlying,
                 "--limit", str(limit), "--feed", self.cfg.option_feed]
        if exp_gte:
            flags += ["--expiration-date-gte", exp_gte[:10]]
        if exp_lte:
            flags += ["--expiration-date-lte", exp_lte[:10]]
        if strike_gte is not None:
            flags += ["--strike-price-gte", str(strike_gte)]
        if strike_lte is not None:
            flags += ["--strike-price-lte", str(strike_lte)]
        if kind:
            flags += ["--type", kind]
        snaps = self._paged(self._head(OPTION_CHAIN), flags, key="snapshots")
        return snaps or {}

    def _i_option_contracts(self, underlying: str, exp_gte: str = None,
                            exp_lte: str = None, kind: str = None,
                            strike_gte: float = None, strike_lte: float = None,
                            limit: int = 1000):
        flags = ["--underlying-symbols", underlying, "--limit", str(limit)]
        if exp_gte:
            flags += ["--expiration-date-gte", exp_gte[:10]]
        if exp_lte:
            flags += ["--expiration-date-lte", exp_lte[:10]]
        r = self._paged(self._head(OPTION_CONTRACTS), flags, key="option_contracts")
        return r or []

    # ── writes ───────────────────────────────────────────────────────────────

    def _i_cancel_order(self, order_id: str):
        self._run(self._head(CANCEL_ORDER) + ["--order-id", order_id],
                  expect_json=False)
        return {"cancelled": order_id}

    def _i_close_position(self, symbol: str, qty=None, percentage=None):
        args = self._head(CLOSE_POSITION) + ["--symbol-or-asset-id", symbol]
        if qty is not None:
            args += ["--qty", str(qty)]
        elif percentage is not None:
            args += ["--percentage", str(percentage)]
        return self._run(args)

    def _i_submit_order(self, payload: dict, cli_dry_run: bool = False):
        """Single-leg and multi-leg, both through `alpaca order submit`.

        For `mleg` the CLI takes the legs as a JSON array string, and `--symbol`
        and `--side` must be omitted - they are per-leg for a spread. Getting
        that wrong is the difference between a spread and four naked options.
        """
        args = self._head(SUBMIT_ORDER) + [
            "--qty", str(payload["qty"]),
            "--type", str(payload.get("type", "limit")),
            "--time-in-force", str(payload.get("time_in_force", "day")),
        ]
        if payload.get("limit_price") is not None:
            args += ["--limit-price", str(payload["limit_price"])]
        if payload.get("client_order_id"):
            args += ["--client-order-id", str(payload["client_order_id"])[:128]]

        if payload.get("order_class") == "mleg":
            legs = payload.get("legs") or []
            if not 2 <= len(legs) <= 4:
                raise Unsupported(f"cli accepts 2-4 mleg legs, got {len(legs)}")
            args += ["--order-class", "mleg",
                     "--legs", json.dumps(legs, separators=(",", ":"))]
        else:
            args += ["--symbol", str(payload["symbol"]),
                     "--side", str(payload["side"])]
            if payload.get("order_class"):
                args += ["--order-class", str(payload["order_class"])]
            if payload.get("position_intent"):
                args += ["--position-intent", str(payload["position_intent"])]

        if cli_dry_run:
            # The broker echoes the body it would have accepted. Far better
            # evidence than the desk printing its own guess.
            args += ["--dry-run"]

        return self._run(args)
