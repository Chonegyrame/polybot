"""Probe gamma directly for one of the silently-dropped cids to find out
why it doesn't come back via /markets?condition_ids=...
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.services.polymarket import PolymarketClient  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("probe")
log.setLevel(logging.INFO)


async def main() -> None:
    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            wallets = [
                r["proxy_wallet"] for r in await conn.fetch(
                    """
                    SELECT proxy_wallet
                    FROM leaderboard_snapshots
                    WHERE category = 'overall' AND time_period = 'all'
                      AND order_by = 'PNL'
                      AND snapshot_date = (SELECT MAX(snapshot_date)
                                           FROM leaderboard_snapshots)
                    ORDER BY rank ASC
                    LIMIT 5
                    """
                )
            ]

        async with PolymarketClient() as pm:
            # Find one dropped cid + its raw record
            for w in wallets:
                positions = await pm.get_positions(w, limit=500)
                if not positions:
                    continue
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT condition_id FROM markets "
                        "WHERE condition_id = ANY($1::TEXT[])",
                        [p.condition_id for p in positions if p.condition_id],
                    )
                in_db = {r["condition_id"] for r in rows}
                missing_cids = [
                    p.condition_id for p in positions
                    if p.condition_id and p.condition_id not in in_db
                ]
                if not missing_cids:
                    continue
                fetched = await pm.get_markets_by_condition_ids(missing_cids)
                gamma_returned = {m.condition_id for m in fetched}
                truly_dropped = [
                    p for p in positions
                    if p.condition_id
                    and p.condition_id not in in_db
                    and p.condition_id not in gamma_returned
                ]
                if not truly_dropped:
                    continue

                target = truly_dropped[0]
                print(f"\nProbing dropped cid: {target.condition_id}")
                print(f"  /positions raw record:")
                raw = getattr(target, "raw", {}) or {}
                for k, v in raw.items():
                    print(f"    {k:30s} {repr(v)[:120]}")
                print()

                # 1. Direct gamma /markets with single cid (not batched)
                async with httpx.AsyncClient(timeout=30) as cx:
                    print("  -- Probe 1: gamma /markets?condition_ids={cid} (single)")
                    r = await cx.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"condition_ids": target.condition_id, "limit": 1},
                    )
                    print(f"    status={r.status_code}, body length={len(r.text)}")
                    print(f"    body[:500]={r.text[:500]}")
                    print()

                    # 2. Gamma with closed=true (maybe it's marked closed in gamma?)
                    print("  -- Probe 2: gamma /markets?condition_ids={cid}&closed=true")
                    r = await cx.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"condition_ids": target.condition_id, "closed": "true", "limit": 1},
                    )
                    print(f"    status={r.status_code}, body length={len(r.text)}")
                    print(f"    body[:500]={r.text[:500]}")
                    print()

                    # 3. Try by gamma marketId — /positions has `asset` and `conditionId`
                    # but no gamma id; the asset is the CLOB token id.
                    print("  -- Probe 3: gamma /events?slug={eventSlug}")
                    if raw.get("eventSlug"):
                        r = await cx.get(
                            "https://gamma-api.polymarket.com/events",
                            params={"slug": raw["eventSlug"], "limit": 1},
                        )
                        print(f"    status={r.status_code}, body length={len(r.text)}")
                        if r.status_code == 200:
                            data = r.json()
                            if isinstance(data, list) and data:
                                ev = data[0]
                                print(f"    event title : {ev.get('title')}")
                                print(f"    event closed: {ev.get('closed')}")
                                # Look for our cid in the event's markets
                                ev_markets = ev.get("markets") or []
                                hit = next((m for m in ev_markets if m.get("conditionId") == target.condition_id), None)
                                if hit:
                                    print(f"    cid IS embedded in event response:")
                                    print(f"      conditionId : {hit.get('conditionId')}")
                                    print(f"      closed      : {hit.get('closed')}")
                                    print(f"      active      : {hit.get('active')}")
                                    print(f"      archived    : {hit.get('archived')}")
                                    print(f"      enableOrderBook : {hit.get('enableOrderBook')}")
                                    print(f"      negRisk     : {hit.get('negRisk')}")
                                else:
                                    print(f"    cid NOT in event's markets array (event has {len(ev_markets)} markets)")

                return  # stop after first dropped cid

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
