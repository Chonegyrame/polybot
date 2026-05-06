"""End-to-end signal smoke test — exercises the full pipeline on the data
that's already in the DB.

Assumes you have already run:
    1. scripts/run_snapshot.py         (today's leaderboard snapshot)
    2. scripts/run_market_sync.py      (active events + markets)
    3. scripts/run_position_refresh.py (top traders' open positions)

Then this script just queries the signal detector across a handful of
representative selections and prints what's firing.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.services.signal_detector import Signal, detect_signals  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def hr(label: str) -> None:
    print(f"\n{'=' * 90}\n {label}\n{'=' * 90}")


def render(signals: list[Signal], n: int = 8) -> None:
    if not signals:
        print("  (no signals firing)")
        return
    for s in signals[:n]:
        q = (s.market_question or s.market_slug or s.condition_id[:14])[:70]
        skew_pct = s.direction_skew * 100
        avg_pf_pct = s.avg_portfolio_fraction * 100
        print(
            f"  [{s.direction:>3} {skew_pct:5.1f}%]  "
            f"{s.trader_count:>3} traders  "
            f"${s.aggregate_usdc:>11,.0f}  "
            f"avg_pf={avg_pf_pct:>5.2f}%  "
            f"price={s.current_price or 0:.3f}  "
            f"|  {q}"
        )


async def main() -> int:
    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            for mode in ("absolute", "hybrid"):
                for category in ("overall", "politics", "sports", "crypto", "finance"):
                    for top_n in (50, 100):
                        hr(f"{mode.upper()} / {category} / top {top_n}")
                        sigs = await detect_signals(conn, mode, category, top_n)
                        print(f"  signals firing: {len(sigs)}")
                        render(sigs, n=8)
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
