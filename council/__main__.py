"""Command line entry point.

    python -m council preflight            check keys, level, balance, pipes
    python -m council mcp-doctor           connect to the Alpaca MCP server
    python -m council once                 run one cycle
    python -m council once --mock          run one cycle offline, no keys needed
    python -m council once --dry-run       full pipeline, orders printed not sent
    python -m council loop --interval 600  trade the session
    python -m council report               performance and the open book
    python -m council dashboard            live web view on :8787
"""
from __future__ import annotations

import argparse
import sys
import time

from .config import Config
from .engine import Desk
from .util import banner, log


def _cfg(args) -> Config:
    cfg = Config.load(args.env)
    cfg.dry_run = bool(getattr(args, "dry_run", False))
    cfg.mock = bool(getattr(args, "mock", False))
    if cfg.mock:
        cfg.transport_order = ["mock"]
    if getattr(args, "universe", None):
        cfg.universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]
    return cfg


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_preflight(args) -> int:
    cfg = _cfg(args)
    banner("Theta Council preflight")
    print(f"  keys present      {'yes' if cfg.has_keys else 'NO - set ALPACA_API_KEY / ALPACA_SECRET_KEY'}")
    print(f"  environment       {'paper' if cfg.paper else 'LIVE'}")
    print(f"  transport order   {', '.join(cfg.transport_order)}")
    print(f"  llm provider      {cfg.llm_provider}")
    print(f"  universe          {', '.join(cfg.universe)}")

    desk = Desk(cfg)
    try:
        acct = desk.router.call("account")
        level = acct.get("options_trading_level")
        equity = acct.get("equity")
        print(f"\n  account id        {acct.get('id') or acct.get('account_number')}")
        print(f"  status            {acct.get('status')}")
        print(f"  equity            ${equity:,.2f}")
        print(f"  options level     {level}")
        print(f"  options BP        ${acct.get('options_buying_power', 0):,.2f}")

        problems = []
        if level is not None and int(level) < 3:
            problems.append(
                f"options trading level is {level}. Spreads need level 3 - raise it in "
                "the Alpaca paper dashboard under Account -> Options.")
        if equity and abs(float(equity) - 100_000) > 1000:
            problems.append(
                f"equity is ${float(equity):,.0f}. The hackathon requires a fresh "
                "paper account funded at $100,000.")
        if cfg.llm_provider == "featherless" and not cfg.featherless_key:
            problems.append("FEATHERLESS_API_KEY is empty - the desk will run "
                            "deterministically and forfeit the partner prize.")

        clock = desk.router.call("clock")
        print(f"  market            {'OPEN' if clock.get('is_open') else 'closed'}"
              f"  (next open {clock.get('next_open', '?')[:16]})")

        banner("data check")
        ok = 0
        for sym in cfg.universe:
            try:
                bars = desk.router.call("stock_bars", symbol=sym, limit=60)
                chain = desk.router.call("option_chain", underlying=sym)
                print(f"  {sym:<6} {len(bars):>4} bars   {len(chain):>5} contracts")
                if bars and chain:
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  {sym:<6} FAILED: {str(exc)[:110]}")
        print(f"\n  {ok}/{len(cfg.universe)} symbols fully tradable")

        desk.router.print_summary()
        if problems:
            banner("fix before submitting")
            for p in problems:
                print(f"  ! {p}")
            return 1
        banner("ready to trade")
        return 0
    finally:
        desk.close()


def cmd_mcp_doctor(args) -> int:
    cfg = _cfg(args)
    from .transports.mcp import McpTransport
    banner("Alpaca MCP server")
    print(f"  launch command    {cfg.mcp_cmd}")
    t = McpTransport(cfg)
    if not t.boot():
        print(f"  status            NOT AVAILABLE ({t._boot_error})")
        print("\n  Install uv, then:  uvx alpaca-mcp-server")
        print("  The desk still runs - the router fails over to the CLI and REST.")
        return 1
    print(f"  status            connected")
    print(f"  tools advertised  {len(t.tools)}")
    banner("intent bindings discovered at runtime")
    for intent, tool in sorted(t.bindings.items()):
        props = ", ".join(sorted(t._schema_props(tool))[:8]) or "(no schema)"
        print(f"  {intent:<17} -> {tool}")
        print(f"  {'':<17}    args: {props}")
    unbound = [i for i in ("clock", "account", "positions", "orders",
                           "submit_order", "option_chain")
               if i not in t.bindings]
    if unbound:
        print(f"\n  unbound intents   {', '.join(unbound)} (router will fail over)")
    banner("all advertised tools")
    names = sorted(t.tools)
    for i in range(0, len(names), 3):
        print("  " + "".join(f"{n:<34}" for n in names[i:i + 3]))
    t.close()
    return 0


