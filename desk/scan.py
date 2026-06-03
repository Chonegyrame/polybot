"""Screener scan runner + a self-contained daily scheduler.

run_scan() walks the watchlist, pulls daily history (Yahoo), runs the 50/200
golden-cross detector, and logs any *fresh* cross to screen_signals (idempotent
via UNIQUE(ticker, kind, cross_date) — the same cross is never logged twice, so
re-scans don't spam the badge).

The scheduler is a daemon thread owned entirely by the desk — it does NOT touch
the Polymarket scheduler/lifespan. It runs once per ET calendar day (shortly
after startup if today's scan hasn't happened, then re-checks every 30 min), so
when you open the desk in the morning the badge already reflects the latest
session. A manual "Scan now" calls the same run_scan(). Disable the background
thread with DESK_SCREENER_SCHEDULER=0.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from desk import db, market_data, screener

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

log = logging.getLogger("desk.scan")

_scan_lock = threading.Lock()
_started = False


def _et_date() -> str:
    now = datetime.now(_ET) if _ET else datetime.now(timezone.utc)
    return now.date().isoformat()


def get_meta(conn, key: str):
    row = conn.execute("SELECT value FROM screen_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO screen_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def run_scan(reason: str = "manual") -> dict:
    """Scan the whole watchlist once. Serialized so manual + daily can't overlap."""
    with _scan_lock:
        conn = db.get_conn()
        tickers = [r["ticker"] for r in
                   conn.execute("SELECT ticker FROM screen_tickers ORDER BY ticker").fetchall()]
        kind = f"golden_{screener.EMA_FAST}_{screener.EMA_SLOW}"
        scanned = 0
        new_signals = 0
        hits: list[dict] = []
        errors: list[dict] = []

        for tk in tickers:
            try:
                closes, dates = market_data.fetch_daily_closes(tk)
            except Exception as e:
                errors.append({"ticker": tk, "error": str(e)})
                continue
            scanned += 1
            det = screener.detect_cross(closes)
            if not screener.is_fresh_golden(det):
                continue
            ci = det["cross_index"]
            cross_date = dates[ci]
            cur = conn.execute(
                "INSERT OR IGNORE INTO screen_signals "
                "(id, ticker, kind, cross_date, detected_at, days_since, fast_ema, slow_ema, last_close, seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,0)",
                (uuid.uuid4().hex[:16], tk, kind, cross_date, db._now(),
                 det["days_since"], det["fast_ema"], det["slow_ema"], det["last_close"]),
            )
            if cur.rowcount:
                new_signals += 1
            else:
                # Same cross already known — refresh its live numbers, keep `seen`.
                conn.execute(
                    "UPDATE screen_signals SET days_since=?, fast_ema=?, slow_ema=?, last_close=?, detected_at=? "
                    "WHERE ticker=? AND kind=? AND cross_date=?",
                    (det["days_since"], det["fast_ema"], det["slow_ema"], det["last_close"], db._now(),
                     tk, kind, cross_date),
                )
            hits.append({"ticker": tk, "cross_date": cross_date, "days_since": det["days_since"]})

        now = db._now()
        summary = {"scanned": scanned, "watchlist": len(tickers), "hits": len(hits),
                   "new_signals": new_signals, "errors": len(errors), "reason": reason}
        set_meta(conn, "last_scan_at", now)
        set_meta(conn, "last_scan_date", _et_date())
        set_meta(conn, "last_scan_summary", json.dumps(summary))
        conn.commit()

        result = dict(summary)
        result["last_scan_at"] = now
        result["error_detail"] = errors
        if errors:
            log.warning("scan (%s): %d/%d ok, %d errors", reason, scanned, len(tickers), len(errors))
        return result


# ── background daily scheduler (isolated daemon thread) ──

def _maybe_daily_scan() -> None:
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM screen_tickers").fetchone()[0]
    if n == 0:
        return
    if get_meta(conn, "last_scan_date") != _et_date():
        run_scan(reason="daily")


def _loop() -> None:
    time.sleep(15)  # let the app finish booting
    while True:
        try:
            _maybe_daily_scan()
        except Exception:
            log.exception("daily screener check failed")
        time.sleep(1800)  # re-check every 30 min; fires when the ET date rolls over


def start_scheduler() -> None:
    global _started
    if _started:
        return
    if os.environ.get("DESK_SCREENER_SCHEDULER", "1").strip().lower() in ("0", "false", "no"):
        log.info("desk screener scheduler disabled via DESK_SCREENER_SCHEDULER")
        return
    _started = True
    threading.Thread(target=_loop, name="desk-screener", daemon=True).start()
    log.info("desk screener daily scheduler started")
