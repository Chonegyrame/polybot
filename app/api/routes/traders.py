"""Trader endpoints — top-N for the UI ranking list, drill-down for the modal."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.db import crud
from app.services.polymarket import LeaderboardCategory
from app.services.trader_ranker import RankingMode, rank_traders

router = APIRouter(prefix="/traders", tags=["traders"])

VALID_MODES = ("absolute", "hybrid", "specialist")
VALID_CATEGORIES = ("overall", "politics", "sports", "crypto", "culture", "tech", "finance")


@router.get("/top")
async def get_top_traders(
    mode: str = Query("absolute"),
    category: str = Query("overall"),
    top_n: int = Query(50, ge=20, le=100),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Top-N traders for the user's (mode, category) selection.

    `top_n` is clamped to 20-100 per UI-SPEC. Step 5 default is 50.
    """
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {VALID_CATEGORIES}")

    traders = await rank_traders(
        conn,
        mode=mode,             # type: ignore[arg-type]
        category=category,     # type: ignore[arg-type]
        top_n=top_n,
    )
    enrichment: dict[str, dict[str, Any]] = {}
    if traders:
        enrichment = await crud.get_top_traders_enrichment(
            conn, wallets=[t.proxy_wallet for t in traders],
        )
    rows: list[dict[str, Any]] = []
    for t in traders:
        d = asdict(t)
        e = enrichment.get(t.proxy_wallet)
        d["n_resolved"] = int(e["n_resolved"]) if e and e.get("n_resolved") is not None else 0
        d["n_active"] = int(e["n_active"]) if e and e.get("n_active") is not None else 0
        d["cluster_id"] = e["cluster_id"] if e else None
        rows.append(d)
    return {
        "mode": mode,
        "category": category,
        "top_n": top_n,
        "traders": rows,
    }


@router.get("/{wallet}")
async def get_trader(
    wallet: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Drill-down: profile + per-category stats + open positions + recent trades.

    Per UI-SPEC the modal opens on wallet click.
    """
    # F23: extracted inline SQL into named crud helpers (CLAUDE.md rule).
    wallet = wallet.lower()
    profile = await crud.get_trader_profile(conn, wallet)
    if profile is None:
        raise HTTPException(404, f"trader {wallet} not found")
    per_category = await crud.get_trader_per_category_stats(conn, wallet)
    positions = await crud.get_trader_open_positions(conn, wallet, limit=200)
    classification = await crud.get_trader_classification(conn, wallet)
    cluster_row = await crud.get_trader_sybil_cluster(conn, wallet)
    # Profile aggregates derived from per_category — the leaderboard 'overall'
    # row is authoritative for total pnl/vol/roi; n_positions is just the
    # count of currently-open positions returned above. The UI drill-down
    # header reads these directly off `profile`.
    overall = next((r for r in per_category if r.get("category") == "overall"), None)
    if overall is not None:
        profile["pnl"] = float(overall["pnl"]) if overall.get("pnl") is not None else 0.0
        profile["vol"] = float(overall["vol"]) if overall.get("vol") is not None else 0.0
        profile["roi"] = float(overall["roi"]) if overall.get("roi") is not None else 0.0
    else:
        # Fallback: sum across non-overall categories if 'overall' wasn't in
        # the latest snapshot for this wallet.
        total_pnl = sum(float(r["pnl"]) for r in per_category if r.get("pnl") is not None)
        total_vol = sum(float(r["vol"]) for r in per_category if r.get("vol") is not None)
        profile["pnl"] = total_pnl
        profile["vol"] = total_vol
        profile["roi"] = (total_pnl / total_vol) if total_vol > 0 else 0.0
    profile["n_positions"] = len(positions)
    return {
        "profile": profile,
        "classification": classification,
        "cluster": cluster_row,
        "per_category": per_category,
        "open_positions": positions,
    }
