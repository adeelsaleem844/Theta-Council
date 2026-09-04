"""One-command credential setup.

    python setup_keys.py

Prompts for your Alpaca keys (and optionally a Featherless key), checks them
against the live paper API before saving, and writes them into .env without
disturbing anything else in the file.

Secrets are read with getpass, so nothing is echoed to the terminal, nothing is
passed on the command line where it would land in shell history, and nothing is
printed back. The only things this ever displays are the account id, the equity,
and the options level - the three facts you need to confirm you are pointed at
the right account.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ENV = pathlib.Path(__file__).resolve().parent / ".env"
PAPER = "https://paper-api.alpaca.markets"
FEATHERLESS = "https://api.featherless.ai/v1"

G, Y, R, D, B = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m"
X = "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    try:                       # enable ANSI on older Windows consoles
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        G = Y = R = D = B = X = ""


def say(msg=""):
    print(msg, flush=True)


def ask(label: str, secret: bool = True) -> str:
    fn = getpass.getpass if secret else input
    try:
        raw = fn(f"  {label}: ")
    except (EOFError, KeyboardInterrupt):
        say("\n  cancelled")
        sys.exit(130)
    # Console pastes routinely carry a trailing newline, stray quotes, or a
    # zero-width character. Strip them rather than sending them to the API.
    return raw.strip().strip('"').strip("'").replace("​", "").strip()


def shape(name: str, value: str) -> str:
    """Describe a secret without revealing it, so a bad paste is diagnosable."""
    if not value:
        return f"{name}: EMPTY - the paste did not land"
    head = value[:2] if len(value) >= 2 else "?"
    notes = []
    if any(c.isspace() for c in value):
        notes.append("contains a space")
    if not value.isascii():
        notes.append("has non-ASCII characters")
    extra = (", " + ", ".join(notes)) if notes else ""
    return f'{name}: {len(value)} characters, starts "{head}"{extra}'


# ── validation ───────────────────────────────────────────────────────────────

def check_alpaca(key: str, secret: str):
    req = urllib.request.Request(
        f"{PAPER}/v2/account",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                 "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        if e.code in (401, 403):
            return None, ("Alpaca rejected these credentials (HTTP %d). Check "
                          "you copied the key ID and the SECRET, and that they "
                          "are PAPER keys, not live ones." % e.code)
        return None, f"Alpaca returned HTTP {e.code}: {body}"
    except Exception as e:
        return None, f"could not reach Alpaca: {e}"


def check_featherless(key: str):
    req = urllib.request.Request(
        f"{FEATHERLESS}/models",
        headers={"authorization": f"Bearer {key}", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            r.read()
        return True, None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Featherless rejected the key"
        return True, None          # any non-auth reply means the key was accepted
    except Exception as e:
        return False, f"could not reach Featherless: {e}"


# ── .env editing ─────────────────────────────────────────────────────────────

def write_env(updates: dict) -> None:
    """Set keys in .env, preserving every other line and all comments."""
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    lines = text.splitlines()
    for name, value in updates.items():
        pattern = re.compile(rf"^{re.escape(name)}=.*$")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{name}={value}"
                break
        else:
            lines.append(f"{name}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    say(f"\n{B}Theta Council - credential setup{X}")
    say(f"{D}  Nothing you type is echoed, logged, or printed back.{X}")
    say(f"{D}  Alpaca paper keys:  https://app.alpaca.markets/account/configuration -> API{X}\n")

    key = secret = ""
    acct = None
    for attempt in (1, 2, 3):
        key = ask("ALPACA_API_KEY    (starts PK)")
        secret = ask("ALPACA_SECRET_KEY")

        # Report the SHAPE of what arrived, never the value. A silent paste
        # failure is otherwise indistinguishable from a wrong key.
        say("")
        say(f"{D}  received  {shape('key   ', key)}{X}")
        say(f"{D}            {shape('secret', secret)}{X}")

        hints = []
        if not key or not secret:
            hints.append("One field is empty. In this window RIGHT-CLICK "
                         "pastes; Ctrl+V often does not.")
        elif key == secret:
            hints.append("Key and secret are identical - the same value went "
                         "into both prompts.")
        else:
            if key.upper().startswith("AK"):
                hints.append("That key starts 'AK', which is a LIVE key. You "
                             "need the PAPER key, which starts 'PK'.")
            elif not key.upper().startswith("PK"):
                hints.append("A paper key ID normally starts 'PK'. Check you "
                             "copied the Key ID and not something else.")
            if len(secret) < 30:
                hints.append("The secret looks short; it is usually 40+ "
                             "characters. It may have been truncated.")

        if hints and attempt < 3:
            say("")
            for h in hints:
                say(f"{Y}  ! {h}{X}")
            say(f"\n{D}  Attempt {attempt} of 3 - let us try again.{X}\n")
            continue

        say(f"\n  checking against {PAPER} ...")
        acct, err = check_alpaca(key, secret)
        if not err:
            break

        say(f"{R}  FAILED: {err}{X}")
        if attempt >= 3:
            say(f"{D}  Nothing was written. Fix and re-run this file.{X}")
            return 1
        say(f"\n{Y}  Nothing was written. Most likely causes, in order:{X}")
        say(f"{Y}    1. The secret is shown ONCE, at the moment you generate")
        say(f"       the key. If you copied it from the page afterwards, it is")
        say(f"       not the real secret. Click Generate New Key and copy BOTH")
        say(f"       values from that panel before closing it.{X}")
        say(f"{Y}    2. The paste silently failed - right-click to paste.{X}")
        say(f"{Y}    3. These are Live keys rather than Paper keys.{X}")
        say(f"\n{D}  Attempt {attempt} of 3 - try again.{X}\n")

    if acct is None:
        say(f"{R}  could not authenticate; nothing written{X}")
        return 1

    equity = float(acct.get("equity") or 0)
    level = int(float(acct.get("options_trading_level") or 0))
    acct_no = acct.get("account_number") or acct.get("id") or "?"

    say(f"{G}  connected{X}")
    say(f"    account         {acct_no}")
    say(f"    status          {acct.get('status')}")
    say(f"    equity          ${equity:,.2f}")
    say(f"    options level   {level}")

    problems = []
    if level < 3:
        problems.append(f"options level is {level}; spreads need level 3 "
                        "(Account -> Configure -> Options)")
    if abs(equity - 100_000) > 1000:
        problems.append(f"equity is ${equity:,.0f}; the hackathon requires a "
                        "fresh paper account funded at $100,000")

    updates = {"ALPACA_API_KEY": key, "ALPACA_SECRET_KEY": secret,
               "ALPACA_PAPER_TRADE": "true"}
    if acct_no and acct_no != "?":
        updates["ALPACA_ACCOUNT_ID"] = acct_no

    say(f"\n{D}  Featherless is optional - it unlocks the partner prize and the{X}")
    say(f"{D}  reasoning layer. Press Enter to skip.{X}")
    fkey = ask("FEATHERLESS_API_KEY (optional)")
    if fkey:
        ok, ferr = check_featherless(fkey)
        if ok:
            say(f"{G}  featherless key accepted{X}")
            updates["FEATHERLESS_API_KEY"] = fkey
            updates["LLM_PROVIDER"] = "featherless"
        else:
            say(f"{Y}  {ferr} - saving anyway, the desk falls back to "
                f"deterministic mode{X}")
            updates["FEATHERLESS_API_KEY"] = fkey
    else:
        say(f"{D}  skipped; LLM_PROVIDER stays as configured{X}")

    write_env(updates)
    say(f"\n{G}  wrote {ENV}{X}")

    if problems:
        say(f"\n{Y}  fix before trading:{X}")
        for p in problems:
            say(f"{Y}    ! {p}{X}")

    say(f"\n{B}  next:{X}  python -m council preflight")
    say(f"{D}         then: python -m council once --dry-run{X}\n")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
