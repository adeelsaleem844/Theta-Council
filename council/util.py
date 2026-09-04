"""Small shared helpers. Standard library only."""
from __future__ import annotations

import json
import os
import sys
import time
import datetime as _dt
from typing import Any, Callable

# ── env ──────────────────────────────────────────────────────────────────────

def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so we don't need python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if val and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            val = val.split("   #")[0].rstrip()
            os.environ.setdefault(key, val)


def env(key: str, default: Any = None) -> Any:
    return os.environ.get(key, default)


def env_f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def env_i(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return default


def env_b(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


# ── time ─────────────────────────────────────────────────────────────────────

def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime | None = None) -> str:
    return (ts or utcnow()).isoformat(timespec="seconds")


def today_iso() -> str:
    return utcnow().date().isoformat()


def days_between(a: str, b: str) -> int:
    """Calendar days from ISO date `a` to ISO date `b`."""
    da = _dt.date.fromisoformat(a[:10])
    db = _dt.date.fromisoformat(b[:10])
    return (db - da).days


# ── logging ──────────────────────────────────────────────────────────────────

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_COLOR = {"DEBUG": "\033[90m", "INFO": "\033[36m", "WARN": "\033[33m", "ERROR": "\033[31m"}
_RESET = "\033[0m"
_MIN = _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), 20)

# Windows consoles still default to cp1252, which cannot encode the box-drawing
# characters in the report. Ask for UTF-8 and degrade instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_USE_COLOR = bool(os.environ.get("FORCE_COLOR")) or (
    sys.stdout.isatty() and os.environ.get("TERM") != "dumb")


def log(level: str, msg: str, **kv: Any) -> None:
    if _LEVELS.get(level, 20) < _MIN:
        return
    extra = " ".join(f"{k}={_fmt(v)}" for k, v in kv.items())
    stamp = utcnow().strftime("%H:%M:%S")
    tag = f"{level:<5}"
    if _USE_COLOR:
        tag = f"{_COLOR.get(level,'')}{tag}{_RESET}"
    line = f"{stamp} {tag} {msg}"
    if extra:
        line += f"  {extra}"
    print(line, flush=True)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.4g}"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), default=str)
    return str(v)


def banner(text: str) -> None:
    bar = "─" * max(0, 66 - len(text))
    print(f"\n\033[1m── {text} {bar}\033[0m" if _USE_COLOR else f"\n-- {text} {bar}", flush=True)


# ── misc ─────────────────────────────────────────────────────────────────────

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def retry(fn: Callable[[], Any], attempts: int = 3, backoff: float = 0.6,
          what: str = "call") -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - transport errors are opaque
            last = exc
            if i < attempts - 1:
                time.sleep(backoff * (2 ** i))
    raise RuntimeError(f"{what} failed after {attempts} attempts: {last}") from last


def jsonable(obj: Any) -> Any:
    """Recursively coerce dataclasses / sets / datetimes into JSON-safe types."""
    import dataclasses
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, float):
        return round(obj, 6)
    return obj
