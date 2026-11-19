"""
Replay NVDA Two Key Turn's rules on TickerDB's daily data, 2019 to the end of the backtest: the trades
they make.

    export TICKERDB_API_KEY=tdb_...      # https://tickerdb.com/setup
    python backtest.py                   # fetch the data, then replay
    python backtest.py --offline         # replay again on the cached data/nvda_daily.csv and data/yield_2y.csv

Daily history from 2019 needs a TickerDB plan that includes it (Pro: 2019 on). The fetch is NVDA's daily
rows from /v1/history and the 2-year Treasury yield from /v1/market, cached in data/.

Point in time: a decision at a close uses only data up to that close, and the order fills at the next
session's open. Prices are as traded on the day (NVDA split 4-for-1 in 2021 and 10-for-1 in 2024, so
the 2019 fills are near $160). A trade that spans a split states its entry price in the shares held at
the exit, as the record does: bought at $570.00 before the 4-for-1 reads $142.50.

The first decision is 2019-07-03: the first session with a 2-year yield 126 sessions earlier (TickerDB's
market history starts on 2019-01-02). The replay starts in cash and is long or short from the next open on.

Writes results/replay-trades.csv (each long and each short: entry and exit with their fill prices) and
checks it against the published record (results/record.json, written by readme.py): the closed trades
must be exactly the same. The performance figures are the record's, valued at every daily close with
dividends (readme.py); this replay values nothing.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import model

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
FETCH_FROM = "2019-01-01"


def end_date() -> str | None:
    """The published backtest's last day (results/record.json): the replay stops there."""
    record = RESULTS / "record.json"
    if not record.exists():
        return None
    return json.loads(record.read_text(encoding="utf-8"))["backtest"]["end"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"No cached data at {path}. Run without --offline first.")
    with path.open() as f:
        return [{k: (v if k == "date" else (None if v == "" else float(v))) for k, v in row.items()} for row in csv.DictReader(f)]


def _write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in fields})


def load(offline: bool) -> tuple[list[dict], dict]:
    """(NVDA rows {date, open, close, slope, basis}, {date: 2-year yield}), up to the published backtest's end."""
    files = DATA / "nvda_daily.csv", DATA / "yield_2y.csv"
    end = end_date()
    if not offline:
        _write(files[0], model.fetch_daily(model.TICKER, FETCH_FROM, end, fields=model.TREND_FIELD), ["date", "open", "close", "slope", "basis"])
        _write(files[1], [{"date": d, "yield_2y": y} for d, y in sorted(model.fetch_yields(FETCH_FROM, end or date.today().isoformat()).items())],
               ["date", "yield_2y"])
    nvda, yld = (_read(p) for p in files)
    return [r for r in nvda if end is None or r["date"] <= end], {r["date"]: r["yield_2y"] for r in yld}


def simulate(nvda: list[dict], yields: dict) -> list[dict]:
    """Trades: each long and each short {side, entry_date, entry_price, exit_date, exit_price, reason}; the
    one still open at the last row has no exit. A flip closes one side and opens the other at the same
    open. A session is a day NVDA traded."""
    ys = [yields.get(r["date"]) for r in nvda]
    first = next((i for i in range(len(nvda)) if model.rates_rising(ys, i) is not None), None)
    if first is None:
        raise SystemExit(f"Not enough history: the rates rule needs {model.RATES_LOOKBACK} sessions before the first decision.")
    side = model.FLAT
    trades: list[dict] = []
    cur: dict | None = None
    for i in range(first, len(nvda) - 1):
        target, why = model.decide(model.trend_rising(nvda[i]["slope"]), model.rates_rising(ys, i), side)
        if target == side:
            continue
        fill_date, fill_price = nvda[i + 1]["date"], nvda[i + 1]["open"]
        if cur:
            was, now = cur.pop("basis"), nvda[i + 1].get("basis")
            if was and now and was != now:                       # a split while held: entry in the exit's shares
                cur["entry_price"] = round(cur["entry_price"] * now / was, 6)
            cur.update(exit_date=fill_date, exit_price=fill_price, reason=why)
            trades.append(cur)
        cur = {"side": target, "entry_date": fill_date, "entry_price": fill_price, "basis": nvda[i + 1].get("basis")}
        side = target
    if cur:
        cur.pop("basis")
        trades.append({**cur, "exit_date": None, "exit_price": None, "reason": "open"})
    return trades


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--offline", action="store_true", help="use data/*.csv instead of fetching")
    args = ap.parse_args()

    nvda, yields = load(args.offline)
    trades = simulate(nvda, yields)
    _write(RESULTS / "replay-trades.csv", trades, ["side", "entry_date", "entry_price", "exit_date", "exit_price", "reason"])
    closed = [t for t in trades if t["exit_date"]]
    print(f"Replayed {nvda[0]['date']} to {nvda[-1]['date']}: {len(closed)} closed trades in {model.TICKER}, "
          f"now: {trades[-1]['side'] if len(closed) < len(trades) else model.FLAT}.")
    print("Wrote results/replay-trades.csv.")
    import readme
    if readme.RECORD.exists():
        problems = readme.replay_differences(readme.load())
        if problems:
            print("MISMATCH:", *problems)
            raise SystemExit(1)
        print("The replay makes exactly the published record's trades.")


if __name__ == "__main__":
    main()
