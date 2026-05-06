"""Apply unapplied SQL migrations from `migrations/` to the database.

Tracks applied migrations in the `_migrations` table (created on first run by
the initial schema migration itself). Applies each missing migration in
filename order inside its own transaction; aborts the whole batch on first
error.

Usage:
    ./venv/Scripts/python.exe scripts/apply_migrations.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.connection import close_pool, init_pool  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("migrate")

MIGRATIONS_DIR = ROOT / "migrations"


async def applied_names(conn) -> set[str]:
    """Return the set of migration names already applied. Empty if table missing."""
    exists = await conn.fetchval(
        "SELECT to_regclass('public._migrations') IS NOT NULL"
    )
    if not exists:
        return set()
    rows = await conn.fetch("SELECT name FROM _migrations")
    return {r["name"] for r in rows}


async def run() -> int:
    if not settings.database_url:
        log.error("DATABASE_URL is not set — aborting")
        return 1

    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("no migrations found in %s", MIGRATIONS_DIR)
        return 0
    log.info("found %d migration file(s) in %s", len(files), MIGRATIONS_DIR)

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            already = await applied_names(conn)
            log.info("already applied: %s", sorted(already) or "(none)")

            for path in files:
                name = path.stem  # filename without .sql
                if name in already:
                    log.info("  skip %s (already applied)", name)
                    continue
                sql = path.read_text(encoding="utf-8")
                log.info("  apply %s ...", name)
                async with conn.transaction():
                    await conn.execute(sql)
                    # The 001 migration creates _migrations itself; for
                    # subsequent ones, record explicitly. INSERT is idempotent
                    # because of the PRIMARY KEY.
                    await conn.execute(
                        "INSERT INTO _migrations (name) VALUES ($1) ON CONFLICT DO NOTHING",
                        name,
                    )
                log.info("  applied %s ✓", name)

            final = await applied_names(conn)
            log.info("done. applied set: %s", sorted(final))
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
