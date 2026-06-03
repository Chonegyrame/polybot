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

import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.polymarket import PolymarketClient
from esports.analysis import wallet_equity_curve
from esports.consensus import group_into_matches
from esports.db import DEFAULT_DB

router = APIRouter(prefix="/esports", tags=["esports"])

# Joined action row -> shaped dict shared by /actions and /matches.
# Pulls wallet name/follow + the market's closed flag from the universe.
_ACTION_SQL = """
    SELECT a.*, s.name, s.follow,
           em.closed AS market_closed, em.resolved_outcome AS resolved_outcome,
           em.start_time AS market_start
      FROM esports_sharp_actions a
      LEFT JOIN esports_sharps  s  ON s.wallet = a.wallet
      LEFT JOIN esports_markets em ON em.condition_id = a.condition_id
"""


def _shape_action(r: sqlite3.Row) -> dict[str, Any]:
    """One feed row. `notional` is computed (size×price) when the data-api omits
    usdcSize — which it does for every /trades?user row, so the Size column was
    otherwise always blank. `slippage` = extra we'd pay buying at the ask now."""
    their, ask = r["their_price"], r["live_ask"]
    notional = r["usdc_size"]
    if notional is None and r["size"] is not None and their is not None:
        notional = r["size"] * their
    slip = (ask - their) if (their is not None and ask is not None
                             and r["side"] == "BUY") else None
    closed = r["market_closed"]
    return {
        "id": r["id"], "wallet": r["wallet"], "name": r["name"],
        "follow": bool(r["follow"]) if r["follow"] is not None else None,
        "condition_id": r["condition_id"], "asset": r["asset"], "title": r["title"],
        "slug": r["slug"], "outcome": r["outcome"], "side": r["side"],
        "game": r["game"], "market_type": r["market_type"],
        "their_price": their, "size": r["size"], "usdc_size": r["usdc_size"],
        "notional": notional,
        "live_bid": r["live_bid"], "live_ask": ask, "slippage": slip,
        "market_open": (closed == 0) if closed is not None else None,
        "resolved_outcome": r["resolved_outcome"],
        "start_time": _iso(r["market_start"]),
        "traded_at": _iso(r["traded_at"]),
        "detected_at": _iso(r["detected_at"]),
    }


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


