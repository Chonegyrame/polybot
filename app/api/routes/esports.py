"""Esports sharp tracker — read API over the LOCAL SQLite store.

The esports section is deliberately NOT in Supabase (see ESPORTS_PLAN.md): the
tracker (`esports.tracker`) writes a local file, and this router reads it so the
existing dashboard UI can render the watchlist + live action feed. Read-only,
opened in SQLite `mode=ro`, so it never contends with the tracker's writes.

Two processes are involved: this API (polybot/uvicorn) serves + reads; the
tracker (`esports.bat`) writes. Both can run at once; if the DB doesn't exist
yet (tracker never started), endpoints return empty payloads with
`tracking: false` rather than erroring.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.polymarket import PolymarketClient
from esports.analysis import wallet_equity_curve
from esports.db import DEFAULT_DB

router = APIRouter(prefix="/esports", tags=["esports"])


def _ro_conn() -> sqlite3.Connection | None:
    """Open the local esports DB read-only. None if it doesn't exist yet."""
    if not DEFAULT_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _iso(epoch: Any) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


@router.get("/summary")
async def summary() -> dict[str, Any]:
    conn = _ro_conn()
    if conn is None:
        return {"tracking": False, "wallets": 0, "follow": 0, "watch": 0,
                "actions": 0, "last_detected_at": None}
    try:
        w = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(follow),0) f "
                         "FROM esports_sharps WHERE active=1").fetchone()
        a = conn.execute("SELECT COUNT(*) c, MAX(detected_at) m "
                         "FROM esports_sharp_actions").fetchone()
        return {
            "tracking": True,
            "wallets": w["c"], "follow": w["f"], "watch": w["c"] - w["f"],
            "actions": a["c"], "last_detected_at": _iso(a["m"]),
        }
    finally:
        conn.close()


@router.get("/sharps")
async def sharps() -> dict[str, Any]:
    """Watchlist with vetted stats + how many actions we've logged per wallet."""
    conn = _ro_conn()
    if conn is None:
        return {"sharps": []}
    try:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM esports_sharp_actions a
                        WHERE a.wallet = s.wallet) AS action_count,
                      (SELECT MAX(a.detected_at) FROM esports_sharp_actions a
                        WHERE a.wallet = s.wallet) AS last_action_at
                 FROM esports_sharps s WHERE s.active=1
                ORDER BY s.follow DESC, s.vet_pnl DESC"""
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "wallet": r["wallet"], "name": r["name"], "sectors": r["sectors"],
                "follow": bool(r["follow"]), "note": r["note"],
                "vet_pnl": r["vet_pnl"], "vet_win_rate": r["vet_win_rate"],
                "vet_roi": r["vet_roi"], "vet_median_entry": r["vet_median_entry"],
                "vet_markets": r["vet_markets"],
                "action_count": r["action_count"],
                "last_action_at": _iso(r["last_action_at"]),
            })
        return {"sharps": out}
    finally:
        conn.close()


@router.get("/actions")
async def actions(
    limit: int = Query(100, ge=1, le=500),
    follow_only: bool = Query(False),
    game: str | None = Query(None, description="filter: 'lol' or 'cs'"),
    market_type: str | None = Query(None, description="filter: winner/handicap/total/prop"),
) -> dict[str, Any]:
    """Recent detected entries/exits, newest first, joined with wallet name/follow."""
    conn = _ro_conn()
    if conn is None:
        return {"actions": []}
    try:
        clauses, params = [], []
        if follow_only:
            clauses.append("s.follow=1")
        if game:
            clauses.append("a.game=?"); params.append(game)
        if market_type:
            clauses.append("a.market_type=?"); params.append(market_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"""SELECT a.*, s.name, s.follow
                  FROM esports_sharp_actions a
                  LEFT JOIN esports_sharps s ON s.wallet = a.wallet
                  {where}
                 ORDER BY a.detected_at DESC LIMIT ?""",
            params,
        ).fetchall()
        out = []
        for r in rows:
            their = r["their_price"]
            ask = r["live_ask"]
            # "slippage" = extra we'd pay vs their fill if we BUY at the ask now.
            slip = (ask - their) if (their is not None and ask is not None
                                     and r["side"] == "BUY") else None
            out.append({
                "id": r["id"], "wallet": r["wallet"], "name": r["name"],
                "follow": bool(r["follow"]) if r["follow"] is not None else None,
                "condition_id": r["condition_id"], "title": r["title"],
                "slug": r["slug"], "outcome": r["outcome"], "side": r["side"],
                "game": r["game"], "market_type": r["market_type"],
                "their_price": their, "size": r["size"], "usdc_size": r["usdc_size"],
                "live_bid": r["live_bid"], "live_ask": ask, "slippage": slip,
                "traded_at": _iso(r["traded_at"]),
                "detected_at": _iso(r["detected_at"]),
            })
        return {"actions": out}
    finally:
        conn.close()


@router.get("/wallet/{wallet}")
async def wallet_detail(wallet: str) -> dict[str, Any]:
    """One sharp's detail for the modal: watchlist meta, recent-form equity
    curve (reconstructed from <=2500 trades), and the actions we've logged."""
    wallet = wallet.lower()
    meta: dict[str, Any] | None = None
    logged: list[dict[str, Any]] = []
    conn = _ro_conn()
    if conn is not None:
        try:
            row = conn.execute("SELECT * FROM esports_sharps WHERE wallet=?", (wallet,)).fetchone()
            if row is not None:
                meta = {
                    "wallet": row["wallet"], "name": row["name"], "sectors": row["sectors"],
                    "follow": bool(row["follow"]), "note": row["note"],
                    "vet_pnl": row["vet_pnl"], "vet_win_rate": row["vet_win_rate"],
                    "vet_roi": row["vet_roi"], "vet_median_entry": row["vet_median_entry"],
                }
            for r in conn.execute(
                """SELECT * FROM esports_sharp_actions WHERE wallet=?
                   ORDER BY detected_at DESC LIMIT 40""", (wallet,)):
                logged.append({
                    "id": r["id"], "title": r["title"], "side": r["side"],
                    "game": r["game"], "market_type": r["market_type"],
                    "outcome": r["outcome"], "their_price": r["their_price"],
                    "live_ask": r["live_ask"], "usdc_size": r["usdc_size"],
                    "condition_id": r["condition_id"], "detected_at": _iso(r["detected_at"]),
                })
        finally:
            conn.close()

    if meta is None:
        raise HTTPException(status_code=404, detail="wallet not on esports watchlist")

    try:
        async with PolymarketClient() as pm:
            curve = await wallet_equity_curve(pm, wallet, time.time())
    except Exception:  # noqa: BLE001 — curve is best-effort; modal still useful without it
        curve = {"points": [], "markets": 0, "total_pnl": None, "win_rate": None, "error": True}

    return {"meta": meta, "curve": curve, "actions": logged}
