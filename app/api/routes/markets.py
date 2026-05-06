"""GET /markets/{condition_id} — enriched single-market view for drill-down."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn
from app.db import crud

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
