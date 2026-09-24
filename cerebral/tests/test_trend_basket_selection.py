import tempfile
import unittest
from datetime import date
from pathlib import Path
import pandas as pd

from cerebral.settings import SettingsStore

from cerebral.trading.trend_basket_selection import (
    compute_breadth,
    rank_by_momentum,
    RisingEdgeGate,
)


class MockFetchBars:
    def __init__(self, data: dict[str, pd.DataFrame]):
        self.data = data

    def __call__(self, sym: str, n: int, freq: str) -> pd.DataFrame:
        if sym not in self.data:
            raise KeyError(sym)
        df = self.data[sym].copy()
        if len(df) < n:
            return df.iloc[:len(df)]
        return df


class TestComputeBreadth(unittest.TestCase):
    def test_basic_breadth(self):
        data = {
            "A": pd.DataFrame({"close": [10.0] * 50 + [11.0]}),
            "B": pd.DataFrame({"close": [10.0] * 50 + [9.0]}),
            "C": pd.DataFrame({"close": [10.0] * 50 + [12.0]}),
            "D": pd.DataFrame({"close": [10.0] * 50 + [10.0]}),
        }
        fetch = MockFetchBars(data)
        res = compute_breadth(["A", "B", "C", "D"], fetch, min_measured=1)
        
        self.assertEqual(res.above_ma, ["A", "C"])
        self.assertEqual(res.below_ma, ["B", "D"])
        self.assertAlmostEqual(res.breadth, 0.5)

    def test_insufficient_history_excluded(self):
        data = {
            "SHORT": pd.DataFrame({"close": [10.0] * 20 + [11.0]}),
            "LONG":  pd.DataFrame({"close": [10.0] * 50 + [12.0]}),
        }
        fetch = MockFetchBars(data)
        res = compute_breadth(["SHORT", "LONG"], fetch, min_measured=1)
        
        # SHORT should be excluded from both numerator and denominator
        self.assertEqual(res.above_ma, ["LONG"])
        self.assertEqual(res.below_ma, [])
        self.assertAlmostEqual(res.breadth, 1.0)
        
    def test_no_candidates_is_no_reading_not_zero(self):
        """An empty pool must not read as 0% breadth -- the gate would lock that in for the day."""
        res = compute_breadth([], MockFetchBars({}))
        self.assertIsNone(res.breadth)

    def test_too_few_measured_is_no_reading(self):
        data = {s: pd.DataFrame({"close": [10.0] * 50 + [11.0]}) for s in "ABC"}
        self.assertIsNone(compute_breadth(list("ABC"), MockFetchBars(data)).breadth)  # 3 < 20

    def test_gate_keeps_its_state_through_a_missing_reading(self):
        gate = RisingEdgeGate(threshold=0.55, sustain=0.50)
        self.assertTrue(gate.refresh(0.56, date(2026, 10, 1)))
        self.assertTrue(gate.refresh(None, date(2026, 10, 2)))  # no reading: stays active
        self.assertEqual(gate.last_reading["last_checked"], "2026-10-01")  # not locked in


class TestRankByMomentum(unittest.TestCase):
    def test_ranking_order_is_momentum_times_volatility(self):
        """ADR-0038's locked score. CHOPPY has less momentum than SMOOTH (+10% vs +20%) but far
        more volatility, so it ranks first; pure momentum would put SMOOTH first."""
        data = {
            "SMOOTH": pd.DataFrame({"close": [100.0, 110.0, 120.0]}),
            "CHOPPY": pd.DataFrame({"close": [100.0, 150.0, 110.0]}),
            "FLAT":   pd.DataFrame({"close": [100.0, 101.0, 100.0]}),
        }
        ranked = rank_by_momentum(["SMOOTH", "CHOPPY", "FLAT"], MockFetchBars(data), horizon=2)
        self.assertEqual(ranked, ["CHOPPY", "SMOOTH", "FLAT"])

    def test_excludes_short_history(self):
        data = {
            "SHORT": pd.DataFrame({"close": [100.0, 110.0]}),
            "LONG":  pd.DataFrame({"close": [100.0, 110.0, 115.0]}),
        }
        ranked = rank_by_momentum(["SHORT", "LONG"], MockFetchBars(data), horizon=2)
        self.assertEqual(ranked, ["LONG"])


