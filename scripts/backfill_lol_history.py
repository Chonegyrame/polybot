"""Historical backfill of resolved Polymarket LoL markets.

Sweeps gamma-api with `closed=true` filter for tag_slug in ("league-of-legends",
"lol"), persists every event + market + LoL classification, harvesting the
closing-line scalar fields (lastTradePrice, bestBid, bestAsk, volumeNum,
closedTime) into polymarket_lol_market_meta in the same pass.

We don't pull clob/prices-history — verified empirically (2026-05-14) to
return empty for resolved markets. The free gamma fields are the only
post-resolution price information Polymarket exposes.

Usage:
    PYTHONPATH=. ./venv/Scripts/python.exe scripts/backfill_lol_history.py

Idempotent — re-running is safe (all writes are upserts).
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.db import crud
from app.db.connection import init_pool
from app.services.polymarket import PolymarketClient
from app.services.polymarket_lol import discover_lol_events_and_classify

log = logging.getLogger("backfill_lol_history")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    started = time.monotonic()

    print("=" * 70)
    print("Polymarket LoL historical backfill")
    print("=" * 70)
    print(
        "Sweeping gamma-api closed=true for tag_slug in (league-of-legends, lol)..."
    )
    print(
        "Will harvest metadata + outcome + closing-line scalars in one pass.\n"
    )

    pool = await init_pool(min_size=1, max_size=2)

    # Initial state for the delta print
    async with pool.acquire() as conn:
        before_total = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_market_meta"
        )
        before_resolved = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_market_meta WHERE market_closed = TRUE"
        )

    print(f"[before] total meta rows={before_total}, resolved={before_resolved}\n")

    async with PolymarketClient() as pm:
        async with pool.acquire() as conn:
            # include_closed=True flips the gamma `closed` filter from
            # False to None, which means "return both open and closed
            # markets." For backfill we specifically want the closed
            # ones, but including open in the sweep is harmless (they're
            # also picked up by the regular discovery job).
            events_seen, markets_seen, classified = (
                await discover_lol_events_and_classify(
                    conn, pm,
                    page_size=100,
                    max_pages_per_tag=200,   # safety bound; LoL history < ~10k events
                    include_closed=True,
                )
            )

    duration = time.monotonic() - started

    async with pool.acquire() as conn:
        after_total = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_market_meta"
        )
        after_resolved = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_market_meta WHERE market_closed = TRUE"
        )
        with_close_line = await conn.fetchval(
            "SELECT COUNT(*) FROM polymarket_lol_market_meta WHERE last_trade_price IS NOT NULL"
        )
        per_league = await conn.fetch(
            """
            SELECT league, COUNT(*) AS n
            FROM polymarket_lol_market_meta
            WHERE market_closed = TRUE
            GROUP BY league
            ORDER BY n DESC
            LIMIT 15
            """
        )
        oldest = await conn.fetchrow(
            """
            SELECT MIN(closed_time) AS oldest, MAX(closed_time) AS newest
            FROM polymarket_lol_market_meta
            WHERE closed_time IS NOT NULL
            """
        )

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    print(f"Duration: {duration / 60:.1f} min")
    print(
        f"This sweep: events={events_seen}, markets_seen={markets_seen}, classified={classified}"
    )
    print(
        f"\n[after] total meta rows={after_total} (+{after_total - before_total}), "
        f"resolved={after_resolved} (+{after_resolved - before_resolved})"
    )
    print(f"        rows with last_trade_price captured: {with_close_line}")
    if oldest and oldest["oldest"]:
        # ASCII-only output — Windows cp1252 stdout doesn't handle the arrow char.
        print(
            f"        resolved-market date range: {oldest['oldest']} -> {oldest['newest']}"
        )
    print("\nResolved markets by league (top 15):")
    for row in per_league:
        print(f"  {row['n']:>5}  {row['league']}")


if __name__ == "__main__":
    asyncio.run(main())
