"""Run the in-process scheduler standalone.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_scheduler.py

Ctrl+C to stop. The scheduler will:
  - run a catch-up daily snapshot if the last one is >24h old
  - refresh positions + log signals every 10 minutes
  - run the daily leaderboard snapshot at 02:00 UTC

When Step 9 lands (FastAPI), this standalone runner becomes optional — the
scheduler will boot inside the API process via lifespan_scheduler().
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.runner import run_forever  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


async def main() -> int:
    try:
        await run_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("interrupted, exiting")
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
