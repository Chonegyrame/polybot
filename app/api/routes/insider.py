"""Insider wallets — manually curated watchlist beyond the leaderboard top-N.

V1 is CRUD only. The position-refresh loop picks these wallets up via
`_gather_tracked_wallets`, so insider holdings show up alongside top-N pool
positions in the signal detector and in /signals/active enrichment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_conn
from app.db import crud

router = APIRouter(prefix="/insider_wallets", tags=["insider_wallets"])

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
    return {"count": len(rows), "wallets": [_serialise(r) for r in rows]}


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
