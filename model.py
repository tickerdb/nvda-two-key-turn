"""
NVDA Two Key Turn: the rules, and the TickerDB data they run on.

The idea: stay in NVIDIA through ordinary pullbacks, and flip to a short only
when its trend has turned down while interest rates are rising, the setting of
its two deep declines since 2018.

Rules, decided at each daily close and traded at the next open:
  1. Trend: NVDA's 100-day average is rising (`trend_ma100_slope_pct` above 0).
  2. Rates: the 2-year Treasury yield (`macro_treasury_yield_2y_pct`) is above
     its level 126 sessions (about six months) earlier.
  3. Hold NVDA unless the trend is not rising AND rates are rising; then sell
     it and short it. Cover and buy NVDA back as soon as either condition
     clears.
  4. 1x long or 1x short, no leverage, no stop loss. A day with a missing
     input changes nothing.

Used by backtest.py (history since 2019) and run.py (today's decision).
TickerDB runs the same rules every day: https://tickerdb.com/model/nvda-two-key-turn
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("TICKERDB_API_BASE", "https://api.tickerdb.com/v1").rstrip("/")
TICKER = "NVDA"
LONG, SHORT, FLAT = "long", "short", "cash"      # FLAT only before the first decision

# ---------------------------------------------------------------- parameters
RATES_LOOKBACK = 126          # sessions: the 2-year yield is compared with its level this many sessions earlier
TREND_FIELD = "trend_ma100_slope_pct"
YIELD_FIELD = "macro_treasury_yield_2y_pct"


# ---------------------------------------------------------------- rules
def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def trend_rising(slope) -> bool | None:
    """Is the 100-day average rising? None when the field is missing."""
    s = _num(slope)
    return None if s is None else s > 0


def rates_rising(yields: list, i: int) -> bool | None:
    """Is the 2-year yield at session i above its level RATES_LOOKBACK sessions earlier?
    yields: one value per session, oldest first. None when either value is missing."""
    if i < RATES_LOOKBACK:
        return None
    now, then = _num(yields[i]), _num(yields[i - RATES_LOOKBACK])
    return None if now is None or then is None else now > then


def decide(trend: bool | None, rates: bool | None, side: str) -> tuple[str, str]:
    """The side to be on from the next open (LONG or SHORT), and why.
    trend: the 100-day average is rising; rates: the 2-year yield is rising; side: LONG, SHORT or FLAT now."""
    if trend is None or rates is None:
        return side, "missing data, no change"
    if not trend and rates:
        return SHORT, "trend down and rates rising"
    if trend:
        return LONG, "trend up" + (", rates rising" if rates else "")
    return LONG, "trend down but rates not rising"


# ---------------------------------------------------------------- data
def api_key() -> str:
    key = os.environ.get("TICKERDB_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set TICKERDB_API_KEY (free key: see https://tickerdb.com/setup).")
    return key


def get(path: str, params: dict | None = None) -> dict:
    url = f"{API}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key()}", "Accept": "application/json",
                                               "User-Agent": "tickerdb-nvda-two-key-turn/1.0 (+https://github.com/tickerdb/nvda-two-key-turn)"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 7:
                time.sleep(int(e.headers.get("Retry-After") or 10))
                continue
            raise SystemExit(f"TickerDB {e.code} on {path}: {body[:500]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == 7:
                raise SystemExit(f"TickerDB network error on {path}: {e}")
            time.sleep(10)
    raise AssertionError("unreachable")


def history(params: dict) -> list[dict]:
    """Every row of one /v1/history request (all pages), oldest first."""
    page = get("/history", params)
    rows: list[dict] = []
    tokens = list(page.get("page_tokens") or [])
    while True:
        rows += page.get("rows", [])
        if page.get("next_page_token"):
            page = get("/history", {**params, "page_token": page["next_page_token"]})
        elif tokens:
            page = get("/history", {**params, "page_token": tokens.pop(0)})
        else:
            break
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_daily(ticker: str, start: str, end: str | None = None, fields: str = "") -> list[dict]:
    """Daily rows of one ticker, oldest first: open and close as traded (a fill price is the price of its
    day), `slope` when asked for, and `basis`: how many of today's shares one share of that day became
    through later splits (1 after the last split). Two /v1/history requests: as traded and split-adjusted."""
    params = {"tickers": ticker, "timeframe": "daily", "ohlcv": "true", "start": start}
    if end:
        params["end"] = end
    adjusted = {r["date"]: _num(r.get("ohlcv_close")) for r in history({**params, "adjustment": "split"})}
    if fields:
        params["fields"] = fields
    rows = []
    for r in history({**params, "adjustment": "raw"}):
        close, adj = _num(r.get("ohlcv_close")), adjusted.get(r["date"])
        if close is None:
            continue
        rows.append({"date": r["date"], "open": _num(r.get("ohlcv_open")), "close": close,
                     "slope": _num(r.get(TREND_FIELD)), "basis": round(close / adj, 4) if adj else None})
    return rows


def fetch_yields(start: str, end: str) -> dict[str, float | None]:
    """{date: 2-year Treasury yield} for every session in the range (GET /v1/market)."""
    out: dict[str, float | None] = {}
    cur = start
    while cur <= end:
        page = get("/market", {"timeframe": "daily", "start": cur, "end": end, "fields": YIELD_FIELD,
                               "order": "asc", "limit": 2000})
        rows = page.get("rows") or []
        for r in rows:
            out[r["date"]] = _num(r.get(YIELD_FIELD))
        if len(rows) < 2000:
            break
        cur = max(out)
    return out
