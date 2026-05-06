"""Manually refresh positions for every wallet in the tracked top-N pool.

Run after `run_market_sync.py` (so market FK targets exist) and after at least
one snapshot has run (so the trader pool is populated).

Run from project root:
    ./venv/Scripts/python.exe scripts/run_position_refresh.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import refresh_top_trader_positions  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await refresh_top_trader_positions()
    finally:
        await close_pool()
    print(
        f"\n=== position refresh complete ===\n"
        f"  wallets targeted          : {result.wallets_targeted}\n"
        f"  wallets succeeded         : {result.wallets_succeeded}\n"
        f"  positions persisted       : {result.positions_persisted}\n"
        f"  portfolio values written  : {result.portfolio_values_persisted}\n"
        f"  failures                  : {len(result.failures)}\n"
        f"  duration                  : {result.duration_seconds:.1f}s"
    )
    if result.failures[:5]:
        print("\n  First 5 failures:")
        for w, err in result.failures[:5]:
            print(f"    - {w}: {err}")
    return 0 if result.wallets_succeeded > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
