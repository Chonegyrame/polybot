"""Watchlist endpoints — markets building consensus that haven't crossed
the official signal floors yet.

B3: floors are ≥2 traders / ≥$5k aggregate / ≥60% skew (vs 5/$25k/60% for
official signals). Watchlist rows are NOT eligible for paper trading or
backtest — purely a UI surface for "stuff to keep an eye on."
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.api.routes.traders import VALID_CATEGORIES, VALID_MODES
from app.db import crud

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("/active")
async def get_active_watchlist(
    mode: str = Query("absolute"),
    category: str = Query("overall"),
    top_n: int = Query(50, ge=20, le=100),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Watchlist candidates for the user's (mode, category, top_n) lens.

    Reads from `watchlist_signals` (persisted by the 10-min refresh cycle).
    Mutually exclusive with /signals/active — anything here is below the
    official signal floors.
    """
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {VALID_CATEGORIES}")

    rows = await crud.list_watchlist_signals(
        conn, mode=mode, category=category, top_n=top_n,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "mode": r["mode"],
            "category": r["category"],
            "top_n": r["top_n"],
            "condition_id": r["condition_id"],
            "direction": r["direction"],
            "trader_count": r["trader_count"],
            "aggregate_usdc": float(r["aggregate_usdc"]) if r["aggregate_usdc"] is not None else None,
            "net_skew": float(r["net_skew"]) if r["net_skew"] is not None else None,
            "avg_portfolio_fraction": (
                float(r["avg_portfolio_fraction"])
                if r["avg_portfolio_fraction"] is not None else None
            ),
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "last_seen_at":  r["last_seen_at"].isoformat()  if r["last_seen_at"]  else None,
            "market_question": r["market_question"],
            "market_slug": r["market_slug"],
            "market_category": r["market_category"],
        })

    return {
        "mode": mode,
        "category": category,
        "top_n": top_n,
        "count": len(out),
        "watchlist": out,
    }
