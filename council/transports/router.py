"""The transport router.

Given an intent, walk the configured pipe order (default `cli,mcp,rest`) and
use the first transport that can serve it. Record who served what. When a pipe
raises, fail over and remember why.

The provenance table this builds is printed at the end of every run and written
into the journal - it is the evidence that the CLI and the MCP server are load
bearing here, not decorative.
"""
from __future__ import annotations

from ..util import log
from .base import ALL_INTENTS, Transport, TransportError, Unsupported
from .cli import CliTransport
from .mcp import McpTransport
from .mock import MockTransport
from .rest import RestTransport

BUILDERS = {"cli": CliTransport, "mcp": McpTransport, "rest": RestTransport,
            "mock": MockTransport}


class Router:
    def __init__(self, cfg):
        self.cfg = cfg
        self.transports: list = []
        self.provenance: dict = {}
        self.failovers: list = []

        names = ["mock"] if cfg.mock else list(cfg.transport_order)
        if not cfg.mock and "rest" not in names:
            names.append("rest")  # always keep a guaranteed floor
        for name in names:
            builder = BUILDERS.get(name)
            if builder is None:
                log("WARN", "unknown transport in TRANSPORT_ORDER", name=name)
                continue
            t = builder(cfg)
            try:
                ok = t.available()
            except Exception as exc:  # noqa: BLE001
                ok, _ = False, exc
            if ok:
                self.transports.append(t)
                log("INFO", "transport ready", pipe=t.name)
            else:
                log("INFO", "transport skipped", pipe=t.name)
        if not self.transports:
            raise RuntimeError(
                "No Alpaca transport is available. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY in .env, or run with --mock.")

    # ── dispatch ─────────────────────────────────────────────────────────────

    def call(self, intent: str, **kw):
        errors = []
        for t in self.transports:
            try:
                if not t.supports(intent):
                    continue
            except Exception:
                continue
            try:
                result = t.call(intent, **kw)
            except Unsupported as exc:
                errors.append(f"{t.name}: unsupported ({exc})")
                continue
            except TransportError as exc:
                errors.append(f"{t.name}: {exc}")
                self.failovers.append({"intent": intent, "pipe": t.name,
                                       "error": str(exc)[:240]})
                log("WARN", "transport failover", intent=intent, pipe=t.name,
                    error=str(exc)[:160])
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{t.name}: {type(exc).__name__} {exc}")
                self.failovers.append({"intent": intent, "pipe": t.name,
                                       "error": str(exc)[:240]})
                continue
            self.provenance.setdefault(intent, {})
            self.provenance[intent][t.name] = self.provenance[intent].get(t.name, 0) + 1
            return result
        raise TransportError(f"all transports failed for {intent}: " + " | ".join(errors))

    # ── reporting ────────────────────────────────────────────────────────────

    def pipes(self) -> list:
        return [t.name for t in self.transports]

    def summary(self) -> dict:
        totals: dict = {}
        for served in self.provenance.values():
            for pipe, n in served.items():
                totals[pipe] = totals.get(pipe, 0) + n
        return {"pipes": self.pipes(), "by_intent": self.provenance,
                "totals": totals, "failovers": self.failovers[-25:]}

    def print_summary(self) -> None:
        from ..util import banner
        banner("Alpaca transport provenance")
        s = self.summary()
        print(f"  pipes available : {', '.join(s['pipes'])}")
        for intent in ALL_INTENTS:
            served = self.provenance.get(intent)
            if served:
                detail = ", ".join(f"{k}x{v}" for k, v in served.items())
                print(f"  {intent:<17} {detail}")
        if s["totals"]:
            tot = ", ".join(f"{k}={v}" for k, v in sorted(s["totals"].items()))
            print(f"  calls served    : {tot}")
        if s["failovers"]:
            print(f"  failovers       : {len(self.failovers)} (see journal)")

    def close(self) -> None:
        for t in self.transports:
            try:
                t.close()
            except Exception:
                pass

    # ── convenience wrappers used by the agents ──────────────────────────────

    def transport(self, name: str):
        """Reach a specific pipe by name, for the few places that need one.

        Used by the order layer to borrow the CLI's own `--dry-run`, which gets
        the request body validated by the broker instead of by us.
        """
        return next((t for t in self.transports if t.name == name), None)

    def mcp_transport(self):
        return self.transport("mcp")
