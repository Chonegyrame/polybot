"""Signal endpoints — active feed + new-since-timestamp badge."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.api.routes.traders import VALID_CATEGORIES, VALID_MODES
from app.db import crud
from app.services.signal_detector import detect_signals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/active")
async def get_active_signals(
    mode: str = Query("absolute"),
    category: str = Query("overall"),
    top_n: int = Query(50, ge=20, le=100),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Currently-firing consensus signals for the user's (mode, category, top_n).

    Computed live (not from signal_log) so the UI sees the latest state.
    The signal_log is for historical / backtest purposes, not display.

    Each signal is enriched with `liquidity_tier` (and `liquidity_at_signal_usdc`
    when available) from signal_log, since orderbook depth was captured at
    first-fire and isn't recomputed live. Fresh signals never previously
    fired carry liquidity_tier=None — the UI should render that as a hint
    that depth hasn't been measured yet.
    """
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {VALID_CATEGORIES}")

    signals = await detect_signals(
        conn,
        mode=mode,             # type: ignore[arg-type]
        category=category,     # type: ignore[arg-type]
        top_n=top_n,
    )

    # Enrich with liquidity_tier from signal_log + exit info from signal_exits
    # (single bulk query, LEFT JOIN signal_exits so non-exited signals come
    # back with NULLs). B12 also fetches insider holdings to flag any signal
    # whose contributing pool overlaps the manually curated insider list.
    enriched: list[dict[str, Any]] = []
    if signals:
        cids = [s.condition_id for s in signals]
        insider_pairs = await crud.insider_holdings_for_markets(conn, cids)
        # F23: extracted to crud.get_signal_enrichment (CLAUDE.md rule)
        info_by_key = await crud.get_signal_enrichment(
            conn, mode=mode, category=category, top_n=top_n, condition_ids=cids,
        )
        for s in signals:
            d = asdict(s)
            extra = info_by_key.get((s.condition_id, s.direction))
            d["liquidity_tier"] = extra["liquidity_tier"] if extra else None
            d["liquidity_at_signal_usdc"] = (
                float(extra["liquidity_at_signal_usdc"])
                if extra and extra["liquidity_at_signal_usdc"] is not None else None
            )
            d["signal_entry_offer"] = (
                float(extra["signal_entry_offer"])
                if extra and extra["signal_entry_offer"] is not None else None
            )
            d["signal_entry_source"] = extra["signal_entry_source"] if extra else None
            # R4+R7 (Pass 3): counterparty_count is the new int. Boolean
            # warning is derived for back-compat (count > 0 -> True).
            d["counterparty_count"] = (
                int(extra["counterparty_count"]) if extra and extra.get("counterparty_count") is not None else 0
            )
            d["counterparty_warning"] = d["counterparty_count"] > 0
            # B1: exit-event enrichment. `has_exited` is the simple bool the
            # UI uses for the strikethrough/badge. `exit_event` carries the
            # detail dict for tooltips and side-by-side strategy compare.
            if extra and extra["exit_id"] is not None:
                d["has_exited"] = True
                d["exit_event"] = {
                    "exited_at": (
                        extra["exited_at"].isoformat()
                        if extra["exited_at"] is not None else None
                    ),
                    "drop_reason": extra["exit_drop_reason"],
                    "exit_bid_price": (
                        float(extra["exit_bid_price"])
                        if extra["exit_bid_price"] is not None else None
                    ),
                    "exit_trader_count": extra["exit_trader_count"],
                    "peak_trader_count": extra["peak_trader_count"],
                    "exit_aggregate_usdc": (
                        float(extra["exit_aggregate_usdc"])
                        if extra["exit_aggregate_usdc"] is not None else None
                    ),
                    "peak_aggregate_usdc": (
                        float(extra["peak_aggregate_usdc"])
                        if extra["peak_aggregate_usdc"] is not None else None
                    ),
                }
            else:
                d["has_exited"] = False
                d["exit_event"] = None
            # B12: insider overlap — True if any insider wallet currently holds
            # this side of this market in `positions`.
            d["has_insider"] = (s.condition_id, s.direction) in insider_pairs
            enriched.append(d)

    return {
        "mode": mode,
        "category": category,
        "top_n": top_n,
        "count": len(signals),
        "signals": enriched,
    }


@router.get("/exits/recent")
async def get_recent_exits(
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    limit: int = Query(100, ge=1, le=500),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Recent smart-money exit events for the alerts feed.

    Returns one row per exit, joined with the original signal_log row + market
    metadata so the UI can render an alert card without follow-up queries.
    """
    rows = await crud.list_recent_signal_exits(conn, hours=hours, limit=limit)

    # Coerce numerics (asyncpg returns Decimal for NUMERIC) so JSON encoder is happy.
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "exit_id": r["exit_id"],
            "signal_log_id": r["signal_log_id"],
            "exited_at": r["exited_at"].isoformat() if r["exited_at"] is not None else None,
            "exit_trader_count": r["exit_trader_count"],
            "peak_trader_count": r["peak_trader_count"],
            "exit_aggregate_usdc": (
                float(r["exit_aggregate_usdc"])
                if r["exit_aggregate_usdc"] is not None else None
            ),
            "peak_aggregate_usdc": (
                float(r["peak_aggregate_usdc"])
                if r["peak_aggregate_usdc"] is not None else None
            ),
            "drop_reason": r["drop_reason"],
            "exit_bid_price": (
                float(r["exit_bid_price"])
                if r["exit_bid_price"] is not None else None
            ),
            "mode": r["mode"],
            "category": r["category"],
            "top_n": r["top_n"],
            "condition_id": r["condition_id"],
            "direction": r["direction"],
            "first_fired_at": (
                r["first_fired_at"].isoformat()
                if r["first_fired_at"] is not None else None
            ),
            "market_question": r["market_question"],
            "market_slug": r["market_slug"],
        })

    return {
        "window_hours": hours,
        "count": len(out),
        "exits": out,
    }


@router.get("/new")
async def get_new_signals(
    since: datetime = Query(..., description="ISO 8601 timestamp; UI passes its localStorage.lastReadSignalsAt"),
    mode: str = Query("absolute"),
    category: str = Query("overall"),
    top_n: int = Query(50, ge=20, le=100),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Count signals first fired after `since` for this UI selection.

    Drives the "X new signals since" header badge. UI polls this on the
    same 10-min cadence as the rest of the dashboard.
    """
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {VALID_CATEGORIES}")

    n = await crud.count_new_signals_since(
        conn, mode=mode, category=category, top_n=top_n, since=since
    )
    return {
        "mode": mode, "category": category, "top_n": top_n,
        "since": since.isoformat(), "count": n,
    }
