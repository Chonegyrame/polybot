"""Run the Polymarket-market ↔ Oracle's Elixir-game join across every
classified LoL market in our DB. Reports coverage rate, lands ambiguous
fuzzy matches in lol_match_review, and stamps confident matches with
their OE gameid(s).

Usage (from project root):
    PYTHONPATH=. ./venv/Scripts/python.exe scripts/join_pm_to_oe.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import Counter

# Windows stdout defaults to cp1252; switch to UTF-8 so we can print team
# names with diacritics without UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

from app.db.connection import init_pool
from app.services.lol_match_join import (
    join_one_market,
    seed_alias_table,
    _load_alias_table,
)


async def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,  # noisy on info, so quiet for the join
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    started = time.monotonic()
    print("=" * 70)
    print("Polymarket <-> Oracle's Elixir join")
    print("=" * 70)

    pool = await init_pool(min_size=1, max_size=2)

    # Step 1: seed the alias table from in-code POLYMARKET_TO_OE
    async with pool.acquire() as conn:
        seeded = await seed_alias_table(conn)
    print(f"[seed] inserted/skipped {seeded} alias rows from POLYMARKET_TO_OE")

    # Step 2: pull every classified Polymarket LoL market
    async with pool.acquire() as conn:
        markets = await conn.fetch(
            """
            SELECT
                mm.condition_id,
                mm.event_id,
                mm.team_a,
                mm.team_b,
                mm.league,
                mm.market_kind,
                mm.game_number,
                e.start_time,
                e.end_date
            FROM polymarket_lol_market_meta mm
            LEFT JOIN events e ON e.id = mm.event_id
            ORDER BY e.start_time NULLS LAST
            """
        )
    print(f"[load] {len(markets)} classified Polymarket markets to attempt")

    # Step 3: run the join for each market, accumulate stats
    status_counts: Counter[str] = Counter()
    layer_counter: Counter[str] = Counter()
    review_rows = []
    sample_unresolved = []

    async with pool.acquire() as conn:
        alias_map = await _load_alias_table(conn)

    for i, m in enumerate(markets):
        # Pick a reasonable anchor time: start_time first, then end_date.
        anchor = m["start_time"] or m["end_date"]
        async with pool.acquire() as conn:
            result = await join_one_market(
                conn,
                pm_condition_id=m["condition_id"],
                pm_event_id=m["event_id"],
                pm_team_a=m["team_a"] or "",
                pm_team_b=m["team_b"] or "",
                pm_league_str=m["league"],
                pm_start_time=anchor,
                market_kind=m["market_kind"],
                game_number=m["game_number"],
                alias_map=alias_map,
            )

        status_counts[result.status] += 1
        layer_counter[f"a:{result.layer_a}"] += 1
        layer_counter[f"b:{result.layer_b}"] += 1

        if result.status == "review_queued":
            review_rows.append(result)
        elif result.status == "no_team_match" and len(sample_unresolved) < 20:
            sample_unresolved.append(
                f"  {result.pm_team_a!r:>30s}  vs  {result.pm_team_b!r:<30s}"
                f"  league={result.pm_league!r:<40s}"
                f"  layers=({result.layer_a},{result.layer_b})"
            )

        if (i + 1) % 500 == 0:
            print(f"  ... {i+1}/{len(markets)} markets processed")

    duration = time.monotonic() - started
    print()
    print("=" * 70)
    print(f"JOIN COMPLETE in {duration:.1f}s")
    print("=" * 70)
    total = len(markets)
    print(f"\nResolution status distribution (out of {total}):")
    for status, n in sorted(status_counts.items(), key=lambda x: -x[1]):
        pct = (n / total * 100) if total else 0
        print(f"  {status:>20s} : {n:>5d}  ({pct:.1f}%)")

    print(f"\nLayer breakdown (counts; team_a + team_b separately):")
    for layer, n in sorted(layer_counter.items(), key=lambda x: -x[1]):
        print(f"  {layer:>25s} : {n}")

    print(f"\nReview queue length: {len(review_rows)}")
    if review_rows[:5]:
        print("Sample review-queue entries (first 5):")
        for r in review_rows[:5]:
            print(
                f"  {r.pm_team_a!r:>30s}  vs  {r.pm_team_b!r:<30s}"
                f"  suggested=({r.oe_team_a_name!r}, {r.oe_team_b_name!r})"
                f"  {r.note}"
            )

    if sample_unresolved:
        print("\nSample unresolved 'no_team_match' (first 20):")
        for s in sample_unresolved:
            print(s)

    # Persist review-queue rows
    if review_rows:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for r in review_rows:
                    await conn.execute(
                        """
                        INSERT INTO lol_match_review (
                            polymarket_event_id, pm_team_a, pm_team_b,
                            pm_start_time, pm_league,
                            suggested_oe_team_a, suggested_oe_team_b,
                            score_a, score_b, candidate_gameids, status
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending')
                        """,
                        r.pm_event_id, r.pm_team_a, r.pm_team_b,
                        r.pm_start_time, r.pm_league,
                        r.oe_team_a_name, r.oe_team_b_name,
                        # Scores parse out of note field, simplified for V1
                        None, None,
                        list(r.matched_gameids),
                    )
        print(f"\n[persist] inserted {len(review_rows)} rows into lol_match_review")


if __name__ == "__main__":
    asyncio.run(main())
