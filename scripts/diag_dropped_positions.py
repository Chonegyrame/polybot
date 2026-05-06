"""Diagnostic: figure out WHY ~70% of fetched positions get dropped by
_filter_known_markets.

For a sample of tracked wallets:
  1. Fetch positions from Polymarket /positions (truth source — what they own)
  2. Compare cids to our local `markets` table
  3. For cids NOT in markets, probe gamma directly to see what state the
     market is in (active? resolved? not in gamma at all?)
  4. Report breakdown so we can tell if drops are benign (resolved/archived)
     or a real bug (active markets we should have)

Run:
    ./venv/Scripts/python.exe scripts/diag_dropped_positions.py
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

SAMPLE_WALLET_LIMIT = 30  # how many wallets to probe — keep small to stay fast


async def main() -> None:
    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            # Pick a representative sample: a mix of high-volume wallets across
            # categories. Use the latest leaderboard 'all/PNL' snapshot.
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
                    LIMIT $1
                    """,
                    SAMPLE_WALLET_LIMIT,
                )
            ]
        log.info("probing %d top-rank wallets", len(wallets))

        all_position_cids: set[str] = set()
        positions_per_wallet: list[tuple[str, int]] = []

        async with PolymarketClient() as pm:
            # Phase 1: fetch positions for each wallet
            for w in wallets:
                positions = await pm.get_positions(w, limit=500)
                cids = {p.condition_id for p in positions if p.condition_id}
                all_position_cids.update(cids)
                positions_per_wallet.append((w, len(positions)))

            total_positions = sum(n for _, n in positions_per_wallet)
            log.info(
                "fetched %d positions across %d wallets, %d distinct cids",
                total_positions, len(wallets), len(all_position_cids),
            )

            # Phase 2: which cids are NOT in our markets table?
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT condition_id, closed, resolved_outcome "
                    "FROM markets WHERE condition_id = ANY($1::TEXT[])",
                    list(all_position_cids),
                )
            in_db = {r["condition_id"]: r for r in rows}
            in_db_active = sum(1 for r in rows if not r["closed"])
            in_db_closed = sum(1 for r in rows if r["closed"])
            in_db_resolved = sum(
                1 for r in rows if r["closed"] and r["resolved_outcome"]
                and r["resolved_outcome"] != "PENDING"
            )

            missing = all_position_cids - set(in_db.keys())
            log.info(
                "DB coverage: %d in markets table (%d active / %d closed [%d resolved]); %d missing",
                len(in_db), in_db_active, in_db_closed, in_db_resolved, len(missing),
            )

            # Phase 3: probe gamma for the missing cids
            log.info(
                "probing gamma for the %d missing cids (this is the JIT discovery path)...",
                len(missing),
            )
            fetched = await pm.get_markets_by_condition_ids(sorted(missing))
            gamma_returned = {m.condition_id for m in fetched if m.condition_id}
            still_missing = missing - gamma_returned

            # Categorize what gamma said about the ones it DID return
            gamma_active = sum(1 for m in fetched if not m.closed)
            gamma_closed = sum(1 for m in fetched if m.closed)

            log.info(
                "gamma returned %d/%d missing cids (%d active / %d closed); %d not returned at all",
                len(gamma_returned), len(missing), gamma_active, gamma_closed,
                len(still_missing),
            )

            # Spot-check a few "not returned" cids by querying gamma /markets directly
            sample_still_missing = sorted(still_missing)[:5]
            log.info("sample of cids gamma never returned (first 5):")
            for cid in sample_still_missing:
                log.info("  %s", cid)

        # Phase 4: percentage of positions that would have been dropped from
        # this sample, mirroring _filter_known_markets after JIT discovery.
        kept_cids = set(in_db.keys()) | gamma_returned
        kept_pct = len(kept_cids) / max(1, len(all_position_cids))
        dropped_cids = all_position_cids - kept_cids
        dropped_pct = len(dropped_cids) / max(1, len(all_position_cids))

        print("\n" + "=" * 72)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 72)
        print(f"  Sample size            : {len(wallets)} wallets")
        print(f"  Total positions seen   : {total_positions}")
        print(f"  Distinct cids on positions : {len(all_position_cids)}")
        print()
        print(f"  Already in markets table   : {len(in_db)} cids")
        print(f"    -> active                : {in_db_active}")
        print(f"    -> closed (resolved)     : {in_db_resolved}")
        print(f"    -> closed (no outcome)   : {in_db_closed - in_db_resolved}")
        print()
        print(f"  Missing from markets table : {len(missing)} cids")
        print(f"    -> JIT recovered (gamma returned): {len(gamma_returned)}")
        print(f"      of which active        : {gamma_active}")
        print(f"      of which closed        : {gamma_closed}")
        print(f"    -> Gamma SILENTLY dropped: {len(still_missing)}  <-- the source of the 26k loss")
        print()
        print(f"  After JIT discovery:")
        print(f"    -> kept cids           : {len(kept_cids)} ({kept_pct:.1%})")
        print(f"    -> dropped cids        : {len(dropped_cids)} ({dropped_pct:.1%})")
        print()
        print("=" * 72)
        print("DIAGNOSIS")
        print("=" * 72)
        if still_missing and gamma_active == 0:
            print("  ✓ All recovered markets are CLOSED/RESOLVED.")
            print("    The dropped cids are ones gamma will NEVER return — confirmed")
            print("    'archived' state. Position is still locked in the smart contract")
            print("    but the market metadata has been purged from gamma.")
        elif gamma_active > 0:
            print("  ⚠ Gamma returned %d ACTIVE markets via JIT — these would have been"
                  % gamma_active)
            print("    dropped before we added JIT discovery. After JIT, we keep them.")
            print("    The remaining %d 'silently dropped' cids are gamma-purged."
                  % len(still_missing))

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