class TestRisingEdgeGate(unittest.TestCase):
    def test_rising_edge_detection(self):
        gate = RisingEdgeGate(threshold=0.6)
        
        self.assertTrue(gate.current)  # Fail-open initial state
        
        d1 = date(2023, 1, 1)
        self.assertFalse(gate.refresh(0.5, d1))   # off
        
        d2 = date(2023, 1, 2)
        self.assertTrue(gate.refresh(0.65, d2))   # off -> on IS a new edge
        
        d3 = date(2023, 1, 3)
        self.assertFalse(gate.refresh(0.7, d3))   # on -> on is NOT a new edge
        
        d4 = date(2023, 1, 4)
        self.assertFalse(gate.refresh(0.5, d4))   # on -> off
        
        d5 = date(2023, 1, 5)
        self.assertTrue(gate.refresh(0.61, d5))   # off -> on fires again

    def test_fail_open_on_error(self):
        gate = RisingEdgeGate(threshold=0.6)
        d1 = date(2023, 1, 1)
        
        self.assertTrue(gate.refresh(None, d1))  # None -> no state change
        self.assertTrue(gate.current)
        
        # Verify robustness to unexpected values
        try:
            gate.refresh(0.5, date(2023, 1, 2))
        except Exception:
            self.fail("RisingEdgeGate.refresh raised unexpectedly on valid inputs")

    def test_sustain_keeps_the_regime_active_until_breadth_drops_below_it(self):
        """Trigger 55% / sustain 50%: active from the cross, through a dip to 52%, off at 49%,
        and a later reading above 50% (but not a fresh cross above 55%) stays off."""
        gate = RisingEdgeGate(threshold=0.55, sustain=0.50)
        steps = [(0.54, False), (0.56, True), (0.60, True), (0.52, True), (0.49, False), (0.53, False),
                 (0.56, True)]
        for day, (breadth, expected) in enumerate(steps, start=1):
            self.assertEqual(gate.refresh(breadth, date(2026, 10, day)), expected, (day, breadth))

    def test_active_state_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "s.json")
            RisingEdgeGate(threshold=0.55, sustain=0.50, store=store).refresh(0.56, date(2026, 10, 1))
            restarted = RisingEdgeGate(threshold=0.55, sustain=0.50, store=store)
            self.assertTrue(restarted.refresh(0.52, date(2026, 10, 2)))  # still above sustain

    def test_state_survives_a_restart(self):
        """A restart must remember yesterday's reading: breadth already above threshold yesterday
        and today is NOT a new edge, even for a freshly constructed gate."""
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "s.json")
            RisingEdgeGate(threshold=0.6, store=store).refresh(0.65, date(2026, 9, 23))
            restarted = RisingEdgeGate(threshold=0.6, store=store)
            self.assertFalse(restarted.refresh(0.70, date(2026, 9, 24)))
            self.assertEqual(restarted.last_reading["breadth"], 0.70)

    def test_last_reading_before_any_refresh(self):
        gate = RisingEdgeGate(threshold=0.6)
        reading = gate.last_reading
        self.assertIsNone(reading["breadth"])
        self.assertEqual(reading["threshold"], 0.6)
        self.assertIsNone(reading["last_checked"])

    def test_last_reading_reflects_the_last_refresh(self):
        gate = RisingEdgeGate(threshold=0.6)
        gate.refresh(0.625, date(2026, 9, 23))
        reading = gate.last_reading
        self.assertEqual(reading["breadth"], 0.625)
        self.assertEqual(reading["threshold"], 0.6)
        self.assertTrue(reading["is_rising_edge"])
        self.assertEqual(reading["last_checked"], "2026-09-23")

    def test_last_reading_does_not_update_on_a_same_day_recheck(self):
        """A later call the SAME day is a no-op by design (refresh's own once-per-day cache) --
        last_reading must reflect that frozen state, not silently show a fresher value that was
        never actually locked in."""
        gate = RisingEdgeGate(threshold=0.6)
        gate.refresh(0.65, date(2026, 9, 23))
        gate.refresh(0.30, date(2026, 9, 23))  # same day, different reading -- ignored
        reading = gate.last_reading
        self.assertEqual(reading["breadth"], 0.65)


if __name__ == "__main__":
    unittest.main()
