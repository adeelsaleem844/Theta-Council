"""The council: five agents, one veto.

    Analyst        which way is this leaning, and how much do we believe it
    VolScout       is the market paying more for movement than it delivers
    Architect      turn that into real strikes, and price the edge in dollars
    Adjudicator    the LLM - cross-sectional judgement, veto and size dial only
    RiskOfficer    deterministic gates, sizing, kill switches. Holds the veto.
    PositionManager exits, which is where the P&L actually comes from
    Bandit         per (regime, structure) memory that feeds back into sizing
"""
from .analyst import Analyst
from .architect import Architect
from .bandit import Bandit
from .llm import Adjudicator
from .manager import PositionManager
from .risk_officer import RiskOfficer
from .vol_scout import VolScout

__all__ = ["Analyst", "VolScout", "Architect", "Adjudicator", "RiskOfficer",
           "PositionManager", "Bandit"]
