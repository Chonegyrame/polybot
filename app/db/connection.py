"""asyncpg connection pool for Supabase Postgres.

Single global pool, lazy-initialized on first call. The pool reads DATABASE_URL
from env. Caller is expected to use `async with get_pool().acquire() as conn`
or one of the helpers in `app.db.crud`.
"""

from __future__ import annotations

import logging
import zlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(min_size: int = 1, max_size: int = 12) -> asyncpg.Pool:
    """Initialize the global pool. Idempotent — returns existing pool if set."""
    global _pool
    if _pool is not None:
        return _pool
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add the Supabase Postgres connection string "
            "to .env (Project Settings -> Database -> Connection string)."
        )
    log.info("opening asyncpg pool (min=%d max=%d)", min_size, max_size)
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        log.info("closing asyncpg pool")
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool not initialized. Call init_pool() first.")
    return _pool


async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Convenience: yields a connection from the pool."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


def _lock_id(job_name: str) -> int:
    """Stable 32-bit signed int derived from a job name, for pg_advisory_lock.

    pg_advisory_lock takes int8 (or two int4s). Using zlib.crc32 keeps the id
    stable across Python versions/processes — `hash()` is randomized per
    interpreter run via PYTHONHASHSEED, which would let two runs treat the
    same job name as different locks (defeating the point).
    """
    # crc32 returns 0..2^32-1; subtract to fit Postgres int4 range
    return zlib.crc32(job_name.encode("utf-8")) - (1 << 31)


@asynccontextmanager
async def job_lock(job_name: str) -> AsyncIterator[bool]:
    """Postgres session-scope advisory lock keyed on job_name.

    Yields True if we acquired it, False if another holder has it. Use to
    serialize long-running jobs across processes (e.g. APScheduler running
    `refresh_positions_then_log_signals` while the user manually triggers
    `scripts/run_position_refresh.py` — both could otherwise race against
    the same DB writes).

    Holds one pool connection for the lifetime of the lock; the job body
    should still acquire its own connections from the pool for actual work.
    Release is automatic on context exit. With pool size 12 the held
    connection is well within budget.

    Usage:
        async with job_lock("refresh_positions") as got:
            if not got:
                log.info("another worker holds the lock — skipping")
                return
            ... do the work ...
    """
    pool = await init_pool()
    lid = _lock_id(job_name)
    async with pool.acquire() as conn:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lid)
        if not got:
            yield False
            return
        try:
            yield True
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", lid)
            except Exception as e:  # noqa: BLE001 — release is best-effort
                log.warning("advisory unlock failed for %s: %r", job_name, e)
