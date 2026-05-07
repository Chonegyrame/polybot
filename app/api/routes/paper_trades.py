"""Paper trades — discretionary "fake money" entries on signals.

Same execution model as the backtest engine: book snapshot at click time,
square-root slippage on entry, per-category taker fee. The point is to let
the user evaluate the system in real time without putting actual capital
at risk. Once a market resolves, paper trades on it should be auto-closed
at the resolution outcome — that's a separate scheduler job (TODO).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel  # FastAPI v0.136 supports plain dataclasses too,
# but request bodies still need a model class — Pydantic is the path of least
# friction here. Falls back to dataclasses if pydantic isn't importable.

from app.api.deps import get_conn
from app.db import crud
from app.services.orderbook import compute_book_metrics
from app.services.paper_trade_close import compute_realized_pnl, estimate_open_costs
from app.services.polymarket import PolymarketClient

router = APIRouter(prefix="/paper_trades", tags=["paper_trades"])

# F11: status whitelist must include all values written by the codebase.
# Migration 005 added 'closed_exit' (smart-money-exit auto-close path);
# the route's whitelist used to omit it, so /paper_trades?status=closed_exit
# returned 400 even though such rows existed in the DB.
VALID_PAPER_TRADE_STATUSES = (
    "open", "closed_resolved", "closed_manual", "closed_exit",
)


class OpenPaperTradeRequest(BaseModel):
    condition_id: str
    direction: str  # "YES" | "NO"
    size_usdc: float
    signal_log_id: int | None = None
    notes: str | None = None


# R10 (Pass 3): _estimate_costs moved to app.services.paper_trade_close
# (estimate_open_costs) so manual + auto-close paths use one shared
# implementation. Old version used wrong flat-percentage fee model with
# placeholder rates; new version uses the official Polymarket curve.
def _estimate_costs(
    entry_price: float, size_usdc: float, category: str | None,
    liquidity_5c: float | None,
) -> tuple[float, float, float]:
    return estimate_open_costs(
        entry_price=entry_price, size_usdc=size_usdc,
        category=category, liquidity_5c_usdc=liquidity_5c,
    )


@router.post("")
async def open_paper_trade(
    req: OpenPaperTradeRequest,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Open a paper trade at the current CLOB book ask."""
    if req.direction not in ("YES", "NO"):
        raise HTTPException(400, "direction must be YES or NO")
    if req.size_usdc <= 0:
        raise HTTPException(400, "size_usdc must be positive")

    # Look up market + tokens + category
    # F23: replaced inline SQL with crud helper (CLAUDE.md rule).
    mkt = await crud.get_market_tokens_and_category(conn, req.condition_id)
    if mkt is None:
        raise HTTPException(404, f"market {req.condition_id} not found")
    token_id = mkt["clob_token_yes"] if req.direction == "YES" else mkt["clob_token_no"]
    if not token_id:
        raise HTTPException(409, f"no CLOB token for {req.direction} side of this market")

    # Snapshot the book NOW
    async with PolymarketClient() as pm:
        book = await pm.get_orderbook(token_id)
    metrics = compute_book_metrics(book, req.direction)
    if not metrics.available or metrics.entry_offer is None:
        raise HTTPException(503, "no live book available for this market right now")

    effective_entry, fee_usdc, slippage_usdc = _estimate_costs(
        metrics.entry_offer, req.size_usdc, mkt["category"], metrics.liquidity_5c_usdc,
    )

    trade_id = await crud.insert_paper_trade(
        conn,
        signal_log_id=req.signal_log_id,
        condition_id=req.condition_id,
        direction=req.direction,
        entry_price=metrics.entry_offer,
        entry_mid=metrics.mid,
        entry_size_usdc=req.size_usdc,
        entry_fee_usdc=fee_usdc,
        entry_slippage_usdc=slippage_usdc,
        notes=req.notes,
    )
    trade = await crud.get_paper_trade(conn, trade_id)
    assert trade is not None
    return {
        **trade,
        "effective_entry_price": round(effective_entry, 6),
    }


@router.get("")
async def list_trades(
    status: str | None = None,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if status is not None and status not in VALID_PAPER_TRADE_STATUSES:
        raise HTTPException(
            400, f"invalid status; must be one of {VALID_PAPER_TRADE_STATUSES}",
        )
    trades = await crud.list_paper_trades(conn, status=status)
    return {"trades": trades, "count": len(trades)}


@router.get("/{trade_id}")
async def get_trade(
    trade_id: int, conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    t = await crud.get_paper_trade(conn, trade_id)
    if t is None:
        raise HTTPException(404, f"paper_trade {trade_id} not found")
    return t


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: int, conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Manual exit at the current book ask of the OPPOSITE side.

    To exit a YES position you SELL it back, which for backtest realism
    means crossing into the bid side of YES (we read best_bid). Slippage
    in reverse, fee on exit too.
    """
    t = await crud.get_paper_trade(conn, trade_id)
    if t is None:
        raise HTTPException(404, "trade not found")
    if t["status"] != "open":
        raise HTTPException(409, f"trade is {t['status']}, cannot close")

    # F23: replaced inline SQL with crud helper.
    mkt = await crud.get_market_tokens_and_category(conn, t["condition_id"])
    if mkt is None:
        raise HTTPException(404, "market metadata missing for trade")
    token_id = mkt["clob_token_yes"] if t["direction"] == "YES" else mkt["clob_token_no"]
    async with PolymarketClient() as pm:
        book = await pm.get_orderbook(token_id) if token_id else None
    metrics = compute_book_metrics(book, t["direction"])
    if not metrics.available or metrics.best_bid is None:
        raise HTTPException(503, "no live book available; cannot close at market right now")

    exit_price = metrics.best_bid  # selling crosses to bid

    # R10 (Pass 3): unified close formula via paper_trade_close helper.
    # Pre-fix this path silently ignored entry_slippage_usdc and double-counted
    # nothing for entry fee (comment claimed "already deducted at open" but
    # it never was), so manual closes reported P&L higher than reality. The
    # helper makes manual + auto-close-resolved + auto-close-on-exit all use
    # the same Polymarket-correct math.
    close = compute_realized_pnl(
        entry_price=float(t["entry_price"]),
        entry_size_usdc=float(t["entry_size_usdc"]),
        entry_slippage_usdc=float(t.get("entry_slippage_usdc") or 0.0),
        entry_fee_usdc=float(t.get("entry_fee_usdc") or 0.0),
        exit_price=exit_price,
        exit_kind="manual",
        category=mkt["category"],
    )

    ok = await crud.close_paper_trade_manual(
        conn, trade_id, exit_price, close.realized_pnl_usdc,
    )
    if not ok:
        raise HTTPException(409, "close failed (concurrent update?)")
    return await crud.get_paper_trade(conn, trade_id) or {}