def cmd_once(args) -> int:
    cfg = _cfg(args)
    desk = Desk(cfg)
    try:
        rep = desk.cycle()
        desk.print_report(rep)
        return 0
    finally:
        desk.close()


def cmd_loop(args) -> int:
    cfg = _cfg(args)
    desk = Desk(cfg)
    n = 0
    try:
        while True:
            n += 1
            try:
                rep = desk.cycle()
                desk.print_report(rep)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log("ERROR", "cycle failed, continuing", error=str(exc)[:300])
                desk.journal.emit("cycle_error", {"error": str(exc)[:800]})
            if args.max_cycles and n >= args.max_cycles:
                break
            log("INFO", f"sleeping {args.interval}s until the next cycle")
            time.sleep(args.interval)
        return 0
    except KeyboardInterrupt:
        print("\nstopped by operator")
        return 0
    finally:
        desk.close()


def cmd_report(args) -> int:
    cfg = _cfg(args)
    from .journal import Journal
    j = Journal(cfg)
    st = j.stats()
    banner("Theta Council performance")
    for k in ("cycles", "closed", "wins", "losses", "win_rate", "realized_pl",
              "avg_win", "avg_loss", "profit_factor", "open_structures",
              "open_risk", "decisions_blocked"):
        print(f"  {k:<20}{st[k]}")

    open_trades = j.open_trades()
    banner(f"open book ({len(open_trades)})")
    for t in open_trades:
        legs = " ".join(f"{'+' if l['side'] == 'buy' else '-'}{l['strike']:g}"
                        f"{l['kind'][0].upper()}" for l in t["legs"])
        print(f"  {t['underlying']:<6}{t['structure']:<20}{t['expiry']}  "
              f"x{t['qty']}  net {t['net_price']:.2f}  risk "
              f"${float(t['max_loss']) * int(t['qty']):,.0f}   {legs}")

    closed = j.closed_trades(limit=25)
    banner(f"last {len(closed)} closed")
    for t in closed:
        print(f"  {t['underlying']:<6}{t['structure']:<20}"
              f"[{t['close_rule'] or '?':<6}] P&L ${float(t['realized_pl'] or 0):+,.2f}")

    from .agents.bandit import Bandit
    rows = Bandit(j).table()
    if rows:
        banner("track record by regime x structure")
        for r in rows:
            print(f"  {r['regime']:<10}{r['structure']:<20}{r['wins']}W/{r['losses']}L"
                  f"   prior {r['prior']:.3f}")
    j.close()
    return 0


def cmd_dashboard(args) -> int:
    cfg = _cfg(args)
    from .dashboard import serve
    serve(cfg, port=args.port)
    return 0


# ── argv ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # Shared flags live on a parent parser so they work after the subcommand,
    # which is where people instinctively type them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", default=".env", help="path to the env file")
    common.add_argument("--mock", action="store_true",
                        help="run against a built-in simulated market, no keys needed")
    common.add_argument("--dry-run", action="store_true",
                        help="run the full pipeline but print orders instead of sending")
    common.add_argument("--universe", help="override the symbol list, comma separated")

    p = argparse.ArgumentParser(
        prog="python -m council",
        description="Theta Council - an autonomous options desk on Alpaca.",
        epilog="Flags go after the subcommand, e.g. `once --mock`.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("preflight", parents=[common],
                   help="verify account, level, balance and pipes")
    sub.add_parser("mcp-doctor", parents=[common],
                   help="connect to the Alpaca MCP server and map tools")
    sub.add_parser("once", parents=[common], help="run a single cycle")
    lp = sub.add_parser("loop", parents=[common], help="run continuously")
    lp.add_argument("--interval", type=int, default=600, help="seconds between cycles")
    lp.add_argument("--max-cycles", type=int, default=0, help="stop after N cycles")
    sub.add_parser("report", parents=[common],
                   help="performance, open book, track record")
    db = sub.add_parser("dashboard", parents=[common],
                        help="serve the live web dashboard")
    db.add_argument("--port", type=int, default=8787)
    return p


HANDLERS = {"preflight": cmd_preflight, "mcp-doctor": cmd_mcp_doctor,
            "once": cmd_once, "loop": cmd_loop, "report": cmd_report,
            "dashboard": cmd_dashboard}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    try:
        return HANDLERS[args.cmd](args)
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        log("ERROR", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
