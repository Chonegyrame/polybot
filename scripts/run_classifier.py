"""Classify all tracked wallets behaviorally (MM / arbitrage / directional).

Run from project root:
    ./venv/Scripts/python.exe scripts/run_classifier.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import classify_tracked_wallets  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await classify_tracked_wallets()
    finally:
        await close_pool()
    print(
        f"\n=== classification complete ===\n"
        f"  classified  : {result.wallets_classified}\n"
        f"  failures    : {len(result.failures)}\n"
        f"  duration    : {result.duration_seconds:.1f}s\n"
        f"  distribution:"
    )
    for k, v in sorted(result.by_class.items(), key=lambda x: -x[1]):
        print(f"    {k:>14} : {v}")
    if result.failures:
        print("\n  failure details (first 5):")
        for wallet, err in result.failures[:5]:
            print(f"    - {wallet[:12]}: {err[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
