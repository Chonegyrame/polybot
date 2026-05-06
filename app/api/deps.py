"""FastAPI dependency injectors — DB connection per request."""

from __future__ import annotations

from typing import AsyncIterator

import asyncpg

from app.db.connection import init_pool


async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """Yield an asyncpg connection from the shared pool, returning it on exit."""
    pool = await init_pool()
    async with pool.acquire() as conn:
        yield conn
