"""Targeted backfill: refetch start_time for every event we've classified in
polymarket_lol_market_meta but whose events.start_time is currently NULL.

Cheaper than re-running the full historical backfill — we only re-fetch the
events we already know about (~5000), in batches of 50 via gamma's repeated-
key id-filter. ~10 minutes runtime, idempotent.

Usage:
    PYTHONPATH=. ./venv/Scripts/python.exe scripts/backfill_lol_start_times.py
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.db import crud
from app.db.connection import init_pool
from app.services.market_sync import _derive_category, _parse_iso
from app.services.polymarket import PolymarketClient


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    log = logging.getLogger("backfill_lol_start_times")
    started = time.monotonic()

    pool = await init_pool(min_size=1, max_size=2)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT mm.event_id
            FROM polymarket_lol_market_meta mm
            JOIN events e ON e.id = mm.event_id
            WHERE e.start_time IS NULL
            """
        )
    event_ids = [r["event_id"] for r in rows if r["event_id"]]

    print(f"events to refetch for start_time: {len(event_ids)}")
    if not event_ids:
        print("nothing to do, all classified events already have start_time")
        return

    updated = 0
    not_found = 0
    no_start_time = 0

    async with PolymarketClient() as pm:
        events = await pm.get_events_by_ids(event_ids, batch_size=50)
        log.info("gamma returned %d / %d requested events", len(events), len(event_ids))
        async with pool.acquire() as conn:
            async with conn.transaction():
                for ev in events:
                    start_time_dt = _parse_iso(ev.start_time)
                    if start_time_dt is None:
                        no_start_time += 1
                    await crud.upsert_event(
                        conn,
                        event_id=ev.id,
                        slug=ev.slug,
                        title=ev.title,
                        category=_derive_category(ev.tags, fallback=ev.category),
                        tags=ev.tags,
                        start_time=start_time_dt,
                        end_date=_parse_iso(ev.end_date),
                        closed=ev.closed,
                    )
                    if start_time_dt is not None:
                        updated += 1
        not_found = len(event_ids) - len(events)

    async with pool.acquire() as conn:
        with_start = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT e.id)
            FROM polymarket_lol_market_meta mm
            JOIN events e ON e.id = mm.event_id
            WHERE e.start_time IS NOT NULL
            """
        )
        without_start = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT e.id)
            FROM polymarket_lol_market_meta mm
            JOIN events e ON e.id = mm.event_id
            WHERE e.start_time IS NULL
            """
        )

    duration = time.monotonic() - started
    print()
    print(f"Duration: {duration:.1f}s")
    print(f"start_time populated: {updated}")
    print(f"event returned with NULL startTime (gamma omitted): {no_start_time}")
    print(f"event_id not found in gamma response: {not_found}")
    print()
    print(f"[final] LoL events with start_time: {with_start}")
    print(f"[final] LoL events still NULL start_time: {without_start}")


if __name__ == "__main__":
    asyncio.run(main())