@router.get("/health")
async def health() -> dict[str, Any]:
    """Real tracker liveness for the UI ring: how long since the last poll pass,
    the configured interval, and whether it's cycling cleanly / lagging / stalled."""
    down = {"status": "down", "age_seconds": None, "cycle_seconds": None,
            "cycles": 0, "wallets": 0, "errors_last_cycle": 0, "last_error": None}
    conn = _ro_conn()
    if conn is None:
        return down
    try:
        r = conn.execute("SELECT * FROM tracker_status WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return down  # table absent — tracker hasn't run the new code yet
    finally:
        conn.close()
    if r is None or r["last_cycle_at"] is None:
        return down

    now = time.time()
    age = now - r["last_cycle_at"]
    cs = r["cycle_seconds"] or 8.0
    errs = r["errors_last_cycle"] or 0
    wallets = r["wallets"] or 0
    last_ms = r["last_cycle_ms"]
    if age > cs * 3 + 5:
        status = "stale"            # heartbeat exists but tracker stopped cycling
    elif errs >= max(3, wallets // 2):
        status = "error"            # most wallet polls are failing (API trouble)
    elif last_ms is not None and last_ms > cs * 1000:
        status = "lagging"          # a pass takes longer than its interval
    else:
        status = "ok"
    return {
        "status": status, "age_seconds": age, "cycle_seconds": cs,
        "last_cycle_ms": last_ms, "cycles": r["cycles"], "wallets": wallets,
        "errors_last_cycle": errs, "last_error": r["last_error"],
        "last_error_at": _iso(r["last_error_at"]),
    }


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
            f"{_ACTION_SQL} {where} ORDER BY a.detected_at DESC LIMIT ?", params,
        ).fetchall()
        return {"actions": [_shape_action(r) for r in rows]}
    finally:
        conn.close()


@router.get("/matches")
async def matches(
    window: int = Query(400, ge=1, le=2000, description="how many recent actions to roll up"),
    follow_only: bool = Query(False),
    game: str | None = Query(None, description="filter: 'lol' or 'cs'"),
) -> dict[str, Any]:
    """Recent actions rolled up into match-level consensus cards (live first).

    This is the headline view: instead of a flat tape where six sharps backing
    the same team look like six unrelated rows, you see one card — "5 of 6 on
    ThunderTalk, avg entry 0.52, you'd pay 0.56 now, $12k in".
    """
    conn = _ro_conn()
    if conn is None:
        return {"matches": [], "live_count": 0, "sharps_active": 0, "notional": 0}
    try:
        clauses, params = [], []
        if follow_only:
            clauses.append("s.follow=1")
        if game:
            clauses.append("a.game=?"); params.append(game)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(window)
        rows = conn.execute(
            f"{_ACTION_SQL} {where} ORDER BY a.detected_at DESC LIMIT ?", params,
        ).fetchall()
        actions = [_shape_action(r) for r in rows]
        grouped = group_into_matches(actions, max_matches=80)
        return {
            "matches": grouped,
            "live_count": sum(1 for m in grouped if m["is_live"]),
            "sharps_active": len({a["wallet"] for a in actions}),
            "notional": sum((a.get("notional") or 0) for a in actions),
        }
    finally:
        conn.close()


@router.get("/live_asks")
async def live_asks(
    assets: str = Query(..., description="comma-separated CLOB outcome token ids"),
) -> dict[str, Any]:
    """Current best ask for each token id, fetched LIVE from the CLOB book.

    Read-only and on-demand: the UI calls this only for the open markets in a
    match card you've EXPANDED, so a stale captured ask can be replaced with what
    you'd actually pay right now. Capped + deduped to bound calls against the
    shared rate limiter the tracker also uses. Nothing is written; the tracker,
    capture logic and backtest data are untouched."""
    ids = list(dict.fromkeys(x for x in (assets.split(",") if assets else []) if x))[:24]
    out: dict[str, float | None] = {}
    if not ids:
        return {"asks": out, "fetched_at": _iso(time.time())}

    async def _one(pm: PolymarketClient, tid: str) -> None:
        try:
            book = await pm.get_orderbook(tid)
            prices = [float(l["price"]) for l in ((book or {}).get("asks") or [])
                      if l.get("price") is not None]
            out[tid] = min(prices) if prices else None  # best ask = lowest offer
        except Exception:  # noqa: BLE001 — degrade to null for this token
            out[tid] = None

    try:
        async with PolymarketClient() as pm:
            await asyncio.gather(*[_one(pm, t) for t in ids])
    except Exception:  # noqa: BLE001 — whole-batch failure → all null, UI keeps captured
        pass
    return {"asks": out, "fetched_at": _iso(time.time())}


@router.get("/scoreboard")
async def scoreboard(game: str | None = Query(None)) -> dict[str, Any]:
    """Honest forward-test: over markets that have RESOLVED since we started
    capturing, did the sharp consensus (the lean side) win, and what would
    following it have returned per $1 at the price you'd actually have paid?"""
    conn = _ro_conn()
    empty = {"resolved_markets": 0, "consensus_correct": 0, "hit_rate": None,
             "avg_follow_pnl": None, "recent": []}
    if conn is None:
        return empty
    try:
        rows = conn.execute(f"{_ACTION_SQL} ORDER BY a.detected_at DESC LIMIT 5000").fetchall()
    finally:
        conn.close()
    actions = [_shape_action(r) for r in rows]
    if game:
        actions = [a for a in actions if a["game"] == game]
    matches = group_into_matches(actions, max_matches=10000)
    # Only markets that resolved AND had a directional consensus (a lean side)
    # — a market where sharps only exited has no "consensus" to score.
    mkts = [mk for m in matches for mk in m["markets"]
            if mk["resolved"] and mk["lean_outcome"]]
    if not mkts:
        return empty
    correct = sum(1 for mk in mkts if mk["consensus_correct"])
    pnls = [mk["follow_pnl"] for mk in mkts if mk["follow_pnl"] is not None]
    # newest resolved first, for a small "recent results" list in the UI
    recent = sorted(mkts, key=lambda mk: max((a["detected_at"] or "" for a in mk["actions"]), default=""),
                    reverse=True)[:12]
    return {
        "resolved_markets": len(mkts),
        "consensus_correct": correct,
        "hit_rate": correct / len(mkts),
        "avg_follow_pnl": (sum(pnls) / len(pnls)) if pnls else None,
        "recent": [{
            "label": mk["label"], "lean_outcome": mk["lean_outcome"],
            "resolved_outcome": mk["resolved_outcome"],
            "correct": mk["consensus_correct"], "follow_pnl": mk["follow_pnl"],
            "buyers": mk["buyers"],
        } for mk in recent],
    }


@router.get("/wallet/{wallet}")
async def wallet_detail(wallet: str) -> dict[str, Any]:
    """Fast part of a sharp's detail: watchlist meta + the actions we've logged,
    both straight from local SQLite so the modal paints instantly. The slow
    equity curve is a SEPARATE call (/wallet/{w}/curve) so it never blocks the
    modal opening — the previous bundled version hung ~8s on 'Loading…'."""
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
                    "vet_markets": row["vet_markets"],
                }
            for r in conn.execute(
                """SELECT * FROM esports_sharp_actions WHERE wallet=?
                   ORDER BY detected_at DESC LIMIT 40""", (wallet,)):
                their = r["their_price"]
                notional = r["usdc_size"]
                if notional is None and r["size"] is not None and their is not None:
                    notional = r["size"] * their
                logged.append({
                    "id": r["id"], "title": r["title"], "side": r["side"],
                    "game": r["game"], "market_type": r["market_type"],
                    "outcome": r["outcome"], "their_price": their,
                    "live_ask": r["live_ask"], "usdc_size": r["usdc_size"], "notional": notional,
                    "condition_id": r["condition_id"], "detected_at": _iso(r["detected_at"]),
                })
        finally:
            conn.close()

    if meta is None:
        raise HTTPException(status_code=404, detail="wallet not on esports watchlist")
    return {"meta": meta, "actions": logged}


@router.get("/wallet/{wallet}/curve")
async def wallet_curve(wallet: str) -> dict[str, Any]:
    """Slow part: recent-form esports equity curve (≤2500 trades reconstructed
    over the network, 5-min cached). Loaded lazily by the modal."""
    wallet = wallet.lower()
    try:
        async with PolymarketClient() as pm:
            curve = await wallet_equity_curve(pm, wallet, time.time())
    except Exception:  # noqa: BLE001 — best-effort; modal stays useful without it
        curve = {"points": [], "markets": 0, "total_pnl": None, "win_rate": None, "error": True}
    return {"curve": curve}
