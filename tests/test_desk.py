"""The parts that decide whether money is lost: risk gates, sizing, order
construction, and the full pipeline end to end against the mock market.
"""
import datetime as dt
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from council import orders                                  # noqa: E402
from council.agents.analyst import Analyst                  # noqa: E402
from council.agents.architect import Architect              # noqa: E402
from council.agents.risk_officer import RiskOfficer         # noqa: E402
from council.agents.types import (BULL_PUT_SPREAD, IRON_CONDOR, Candidate,  # noqa: E402
                                  Leg)
from council.agents.vol_scout import VolScout                # noqa: E402
from council.config import Config, _parse_tenors             # noqa: E402
from council.engine import Desk                              # noqa: E402
from council.market import MarketIntake                      # noqa: E402
from council.transports.router import Router                 # noqa: E402


def mock_cfg(state_dir: str) -> Config:
    cfg = Config()
    cfg.mock = True
    cfg.transport_order = ["mock"]
    cfg.universe = ["SPY", "GOOGL", "NVDA"]
    cfg.state_dir = state_dir
    cfg.llm_provider = "none"
    return cfg


class TestTenorParsing(unittest.TestCase):
    def test_parses_buckets(self):
        self.assertEqual(_parse_tenors("21-45,2-9", 14, 45), ((21, 45), (2, 9)))

    def test_single_number_is_a_point_bucket(self):
        self.assertEqual(_parse_tenors("7", 14, 45), ((7, 7),))

    def test_garbage_falls_back_to_the_dte_window(self):
        self.assertEqual(_parse_tenors("nonsense,-,9-2", 14, 45), ((14, 45),))
        self.assertEqual(_parse_tenors("", 10, 20), ((10, 20),))


class TestOrderConstruction(unittest.TestCase):
    def _candidate(self, credit=True):
        legs = [Leg(symbol="SPY261016P00600000", side="sell", kind="put",
                    strike=600.0, expiry="2026-10-16", mid=3.00, bid=2.95,
                    ask=3.05, delta=-0.20, open_interest=5000),
                Leg(symbol="SPY261016P00595000", side="buy", kind="put",
                    strike=595.0, expiry="2026-10-16", mid=2.10, bid=2.05,
                    ask=2.15, delta=-0.15, open_interest=4000)]
        return Candidate(cid="X", underlying="SPY", structure=BULL_PUT_SPREAD,
                         legs=legs, expiry="2026-10-16", dte=42,
                         is_credit=credit, net_price=0.85, width=5.0,
                         max_loss=415.0, max_gain=85.0, qty=3)

    def test_mleg_payload_shape(self):
        p = orders.build_entry(self._candidate(), "coid-1")
        self.assertEqual(p["order_class"], "mleg")
        self.assertEqual(p["type"], "limit")
        self.assertEqual(p["time_in_force"], "day")
        self.assertEqual(p["qty"], "3")
        self.assertEqual(len(p["legs"]), 2)
        for leg in p["legs"]:
            self.assertIn(leg["side"], ("buy", "sell"))
            self.assertEqual(leg["ratio_qty"], "1")
            self.assertIn(leg["position_intent"], ("buy_to_open", "sell_to_open"))

    def test_credit_is_sent_negative(self):
        # Alpaca shows credits as negative and debits as positive.
        p = orders.build_entry(self._candidate(credit=True))
        self.assertLess(float(p["limit_price"]), 0.0)
        d = orders.build_entry(self._candidate(credit=False))
        self.assertGreater(float(d["limit_price"]), 0.0)

    def test_no_naked_leg_ever_leaves_the_builder(self):
        # A short leg must always be accompanied by its long wing in the SAME
        # order, otherwise the account is momentarily naked short.
        p = orders.build_entry(self._candidate())
        sides = {leg["side"] for leg in p["legs"]}
        self.assertEqual(sides, {"buy", "sell"})

    def test_exit_reverses_every_leg_and_closes(self):
        trade = {"qty": 2, "is_credit": True,
                 "legs": [{"symbol": "A", "side": "sell", "ratio": 1},
                          {"symbol": "B", "side": "buy", "ratio": 1}]}
        p = orders.build_exit(trade, close_value=0.40, urgency="urgent")
        self.assertEqual(p["qty"], "2")
        intents = {leg["symbol"]: leg for leg in p["legs"]}
        self.assertEqual(intents["A"]["side"], "buy")
        self.assertEqual(intents["A"]["position_intent"], "buy_to_close")
        self.assertEqual(intents["B"]["side"], "sell")
        self.assertEqual(intents["B"]["position_intent"], "sell_to_close")

    def test_urgent_exit_pays_more_than_a_patient_one(self):
        trade = {"qty": 1, "is_credit": True,
                 "legs": [{"symbol": "A", "side": "sell", "ratio": 1},
                          {"symbol": "B", "side": "buy", "ratio": 1}]}
        calm = abs(float(orders.build_exit(trade, 1.00, "normal")["limit_price"]))
        rush = abs(float(orders.build_exit(trade, 1.00, "urgent")["limit_price"]))
        self.assertGreater(rush, calm)

    def test_tick_rounding(self):
        self.assertEqual(orders.tick_for(1.23), 0.01)
        self.assertEqual(orders.tick_for(4.10), 0.05)
        self.assertAlmostEqual(orders.round_to_tick(1.238, favour="us"), 1.23)
        self.assertAlmostEqual(orders.round_to_tick(1.232, favour="them"), 1.24)


