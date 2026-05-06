"""DB smoke test — verifies connection works and tables exist.

Run after `scripts/apply_migrations.py`:
    ./venv/Scripts/python.exe scripts/smoke_db.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import crud  # noqa: E402
from app.db.connection import close_pool, init_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

EXPECTED_TABLES = {
    "_migrations",
    "traders",
    "leaderboard_snapshots",
    "events",
    "markets",
    "positions",
    "portfolio_value_snapshots",
    "signal_log",
}


async def main() -> int:
    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            info = await crud.ping(conn)
            print("Connected:", info)

            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = {r["tablename"] for r in rows}
            print("\nTables in public schema:")
            for t in sorted(tables):
                marker = "OK" if t in EXPECTED_TABLES else "??"
                print(f"  [{marker}]  {t}")

            missing = EXPECTED_TABLES - tables
            extra = tables - EXPECTED_TABLES
            if missing:
                print(f"\nMISSING expected tables: {sorted(missing)}")
            if extra:
                print(f"\nUnexpected/extra tables: {sorted(extra)}")
            if not missing:
                print("\nAll expected tables present.")

            mig_rows = await conn.fetch("SELECT name, applied_at FROM _migrations ORDER BY applied_at")
            print(f"\nApplied migrations:")
            for r in mig_rows:
                print(f"  {r['name']}  @ {r['applied_at']}")

            return 0 if not missing else 1
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
