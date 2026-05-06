"""Manually sync all active Polymarket events + markets into the DB.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_market_sync.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.services.market_sync import sync_active_markets  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await sync_active_markets()
    finally:
        await close_pool()
    print(
        f"\n=== market sync complete ===\n"
        f"  mode             : {'FULL' if result.full_sync else 'INCREMENTAL'}\n"
        f"  events written   : {result.events_seen}\n"
        f"  markets written  : {result.markets_seen}\n"
        f"  stopped_at_cutoff: {result.stopped_at_cutoff}\n"
        f"  duration         : {result.duration_seconds:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
