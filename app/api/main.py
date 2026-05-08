"""FastAPI app — the HTTP face of the smart-money tracker.

Boots the in-process scheduler via the lifespan hook so a single `uvicorn`
command runs both the API and the 10-min refresh / signal-logging jobs.

Run locally:
    ./venv/Scripts/python.exe -m uvicorn app.api.main:app --reload
"""

from __future__ import annotations

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
    insider,
    markets,
    paper_trades,
    signals,
    system,
    traders,
    watchlist,
)
from app.scheduler.runner import lifespan_scheduler

UI_DIR = Path(__file__).resolve().parent.parent.parent / "ui"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot the scheduler alongside the API. Stops cleanly on shutdown."""
    async with lifespan_scheduler():
        yield


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

# Serve the UI at /ui/* from the same FastAPI process so a single `uvicorn`
# command boots backend + scheduler + UI. html=True makes /ui/ resolve to
# /ui/index.html so the user can navigate to /ui directly.
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """Service identity probe (curl/health checks). Browser visits get
    redirected to the UI for convenience — `Accept: text/html` is the tell."""
    if "text/html" in request.headers.get("accept", "") and UI_DIR.exists():
        return RedirectResponse(url="/ui/")
    return {"app": "polymarket-smart-money", "version": "0.9.0", "docs": "/docs"}
