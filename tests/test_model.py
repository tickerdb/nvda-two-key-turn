"""Offline tests of the rules and the replay on small synthetic rows.   python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backtest  # noqa: E402
import model  # noqa: E402

N, S, C = model.LONG, model.SHORT, model.FLAT
L = model.RATES_LOOKBACK


class Rules(unittest.TestCase):
    def test_trend_needs_a_positive_slope(self):
        self.assertTrue(model.trend_rising(0.01))
        self.assertTrue(model.trend_rising("0.4"))
        self.assertFalse(model.trend_rising(0))
        self.assertFalse(model.trend_rising(-0.3))
        self.assertIsNone(model.trend_rising(None))
        self.assertIsNone(model.trend_rising(float("nan")))

    def test_rates_compare_with_the_lookback_session(self):
        ys = [4.0] * L + [4.1]
        self.assertTrue(model.rates_rising(ys, L))
        self.assertFalse(model.rates_rising([4.0] * (L + 1), L))            # equal is not rising
        self.assertFalse(model.rates_rising([4.0] * L + [3.9], L))
        self.assertIsNone(model.rates_rising(ys, L - 1))                    # not enough history
        self.assertIsNone(model.rates_rising([None] + [4.0] * L, L))        # the earlier value is missing
        self.assertIsNone(model.rates_rising([4.0] * L + [None], L))
        # only the session exactly L back counts, not anything in between
        self.assertFalse(model.rates_rising([4.0] + [9.0] * (L - 1) + [4.0], L))

    def test_short_needs_both_keys(self):
        self.assertEqual(model.decide(False, True, N)[0], S)
        self.assertEqual(model.decide(False, True, C)[0], S)      # also from the start
        self.assertEqual(model.decide(False, False, N)[0], N)     # trend down alone: hold
        self.assertEqual(model.decide(True, True, N)[0], N)       # rates rising alone: hold
        self.assertEqual(model.decide(True, False, N)[0], N)

    def test_returns_when_either_key_clears(self):
        self.assertEqual(model.decide(False, True, S)[0], S)
        self.assertEqual(model.decide(True, True, S)[0], N)
        self.assertEqual(model.decide(False, False, S)[0], N)

    def test_missing_data_changes_nothing(self):
        for holding in (N, S, C):
            self.assertEqual(model.decide(None, True, holding)[0], holding)
            self.assertEqual(model.decide(False, None, holding)[0], holding)


def series(slopes, yields_tail, opens=None, basis=None):
    """L warm-up sessions at a 4.0 yield, then one session per slope."""
    n = L + len(slopes)
    dates = [f"d{i:04d}" for i in range(n)]
    opens = opens or [100.0] * len(slopes)
    basis = basis or [1.0] * len(slopes)
    nvda = [{"date": d, "open": 100.0, "close": 100.0, "slope": 1.0, "basis": basis[0]} for d in dates[:L]]
    nvda += [{"date": d, "open": o, "close": o, "slope": s, "basis": b} for d, o, s, b in zip(dates[L:], opens, slopes, basis)]
    return nvda, dict(zip(dates, [4.0] * L + yields_tail))


class Replay(unittest.TestCase):
    def test_decides_at_the_close_and_fills_at_the_next_open(self):
        # session L: trend up -> buy at L+1's open. session L+2: trend down, rates up -> sell and short at
        # L+3's open. session L+4 (rates no longer rising) is the last row: its order has no next open yet.
        trades = backtest.simulate(*series([1, 1, -1, -1, -1], [4.0, 4.0, 5.0, 5.0, 3.0], [100, 101, 110, 90, 91]))
        self.assertEqual(len(trades), 2)
        t, short = trades
        self.assertEqual((t["side"], t["entry_date"], t["exit_date"]), ("long", f"d{L + 1:04d}", f"d{L + 3:04d}"))
        self.assertEqual((t["entry_price"], t["exit_price"]), (101, 90))
        self.assertEqual(t["reason"], "trend down and rates rising")
        # the short opens at the open the long was sold at, and is still open
        self.assertEqual((short["side"], short["entry_date"], short["entry_price"]), ("short", f"d{L + 3:04d}", 90))
        self.assertIsNone(short["exit_date"])

    def test_a_long_closes_the_short_at_the_same_open(self):
        # short from L+1; session L+2 rates clear -> cover and buy at L+3's open
        trades = backtest.simulate(*series([-1, -1, -1, -1, -1], [5.0, 5.0, 4.0, 4.0, 4.0], [100, 101, 102, 95, 96]))
        short, long = trades
        self.assertEqual((short["side"], short["entry_price"], short["exit_date"], short["exit_price"]),
                         ("short", 101, f"d{L + 3:04d}", 95))
        self.assertEqual(short["reason"], "trend down but rates not rising")
        self.assertEqual((long["side"], long["entry_date"], long["entry_price"], long["exit_date"]),
                         ("long", f"d{L + 3:04d}", 95, None))

    def test_open_trade_is_reported_without_an_exit(self):
        trades = backtest.simulate(*series([1, 1, 1], [4.0, 4.0, 4.0]))
        self.assertEqual(len(trades), 1)
        self.assertIsNone(trades[0]["exit_date"])
        self.assertNotIn("basis", trades[0])

    def test_a_missing_slope_changes_nothing(self):
        trades = backtest.simulate(*series([1, None, -1, -1], [4.0, 5.0, 5.0, 5.0]))
        self.assertEqual(trades[0]["exit_date"], f"d{L + 3:04d}")     # sold on the first complete sell signal
        self.assertEqual(trades[1]["side"], "short")

    def test_entry_price_is_restated_for_a_split_while_held(self):
        # bought at 400 when one share was four of today's; sold after the 4-for-1 at 120
        trades = backtest.simulate(*series([1, 1, -1, -1], [4.0, 4.0, 5.0, 5.0], [400, 400, 100, 120], [4.0, 4.0, 1.0, 1.0]))
        self.assertEqual((trades[0]["entry_price"], trades[0]["exit_price"]), (100.0, 120))

    def test_needs_the_lookback_before_the_first_decision(self):
        nvda, yields = series([1], [4.0])
        with self.assertRaises(SystemExit):
            backtest.simulate(nvda[:L], yields)


if __name__ == "__main__":
    unittest.main()
