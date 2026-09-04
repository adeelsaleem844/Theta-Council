"""Configuration and the risk budget.

Every number the Risk Officer enforces lives here, loaded from the environment,
so a judge can re-run the desk with a different risk appetite without touching
a line of code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .util import env, env_b, env_f, env_i, load_dotenv

def _parse_tenors(spec: str, dte_min: int, dte_max: int) -> tuple:
    """Parse TENORS="21-45,2-9" into ((21,45),(2,9)). Falls back to one bucket."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            a, b = int(lo), int(hi or lo)
        except ValueError:
            continue
        if 0 < a <= b:
            out.append((a, b))
    return tuple(out) if out else ((dte_min, dte_max),)


TRADING_PAPER = "https://paper-api.alpaca.markets"
TRADING_LIVE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


@dataclass
class RiskBudget:
    """Hard limits. The LLM cannot widen any of these - only tighten them."""

    max_risk_per_trade_pct: float = 2.0      # % of equity at risk in one structure
    max_deployed_risk_pct: float = 12.0      # % of equity at risk across the book
    max_concurrent_positions: int = 6
    max_positions_per_underlying: int = 2
    max_new_entries_per_cycle: int = 3
    max_net_delta_per_100k: float = 150.0    # share-equivalent delta band
    daily_loss_kill_pct: float = 2.5         # day P&L that halts new entries
    drawdown_kill_pct: float = 8.0           # peak-to-trough that stands the desk down
    min_credit_to_width: float = 0.15        # never sell a spread for scraps
    min_debit_reward_ratio: float = 1.4      # long spreads need >= 1.4:1 payoff
    min_open_interest: int = 250
    max_spread_pct_of_mid: float = 12.0      # option bid/ask quality gate
    dte_min: int = 14
    dte_max: int = 45
    # Tenor buckets the Architect builds into, as (min_dte, max_dte). One
    # bucket per book: a 21-45 day core income book earns most of its return
    # from time decay over weeks; a short-dated bucket earns far more theta per
    # day at much higher gamma. Configure with TENORS=21-45,2-9
    tenors: tuple = ((14, 45),)
    min_vrp_ratio: float = 1.12              # IV/RV needed before we SELL premium
    max_vrp_ratio_for_debit: float = 0.98    # IV/RV under which we BUY premium
    min_short_delta: float = 0.10            # no lottery-ticket wings
    max_short_delta: float = 0.28
    take_profit_pct: float = 55.0            # close credit trades at 55% of max
    stop_loss_mult: float = 2.0              # exit at 2x the credit received
    time_exit_dte: int = 7                   # never hold short gamma into expiry
    delta_breach: float = 0.40               # short strike delta -> defend
    min_bp_reserve_pct: float = 25.0         # keep a quarter of buying power free
    no_entry_first_minutes: int = 10
    no_entry_last_minutes: int = 20
    earnings_blackout_days: int = 3


