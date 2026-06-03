"""FastAPI app — the HTTP face of the smart-money tracker.

Boots the in-process scheduler via the lifespan hook so a single `uvicorn`
command runs both the API and the 10-min refresh / signal-logging jobs.

Run locally:
    ./venv/Scripts/python.exe -m uvicorn app.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    backtest,
    esports,
    insider,
    markets,
    paper_trades,
    signals,
    system,
    traders,
    watchlist,
)
from app.config import settings
from app.scheduler.runner import lifespan_scheduler

UI_DIR = Path(__file__).resolve().parent.parent.parent / "ui"
DESK_UI_DIR = Path(__file__).resolve().parent.parent.parent / "desk_ui"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot the scheduler + esports tracker alongside the API.

    The esports sharp tracker runs as an in-process asyncio task (writes its own
    local SQLite, reads via PolymarketClient's shared rate limiter), so one
    `polybot`/uvicorn launch = UI + Supabase jobs + esports tracking. Disable
    with ESPORTS_TRACKER_ENABLED=false (then run it standalone via esports.bat).
    """
    async with lifespan_scheduler():
        tracker_task: asyncio.Task | None = None
        if settings.esports_tracker_enabled:
            from esports.tracker import run as run_esports_tracker
            tracker_task = asyncio.create_task(
                run_esports_tracker(settings.esports_tracker_cycle_seconds)
            )
            log.info("esports tracker task started (in-process)")
        try:
            yield
        finally:
            if tracker_task is not None:
                tracker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tracker_task


app = FastAPI(
    title="Polymarket Smart Money Tracker",
    version="0.9.0",
    lifespan=lifespan,
)

# Permissive CORS — this is a single-user local tool, not internet-facing.
# Tighten if/when deployed to Railway with a known frontend origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(traders.router)
app.include_router(signals.router)
app.include_router(markets.router)
app.include_router(backtest.router)
app.include_router(paper_trades.router)
app.include_router(insider.router)
app.include_router(watchlist.router)
app.include_router(esports.router)

# BIG STOCK desk — a fully isolated sibling module (own SQLite, own router).
# It imports nothing from app.*; these two lines are the ONLY coupling. Guarded
# so a desk-side import error can never take down the Polymarket API.
try:
    from desk.api import router as desk_router

    app.include_router(desk_router)
except Exception:  # pragma: no cover - desk is optional, never block the core app
    log.exception("BIG STOCK desk failed to load; continuing without it")

# Serve the UI at /ui/* from the same FastAPI process so a single `uvicorn`
# command boots backend + scheduler + UI. html=True makes /ui/ resolve to
# /ui/index.html so the user can navigate to /ui directly.
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

# Serve the BIG STOCK desk UI at /desk/* from the same process. html=True makes
# /desk/ resolve to /desk/index.html.
if DESK_UI_DIR.exists():
    app.mount("/desk", StaticFiles(directory=str(DESK_UI_DIR), html=True), name="desk")


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """Service identity probe (curl/health checks). Browser visits get
    redirected to the UI for convenience — `Accept: text/html` is the tell."""
    if "text/html" in request.headers.get("accept", "") and UI_DIR.exists():
        return RedirectResponse(url="/ui/")
    return {"app": "polymarket-smart-money", "version": "0.9.0", "docs": "/docs"}
