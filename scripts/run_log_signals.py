"""Manually run the log_signals job once.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_log_signals.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import log_signals  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await log_signals()
    finally:
        await close_pool()
    print(
        f"\n=== log_signals complete ===\n"
        f"  combos run     : {result.combos_run}\n"
        f"  signals seen   : {result.signals_seen}\n"
        f"  new signals    : {result.new_signals}\n"
        f"  failures       : {len(result.failures)}\n"
        f"  duration       : {result.duration_seconds:.1f}s"
    )
    if result.failures:
        print("\n  failure details:")
        for label, err in result.failures[:10]:
            print(f"    - {label}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
