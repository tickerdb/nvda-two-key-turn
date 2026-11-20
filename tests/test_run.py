"""Offline tests of how run.py starts from the model's current book.   python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import book  # noqa: E402
import model  # noqa: E402
import run  # noqa: E402

SHORT = {"ticker": "NVDA", "side": "short", "position_state": "held", "entry_date": "2026-11-05",
         "entry_price": 196.07, "price": 145.98, "return_pct": 25.347}


class StartFromTheBook(unittest.TestCase):
    def test_a_held_position_is_the_starting_state(self):
        state = run.from_book({"date": "2026-11-19", "holdings": [SHORT]})
        self.assertEqual((state["side"], state["entry_date"], state["entry_price"], state["last_decided"]),
                         ("short", "2026-11-05", 196.07, "2026-11-19"))
        self.assertEqual(run.position(state), "short NVDA since 2026-11-05 at $196.07")

    def test_a_flip_decided_at_the_close_starts_from_the_new_side(self):
        exiting = {**SHORT, "position_state": "exiting"}
        entering = {"ticker": "NVDA", "side": "long", "position_state": "entering", "entry_date": None, "entry_price": None}
        state = run.from_book({"date": "2026-11-19", "holdings": [exiting, entering]})
        self.assertEqual((state["side"], state["entry_date"], state["flip_day"]), ("long", "", "2026-11-19"))
        self.assertEqual(run.position(state), "long NVDA from the next open")
        run.fill_entry(state, [{"date": "2026-11-19", "open": 150.0}, {"date": "2026-11-20", "open": 147.5}])
        self.assertEqual((state["entry_date"], state["entry_price"]), ("2026-11-20", 147.5))

    def test_an_empty_book_or_none_starts_in_cash(self):
        self.assertEqual(run.from_book({"date": "2026-11-19", "holdings": []})["side"], model.FLAT)
        self.assertEqual(run.from_book(None), {"side": model.FLAT, "entry_date": "", "entry_price": None,
                                               "flip_day": "", "last_decided": ""})

    def test_book_lines(self):
        self.assertEqual(book.describe(SHORT), "short NVDA since 2026-11-05 at $196.07 (+25.3% at the close)")
        self.assertEqual(book.describe({**SHORT, "position_state": "exiting"}),
                         "short NVDA since 2026-11-05 at $196.07 (+25.3% at the close), covered at the next open")
        self.assertEqual(book.describe({"ticker": "NVDA", "side": "long", "position_state": "entering"}),
                         "buy NVDA at the next open")

    def test_fetch_falls_back_to_cash_on_an_api_error(self):
        def refused(path):
            raise SystemExit("TickerDB 403")
        self.assertIsNone(book.fetch(refused, "nvda-two-key-turn"))
        got = book.fetch(lambda path: {"date": "2026-11-19", "holdings": [SHORT]}, "nvda-two-key-turn")
        self.assertEqual(got["holdings"][0]["side"], "short")


if __name__ == "__main__":
    unittest.main()
