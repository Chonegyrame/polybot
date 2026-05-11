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


async def _enrich_signals(
    conn: asyncpg.Connection,
    signals: list[Any],
    *,
    mode: str,
    category: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Enrich raw Signal dataclasses with persisted signal_log fields, exit
    state, lens info, and insider overlap. Shared by /signals/active and
    /signals/new so both endpoints emit identical card-shaped dicts."""
    if not signals:
        return []
    cids = [s.condition_id for s in signals]
    insider_pairs = await crud.insider_holdings_for_markets(conn, cids)
    info_by_key = await crud.get_signal_enrichment(
        conn, mode=mode, category=category, top_n=top_n, condition_ids=cids,
    )
    out: list[dict[str, Any]] = []
    for s in signals:
        d = asdict(s)
        extra = info_by_key.get((s.condition_id, s.direction))
        d["signal_log_id"] = extra["signal_log_id"] if extra else None
        d["first_fired_at"] = (
            extra["first_fired_at"].isoformat()
            if extra and extra["first_fired_at"] is not None else None
        )
        d["last_seen_at"] = (
            extra["last_seen_at"].isoformat()
            if extra and extra["last_seen_at"] is not None else None
        )
        d["peak_trader_count"] = (
            extra["signal_peak_trader_count"]
            if extra and extra.get("signal_peak_trader_count") is not None else None
        )
        d["peak_aggregate_usdc"] = (
            float(extra["signal_peak_aggregate_usdc"])
            if extra and extra.get("signal_peak_aggregate_usdc") is not None else None
        )
        d["signal_entry_spread_bps"] = (
            extra["signal_entry_spread_bps"]
            if extra and extra.get("signal_entry_spread_bps") is not None else None
        )
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
        smart_money_basis = (
            float(extra["first_top_trader_entry_price"])
            if extra and extra.get("first_top_trader_entry_price") is not None else None
        )
        if d["signal_entry_offer"] is not None and smart_money_basis and smart_money_basis > 0:
            d["gap_to_smart_money"] = d["signal_entry_offer"] / smart_money_basis - 1.0
        else:
            d["gap_to_smart_money"] = None
        d["lens_count"] = (
            int(extra["lens_count"]) if extra and extra.get("lens_count") is not None else 1
        )
        d["lens_list"] = list(extra["lens_list"]) if extra and extra.get("lens_list") else []
        d["counterparty_count"] = (
            int(extra["counterparty_count"]) if extra and extra.get("counterparty_count") is not None else 0
        )
        d["counterparty_warning"] = d["counterparty_count"] > 0
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
                "event_type": extra.get("exit_event_type"),
            }
        else:
            d["has_exited"] = False
            d["exit_event"] = None
        d["has_insider"] = (s.condition_id, s.direction) in insider_pairs
        out.append(d)
    return out


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
    enriched = await _enrich_signals(
        conn, signals, mode=mode, category=category, top_n=top_n,
    )
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


def _why_label_for_lost(row: dict[str, Any]) -> tuple[str, str | None]:
    """Returns (label, detail) — best-guess explanation for why a signal
    rolled off the active feed. Order matters: most-specific first.

    Heuristic, not authoritative — labels are derived server-side from
    markets/exit/position state at query time, so they update as the world
    changes (a signal labelled "No longer firing" today may flip to "Market
    resolved YES" once the resolution lands).
    """
    if row.get("resolved_outcome"):
        return (f"Market resolved {row['resolved_outcome']}",
                "settlement complete; redemption only")
    if row.get("market_closed"):
        return ("Market closed", "no longer accepting trades")
    cur = row.get("recent_cur_price")
    if cur is not None and (cur > 0.92 or cur < 0.02):
        return ("Effectively resolved",
                f"price sat at ${cur:.2f}; no tradeable depth left")
    end_date = row.get("end_date")
    if end_date is not None:
        from datetime import datetime, timezone, timedelta
        if end_date < datetime.now(timezone.utc) - timedelta(days=7):
            return ("Effectively resolved",
                    "end date passed >7 days ago; awaiting formal resolution")
    last_exit_type = row.get("last_exit_event_type")
    if last_exit_type == "exit":
        return ("Smart money exited",
                "trader headcount or aggregate USDC dropped >=50%")
    if last_exit_type == "trim":
        return ("Trimmed below floor",
                "trader headcount or aggregate USDC dropped >=25%; signal then fell off")
    return ("No longer firing", "trader/aggregate dropped below detection floors")


@router.get("/lost")
async def get_lost_signals(
    hours: int = Query(72, ge=1, le=168, description="Look-back window in hours; default 3 days"),
    limit: int = Query(200, ge=1, le=500),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Signals that fired within the look-back window but stopped firing on
    every (mode, category, top_n) combo. Powers the News tab Card B.

    The default 72h window also serves as the "auto-purge" mechanism — older
    signals fall out of the result set naturally, so dismissed-but-not-yet-
    purged items will disappear after 3 days even if the user never returns
    to the page. Client-side dismissal lives in localStorage; this endpoint
    makes no backend write.
    """
    rows = await crud.list_lost_signals(conn, hours=hours, limit=limit)

    out: list[dict[str, Any]] = []
    for r in rows:
        why_label, why_detail = _why_label_for_lost(r)
        out.append({
            "signal_log_id": r["signal_log_id"],
            "condition_id": r["condition_id"],
            "direction": r["direction"],
            "market_question": r["market_question"],
            "market_slug": r["market_slug"],
            "market_category": r["market_category"],
            "event_id": r["event_id"],
            "first_fired_at": (
                r["first_fired_at"].isoformat()
                if r["first_fired_at"] is not None else None
            ),
            "last_seen_at": (
                r["last_seen_at"].isoformat()
                if r["last_seen_at"] is not None else None
            ),
            "peak_trader_count": r["peak_trader_count"],
            "peak_aggregate_usdc": r["peak_aggregate_usdc"],
            "smart_money_entry_price": r["smart_money_entry_price"],
            "signal_entry_offer": r["signal_entry_offer"],
            "recent_cur_price": r["recent_cur_price"],
            "market_closed": r["market_closed"],
            "resolved_outcome": r["resolved_outcome"],
            "end_date": (
                r["end_date"].isoformat()
                if r["end_date"] is not None else None
            ),
            "last_exit_event_type": r["last_exit_event_type"],
            "last_exit_at": (
                r["last_exit_at"].isoformat()
                if r["last_exit_at"] is not None else None
            ),
            "open_paper_trade_id": r["open_paper_trade_id"],
            "why_label": why_label,
            "why_detail": why_detail,
        })

    return {
        "window_hours": hours,
        "count": len(out),
        "lost_signals": out,
    }


@router.get("/{signal_log_id}/contributors")
async def get_signal_contributors(
    signal_log_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Pass 5: contributors + counterparty panel for one signal.

    Backs the UI-SPEC.md Section 2 expandable panel that shows WHO is
    contributing to the signal and which top-N traders are on the
    opposite side. Cluster-aware: a 4-wallet sybil cluster appears as
    ONE row with `cluster_size=4`. `is_hedged=True` flags entities
    holding both sides of the market. Counterparty list is filtered by
    `is_counterparty` (>= $5k opposite-side USDC + >= 75% concentration).

    Returns 404 when the signal_log row doesn't exist.
    """
    result = await crud.get_signal_contributors_and_counterparty(
        conn, signal_log_id,
    )
    if result is None:
        raise HTTPException(404, f"signal_log_id={signal_log_id} not found")
    return result


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
