"""Confirm at scale: of the cids gamma silently drops on default JIT,
how many DOES it return when we pass closed=true?

If ~all of them come back as `closed=true`, the drop is benign — these
are resolved markets that signal_detector would filter out anyway via
`WHERE m.closed = FALSE`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.services.polymarket import PolymarketClient  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("diag")
log.setLevel(logging.INFO)

GAMMA_BASE = "https://gamma-api.polymarket.com"
SAMPLE_WALLETS = 30


async def fetch_with_closed_param(
    cx: httpx.AsyncClient, cids: list[str], closed: bool, batch: int = 50
) -> list[dict]:
    """Fetch markets passing the closed= flag, paginating in chunks of `batch`."""
    out: list[dict] = []
    for i in range(0, len(cids), batch):
        chunk = cids[i:i + batch]
        params = [("condition_ids", c) for c in chunk]
        params.append(("closed", "true" if closed else "false"))
        params.append(("limit", str(len(chunk))))
        r = await cx.get(f"{GAMMA_BASE}/markets", params=params)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                out.extend(data)
    return out


async def main() -> None:
    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            wallets = [
                r["proxy_wallet"] for r in await conn.fetch(
                    """
                    SELECT proxy_wallet FROM leaderboard_snapshots
                    WHERE category = 'overall' AND time_period = 'all'
                      AND order_by = 'PNL'
                      AND snapshot_date = (SELECT MAX(snapshot_date) FROM leaderboard_snapshots)
                    ORDER BY rank ASC LIMIT $1
                    """,
                    SAMPLE_WALLETS,
                )
            ]

        all_position_cids: set[str] = set()
        async with PolymarketClient() as pm:
            for w in wallets:
                ps = await pm.get_positions(w, limit=500)
                all_position_cids.update(p.condition_id for p in ps if p.condition_id)
            log.info("collected %d distinct cids across %d wallets", len(all_position_cids), len(wallets))

            # Query our DB for what we already have
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT condition_id FROM markets WHERE condition_id = ANY($1::TEXT[])",
                    list(all_position_cids),
                )
            in_db = {r["condition_id"] for r in rows}
            missing_cids = sorted(all_position_cids - in_db)
            log.info("%d cids missing from DB; probing gamma...", len(missing_cids))

            async with httpx.AsyncClient(timeout=30) as cx:
                # JIT-equivalent (default closed=false in our client adds no flag,
                # so we test default=no-flag vs closed=true)
                jit_default = await pm.get_markets_by_condition_ids(missing_cids)
                jit_default_cids = {m.condition_id for m in jit_default}
                log.info("default (closed=false) returns %d/%d", len(jit_default_cids), len(missing_cids))

                # Same set with closed=true
                still_missing = [c for c in missing_cids if c not in jit_default_cids]
                closed_results = await fetch_with_closed_param(cx, still_missing, closed=True, batch=50)
                closed_returned_cids = {m.get("conditionId") for m in closed_results if m.get("conditionId")}
                log.info("closed=true returns %d additional", len(closed_returned_cids))

                # Cids gamma still won't acknowledge on either query
                truly_silent = [c for c in still_missing if c not in closed_returned_cids]

        # State histogram on the closed-recovered set
        active_count = sum(1 for m in closed_results if not m.get("closed"))
        closed_count = sum(1 for m in closed_results if m.get("closed"))
        archived_count = sum(1 for m in closed_results if m.get("archived"))

        # Count how many of those are also negRisk
        negrisk_count = sum(1 for m in closed_results if m.get("negRisk"))

        # End-date distribution (resolved markets should have endDate in the past)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        end_past = 0
        end_future = 0
        end_unparseable = 0
        for m in closed_results:
            ed_raw = m.get("endDate")
            if not ed_raw:
                end_unparseable += 1
                continue
            try:
                ed = datetime.fromisoformat(ed_raw.replace("Z", "+00:00"))
                if ed.tzinfo is None:
                    ed = ed.replace(tzinfo=timezone.utc)
                if ed < now:
                    end_past += 1
                else:
                    end_future += 1
            except (ValueError, TypeError):
                end_unparseable += 1

        print("\n" + "=" * 72)
        print("AT-SCALE BREAKDOWN OF SILENTLY-DROPPED CIDS")
        print("=" * 72)
        print(f"  total distinct cids on tracked positions : {len(all_position_cids)}")
        print(f"  already in markets table                  : {len(in_db)}")
        print(f"  missing from markets table                : {len(missing_cids)}")
        print()
        print(f"  Of those missing cids, gamma returns:")
        print(f"    default (closed=false) — current JIT path : {len(jit_default_cids):5d}")
        print(f"    closed=true              — recoverable    : {len(closed_returned_cids):5d}")
        print(f"    silent on BOTH                            : {len(truly_silent):5d}")
        print()
        print(f"  Of the {len(closed_results)} closed=true recovered:")
        print(f"    closed=True flag set : {closed_count}")
        print(f"    active=True flag set : {active_count}")
        print(f"    archived=True flag   : {archived_count}")
        print(f"    negRisk=True flag    : {negrisk_count}")
        print(f"    endDate in past      : {end_past}")
        print(f"    endDate in future    : {end_future}")
        print(f"    endDate unparseable  : {end_unparseable}")
        print()
        print(f"  After full recovery (default + closed=true):")
        recovered_pct = (len(jit_default_cids) + len(closed_returned_cids)) / max(1, len(missing_cids))
        print(f"    {recovered_pct:.1%} of missing cids are recoverable from gamma")
        print(f"    {len(truly_silent)} cids ({len(truly_silent)/max(1,len(missing_cids)):.1%}) are gamma-silent on both")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
