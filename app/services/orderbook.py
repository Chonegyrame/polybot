"""CLOB orderbook metrics — pure functions on top of the raw `/book` payload.

We snapshot a market's orderbook the moment a signal first fires. From the
snapshot we extract:
  - signal_entry_offer  : the ask we'd pay to buy the consensus side now
  - signal_entry_mid    : (bid + ask) / 2
  - spread_bps          : (ask - bid) / mid * 10000
  - liquidity_at_signal : USDC depth within ±5c of mid (both sides)
  - liquidity_tier      : 'thin' (<$5k) | 'medium' ($5k-$25k) | 'deep' (>$25k)

This replaces `first_top_trader_entry_price` (the smart-money cost basis,
which is unreachable since the price has already moved by the time we detect
the signal) as the canonical price input to backtest P&L.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger(__name__)

LIQUIDITY_WINDOW = 0.05    # ±5 cents around mid
THIN_THRESHOLD = 5_000.0   # USDC notional within window
DEEP_THRESHOLD = 25_000.0


@dataclass(frozen=True)
class BookMetrics:
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread_bps: int | None
    entry_offer: float | None         # for our chosen direction (caller passes side)
    liquidity_5c_usdc: float | None
    liquidity_tier: Literal["thin", "medium", "deep", "unknown"]
    bids_top20: list[list[float]]     # [[price, size], ...]
    asks_top20: list[list[float]]
    raw_response_hash: str
    available: bool                   # False if the book had no levels


def _parse_levels(rows: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            p = float(r.get("price"))
            s = float(r.get("size"))
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            out.append((p, s))
    return out


def compute_book_metrics(book: dict[str, Any] | None, direction: str) -> BookMetrics:
    """Reduce a raw orderbook payload to the metrics we persist.

    `direction` ∈ {"YES","NO"}. The CLOB returns the book for the *specific
    outcome token* we queried, so the asks here are always the ask side for
    that outcome; we cross to buy the chosen direction at `best_ask`.
    """
    raw = json.dumps(book or {}, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(raw.encode()).hexdigest()
    empty = BookMetrics(
        best_bid=None, best_ask=None, mid=None, spread_bps=None,
        entry_offer=None, liquidity_5c_usdc=None, liquidity_tier="unknown",
        bids_top20=[], asks_top20=[], raw_response_hash=payload_hash, available=False,
    )
    if not book:
        return empty

    bids = sorted(_parse_levels(book.get("bids")), key=lambda x: x[0], reverse=True)
    asks = sorted(_parse_levels(book.get("asks")), key=lambda x: x[0])
    if not bids or not asks:
        return empty

    best_bid, best_ask = bids[0][0], asks[0][0]

    # R6 (Pass 3): guard against crossed/locked books. Polymarket occasionally
    # returns best_bid >= best_ask during fast moves or on illiquid markets.
    # Without this guard we'd persist a bogus mid (could exceed 1.0), a
    # NEGATIVE spread_bps, and a wrong-window liquidity figure -- all of
    # which feed downstream into backtest math as "real" entry data.
    if best_bid >= best_ask:
        log.warning(
            "R6: crossed book detected (bid=%.4f >= ask=%.4f) -- marking unavailable",
            best_bid, best_ask,
        )
        return empty

    mid = (best_bid + best_ask) / 2
    spread_bps = int(((best_ask - best_bid) / mid) * 10_000) if mid > 0 else None

    # USDC notional within ±5c of mid, on whichever side
    lo, hi = mid - LIQUIDITY_WINDOW, mid + LIQUIDITY_WINDOW
    liq_usdc = sum(p * s for p, s in bids if p >= lo) + sum(p * s for p, s in asks if p <= hi)

    if liq_usdc < THIN_THRESHOLD:
        tier: Literal["thin", "medium", "deep", "unknown"] = "thin"
    elif liq_usdc < DEEP_THRESHOLD:
        tier = "medium"
    else:
        tier = "deep"

    return BookMetrics(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_bps=spread_bps,
        entry_offer=best_ask,  # we cross the spread to buy the consensus side
        liquidity_5c_usdc=liq_usdc,
        liquidity_tier=tier,
        bids_top20=[[p, s] for p, s in bids[:20]],
        asks_top20=[[p, s] for p, s in asks[:20]],
        raw_response_hash=payload_hash,
        available=True,
    )
