"""APScheduler glue — registers our jobs and runs them on a cadence.

Cadences (UTC):
  - 02:00 daily          : daily_leaderboard_snapshot
  - every 10 min          : refresh_positions_then_log_signals (sequential — log
                            needs fresh positions)
  - on startup            : catch_up_snapshot_if_stale (laptop-was-off case)

Jobstore: in-memory. Jobs are defined in code, not added at runtime, so we
don't need persistence — and we already handle the "missed daily snapshot"
case explicitly via catch_up_snapshot_if_stale. Easy to swap to
SQLAlchemyJobStore later if requirements change.

Misfire policy:
  - position refresh : grace 60s, coalesce True. If we missed a tick (laptop
    asleep), don't replay every missed run on resume — just run once.
  - daily snapshot   : grace 86400s (1 day), coalesce True. If we missed today,
    catch_up_snapshot_if_stale already filled the gap on startup, but the grace
    means a *next-day* misfire still fires once if startup was slow.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.scheduler.jobs import (
    catch_up_snapshot_if_stale,
    classify_tracked_wallets,
    compute_trader_category_stats,
    daily_leaderboard_snapshot,
    detect_sybil_clusters_in_pool,
    heal_unavailable_signal_books,
    record_signal_price_snapshots,
    refresh_positions_then_log_signals,
)

log = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    """Build and configure the scheduler. Caller starts/stops it."""
    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,  # never run two of the same job concurrently
        },
    )

    scheduler.add_job(
        refresh_positions_then_log_signals,
        trigger=IntervalTrigger(minutes=10),
        id="refresh_and_log",
        name="Refresh positions + log signals",
        misfire_grace_time=60,
        replace_existing=True,
    )

    scheduler.add_job(
        daily_leaderboard_snapshot,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="daily_snapshot",
        name="Daily leaderboard snapshot",
        misfire_grace_time=86_400,
        replace_existing=True,
    )

    # B5: nightly per-category stats refresh. Runs after the daily snapshot
    # so the leaderboard PnL/Volume it pulls is the freshest possible.
    scheduler.add_job(
        compute_trader_category_stats,
        trigger=CronTrigger(hour=2, minute=30, timezone="UTC"),
        id="daily_trader_stats",
        name="Nightly trader_category_stats refresh",
        misfire_grace_time=86_400,
        replace_existing=True,
    )

    # Weekly: re-classify wallets (catches new MMs/arbs entering the pool)
    # and re-detect sybil clusters. Run early Monday UTC to spread load away
    # from the daily snapshot. Both are heavy-ish (~1 minute each) so we
    # offset them so they don't run concurrently.
    scheduler.add_job(
        classify_tracked_wallets,
        trigger=CronTrigger(day_of_week="mon", hour=3, minute=0, timezone="UTC"),
        id="weekly_classify",
        name="Weekly wallet classification",
        misfire_grace_time=86_400,
        replace_existing=True,
    )
    scheduler.add_job(
        detect_sybil_clusters_in_pool,
        trigger=CronTrigger(day_of_week="mon", hour=3, minute=15, timezone="UTC"),
        id="weekly_sybil",
        name="Weekly sybil cluster detection",
        misfire_grace_time=86_400,
        replace_existing=True,
    )

    # B4/F4/F7: every 10 min, capture bid + ask at +5/15/30/60/120 min
    # after each signal's first fire. Cadence dropped from 30 min to 10 min
    # so the +5 offset (added in F7) is reliably captured (signal needs to
    # be evaluated when its age is in [0, 10] min — 10-min cadence ensures
    # at least one tick falls in that window).
    scheduler.add_job(
        record_signal_price_snapshots,
        trigger=IntervalTrigger(minutes=10),
        id="signal_price_snapshots",
        name="B4/F4/F7 +5/15/30/60/120 min bid+ask snapshots",
        misfire_grace_time=300,
        replace_existing=True,
    )

    # Heal job: retry CLOB book capture for signals stuck on
    # signal_entry_source='unavailable'. 30-min cadence keeps Polymarket API
    # load low while still draining the pool over time. Each row that heals
    # leaves the candidate set, so cost is naturally bounded.
    scheduler.add_job(
        heal_unavailable_signal_books,
        trigger=IntervalTrigger(minutes=30),
        id="heal_unavailable_books",
        name="Retry book capture for signal_entry_source='unavailable' rows",
        misfire_grace_time=600,
        replace_existing=True,
    )

    return scheduler


async def run_forever() -> None:
    """Top-level entry: catch up, start scheduler, block forever.

    Used by `scripts/run_scheduler.py`. Cancellation via KeyboardInterrupt
    triggers a graceful shutdown.
    """
    log.info("running startup catch-up snapshot check...")
    try:
        await catch_up_snapshot_if_stale()
    except Exception as e:  # noqa: BLE001
        log.warning("catch-up snapshot failed (continuing): %s", e)

    scheduler = build_scheduler()
    scheduler.start()
    _log_jobs(scheduler)

    # Same startup catch-up as the FastAPI lifespan path.
    asyncio.create_task(_startup_position_refresh())

    try:
        # Block until cancelled. asyncio.Event() that's never set is the
        # idiomatic way to keep a scheduler process alive.
        await asyncio.Event().wait()
    finally:
        log.info("shutting down scheduler...")
        scheduler.shutdown(wait=True)


@asynccontextmanager
async def lifespan_scheduler() -> AsyncIterator[AsyncIOScheduler]:
    """Async context manager for FastAPI's `lifespan=` hook (Step 9).

    Usage:
        @asynccontextmanager
        async def lifespan(app):
            async with lifespan_scheduler():
                yield
    """
    try:
        await catch_up_snapshot_if_stale()
    except Exception as e:  # noqa: BLE001
        log.warning("catch-up snapshot failed (continuing): %s", e)

    scheduler = build_scheduler()
    scheduler.start()
    _log_jobs(scheduler)

    # Startup catch-up for the 10-min position refresh: APScheduler waits a full
    # interval after start before first fire, so after a sleep+resume or restart
    # data stays stale up to 10 minutes. Kick a one-shot run in the background
    # so the API is reachable immediately and positions refresh in parallel.
    # `max_instances=1` on the job prevents racing the next scheduled tick.
    asyncio.create_task(_startup_position_refresh())

    try:
        yield scheduler
    finally:
        log.info("shutting down scheduler (lifespan exit)...")
        scheduler.shutdown(wait=False)


async def _startup_position_refresh() -> None:
    """One-shot at lifespan startup so positions are fresh immediately,
    not after waiting a full 10-min interval. Errors are logged and swallowed
    -- the next scheduled tick will retry."""
    try:
        log.info("startup position-refresh + signal log kicking off...")
        await refresh_positions_then_log_signals()
        log.info("startup position-refresh complete")
    except Exception as e:  # noqa: BLE001
        log.warning("startup position-refresh failed (next tick will retry): %s", e)


def _log_jobs(scheduler: AsyncIOScheduler) -> None:
    log.info("scheduler started — jobs:")
    for job in scheduler.get_jobs():
        log.info("  %s | next_run=%s | trigger=%s",
                 job.id, job.next_run_time, job.trigger)
