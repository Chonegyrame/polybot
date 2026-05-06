"""Run one full 10-min cycle (refresh -> log signals -> auto-close) end-to-end.

Same path that the in-process scheduler triggers, exercised manually.
Used to verify the advisory-lock wrap and cycle-duration logging from
Session 2 actually fire in production.

Run:
    ./venv/Scripts/python.exe scripts/run_cycle_once.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.scheduler.jobs import refresh_positions_then_log_signals  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> int:
    try:
        refresh, log_res, exits, autoclose = await refresh_positions_then_log_signals()
    finally:
        await close_pool()

    print(
        f"\n=== cycle complete ===\n"
        f"  refresh:   {refresh.wallets_succeeded}/{refresh.wallets_targeted} wallets, "
        f"{refresh.positions_persisted} positions ({refresh.duration_seconds:.1f}s)\n"
        f"  signals:   {log_res.combos_run} combos, {log_res.signals_seen} signals "
        f"({log_res.new_signals} new) ({log_res.duration_seconds:.1f}s)\n"
        f"  exits:     {exits.exits_fired} exits fired, {exits.paper_trades_closed} paper trades closed "
        f"(realized $%+.2f) ({exits.duration_seconds:.1f}s)\n"
        f"  autoclose: {autoclose.trades_closed} trades closed, "
        f"realized $%+.2f ({autoclose.duration_seconds:.1f}s)"
        % (exits.paper_trades_realized_pnl_usdc, autoclose.realized_pnl_total)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
