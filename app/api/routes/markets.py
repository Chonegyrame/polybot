"""GET /markets/{condition_id} — enriched single-market view for drill-down."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn
from app.db import crud
from app.services.orderbook import compute_book_metrics
from app.services.polymarket import PolymarketClient

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("/{condition_id}")
async def get_market(
    condition_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Single market with event context, all tracked positions, and signal history.

    F23: SQL queries refactored into crud.py helpers (CLAUDE.md rule).
    Behavior unchanged.
    """
    market = await crud.get_market_with_event(conn, condition_id)
    if market is None:
        raise HTTPException(404, f"market {condition_id} not found")

    positions_summary = await crud.get_market_positions_summary(conn, condition_id)
    per_trader = await crud.get_market_per_trader(conn, condition_id)
    signals = await crud.get_market_signal_history(conn, condition_id)

    return {
        "market": market,
        "tracked_positions_by_outcome": positions_summary,
        "tracked_positions_per_trader": per_trader,
        "signal_history": signals,
    }


@router.get("/{condition_id}/live_quote")
async def get_live_quote(
    condition_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Live CLOB best bid + best ask for both YES and NO sides of the market.

    The market modal calls this on open + every 30s to render real prices on
    both sides — without this, the user sees only stale `current_price` from
    the position-refresh job (up to 10 min old) and only on the side smart
    money is trading. Returns nulls per-side if that token's book is empty
    or crossed; the caller renders "—" for any null.
    """
    yes_token, no_token = await crud.get_market_clob_tokens(conn, condition_id)
    if yes_token is None and no_token is None:
        raise HTTPException(404, f"market {condition_id} has no CLOB tokens")

    async with PolymarketClient() as pm:
        yes_book = await pm.get_orderbook(yes_token) if yes_token else None
        no_book = await pm.get_orderbook(no_token) if no_token else None

    yes_m = compute_book_metrics(yes_book, "YES")
    no_m = compute_book_metrics(no_book, "NO")

    def _side(m) -> dict[str, float | int | None]:
        if not m.available:
            return {"bid": None, "ask": None, "mid": None, "spread_bps": None}
        return {
            "bid": m.best_bid,
            "ask": m.best_ask,
            "mid": m.mid,
            "spread_bps": m.spread_bps,
        }

    return {
        "condition_id": condition_id,
        "yes": _side(yes_m),
        "no": _side(no_m),
    }
