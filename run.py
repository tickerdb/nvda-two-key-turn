"""
Run the NVDA Two Key Turn model for today: long or short NVDA from the next open.

    export TICKERDB_API_KEY=tdb_...      # https://tickerdb.com/setup
    python run.py

Schedule it once per trading day after TickerDB publishes the day's snapshot
(knowledge cutoff 04:00 UTC), for example 05:00 UTC, before the US open. Each run:

  1. The first time (no state.json): reads what the model holds now, its newest
     book (/v1/models/nvda-two-key-turn/holdings, 1 credit), and starts from it.
  2. Reads the 2-year Treasury yield for the last 300 days from /v1/market.
  3. Fetches NVDA's newest daily rows from /v1/history.
  4. Applies the rules given the side in state.json (long or short), prints
     what to do and the position (side, entry date and price), and saves it.

It never places orders, and a day is decided only once.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import book
import model

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
SLUG = "nvda-two-key-turn"


def from_book(b: dict | None) -> dict:
    """The state to start from: the model's position once its book's next-open orders are filled."""
    if b is None:
        return {"side": model.FLAT, "entry_date": "", "entry_price": None, "flip_day": "", "last_decided": ""}
    held = [h for h in book.after_next_open(b) if h.get("ticker") == model.TICKER]
    if not held:
        return {"side": model.FLAT, "entry_date": "", "entry_price": None, "flip_day": "", "last_decided": b["date"]}
    h = held[0]
    entering = h.get("position_state") == "entering"
    return {"side": h["side"], "entry_date": "" if entering else h.get("entry_date") or "",
            "entry_price": None if entering else h.get("entry_price"), "flip_day": b["date"] if entering else "",
            "last_decided": b["date"]}


def fill_entry(state: dict, rows: list[dict]) -> None:
    """A side taken at a decided close enters at the next session's open: record it once that session exists."""
    if state["side"] == model.FLAT or state.get("entry_date") or not state.get("flip_day"):
        return
    after = [r for r in rows if r["date"] > state["flip_day"] and r.get("open") is not None]
    if after:
        state["entry_date"], state["entry_price"] = after[0]["date"], after[0]["open"]


def position(state: dict) -> str:
    if state["side"] == model.FLAT:
        return "cash"
    text = f"{state['side']} {model.TICKER}"
    if state.get("entry_date"):
        return text + f" since {state['entry_date']}" + (
            f" at ${float(state['entry_price']):,.2f}" if state.get("entry_price") is not None else "")
    return text + " from the next open"


def main() -> None:
    if STATE.exists():
        state = {"entry_date": "", "entry_price": None, "flip_day": "", **json.loads(STATE.read_text())}
    else:
        b = book.fetch(model.get, SLUG)
        if b:
            book.show(b)
        state = from_book(b)
    today = date.today()
    yields = model.fetch_yields((today - timedelta(days=300)).isoformat(), today.isoformat())
    sessions = sorted(yields)
    rows = model.fetch_daily(model.TICKER, (today - timedelta(days=10)).isoformat(), fields=model.TREND_FIELD)
    if not rows or rows[-1]["date"] not in yields:
        raise SystemExit("The newest NVDA day has no market row yet; try again later.")
    day = rows[-1]["date"]
    i = sessions.index(day)
    fill_entry(state, rows)

    if state["last_decided"] >= day:
        STATE.write_text(json.dumps(state, indent=2) + "\n")
        print(f"{day} was already decided. Position: {position(state)} (NVDA closed at ${rows[-1]['close']:,.2f})")
        return

    ys = [yields[d] for d in sessions]
    trend, rates = model.trend_rising(rows[-1]["slope"]), model.rates_rising(ys, i)
    before = state["side"]
    side, why = model.decide(trend, rates, before)
    if side != before:
        state.update({"side": side, "entry_date": "", "entry_price": None, "flip_day": day})
    state["last_decided"] = day
    STATE.write_text(json.dumps(state, indent=2) + "\n")

    then = ys[i - model.RATES_LOOKBACK] if i >= model.RATES_LOOKBACK else None
    print(f"{model.TICKER} close {day}: ${rows[-1]['close']:,.2f}")
    print(f"  100-day average: {'rising' if trend else 'not rising' if trend is not None else 'unknown'} "
          f"(slope {rows[-1]['slope']})")
    print(f"  2-year yield: {ys[i]}% now, {then}% {model.RATES_LOOKBACK} sessions ago: "
          f"{'rising' if rates else 'not rising' if rates is not None else 'unknown'}")
    if side == before:
        print(f"Action: none ({why})")
    elif side == model.SHORT:
        print(f"Action: {'sell ' + model.TICKER + ' and ' if before == model.LONG else ''}short {model.TICKER} "
              f"at the next open ({why})")
    else:
        print(f"Action: {'cover the short and ' if before == model.SHORT else ''}buy {model.TICKER} "
              f"at the next open ({why})")
    print(f"Position: {position(state)}")


if __name__ == "__main__":
    main()
