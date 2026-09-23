import unittest
from datetime import date
import pandas as pd

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
        res = compute_breadth(["A", "B", "C", "D"], fetch)
        
        self.assertEqual(res.above_ma, ["A", "C"])
        self.assertEqual(res.below_ma, ["B", "D"])
        self.assertAlmostEqual(res.breadth, 0.5)

    def test_insufficient_history_excluded(self):
        data = {
            "SHORT": pd.DataFrame({"close": [10.0] * 20 + [11.0]}),
            "LONG":  pd.DataFrame({"close": [10.0] * 50 + [12.0]}),
        }
        fetch = MockFetchBars(data)
        res = compute_breadth(["SHORT", "LONG"], fetch)
        
        # SHORT should be excluded from both numerator and denominator
        self.assertEqual(res.above_ma, ["LONG"])
        self.assertEqual(res.below_ma, [])
        self.assertAlmostEqual(res.breadth, 1.0)
        
    def test_no_candidates(self):
        fetch = MockFetchBars({})
        res = compute_breadth([], fetch)
        self.assertAlmostEqual(res.breadth, 0.0)


class TestRankByMomentum(unittest.TestCase):
    def test_ranking_order(self):
        data = {
            "X": pd.DataFrame({"close": [100.0, 110.0]}),
            "Y": pd.DataFrame({"close": [100.0, 120.0]}),
            "Z": pd.DataFrame({"close": [100.0, 105.0]}),
        }
        fetch = MockFetchBars(data)
        ranked = rank_by_momentum(["X", "Y", "Z"], fetch, horizon=1)
        self.assertEqual(ranked, ["Y", "X", "Z"])

    def test_excludes_short_history(self):
        data = {
            "SHORT": pd.DataFrame({"close": [100.0]}),
            "LONG":  pd.DataFrame({"close": [100.0, 110.0]}),
        }
        fetch = MockFetchBars(data)
        ranked = rank_by_momentum(["SHORT", "LONG"], fetch, horizon=1)
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
