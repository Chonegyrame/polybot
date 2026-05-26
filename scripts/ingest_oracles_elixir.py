"""Ingest one or more Oracle's Elixir annual CSV files into lol_pro_matches.

Usage:
    PYTHONPATH=. ./venv/Scripts/python.exe scripts/ingest_oracles_elixir.py \
        2024_LoL_esports_match_data_from_OraclesElixir.csv \
        2025_LoL_esports_match_data_from_OraclesElixir.csv \
        2026_LoL_esports_match_data_from_OraclesElixir.csv

Idempotent — re-running upserts. Prints summary per-file plus an overall
total + DB row counts after the run.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from app.db.connection import init_pool
from app.services.oracles_elixir import ingest_oracles_elixir_csv


async def main(csv_paths: list[str]) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if not csv_paths:
        print("usage: ingest_oracles_elixir.py <csv> [<csv> ...]")
        sys.exit(2)

    started = time.monotonic()
    print("=" * 70)
    print("Oracle's Elixir CSV ingest")
    print("=" * 70)
    for p in csv_paths:
        print(f"  - {p}")
    print()

    pool = await init_pool(min_size=1, max_size=2)

    total_games = 0
    total_rows = 0
    total_skipped = 0
    per_file = []

    for path in csv_paths:
        if not Path(path).exists():
            print(f"SKIP missing file: {path}")
            continue
        async with pool.acquire() as conn:
            result = await ingest_oracles_elixir_csv(conn, path)
        per_file.append(result)
        total_games += result.games_seen
        total_rows += result.rows_inserted
        total_skipped += result.rows_skipped
        print(
            f"  done: {Path(path).name}"
            f" -- games={result.games_seen}"
            f" rows_inserted={result.rows_inserted}"
            f" skipped={result.rows_skipped}"
            f" ({result.duration_seconds:.1f}s)"
        )

    duration = time.monotonic() - started
    print()
    print("=" * 70)
    print(f"DONE in {duration / 60:.1f} min")
    print(f"  total games processed: {total_games}")
    print(f"  total rows inserted/updated: {total_rows}")
    print(f"  total rows skipped: {total_skipped}")

    # Final DB sanity check
    async with pool.acquire() as conn:
        db_total = await conn.fetchval("SELECT COUNT(*) FROM lol_pro_matches")
        per_year = await conn.fetch(
            "SELECT ingested_year, COUNT(*) FROM lol_pro_matches GROUP BY ingested_year ORDER BY ingested_year"
        )
        top_leagues = await conn.fetch(
            """
            SELECT league, COUNT(*) AS n
            FROM lol_pro_matches
            GROUP BY league
            ORDER BY n DESC
            LIMIT 10
            """
        )
    print(f"\n[DB] lol_pro_matches total rows: {db_total}")
    print("[DB] rows per ingested year:")
    for r in per_year:
        print(f"     {r['ingested_year']}: {r['count']}")
    print("[DB] top 10 leagues by row count:")
    for r in top_leagues:
        print(f"     {r['n']:>6}  {r['league']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
