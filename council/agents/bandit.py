"""The desk's memory: a Beta-Bernoulli posterior per (regime, structure).

Every closed structure is one Bernoulli trial. Iron condors opened in chop get
their own bucket from bull put spreads opened in an uptrend, because they are
different bets that happen to share a broker.

    posterior mean = (wins + 1) / (wins + losses + 2)      # Laplace prior

Raw posterior means are dangerous with three samples, so the value is shrunk
toward 0.5 in proportion to how little evidence exists:

    prior = 0.5 + (mean - 0.5) * min(1, n / CONFIDENCE_N)

That number rides into the Architect's score and into the sizing dial, so a
structure that has been losing gets smaller before it gets abandoned. It is a
slow, honest feedback loop rather than a claim to have learned the market in a
week.
"""
from __future__ import annotations

CONFIDENCE_N = 8.0


class Bandit:
    name = "bandit"

    def __init__(self, journal=None):
        self.journal = journal
        self._cache: dict = {}

    def refresh(self) -> None:
        self._cache = self.journal.outcome_counts() if self.journal else {}

    def counts(self, regime: str, structure: str) -> tuple:
        if not self._cache:
            self.refresh()
        w, l = self._cache.get((regime, structure), (0, 0))
        return w, l

    def prior(self, regime: str, structure: str) -> float:
        wins, losses = self.counts(regime, structure)
        n = wins + losses
        if n == 0:
            return 0.5
        mean = (wins + 1.0) / (n + 2.0)
        shrink = min(1.0, n / CONFIDENCE_N)
        return 0.5 + (mean - 0.5) * shrink

    def table(self) -> list:
        if not self._cache:
            self.refresh()
        rows = []
        for (regime, structure), (w, l) in sorted(self._cache.items()):
            rows.append({
                "regime": regime, "structure": structure, "wins": w, "losses": l,
                "n": w + l, "prior": round(self.prior(regime, structure), 3),
            })
        return rows
