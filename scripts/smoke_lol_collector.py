"""Smoke test for the LoL Polymarket data collector V1.1.

Verifies both tier-1 (active) and tier-2 (watcher) snapshot paths end-to-end
without depending on the scheduler being live.

Usage (from project root):
    ./venv/Scripts/python.exe scripts/smoke_lol_collector.py
"""

from __future__ import annotations

import asyncio
import logging

from app.db import crud
from app.db.connection import init_pool
from app.scheduler.jobs import (
    discover_lol_markets_job,
    snapshot_lol_prices_active_job,
    snapshot_lol_prices_watcher_job,
)

log = logging.getLogger("smoke_lol_collector")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    print("=" * 70)
    print("LoL collector V1.1 smoke test")
    print("=" * 70)

    print("\n[1/4] Running discovery + classification (captures start_time)...")
    disc = await discover_lol_markets_job()
    print(
        f"      events_seen={disc.events_seen} "
        f"markets_seen={disc.markets_seen} "
        f"classified={disc.markets_classified} "
        f"({disc.duration_seconds:.1f}s)"
    )

    print("\n[2/4] Counting tier membership...")
    pool = await init_pool(min_size=1, max_size=2)
    async with pool.acquire() as conn:
        active_count = len(await crud.list_lol_markets_active_tier(conn))
        watcher_count = len(await crud.list_lol_markets_watcher_tier(conn))
        active_sample = await conn.fetch(
            """
            SELECT mm.team_a, mm.team_b, mm.market_kind, mm.game_number,
                   mm.league, e.start_time, e.end_date
            FROM polymarket_lol_market_meta mm
            JOIN markets m ON m.condition_id = mm.condition_id
            LEFT JOIN events e ON e.id = mm.event_id
            WHERE m.closed = FALSE
              AND e.start_time IS NOT NULL
              AND e.start_time BETWEEN NOW() - INTERVAL '6 hours'
                                   AND NOW() + INTERVAL '30 minutes'
            ORDER BY e.start_time
            LIMIT 8
            """
        )
    print(f"      tier-1 active markets:  {active_count}")
    print(f"      tier-2 watcher markets: {watcher_count}")
    print("      sample tier-1 candidates (by start_time window):")
    for row in active_sample:
        gn = f"G{row['game_number']}" if row["game_number"] else "series"
        print(
            f"        - {row['team_a']} vs {row['team_b']} | {gn} | "
            f"{row['league']} | start={row['start_time']}"
        )

    print("\n[3/4] Running tier-1 (active 20s) snapshot tick...")
    snap_active = await snapshot_lol_prices_active_job()
    print(
        f"      attempted={snap_active.markets_attempted} "
        f"written={snap_active.snapshots_written} "
        f"failures={snap_active.failures} "
        f"({snap_active.duration_seconds:.1f}s)"
    )

    print("\n[4/4] Running tier-2 (watcher 5min) snapshot tick...")
    snap_watcher = await snapshot_lol_prices_watcher_job()
    print(
        f"      attempted={snap_watcher.markets_attempted} "
        f"written={snap_watcher.snapshots_written} "
        f"failures={snap_watcher.failures} "
        f"({snap_watcher.duration_seconds:.1f}s)"
    )

    print("\n[verify] Recent snapshot rows:")
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_price_snapshots"
        )
        recent_5m = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_price_snapshots WHERE captured_at > NOW() - INTERVAL '5 minutes'"
        )
    print(f"      total all-time: {total}")
    print(f"      last 5 minutes: {recent_5m}")

    print("\nSmoke test done.")


if __name__ == "__main__":
    asyncio.run(main())
