"""Look at the actual /positions records that get dropped — inspect their
embedded metadata to figure out what state they're in.

Polymarket's /positions response carries extra fields the gamma /markets
endpoint may have purged: `endDate`, `redeemable`, `title`, etc. Reading
those tells us whether dropped positions are (a) on resolved markets the
trader hasn't claimed yet, (b) on tiny illiquid markets, or (c) something
unexpected.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.services.polymarket import PolymarketClient  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("diag")
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
            all_dropped_records: list = []
            for w in wallets:
                positions = await pm.get_positions(w, limit=500)
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
                # Try JIT — anything gamma still returns is recoverable
                fetched = await pm.get_markets_by_condition_ids(missing_cids)
                gamma_returned = {m.condition_id for m in fetched}
                # The truly-dropped ones are missing AND gamma-silent
                truly_dropped = [
                    p for p in positions
                    if p.condition_id
                    and p.condition_id not in in_db
                    and p.condition_id not in gamma_returned
                ]
                all_dropped_records.extend(truly_dropped)
                log.info(
                    "wallet %s: %d positions, %d truly dropped",
                    w[:12], len(positions), len(truly_dropped),
                )

        # Inspect the dropped records
        print("\n" + "=" * 72)
        print("STATE OF SILENTLY-DROPPED POSITIONS")
        print("=" * 72)
        print(f"  total samples : {len(all_dropped_records)}")

        # Aggregate `redeemable`, `mergeable`, sizes, end-dates
        n = len(all_dropped_records)
        if n == 0:
            return
        redeemable_count = sum(1 for p in all_dropped_records if getattr(p, "redeemable", False))
        # Pull from raw — the dataclass may not surface every field
        raw_keys: Counter = Counter()
        end_date_present = 0
        end_date_past = 0
        is_closed_in_raw = 0
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        size_sum = 0.0
        size_n = 0

        sample_titles: list[str] = []
        for p in all_dropped_records:
            raw = getattr(p, "raw", {}) or {}
            for k in raw.keys():
                raw_keys[k] += 1
            if raw.get("endDate"):
                end_date_present += 1
                try:
                    ed = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00"))
                    if ed < now:
                        end_date_past += 1
                except (ValueError, TypeError):
                    pass
            if raw.get("closed") is True:
                is_closed_in_raw += 1
            size = p.size if p.size else 0
            if size:
                size_sum += float(size)
                size_n += 1
            title = raw.get("title") or raw.get("eventTitle")
            if title and len(sample_titles) < 10:
                sample_titles.append(title)

        print(f"  redeemable=True              : {redeemable_count}")
        print(f"  raw['closed']=True           : {is_closed_in_raw}")
        print(f"  raw['endDate'] present       : {end_date_present}")
        print(f"  raw['endDate'] in the past   : {end_date_past}")
        print(f"  avg position size            : {size_sum/max(1,size_n):.2f} (over {size_n} non-zero rows)")
        print()
        print(f"  /positions raw keys (count of rows containing each key):")
        for k, v in raw_keys.most_common(30):
            print(f"    {k:40s} {v:5d}")
        print()
        print(f"  Sample titles of dropped positions:")
        for t in sample_titles:
            print(f"    - {t[:80]}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
