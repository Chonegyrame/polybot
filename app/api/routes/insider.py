"""Insider wallets — manually curated watchlist beyond the leaderboard top-N.

V1 is CRUD only. The position-refresh loop picks these wallets up via
`_gather_tracked_wallets`, so insider holdings show up alongside top-N pool
positions in the signal detector and in /signals/active enrichment.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_conn
from app.db import crud

router = APIRouter(prefix="/insider_wallets", tags=["insider_wallets"])


def _num(v: Any) -> float | None:
    """Coerce asyncpg's NUMERIC (Decimal) → float for JSON, preserving None."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else v


def _serialise_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": row["condition_id"],
        "asset": row["asset"],
        "outcome": row["outcome"],
        "size": _num(row["size"]),
        "avg_price": _num(row["avg_price"]),
        "cur_price": _num(row["cur_price"]),
        "initial_value": _num(row["initial_value"]),
        "current_value": _num(row["current_value"]),
        "cash_pnl": _num(row["cash_pnl"]),
        "realized_pnl": _num(row["realized_pnl"]),
        "percent_pnl": _num(row["percent_pnl"]),
        "first_seen_at": _iso(row["first_seen_at"]),
        "last_updated_at": _iso(row["last_updated_at"]),
        "question": row.get("question"),
        "slug": row.get("slug"),
    }


def _serialise_action(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "proxy_wallet": row["proxy_wallet"],
        "label": row.get("label"),
        "condition_id": row["condition_id"],
        "asset": row["asset"],
        "outcome": row["outcome"],
        "action_type": row["action_type"],
        "size_before": _num(row["size_before"]),
        "size_after": _num(row["size_after"]),
        "size_delta": _num(row["size_delta"]),
        "cur_price": _num(row["cur_price"]),
        "value_delta_usd": _num(row["value_delta_usd"]),
        "occurred_at": _iso(row["occurred_at"]),
        "question": row.get("question"),
        "slug": row.get("slug"),
    }

# Polygon addresses are 42-char hex (0x + 40). Reject anything obviously wrong
# at the boundary so we don't pollute the table with typos.
_WALLET_LEN = 42


class CreateInsiderWalletRequest(BaseModel):
    proxy_wallet: str = Field(..., min_length=_WALLET_LEN, max_length=_WALLET_LEN)
    label: str | None = None
    notes: str | None = None


class UpdateInsiderWalletRequest(BaseModel):
    """PATCH body. Both fields are passed through directly -- send NULL to
    clear, send a string to set. The UI's edit-in-place form sends both
    every time, pre-filled with the current values."""
    label: str | None = None
    notes: str | None = None


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "proxy_wallet": row["proxy_wallet"],
        "label": row["label"],
        "notes": row["notes"],
        "added_at": row["added_at"].isoformat() if isinstance(row["added_at"], datetime) else row["added_at"],
        "last_seen_at": (
            row["last_seen_at"].isoformat()
            if isinstance(row["last_seen_at"], datetime) else row["last_seen_at"]
        ),
    }


@router.get("")
async def list_wallets(
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    rows = await crud.list_insider_wallets(conn)
    out: list[dict[str, Any]] = []
    for r in rows:
        latest = await crud.list_recent_insider_actions_for_wallet(
            conn, r["proxy_wallet"], limit=1,
        )
        item = _serialise(r)
        item["latest_action"] = _serialise_action(latest[0]) if latest else None
        out.append(item)
    return {"count": len(out), "wallets": out}


@router.get("/actions/recent")
async def list_recent_actions(
    limit: int = 50,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Activity feed across all insider wallets (NEW/TRIM/SELL events).
    Joined with wallet label and market question for display."""
    limit = max(1, min(limit, 200))
    rows = await crud.list_recent_insider_actions(conn, limit=limit)
    return {"count": len(rows), "actions": [_serialise_action(r) for r in rows]}


@router.get("/actions/unseen_count")
async def unseen_actions_count(
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Drives the sidebar badge — polled every 30s by the UI."""
    n = await crud.count_unseen_insider_actions(conn)
    return {"count": n}


@router.post("/actions/mark_seen")
async def mark_actions_seen(
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Reset the badge — called when the UI opens the Insider wallets page."""
    n = await crud.mark_insider_actions_seen(conn)
    return {"marked": n}


@router.get("/{proxy_wallet}/positions")
async def list_wallet_positions(
    proxy_wallet: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Open positions for one insider wallet — drives the expandable row.
    404 if the wallet isn't in the insider list (avoids leaking position
    queries for arbitrary addresses through this endpoint)."""
    proxy_wallet = proxy_wallet.lower()
    wallet = await crud.get_insider_wallet(conn, proxy_wallet)
    if wallet is None:
        raise HTTPException(404, f"insider wallet {proxy_wallet} not found")
    rows = await crud.list_positions_for_wallet(conn, proxy_wallet)
    return {
        "proxy_wallet": proxy_wallet,
        "label": wallet.get("label"),
        "count": len(rows),
        "positions": [_serialise_position(r) for r in rows],
    }


@router.post("")
async def add_wallet(
    req: CreateInsiderWalletRequest,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if not req.proxy_wallet.startswith("0x"):
        raise HTTPException(400, "proxy_wallet must start with 0x")
    row = await crud.upsert_insider_wallet(
        conn,
        proxy_wallet=req.proxy_wallet.lower(),
        label=req.label,
        notes=req.notes,
    )
    return _serialise(row)


@router.patch("/{proxy_wallet}")
async def update_wallet(
    proxy_wallet: str,
    req: UpdateInsiderWalletRequest,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    row = await crud.update_insider_wallet(
        conn, proxy_wallet.lower(), label=req.label, notes=req.notes,
    )
    if row is None:
        raise HTTPException(404, f"insider wallet {proxy_wallet} not found")
    return _serialise(row)


@router.delete("/{proxy_wallet}")
async def remove_wallet(
    proxy_wallet: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    deleted = await crud.delete_insider_wallet(conn, proxy_wallet.lower())
    if not deleted:
        raise HTTPException(404, f"insider wallet {proxy_wallet} not found")
    return {"deleted": True, "proxy_wallet": proxy_wallet.lower()}
