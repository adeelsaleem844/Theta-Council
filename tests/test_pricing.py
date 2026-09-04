"""Pricing, indicators and symbol handling.

Runs on the standard library: `python -m unittest discover -s tests`
"""
import datetime as dt
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from council import blackscholes as bs      # noqa: E402
from council import indicators as ind       # noqa: E402
from council import occ                     # noqa: E402


class TestBlackScholes(unittest.TestCase):
    def test_put_call_parity(self):
        s, k, t, v, r = 100.0, 105.0, 0.25, 0.28, 0.045
        call = bs.price(s, k, t, v, "call", r)
        put = bs.price(s, k, t, v, "put", r)
        # C - P = S - K e^{-rt}
        self.assertAlmostEqual(call - put, s - k * math.exp(-r * t), places=6)

    def test_deltas_are_bounded_and_signed(self):
        for k in (80.0, 100.0, 130.0):
            d_call = bs.delta(100.0, k, 0.15, 0.3, "call")
            d_put = bs.delta(100.0, k, 0.15, 0.3, "put")
            self.assertTrue(0.0 <= d_call <= 1.0)
            self.assertTrue(-1.0 <= d_put <= 0.0)
            # Same strike: call delta minus put delta is one (no dividend).
            self.assertAlmostEqual(d_call - d_put, 1.0, places=4)

    def test_implied_vol_round_trip(self):
        for vol in (0.09, 0.22, 0.65, 1.4):
            px = bs.price(100.0, 95.0, 0.12, vol, "put")
            solved = bs.implied_vol(px, 100.0, 95.0, 0.12, "put")
            self.assertIsNotNone(solved)
            self.assertAlmostEqual(solved, vol, places=4)

    def test_implied_vol_rejects_impossible_price(self):
        # A price under intrinsic has no solution and must not be invented.
        self.assertIsNone(bs.implied_vol(0.5, 100.0, 130.0, 0.2, "put"))

    def test_short_dated_theta_is_negative_for_long_options(self):
        self.assertLess(bs.theta(100.0, 100.0, 0.02, 0.3, "call"), 0.0)

    def test_prob_itm_matches_delta_intuition(self):
        # A ~16 delta short put should have roughly a 16% chance of finishing ITM.
        k, t, v = 90.0, 30 / 365.0, 0.25
        d = abs(bs.delta(100.0, k, t, v, "put"))
        p = bs.prob_itm(100.0, k, t, v, "put")
        self.assertLess(abs(d - p), 0.06)

    def test_zero_time_falls_back_to_intrinsic(self):
        self.assertAlmostEqual(bs.price(110.0, 100.0, 0.0, 0.3, "call"), 10.0)
        self.assertAlmostEqual(bs.price(110.0, 100.0, 0.0, 0.3, "put"), 0.0)

    def test_prob_touch_exceeds_prob_itm(self):
        pt = bs.prob_touch(100.0, 92.0, 30 / 365.0, 0.25)
        pi = bs.prob_itm(100.0, 92.0, 30 / 365.0, 0.25, "put")
        self.assertGreater(pt, pi)
        self.assertLessEqual(pt, 1.0)


class TestIndicators(unittest.TestCase):
    def test_rsi_extremes(self):
        self.assertAlmostEqual(ind.rsi([float(i) for i in range(1, 40)], 14), 100.0)
        self.assertLess(ind.rsi([float(i) for i in range(40, 1, -1)], 14), 1.0)

    def test_ema_tracks_a_constant_series(self):
        self.assertAlmostEqual(ind.ema([7.0] * 60, 20), 7.0, places=9)

    def test_realized_vol_of_a_flat_series_is_zero(self):
        self.assertAlmostEqual(ind.realized_vol([100.0] * 40, 20), 0.0, places=12)

    def test_realized_vol_scales_with_noise(self):
        import random
        rnd = random.Random(4)
        quiet = [100.0]
        loud = [100.0]
        for _ in range(200):
            quiet.append(quiet[-1] * math.exp(rnd.gauss(0, 0.004)))
            loud.append(loud[-1] * math.exp(rnd.gauss(0, 0.020)))
        self.assertLess(ind.realized_vol(quiet, 60), ind.realized_vol(loud, 60))

    def test_insufficient_data_returns_none_not_zero(self):
        # Silently returning 0 would look like "no volatility" to the Vol Scout.
        self.assertIsNone(ind.realized_vol([1.0, 2.0], 20))
        self.assertIsNone(ind.rsi([1.0, 2.0], 14))
        self.assertIsNone(ind.ema([1.0], 20))

    def test_percentile_rank(self):
        hist = [float(i) for i in range(1, 101)]
        self.assertAlmostEqual(ind.percentile_rank(hist, 50.0), 50.0, places=6)
        self.assertAlmostEqual(ind.percentile_rank(hist, 100.0), 100.0, places=6)

    def test_max_drawdown(self):
        self.assertAlmostEqual(ind.max_drawdown([100, 120, 90, 130]), -25.0, places=6)


class TestOcc(unittest.TestCase):
    def test_round_trip(self):
        sym = occ.build("NVDA", dt.date(2026, 9, 18), "put", 170.0)
        self.assertEqual(sym, "NVDA260918P00170000")
        p = occ.parse(sym)
        self.assertEqual(p["root"], "NVDA")
        self.assertEqual(p["kind"], "put")
        self.assertEqual(p["strike"], 170.0)
        self.assertEqual(p["expiry"], "2026-09-18")

    def test_fractional_strike(self):
        sym = occ.build("SPY", "2026-10-16", "call", 642.5)
        self.assertEqual(occ.parse(sym)["strike"], 642.5)

    def test_equities_are_not_options(self):
        for bad in ("SPY", "AAPL", "", "BRK.B", "NOTANOPTION123"):
            self.assertFalse(occ.is_option(bad), bad)

    def test_dte(self):
        sym = occ.build("SPY", dt.date(2026, 9, 18), "call", 640.0)
        self.assertEqual(occ.dte(sym, dt.date(2026, 9, 4)), 14)


if __name__ == "__main__":
    unittest.main()
