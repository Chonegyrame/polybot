"""Manually trigger a leaderboard snapshot. Idempotent on date.

Usage:
    ./venv/Scripts/python.exe scripts/run_snapshot.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import daily_leaderboard_snapshot  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> int:
    try:
        result = await daily_leaderboard_snapshot()
    finally:
        await close_pool()

    print(
        f"\n=== snapshot complete ===\n"
        f"  date            : {result.snapshot_date}\n"
        f"  combinations    : {result.total_combinations}\n"
        f"  rows seen       : {result.total_rows_seen}\n"
        f"  unique wallets  : {result.total_unique_wallets}\n"
        f"  failures        : {len(result.failures)}\n"
        f"  duration        : {result.duration_seconds:.1f}s"
    )
    if result.failures:
        print("\n  Failed combinations:")
        for label, err in result.failures:
            print(f"    - {label}: {err}")
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
