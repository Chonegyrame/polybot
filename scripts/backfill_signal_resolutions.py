"""Backfill real resolutions for fired-signal markets.

Why: the incremental market sync only pages `closed=false` events, so markets
that fired a consensus signal and have since resolved on Polymarket stay stuck
at `resolved_outcome=NULL` in our DB. The backtest joins on
`markets.resolved_outcome`, so ~90% of fired signals are invisible to it. This
fetches those markets with `closed=true`, infers the resolution, and records it
via a targeted update (does NOT disturb event_id/slug/etc).

Re-runnable and idempotent. Run:
    python -m scripts.backfill_signal_resolutions
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

from app.db import crud
from app.db.connection import close_pool, init_pool
from app.services.market_sync import _infer_resolved_outcome
from app.services.polymarket import PolymarketClient

logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    pool = await init_pool()
    async with pool.acquire() as conn:
        cids = await crud.list_unresolved_signal_condition_ids(conn)
    print(f"unresolved signal markets to check: {len(cids)}")
    if not cids:
        print("nothing to backfill.")
        await close_pool()
        return

    async with PolymarketClient() as pm:
        markets = await pm.get_markets_by_condition_ids(cids, closed=True)
    print(f"gamma returned {len(markets)} closed markets for those cids")

    outcomes: Counter = Counter()
    updated = 0
    not_closed = 0
    async with pool.acquire() as conn:
        for m in markets:
            res = _infer_resolved_outcome(m)
            if res is None:
                not_closed += 1  # gamma still has it open / unrecognized shape
                continue
            outcomes[res] += 1
            if await crud.set_market_resolution(conn, m.condition_id, res):
                updated += 1

    print(f"resolutions inferred: {dict(outcomes)}")
    print(f"rows updated: {updated}   (returned-but-not-resolvable: {not_closed})")

    async with pool.acquire() as conn:
        remaining = len(await crud.list_unresolved_signal_condition_ids(conn))
        # how many fired signals are now settleable (YES/NO/50_50)?
        settleable = await conn.fetchval(
            """
            SELECT COUNT(*) FROM signal_log s
            JOIN markets m ON m.condition_id = s.condition_id
            WHERE m.resolved_outcome IN ('YES','NO','50_50')
            """
        )
    print(f"signal markets still unresolved: {remaining}")
    print(f"fired signals now SETTLEABLE (YES/NO/50_50): {settleable}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
