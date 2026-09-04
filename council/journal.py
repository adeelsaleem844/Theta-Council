"""The journal - SQLite plus an append-only JSONL stream.

Two claims a trading agent has to be able to back up: *why* it did something,
and *whether that reasoning worked*. So every cycle writes a decision record
containing the market brief the agents saw, each candidate they built, the exact
gate results with the numbers in them, what the model said verbatim, and the
broker's reply. Blocked trades are stored too - a trade that did not happen is
a decision, and a desk that only logs its fills is grading its own homework.

When a structure closes, the realised P&L is attributed back to the cycle and to
the (regime, structure) bucket that produced it, which is what feeds the Bandit.

    state/journal.db      queryable history
    state/journal.jsonl   append-only stream, one JSON object per event
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid

from .util import iso, jsonable, log, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
  cycle_id TEXT PRIMARY KEY, ts TEXT, equity REAL, is_open INTEGER,
  portfolio_regime TEXT, posture TEXT, posture_reason TEXT,
  used_llm INTEGER, model TEXT, candidates INTEGER, submitted INTEGER,
  brief_json TEXT, provenance_json TEXT, gates_json TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id TEXT, ts TEXT, cid TEXT,
  underlying TEXT, structure TEXT, regime TEXT, approved INTEGER, qty INTEGER,
  net_price REAL, width REAL, max_loss REAL, ev_dollars REAL, edge_ratio REAL,
  pop_rv REAL, score REAL, conviction REAL, thesis TEXT, blocked_by TEXT,
  gates_json TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY, cycle_id TEXT, opened_ts TEXT, underlying TEXT,
  structure TEXT, regime TEXT, expiry TEXT, is_credit INTEGER, net_price REAL,
  width REAL, qty INTEGER, max_loss REAL, max_gain REAL, legs_json TEXT,
  entry_order_json TEXT, status TEXT, closed_ts TEXT, close_value REAL,
  realized_pl REAL, close_rule TEXT, exit_order_json TEXT, thesis TEXT
);
CREATE TABLE IF NOT EXISTS iv_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, symbol TEXT,
  atm_iv REAL, rv20 REAL, UNIQUE(day, symbol) ON CONFLICT REPLACE
);
CREATE TABLE IF NOT EXISTS equity_curve (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, equity REAL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_iv_symbol ON iv_history(symbol);
"""