class TestRiskGates(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = mock_cfg(self.dir)
        self.router = Router(self.cfg)
        self.intake = MarketIntake(self.cfg, self.router, None)
        self.brief = self.intake.build()
        self.risk = RiskOfficer(self.cfg, None)

    def tearDown(self):
        self.router.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cand(self, **kw):
        legs = [Leg(symbol="SPY261016P00600000", side="sell", kind="put",
                    strike=600.0, expiry="2026-10-16", mid=3.0, bid=2.95,
                    ask=3.05, delta=-0.20, iv=0.18, open_interest=5000),
                Leg(symbol="SPY261016P00595000", side="buy", kind="put",
                    strike=595.0, expiry="2026-10-16", mid=2.1, bid=2.05,
                    ask=2.15, delta=-0.14, iv=0.19, open_interest=4000)]
        base = dict(cid="C1", underlying="SPY", structure=BULL_PUT_SPREAD,
                    legs=legs, expiry="2026-10-16", dte=30, is_credit=True,
                    net_price=0.90, width=5.0, max_loss=410.0, max_gain=90.0,
                    short_delta=0.20, pop_rv=0.82, pop_iv=0.80,
                    ev_dollars=45.0, edge_ratio=0.11, liquidity=0.8,
                    conviction=1.0, regime="bull")
        base.update(kw)
        return Candidate(**base)

    def _gate(self, verdict, name):
        return next(g for g in verdict.gates if g.name == name)

    def test_a_clean_candidate_passes_and_is_sized(self):
        self.risk.portfolio_gates(self.brief)
        v = self.risk.review(self._cand(), self.brief, True)
        self.assertTrue(v.approved, v.reason)
        self.assertGreaterEqual(v.candidate.qty, 1)

    def test_negative_expectancy_is_blocked(self):
        v = self.risk.review(self._cand(ev_dollars=-30.0, edge_ratio=-0.07),
                             self.brief, True)
        self.assertFalse(self._gate(v, "positive_expectancy").passed)
        self.assertFalse(v.approved)

    def test_thin_credit_relative_to_width_is_blocked(self):
        v = self.risk.review(self._cand(net_price=0.20, width=5.0), self.brief, True)
        self.assertFalse(self._gate(v, "credit_to_width").passed)

    def test_short_delta_outside_the_band_is_blocked(self):
        v = self.risk.review(self._cand(short_delta=0.46), self.brief, True)
        self.assertFalse(self._gate(v, "short_delta_band").passed)

    def test_dte_outside_every_tenor_is_blocked(self):
        v = self.risk.review(self._cand(dte=120), self.brief, True)
        self.assertFalse(self._gate(v, "dte_window").passed)

    def test_risk_per_trade_cap_shrinks_size_to_zero(self):
        # One contract risking 90% of equity cannot be sized at all.
        v = self.risk.review(self._cand(max_loss=self.brief.equity * 0.9),
                             self.brief, True)
        self.assertFalse(self._gate(v, "sizing").passed)
        self.assertEqual(v.candidate.qty, 0)

    def test_size_respects_the_per_trade_budget(self):
        self.risk.portfolio_gates(self.brief)
        cand = self._cand(max_loss=500.0)
        self.risk.review(cand, self.brief, True)
        budget = self.brief.equity * self.cfg.risk.max_risk_per_trade_pct / 100.0
        self.assertLessEqual(cand.qty * 500.0, budget + 1e-6)

    def test_llm_conviction_can_only_shrink_size(self):
        self.risk.portfolio_gates(self.brief)
        full = self._cand(max_loss=500.0, conviction=1.0)
        half = self._cand(max_loss=500.0, conviction=0.5)
        self.risk.review(full, self.brief, True)
        self.risk.review(half, self.brief, True)
        self.assertLess(half.qty, full.qty)

        # Even a conviction above 1.0 - which the adjudicator clamps, but test
        # the floor anyway - cannot buy more size than the budget allows.
        over = self._cand(max_loss=500.0, conviction=9.0)
        self.risk.review(over, self.brief, True)
        self.assertEqual(over.qty, full.qty)

    def test_options_level_below_three_blocks_spreads(self):
        self.brief.account["options_trading_level"] = 2
        v = self.risk.review(self._cand(), self.brief, True)
        self.assertFalse(self._gate(v, "structure_permitted").passed)

    def test_portfolio_stand_down_blocks_every_candidate(self):
        v = self.risk.review(self._cand(), self.brief, False)
        self.assertFalse(self._gate(v, "portfolio_clear").passed)
        self.assertFalse(v.approved)

    def test_daily_loss_kill_switch_trips(self):
        self.brief.account["equity"] = 96_000.0
        self.brief.account["last_equity"] = 100_000.0
        gates = self.risk.portfolio_gates(self.brief)
        g = next(x for x in gates if x.name == "daily_loss_kill")
        self.assertFalse(g.passed)
        self.assertIn("daily_loss_kill", self.risk.stand_down_reasons)

    def test_market_closed_stands_the_desk_down(self):
        self.brief.is_open = False
        gates = self.risk.portfolio_gates(self.brief)
        self.assertFalse(next(x for x in gates if x.name == "market_open").passed)
        self.assertFalse(next(x for x in gates if x.name == "time_of_day").passed)

    def test_earnings_inside_the_trade_blocks_single_names(self):
        self.risk.earnings = {"GOOGL": (dt.date.today()
                                        + dt.timedelta(days=10)).isoformat()}
        v = self.risk.review(
            self._cand(underlying="GOOGL",
                       expiry=(dt.date.today() + dt.timedelta(days=30)).isoformat()),
            self.brief, True)
        self.assertFalse(self._gate(v, "earnings_blackout").passed)

    def test_etfs_are_exempt_from_the_earnings_gate(self):
        self.risk.earnings = {}
        v = self.risk.review(self._cand(underlying="SPY"), self.brief, True)
        self.assertTrue(self._gate(v, "earnings_blackout").passed)

    def test_pending_entries_count_toward_concentration(self):
        pending = [self._cand(cid="P1"), self._cand(cid="P2")]
        v = self.risk.review(self._cand(cid="C3"), self.brief, True, pending=pending)
        self.assertFalse(self._gate(v, "per_underlying_cap").passed)


class TestArchitectEdge(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = mock_cfg(self.dir)
        self.router = Router(self.cfg)
        self.brief = MarketIntake(self.cfg, self.router, None).build()

    def tearDown(self):
        self.router.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_credit_structures_have_defined_positive_risk(self):
        arch, an, vs = Architect(self.cfg), Analyst(self.cfg), VolScout(self.cfg)
        built = 0
        for sym, sb in self.brief.symbols.items():
            cands = arch.design(sb, an.read(sb), vs.read(sb))
            for c in cands:
                built += 1
                self.assertGreater(c.max_loss, 0.0)
                self.assertGreater(c.net_price, 0.0)
                self.assertGreater(c.width, 0.0)
                self.assertTrue(0.0 <= c.pop_rv <= 1.0)
                # Defined risk means the wing is always present.
                self.assertIn("buy", {lg.side for lg in c.legs})
                self.assertIn("sell", {lg.side for lg in c.legs})
                if c.is_credit:
                    self.assertLess(c.net_price, c.width)
        self.assertGreater(built, 0, "mock market produced no candidates")

    def test_selling_premium_is_only_proposed_when_implied_exceeds_realised(self):
        arch, an, vs = Architect(self.cfg), Analyst(self.cfg), VolScout(self.cfg)
        for sym, sb in self.brief.symbols.items():
            vol = vs.read(sb)
            for c in arch.design(sb, an.read(sb), vol):
                if c.is_credit:
                    self.assertGreaterEqual(c.vrp_ratio,
                                            self.cfg.risk.min_vrp_ratio - 1e-9)

    def test_no_candidate_when_the_variance_premium_is_absent(self):
        arch, an, vs = Architect(self.cfg), Analyst(self.cfg), VolScout(self.cfg)
        sb = self.brief.symbols["SPY"]
        sb.vrp_ratio = 1.00        # squarely inside the no-trade band
        sb.atm_iv = sb.rv20
        self.assertEqual(arch.design(sb, an.read(sb), vs.read(sb)), [])


class TestGateInventory(unittest.TestCase):
    """The README, write-up and slides all quote gate counts. Assert them
    against the source so the documentation cannot drift out of date."""

    def _names(self):
        src = pathlib.Path(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "council", "agents", "risk_officer.py")
        ).read_text(encoding="utf-8")
        split = src.index("def review(")
        portfolio = re.findall(r'Gate\("([a-z_]+)"', src[:split])
        candidate = re.findall(r'Gate\("([a-z_]+)"', src[split:])
        dedupe = lambda xs: list(dict.fromkeys(xs))
        return dedupe(portfolio), dedupe(candidate)

    def test_counts_match_the_documentation(self):
        portfolio, candidate = self._names()
        self.assertEqual(len(portfolio), 10, portfolio)
        self.assertEqual(len(candidate), 14, candidate)
        self.assertEqual(len(set(portfolio + candidate)), 24)

    def test_the_kill_switches_exist_by_name(self):
        portfolio, _ = self._names()
        for required in ("daily_loss_kill", "drawdown_kill", "net_delta_band",
                         "deployed_risk", "bp_reserve", "options_level"):
            self.assertIn(required, portfolio)

    def test_sizing_is_a_gate(self):
        _, candidate = self._names()
        self.assertIn("sizing", candidate)
        self.assertIn("positive_expectancy", candidate)


class TestFullCycle(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cycle_trades_journals_and_then_manages(self):
        cfg = mock_cfg(self.dir)
        desk = Desk(cfg)
        try:
            rep = desk.cycle()
            self.assertGreater(len(rep.candidates), 0)
            self.assertGreater(len(rep.submitted), 0, "no orders were sent")
            self.assertLessEqual(len(rep.submitted),
                                 cfg.risk.max_new_entries_per_cycle)

            # Everything the desk considered is on the record, approved or not.
            considered = {v.candidate.cid for v in rep.verdicts}
            self.assertEqual(considered, {c.cid for c in rep.candidates})

            opened = desk.journal.open_trades()
            self.assertEqual(len(opened), len(rep.submitted))
            self.assertGreater(desk.journal.open_risk(), 0.0)

            # Risk actually deployed must respect the book-level cap.
            self.assertLess(desk.journal.open_risk(),
                            rep.equity * cfg.risk.max_deployed_risk_pct / 100.0)

            # A second cycle must manage the open book, not duplicate it.
            rep2 = desk.cycle()
            for t in desk.journal.open_trades():
                self.assertFalse(
                    any(s["cid"].startswith(t["underlying"]) and
                        t["expiry"] in s["cid"] and
                        t["structure"] in ("", s.get("structure", ""))
                        for s in rep2.submitted),
                    "the desk re-opened a structure it already holds")
            self.assertGreaterEqual(len(rep2.held) + len(rep2.closed), 1)
        finally:
            desk.close()

    def test_dry_run_sends_nothing(self):
        cfg = mock_cfg(self.dir)
        cfg.dry_run = True
        desk = Desk(cfg)
        try:
            rep = desk.cycle()
            self.assertGreater(len(rep.candidates), 0)
            self.assertEqual(desk.journal.open_trade_count(), 0)
            for s in rep.submitted:
                self.assertEqual(s["trade_id"], "dry-run")
        finally:
            desk.close()

    def test_provenance_is_recorded_for_every_intent(self):
        cfg = mock_cfg(self.dir)
        desk = Desk(cfg)
        try:
            desk.cycle()
            summary = desk.router.summary()
            for intent in ("clock", "account", "positions", "stock_bars",
                           "option_chain"):
                self.assertIn(intent, summary["by_intent"])
        finally:
            desk.close()


if __name__ == "__main__":
    unittest.main()
