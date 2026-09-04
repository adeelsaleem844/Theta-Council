"""A dependency-free live dashboard, served from the standard library.

    python -m council dashboard --port 8787

Reads the journal directly, so it shows the same numbers the agents acted on -
the open book, every refused trade with the gate that stopped it, the equity
curve, the transport provenance, and the per-(regime, structure) track record.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agents.bandit import Bandit
from .journal import Journal
from .util import banner, log

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "static")


def snapshot(cfg) -> dict:
    j = Journal(cfg)
    try:
        cycles = j.db.execute(
            "SELECT cycle_id, ts, equity, portfolio_regime, posture, "
            "posture_reason, used_llm, model, candidates, submitted, gates_json, "
            "provenance_json FROM cycles ORDER BY ts DESC LIMIT 40").fetchall()
        latest = dict(cycles[0]) if cycles else {}
        for key in ("gates_json", "provenance_json"):
            if latest.get(key):
                try:
                    latest[key.replace("_json", "")] = json.loads(latest.pop(key))
                except Exception:
                    latest.pop(key, None)

        refused = j.db.execute(
            "SELECT cid, underlying, structure, regime, blocked_by, thesis, "
            "max_loss, ev_dollars, edge_ratio, ts FROM decisions "
            "WHERE approved=0 ORDER BY id DESC LIMIT 40").fetchall()

        return {
            "stats": j.stats(),
            "latest_cycle": latest,
            "cycles": [{k: r[k] for k in r.keys()
                        if k not in ("gates_json", "provenance_json")}
                       for r in cycles],
            "open_book": j.open_trades(),
            "closed": j.closed_trades(limit=40),
            "refused": [dict(r) for r in refused],
            "equity_curve": j.equity_series(limit=1500),
            "track_record": Bandit(j).table(),
            "config": {
                "universe": cfg.universe,
                "transport_order": cfg.transport_order,
                "llm_provider": cfg.llm_provider,
                "tenors": [list(t) for t in cfg.risk.tenors],
                "risk": {
                    "max_risk_per_trade_pct": cfg.risk.max_risk_per_trade_pct,
                    "max_deployed_risk_pct": cfg.risk.max_deployed_risk_pct,
                    "max_concurrent_positions": cfg.risk.max_concurrent_positions,
                    "daily_loss_kill_pct": cfg.risk.daily_loss_kill_pct,
                    "drawdown_kill_pct": cfg.risk.drawdown_kill_pct,
                    "take_profit_pct": cfg.risk.take_profit_pct,
                    "stop_loss_mult": cfg.risk.stop_loss_mult,
                    "min_vrp_ratio": cfg.risk.min_vrp_ratio,
                },
            },
            "account_id": cfg.account_id,
        }
    finally:
        j.close()


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                page = os.path.join(STATIC, "dashboard.html")
                if not os.path.exists(page):
                    return self._send(404, b"dashboard.html missing", "text/plain")
                with open(page, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/api/state":
                try:
                    body = json.dumps(snapshot(cfg), default=str).encode()
                except Exception as exc:  # noqa: BLE001
                    body = json.dumps({"error": str(exc)}).encode()
                return self._send(200, body, "application/json")
            return self._send(404, b"not found", "text/plain")

        def log_message(self, fmt, *args):
            return  # the desk does its own logging

    return Handler


def serve(cfg, port: int = 8787) -> None:
    handler = make_handler(cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    banner("Theta Council dashboard")
    print(f"  http://127.0.0.1:{port}")
    print(f"  reading  {os.path.abspath(cfg.state_dir)}")
    print("  ctrl-c to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