class Journal:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg.state_dir, exist_ok=True)
        self.db_path = os.path.join(cfg.state_dir, "journal.db")
        self.stream_path = os.path.join(cfg.state_dir, "journal.jsonl")
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ── stream ───────────────────────────────────────────────────────────────

    def emit(self, kind: str, payload: dict) -> None:
        rec = {"ts": iso(), "kind": kind, **jsonable(payload)}
        with open(self.stream_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        self.db.execute("INSERT INTO events(ts, kind, payload_json) VALUES (?,?,?)",
                        (rec["ts"], kind, json.dumps(jsonable(payload), default=str)))
        self.db.commit()

    # ── cycles and decisions ─────────────────────────────────────────────────

    def start_cycle(self) -> str:
        return f"c-{utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def record_cycle(self, cycle_id: str, brief, portfolio_regime: str,
                     verdict: dict, candidates: int, submitted: int,
                     provenance: dict, portfolio_gates: list) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cycles(cycle_id, ts, equity, is_open, "
            "portfolio_regime, posture, posture_reason, used_llm, model, "
            "candidates, submitted, brief_json, provenance_json, gates_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, iso(), brief.equity, int(brief.is_open), portfolio_regime,
             verdict.get("posture", ""), verdict.get("posture_reason", ""),
             int(bool(verdict.get("used_llm"))), verdict.get("model", ""),
             candidates, submitted,
             json.dumps(jsonable(brief.compact()), default=str),
             json.dumps(jsonable(provenance), default=str),
             json.dumps(jsonable([{"name": g.name, "passed": g.passed,
                                   "detail": g.detail} for g in portfolio_gates]),
                        default=str)))
        self.db.commit()

    def record_decision(self, cycle_id: str, verdict) -> None:
        c = verdict.candidate
        self.db.execute(
            "INSERT INTO decisions(cycle_id, ts, cid, underlying, structure, "
            "regime, approved, qty, net_price, width, max_loss, ev_dollars, "
            "edge_ratio, pop_rv, score, conviction, thesis, blocked_by, gates_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, iso(), c.cid, c.underlying, c.structure, c.regime,
             int(verdict.approved), c.qty, c.net_price, c.width, c.max_loss,
             c.ev_dollars, c.edge_ratio, c.pop_rv, c.score, c.conviction,
             c.thesis, ",".join(verdict.blocked_by()),
             json.dumps([{"name": g.name, "passed": g.passed, "detail": g.detail}
                         for g in verdict.gates], default=str)))
        self.db.commit()

    # ── trades ───────────────────────────────────────────────────────────────

    def open_trade(self, cycle_id: str, cand, order: dict) -> str:
        trade_id = f"t-{utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
        legs = [{"symbol": lg.symbol, "side": lg.side, "ratio": lg.ratio,
                 "kind": lg.kind, "strike": lg.strike, "expiry": lg.expiry,
                 "entry_mid": lg.mid, "entry_delta": lg.delta} for lg in cand.legs]
        self.db.execute(
            "INSERT INTO trades(trade_id, cycle_id, opened_ts, underlying, "
            "structure, regime, expiry, is_credit, net_price, width, qty, "
            "max_loss, max_gain, legs_json, entry_order_json, status, thesis) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, cycle_id, iso(), cand.underlying, cand.structure,
             cand.regime, cand.expiry, int(cand.is_credit), cand.net_price,
             cand.width, cand.qty, cand.max_loss, cand.max_gain,
             json.dumps(legs, default=str), json.dumps(jsonable(order), default=str),
             "open", cand.thesis))
        self.db.commit()
        self.emit("trade_opened", {"trade_id": trade_id, "candidate": cand.compact(),
                                   "order": order})
        return trade_id

    def close_trade(self, trade_id: str, close_value: float, realized_pl: float,
                    rule: str, order: dict) -> None:
        self.db.execute(
            "UPDATE trades SET status='closed', closed_ts=?, close_value=?, "
            "realized_pl=?, close_rule=?, exit_order_json=? WHERE trade_id=?",
            (iso(), close_value, realized_pl, rule,
             json.dumps(jsonable(order), default=str), trade_id))
        self.db.commit()
        self.emit("trade_closed", {"trade_id": trade_id, "realized_pl": realized_pl,
                                   "rule": rule, "close_value": close_value})

    def open_trades(self) -> list:
        rows = self.db.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY opened_ts").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["legs"] = json.loads(d.pop("legs_json") or "[]")
            d["is_credit"] = bool(d["is_credit"])
            out.append(d)
        return out

    def closed_trades(self, limit: int = 200) -> list:
        rows = self.db.execute(
            "SELECT * FROM trades WHERE status='closed' ORDER BY closed_ts DESC "
            "LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["legs"] = json.loads(d.pop("legs_json") or "[]")
            out.append(d)
        return out

    def open_trade_count(self) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0])

    def open_count_for(self, underlying: str) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open' AND underlying=?",
            (underlying,)).fetchone()[0])

    def has_open(self, underlying: str, expiry: str, structure: str) -> bool:
        return bool(self.db.execute(
            "SELECT 1 FROM trades WHERE status='open' AND underlying=? AND "
            "expiry=? AND structure=? LIMIT 1",
            (underlying, expiry, structure)).fetchone())

    def open_risk(self) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(max_loss * qty), 0) FROM trades "
            "WHERE status='open'").fetchone()
        return float(row[0] or 0.0)

    def realized_pl(self) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(realized_pl), 0) FROM trades "
            "WHERE status='closed'").fetchone()
        return float(row[0] or 0.0)

    def outcome_counts(self) -> dict:
        rows = self.db.execute(
            "SELECT regime, structure, realized_pl FROM trades "
            "WHERE status='closed'").fetchall()
        out: dict = {}
        for r in rows:
            key = (r["regime"] or "chop", r["structure"] or "")
            w, l = out.get(key, (0, 0))
            if float(r["realized_pl"] or 0) > 0:
                w += 1
            else:
                l += 1
            out[key] = (w, l)
        return out

    # ── series ───────────────────────────────────────────────────────────────

    def record_iv(self, symbol: str, atm_iv: float, rv20: float) -> None:
        if atm_iv <= 0:
            return
        self.db.execute(
            "INSERT INTO iv_history(ts, day, symbol, atm_iv, rv20) VALUES (?,?,?,?,?)",
            (iso(), iso()[:10], symbol, atm_iv, rv20))
        self.db.commit()

    def iv_history(self, symbol: str, lookback: int = 120) -> list:
        rows = self.db.execute(
            "SELECT atm_iv FROM iv_history WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (symbol, lookback)).fetchall()
        return [float(r["atm_iv"]) for r in rows]

    def record_equity(self, equity: float) -> None:
        if equity <= 0:
            return
        self.db.execute("INSERT INTO equity_curve(ts, equity) VALUES (?,?)",
                        (iso(), equity))
        self.db.commit()

    def equity_series(self, limit: int = 2000) -> list:
        rows = self.db.execute(
            "SELECT ts, equity FROM equity_curve ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        return [{"ts": r["ts"], "equity": float(r["equity"])} for r in rows]

    def equity_peak(self, current: float) -> float:
        row = self.db.execute("SELECT MAX(equity) FROM equity_curve").fetchone()
        return max(float(row[0] or 0.0), current)

    # ── reporting ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        closed = self.closed_trades(limit=1000)
        wins = [t for t in closed if float(t["realized_pl"] or 0) > 0]
        losses = [t for t in closed if float(t["realized_pl"] or 0) <= 0]
        gross_win = sum(float(t["realized_pl"]) for t in wins)
        gross_loss = -sum(float(t["realized_pl"]) for t in losses)
        eq = self.equity_series()
        cyc = self.db.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
        blocked = self.db.execute(
            "SELECT COUNT(*) FROM decisions WHERE approved=0").fetchone()[0]
        return {
            "cycles": int(cyc),
            "decisions_blocked": int(blocked),
            "open_structures": self.open_trade_count(),
            "open_risk": round(self.open_risk(), 2),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
            "realized_pl": round(self.realized_pl(), 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0
            else (999.0 if gross_win > 0 else 0.0),
            "equity_first": eq[0]["equity"] if eq else 0.0,
            "equity_last": eq[-1]["equity"] if eq else 0.0,
        }

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
