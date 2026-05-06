"""Detect sybil clusters across tracked wallets via time-correlation.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_sybil_detection.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.scheduler.jobs import detect_sybil_clusters_in_pool  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await detect_sybil_clusters_in_pool()

        # Print details on each cluster found.
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT wc.cluster_id, wc.evidence,
                       array_agg(cm.proxy_wallet ORDER BY cm.proxy_wallet) AS members
                FROM wallet_clusters wc
                JOIN cluster_membership cm USING (cluster_id)
                WHERE wc.detection_method = 'time_correlation'
                GROUP BY wc.cluster_id, wc.evidence
                ORDER BY (wc.evidence->>'n_members')::int DESC
            """)
        if rows:
            print("\n=== detected clusters ===")
            for r in rows:
                ev = r["evidence"]
                print(f"\ncluster {str(r['cluster_id'])[:8]}  "
                      f"({ev.get('n_members')} wallets, "
                      f"mean rate={ev.get('mean_co_entry_rate'):.0%}):")
                for m in r["members"]:
                    print(f"    {m}")
    finally:
        await close_pool()

    print(
        f"\n=== sybil detection complete ===\n"
        f"  wallets analyzed   : {result.wallets_analyzed}\n"
        f"  clusters found     : {result.clusters_found}\n"
        f"  wallets in clusters: {result.members_in_clusters}\n"
        f"  duration           : {result.duration_seconds:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
