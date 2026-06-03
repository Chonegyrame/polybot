"""FastAPI router for the BIG STOCK desk — all endpoints live under /desk/api.

Self-contained: imports only desk.db and desk.quotes (never app.*). Handlers are
sync `def` so FastAPI runs them in its threadpool, keeping sqlite3's blocking
calls off the event loop. Request bodies use the same camelCase field names the
UI already uses, so the frontend swap from window.BS_DATA to fetch() is 1:1.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from desk import db
from desk import quotes
from desk import scan
from desk import screener

# Create schema once, at import time (idempotent), so the very first request
# already has tables. Then start the isolated daily screener scheduler.
db.init_db()
scan.start_scheduler()

router = APIRouter(prefix="/desk/api", tags=["desk"])


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ───────────────────────────── models ─────────────────────────────

class NoteIn(BaseModel):
    sym: str
    co: Optional[str] = None
    priceAt: Optional[float] = None
    last: Optional[float] = None
    importance: str = "idea"
    group: str = "new"
    when: Optional[str] = None
    thesis: Optional[str] = ""
    levels: list[dict[str, Any]] = Field(default_factory=list)
    invalid: Optional[str] = ""


class TradeIn(BaseModel):
    sym: str
    name: Optional[str] = None
    dir: str = "long"
    date: Optional[str] = None
    time: Optional[str] = None
    entry: Optional[float] = None
    exit: Optional[float] = None
    size: Optional[int] = None
    ticks: Optional[int] = None
    pnl: Optional[float] = None
    r: Optional[float] = None
    setup: Optional[str] = None
    dur: Optional[str] = None
    wentWell: Optional[str] = ""
    wouldChange: Optional[str] = ""


class AlertIn(BaseModel):
    sym: str
    co: Optional[str] = None
    type: str = "price"
    icon: Optional[str] = None
    cond: Optional[str] = None
    detail: Optional[str] = None
    state: str = "armed"
    when: Optional[str] = None


class AlertState(BaseModel):
    state: str  # armed | triggered | paused
    when: Optional[str] = None


class TickerIn(BaseModel):
    ticker: str
    name: Optional[str] = None


# ───────────────────────────── health ─────────────────────────────

@router.get("/health")
def health() -> dict:
    conn = db.get_conn()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("notes", "trades", "alerts")
    }
    return {"ok": True, "db": str(db.DB_PATH), "counts": counts,
            "quotes_enabled": quotes.is_enabled()}


# ───────────────────────────── notes ──────────────────────────────

@router.get("/notes")
def list_notes() -> dict:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM notes ORDER BY created_at DESC").fetchall()
    return {"notes": [db.note_to_dict(r) for r in rows]}


@router.post("/notes")
def create_note(body: NoteIn) -> dict:
    conn = db.get_conn()
    nid = _new_id("n")
    conn.execute(
        "INSERT INTO notes (id, sym, co, price_at, last, importance, grp, when_, thesis, levels, invalid, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (nid, body.sym.upper(), body.co, body.priceAt, body.last, body.importance, body.group,
         body.when, body.thesis, json.dumps(body.levels), body.invalid, db._now()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    return {"note": db.note_to_dict(row)}


@router.patch("/notes/{note_id}")
def update_note(note_id: str, body: NoteIn) -> dict:
    conn = db.get_conn()
    cur = conn.execute(
        "UPDATE notes SET sym=?, co=?, price_at=?, last=?, importance=?, grp=?, when_=?, thesis=?, levels=?, invalid=? "
        "WHERE id=?",
        (body.sym.upper(), body.co, body.priceAt, body.last, body.importance, body.group,
         body.when, body.thesis, json.dumps(body.levels), body.invalid, note_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="note not found")
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return {"note": db.note_to_dict(row)}


@router.delete("/notes/{note_id}")
def delete_note(note_id: str) -> dict:
    conn = db.get_conn()
    cur = conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="note not found")
    return {"deleted": note_id}


# ───────────────────────────── trades ─────────────────────────────

@router.get("/trades")
def list_trades() -> dict:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY created_at DESC").fetchall()
    return {"trades": [db.trade_to_dict(r) for r in rows]}


@router.post("/trades")
def create_trade(body: TradeIn) -> dict:
    conn = db.get_conn()
    tid = _new_id("t")
    conn.execute(
        "INSERT INTO trades (id, sym, name, dir, trade_date, trade_time, entry, exit, size, ticks, pnl, r, setup, dur, went_well, would_change, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, body.sym.upper(), body.name, body.dir, body.date, body.time, body.entry, body.exit,
         body.size, body.ticks, body.pnl, body.r, body.setup, body.dur, body.wentWell, body.wouldChange, db._now()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
    return {"trade": db.trade_to_dict(row)}


@router.patch("/trades/{trade_id}")
def update_trade(trade_id: str, body: TradeIn) -> dict:
    conn = db.get_conn()
    cur = conn.execute(
        "UPDATE trades SET sym=?, name=?, dir=?, trade_date=?, trade_time=?, entry=?, exit=?, size=?, ticks=?, "
        "pnl=?, r=?, setup=?, dur=?, went_well=?, would_change=? WHERE id=?",
        (body.sym.upper(), body.name, body.dir, body.date, body.time, body.entry, body.exit, body.size,
         body.ticks, body.pnl, body.r, body.setup, body.dur, body.wentWell, body.wouldChange, trade_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="trade not found")
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    return {"trade": db.trade_to_dict(row)}


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: str) -> dict:
    conn = db.get_conn()
    cur = conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="trade not found")
    return {"deleted": trade_id}


# ───────────────────────────── alerts ─────────────────────────────

@router.get("/alerts")
def list_alerts() -> dict:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
    return {"alerts": [db.alert_to_dict(r) for r in rows]}


@router.post("/alerts")
def create_alert(body: AlertIn) -> dict:
    conn = db.get_conn()
    aid = _new_id("a")
    conn.execute(
        "INSERT INTO alerts (id, sym, co, type, icon, cond, detail, state, when_, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, body.sym.upper(), body.co, body.type, body.icon, body.cond, body.detail, body.state, body.when, db._now()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    return {"alert": db.alert_to_dict(row)}


@router.patch("/alerts/{alert_id}")
def update_alert_state(alert_id: str, body: AlertState) -> dict:
    conn = db.get_conn()
    cur = conn.execute(
        "UPDATE alerts SET state=?, when_=COALESCE(?, when_) WHERE id=?",
        (body.state, body.when, alert_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="alert not found")
    row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    return {"alert": db.alert_to_dict(row)}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: str) -> dict:
    conn = db.get_conn()
    cur = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"deleted": alert_id}


# ───────────────────────────── quotes ─────────────────────────────

@router.get("/quote/{symbol}")
async def get_quote(symbol: str) -> dict:
    """Live-ish equity quote via Finnhub (free tier). Returns {quote: null}
    when no FINNHUB_API_KEY is configured, so the UI degrades gracefully."""
    q = await quotes.get_quote(symbol.upper())
    return {"quote": q}


# ─────────────────────── golden-cross screener ────────────────────────

@router.get("/screener/watchlist")
def list_watchlist() -> dict:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM screen_tickers ORDER BY ticker").fetchall()
    return {"tickers": [db.ticker_to_dict(r) for r in rows]}


@router.post("/screener/watchlist")
def add_ticker(body: TickerIn) -> dict:
    tk = body.ticker.strip().upper()
    if not tk:
        raise HTTPException(status_code=400, detail="ticker required")
    conn = db.get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO screen_tickers (ticker, name, added_at) VALUES (?,?,?)",
        (tk, body.name, db._now()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM screen_tickers WHERE ticker=?", (tk,)).fetchone()
    return {"ticker": db.ticker_to_dict(row)}


@router.delete("/screener/watchlist/{ticker}")
def remove_ticker(ticker: str) -> dict:
    tk = ticker.strip().upper()
    conn = db.get_conn()
    conn.execute("DELETE FROM screen_tickers WHERE ticker=?", (tk,))
    conn.execute("DELETE FROM screen_signals WHERE ticker=?", (tk,))  # drop its signals too
    conn.commit()
    return {"deleted": tk}


@router.post("/screener/scan")
def scan_now() -> dict:
    """Run the golden-cross scan over the whole watchlist right now (sync def →
    FastAPI threadpool, so the blocking Yahoo fetches don't stall the loop)."""
    return scan.run_scan(reason="manual")


@router.get("/screener/signals")
def list_signals() -> dict:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM screen_signals "
        "ORDER BY (days_since IS NULL), days_since ASC, detected_at DESC"
    ).fetchall()
    return {"signals": [db.signal_to_dict(r) for r in rows]}


@router.get("/screener/summary")
def screener_summary() -> dict:
    """Drives the navbar badge: how many crosses the user hasn't seen yet."""
    conn = db.get_conn()
    unseen = conn.execute("SELECT COUNT(*) FROM screen_signals WHERE seen=0").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM screen_signals").fetchone()[0]
    wl = conn.execute("SELECT COUNT(*) FROM screen_tickers").fetchone()[0]
    summ = scan.get_meta(conn, "last_scan_summary")
    return {
        "unseenCount": unseen,
        "signalCount": total,
        "watchlistCount": wl,
        "lastScanAt": scan.get_meta(conn, "last_scan_at"),
        "lastScanSummary": json.loads(summ) if summ else None,
        "emaFast": screener.EMA_FAST,
        "emaSlow": screener.EMA_SLOW,
        "window": screener.CROSS_WINDOW,
    }


@router.post("/screener/seen")
def mark_seen() -> dict:
    """Clear the badge — mark all current signals as seen."""
    conn = db.get_conn()
    conn.execute("UPDATE screen_signals SET seen=1 WHERE seen=0")
    conn.commit()
    return {"ok": True}
