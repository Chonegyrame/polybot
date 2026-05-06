"""FastAPI app — the HTTP face of the smart-money tracker.

Boots the in-process scheduler via the lifespan hook so a single `uvicorn`
command runs both the API and the 10-min refresh / signal-logging jobs.

Run locally:
    ./venv/Scripts/python.exe -m uvicorn app.api.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": "polymarket-smart-money", "version": "0.9.0", "docs": "/docs"}
