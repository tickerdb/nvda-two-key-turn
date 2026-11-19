"""
Run the NVDA Two Key Turn model for today: long or short NVDA from the next open.

    export TICKERDB_API_KEY=tdb_...      # https://tickerdb.com/setup
    python run.py

Schedule it once per trading day after TickerDB publishes the day's snapshot
(knowledge cutoff 04:00 UTC), for example 05:00 UTC, before the US open. Each run:

  1. Reads the 2-year Treasury yield for the last 300 days from /v1/market.
  2. Fetches NVDA's newest daily rows from /v1/history.
  3. Applies the rules given the side in state.json (long or short), prints
     what to do, and saves the new side.

It never places orders, and a day is decided only once.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import model

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"


def main() -> None:
    today = date.today()
    yields = model.fetch_yields((today - timedelta(days=300)).isoformat(), today.isoformat())
    sessions = sorted(yields)
    rows = model.fetch_daily(model.TICKER, (today - timedelta(days=10)).isoformat(), fields=model.TREND_FIELD)
    if not rows or rows[-1]["date"] not in yields:
        raise SystemExit("The newest NVDA day has no market row yet; try again later.")
    day = rows[-1]["date"]
    i = sessions.index(day)

    state = json.loads(STATE.read_text()) if STATE.exists() else {"side": model.FLAT, "last_decided": ""}
    if state["last_decided"] >= day:
        print(f"{day} was already decided. Position: {state['side']} {model.TICKER}")
        return

    ys = [yields[d] for d in sessions]
    trend, rates = model.trend_rising(rows[-1]["slope"]), model.rates_rising(ys, i)
    side, why = model.decide(trend, rates, state["side"])
    STATE.write_text(json.dumps({"side": side, "last_decided": day}, indent=2) + "\n")

    then = ys[i - model.RATES_LOOKBACK] if i >= model.RATES_LOOKBACK else None
    print(f"{model.TICKER} close {day}: ${rows[-1]['close']:,.2f}")
    print(f"  100-day average: {'rising' if trend else 'not rising' if trend is not None else 'unknown'} "
          f"(slope {rows[-1]['slope']})")
    print(f"  2-year yield: {ys[i]}% now, {then}% {model.RATES_LOOKBACK} sessions ago: "
          f"{'rising' if rates else 'not rising' if rates is not None else 'unknown'}")
    if side == state["side"]:
        print(f"Action: none ({why})")
    elif side == model.SHORT:
        print(f"Action: {'sell ' + model.TICKER + ' and ' if state['side'] == model.LONG else ''}short {model.TICKER} "
              f"at the next open ({why})")
    else:
        print(f"Action: {'cover the short and ' if state['side'] == model.SHORT else ''}buy {model.TICKER} "
              f"at the next open ({why})")
    print(f"Position: {side} {model.TICKER}")


if __name__ == "__main__":
    main()