@dataclass
class Config:
    api_key: str = ""
    secret_key: str = ""
    paper: bool = True
    account_id: str = ""
    stock_feed: str = "iex"
    option_feed: str = "indicative"

    transport_order: list = field(default_factory=lambda: ["cli", "mcp", "rest"])
    cli_bin: str = "alpaca"
    mcp_cmd: str = "uvx alpaca-mcp-server"

    llm_provider: str = "none"
    featherless_key: str = ""
    featherless_base: str = "https://api.featherless.ai/v1"
    featherless_model: str = "meta-llama/Llama-3.3-70B-Instruct"
    anthropic_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    universe: list = field(default_factory=list)
    risk: RiskBudget = field(default_factory=RiskBudget)

    state_dir: str = "state"
    dry_run: bool = False
    mock: bool = False

    @property
    def trading_base(self) -> str:
        return TRADING_PAPER if self.paper else TRADING_LIVE

    @property
    def data_base(self) -> str:
        return DATA_BASE

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @classmethod
    def load(cls, dotenv: str = ".env") -> "Config":
        load_dotenv(dotenv)
        risk = RiskBudget(
            max_risk_per_trade_pct=env_f("MAX_RISK_PER_TRADE_PCT", 2.0),
            max_deployed_risk_pct=env_f("MAX_DEPLOYED_RISK_PCT", 12.0),
            max_concurrent_positions=env_i("MAX_CONCURRENT_POSITIONS", 6),
            max_positions_per_underlying=env_i("MAX_POSITIONS_PER_UNDERLYING", 2),
            max_new_entries_per_cycle=env_i("MAX_NEW_ENTRIES_PER_CYCLE", 3),
            max_net_delta_per_100k=env_f("MAX_NET_DELTA_PER_100K", 150.0),
            daily_loss_kill_pct=env_f("DAILY_LOSS_KILL_PCT", 2.5),
            drawdown_kill_pct=env_f("DRAWDOWN_KILL_PCT", 8.0),
            min_credit_to_width=env_f("MIN_CREDIT_TO_WIDTH", 0.15),
            min_debit_reward_ratio=env_f("MIN_DEBIT_REWARD_RATIO", 1.4),
            min_open_interest=env_i("MIN_OPEN_INTEREST", 250),
            max_spread_pct_of_mid=env_f("MAX_SPREAD_PCT_OF_MID", 12.0),
            dte_min=env_i("DTE_MIN", 14),
            dte_max=env_i("DTE_MAX", 45),
            tenors=_parse_tenors(str(env("TENORS", "")),
                                 env_i("DTE_MIN", 14), env_i("DTE_MAX", 45)),
            min_vrp_ratio=env_f("MIN_VRP_RATIO", 1.12),
            max_vrp_ratio_for_debit=env_f("MAX_VRP_RATIO_FOR_DEBIT", 0.98),
            min_short_delta=env_f("MIN_SHORT_DELTA", 0.10),
            max_short_delta=env_f("MAX_SHORT_DELTA", 0.28),
            take_profit_pct=env_f("TAKE_PROFIT_PCT", 55.0),
            stop_loss_mult=env_f("STOP_LOSS_MULT", 2.0),
            time_exit_dte=env_i("TIME_EXIT_DTE", 7),
            delta_breach=env_f("DELTA_BREACH", 0.40),
            min_bp_reserve_pct=env_f("MIN_BP_RESERVE_PCT", 25.0),
            earnings_blackout_days=env_i("EARNINGS_BLACKOUT_DAYS", 3),
        )
        uni = str(env("UNIVERSE", "SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMD,META,GOOGL,AMZN"))
        order = str(env("TRANSPORT_ORDER", "cli,mcp,rest"))
        return cls(
            api_key=str(env("ALPACA_API_KEY", "") or ""),
            secret_key=str(env("ALPACA_SECRET_KEY", "") or ""),
            paper=env_b("ALPACA_PAPER_TRADE", True),
            account_id=str(env("ALPACA_ACCOUNT_ID", "") or ""),
            stock_feed=str(env("ALPACA_STOCK_FEED", "iex")),
            option_feed=str(env("ALPACA_OPTION_FEED", "indicative")),
            transport_order=[t.strip() for t in order.split(",") if t.strip()],
            cli_bin=str(env("ALPACA_CLI_BIN", "alpaca")),
            mcp_cmd=str(env("ALPACA_MCP_CMD", "uvx alpaca-mcp-server")),
            llm_provider=str(env("LLM_PROVIDER", "none")).lower(),
            featherless_key=str(env("FEATHERLESS_API_KEY", "") or ""),
            featherless_base=str(env("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")),
            featherless_model=str(env("FEATHERLESS_MODEL", "meta-llama/Llama-3.3-70B-Instruct")),
            anthropic_key=str(env("ANTHROPIC_API_KEY", "") or ""),
            anthropic_model=str(env("ANTHROPIC_MODEL", "claude-sonnet-5")),
            universe=[s.strip().upper() for s in uni.split(",") if s.strip()],
            risk=risk,
            state_dir=str(env("STATE_DIR", "state")),
        )
