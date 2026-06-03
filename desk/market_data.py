"""Daily price history for the screener — free, no API key.

Source: Yahoo Finance's chart endpoint (query1.finance.yahoo.com). It's an
unofficial endpoint but stable, keyless, JSON, covers ~every US ticker, and has
no hard daily cap — the right fit for scanning a watchlist for EMA crosses
(Finnhub's free tier gives only a live quote, not the history EMAs need).

Returns ~2y of daily closes so a 200-period EMA is well-seeded. Results are
cached per ticker for a few hours so re-scans the same day don't refetch.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import httpx

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_TTL_SECONDS = 6 * 3600.0
_cache: dict[str, tuple[float, tuple[list[float], list[str]]]] = {}


class HistoryError(Exception):
    pass


def fetch_daily_closes(ticker: str, range_: str = "2y") -> tuple[list[float], list[str]]:
    """Return (closes, iso_dates) of daily bars, oldest→newest. Raises HistoryError."""
    sym = ticker.strip().upper()
    now = time.monotonic()
    cached = _cache.get(sym)
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]

    try:
        with httpx.Client(timeout=12.0, headers={"User-Agent": _UA}) as client:
            r = client.get(_CHART.format(sym=sym), params={"range": range_, "interval": "1d"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # network / HTTP / JSON
        raise HistoryError(f"fetch failed for {sym}: {e}") from e

    chart = (data or {}).get("chart") or {}
    if chart.get("error"):
        raise HistoryError(f"{sym}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise HistoryError(f"{sym}: no data")
    res = results[0]
    timestamps = res.get("timestamp") or []
    quote = (res.get("indicators") or {}).get("quote") or [{}]
    raw_closes = quote[0].get("close") or []

    closes: list[float] = []
    dates: list[str] = []
    for ts, c in zip(timestamps, raw_closes):
        if c is None:
            continue  # Yahoo emits nulls for holidays/halts
        closes.append(float(c))
        dates.append(datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat())

    if not closes:
        raise HistoryError(f"{sym}: empty series")

    _cache[sym] = (now, (closes, dates))
    return closes, dates
