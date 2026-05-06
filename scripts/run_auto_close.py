"""Manually run the paper-trade auto-close job.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_auto_close.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import auto_close_resolved_paper_trades  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        result = await auto_close_resolved_paper_trades()
    finally:
        await close_pool()
    print(
        f"\n=== auto-close complete ===\n"
        f"  trades closed       : {result.trades_closed}\n"
        f"  realized P&L total  : ${result.realized_pnl_total:+,.2f}\n"
        f"  duration            : {result.duration_seconds:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
