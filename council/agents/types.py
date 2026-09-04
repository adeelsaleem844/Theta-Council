"""Shared vocabulary for the council."""
from __future__ import annotations

from dataclasses import dataclass, field

# regimes
BULL = "bull"
BEAR = "bear"
CHOP = "chop"
VOLATILE = "volatile"

# structures
BULL_PUT_SPREAD = "bull_put_spread"        # credit, bullish
BEAR_CALL_SPREAD = "bear_call_spread"      # credit, bearish
IRON_CONDOR = "iron_condor"                # credit, neutral
LONG_CALL_SPREAD = "long_call_spread"      # debit, bullish
LONG_PUT_SPREAD = "long_put_spread"        # debit, bearish

CREDIT_STRUCTURES = (BULL_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR)
DEBIT_STRUCTURES = (LONG_CALL_SPREAD, LONG_PUT_SPREAD)

STRUCTURE_LABEL = {
    BULL_PUT_SPREAD: "bull put spread",
    BEAR_CALL_SPREAD: "bear call spread",
    IRON_CONDOR: "iron condor",
    LONG_CALL_SPREAD: "long call spread",
    LONG_PUT_SPREAD: "long put spread",
}


@dataclass
class Leg:
    symbol: str
    side: str            # buy | sell
    ratio: int = 1
    kind: str = ""       # call | put
    strike: float = 0.0
    expiry: str = ""
    mid: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    delta: float = 0.0
    iv: float = 0.0
    open_interest: int = 0

    @property
    def position_intent(self) -> str:
        return "buy_to_open" if self.side == "buy" else "sell_to_open"

    @property
    def closing_intent(self) -> str:
        return "sell_to_close" if self.side == "buy" else "buy_to_close"


@dataclass
class Regime:
    symbol: str
    label: str = CHOP
    trend_score: float = 0.0      # -1 bearish .. +1 bullish
    conviction: float = 0.0       # 0 .. 1
    notes: list = field(default_factory=list)


@dataclass
class VolView:
    symbol: str
    edge: str = "none"            # sell | buy | none
    vrp_ratio: float = 0.0
    iv: float = 0.0
    rv: float = 0.0
    iv_rank: float = 50.0
    score: float = 0.0            # 0 .. 1
    notes: list = field(default_factory=list)


@dataclass
class Candidate:
    """A fully specified, executable options structure."""
    cid: str
    underlying: str
    structure: str
    legs: list = field(default_factory=list)
    expiry: str = ""
    dte: int = 0
    is_credit: bool = True
    net_price: float = 0.0        # per-share premium: credit received or debit paid
    width: float = 0.0            # widest vertical width, in points
    max_loss: float = 0.0         # dollars per contract
    max_gain: float = 0.0         # dollars per contract
    short_delta: float = 0.0
    pop_iv: float = 0.0           # market-implied probability of profit
    pop_rv: float = 0.0           # our forecast, using realised vol
    ev_dollars: float = 0.0       # expected value per contract under pop_rv
    edge_ratio: float = 0.0       # ev / max_loss  <- the ranking number
    vrp_ratio: float = 0.0
    liquidity: float = 0.0        # 0 .. 1
    score: float = 0.0
    qty: int = 0
    conviction: float = 0.5
    thesis: str = ""
    invalidators: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    prior: float = 0.5            # bandit posterior for (regime, structure)
    regime: str = CHOP

    def label(self) -> str:
        return f"{self.underlying} {STRUCTURE_LABEL.get(self.structure, self.structure)}"

    def strikes(self) -> str:
        parts = []
        for lg in sorted(self.legs, key=lambda x: (x.kind, x.strike)):
            parts.append(f"{'+' if lg.side == 'buy' else '-'}{lg.strike:g}{lg.kind[0].upper()}")
        return "/".join(parts)

    def compact(self) -> dict:
        """The view offered to the LLM. Deliberately small and numeric."""
        return {
            "id": self.cid,
            "symbol": self.underlying,
            "structure": self.structure,
            "regime": self.regime,
            "dte": self.dte,
            "strikes": self.strikes(),
            "net_credit" if self.is_credit else "net_debit": round(self.net_price, 2),
            "width": self.width,
            "max_loss_per_contract": round(self.max_loss, 2),
            "max_gain_per_contract": round(self.max_gain, 2),
            "short_delta": round(self.short_delta, 3),
            "pop_implied": round(self.pop_iv, 3),
            "pop_forecast_rv": round(self.pop_rv, 3),
            "expected_value_per_contract": round(self.ev_dollars, 2),
            "edge_ratio": round(self.edge_ratio, 3),
            "vrp_ratio": round(self.vrp_ratio, 3),
            "liquidity": round(self.liquidity, 2),
            "track_record_prior": round(self.prior, 3),
            "desk_score": round(self.score, 3),
        }


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.passed else "BLOCK"
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class Verdict:
    candidate: Candidate
    approved: bool
    gates: list = field(default_factory=list)
    reason: str = ""

    def blocked_by(self) -> list:
        return [g.name for g in self.gates if not g.passed]
