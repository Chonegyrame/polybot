"""Live equity quotes via Finnhub (free tier ~60 calls/min).

Configured with FINNHUB_API_KEY in .env. With no key, is_enabled() is False and
get_quote() returns None so the UI falls back to the user's stored prices —
nothing breaks. Futures (ES/NQ/CL/GC) are NOT covered by the free tier; that's
fine, the futures journal is manual entry. This is only for stock notes/alerts.

A small in-process TTL cache (15s) keeps repeated polls under the rate limit.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx

_API = "https://finnhub.io/api/v1/quote"
_TTL_SECONDS = 15.0
_cache: dict[str, tuple[float, Optional[dict]]] = {}


def _key() -> str:
    return os.environ.get("FINNHUB_API_KEY", "").strip()


def is_enabled() -> bool:
    return bool(_key())


async def get_quote(symbol: str) -> Optional[dict]:
    """Return {symbol, last, change, pct, high, low, open, prevClose} or None.

    None means "no live price available" (no key, network error, or unknown
    symbol). Callers should fall back to stored data.
    """
    key = _key()
    if not key:
        return None

    now = time.monotonic()
    cached = _cache.get(symbol)
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]

    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(_API, params={"symbol": symbol, "token": key})
            r.raise_for_status()
            d = r.json()
        # Finnhub: c=current, d=change, dp=pct, h/l/o=high/low/open, pc=prevClose.
        # A bogus symbol returns all zeros — treat that as "no data".
        if d and d.get("c"):
            result = {
                "symbol": symbol,
                "last": d.get("c"),
                "change": d.get("d"),
                "pct": d.get("dp"),
                "high": d.get("h"),
                "low": d.get("l"),
                "open": d.get("o"),
                "prevClose": d.get("pc"),
            }
    except Exception:
        result = None  # degrade silently; UI uses stored prices

    _cache[symbol] = (now, result)
    return result
