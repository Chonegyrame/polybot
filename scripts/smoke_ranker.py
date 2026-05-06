"""Smoke test for app.services.trader_ranker — exercises both modes
against the snapshot data in the DB.

Run from project root:
    ./venv/Scripts/python.exe scripts/smoke_ranker.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.services.trader_ranker import RankedTrader, rank_traders  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def hr(label: str) -> None:
    print(f"\n{'=' * 78}\n {label}\n{'=' * 78}")


def show(traders: list[RankedTrader], n: int = 10) -> None:
    if not traders:
        print("  (no traders)")
        return
    for t in traders[:n]:
        roi_pct = t.roi * 100
        ranks_extra = (
            f"  (pnl#{t.pnl_rank}, roi#{t.roi_rank})"
            if t.roi_rank is not None
            else f"  (pnl#{t.pnl_rank})"
        )
        badge = " *" if t.verified_badge else "  "
        print(
            f"  #{t.rank:<3}{badge} {(t.user_name or '<anon>'):<30} "
            f"pnl=${t.pnl:>14,.0f}  vol=${t.vol:>14,.0f}  roi={roi_pct:>+7.1f}%"
            f"{ranks_extra}"
        )


async def main() -> int:
    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            for category in ("overall", "politics", "sports"):
                hr(f"{category.upper()}  —  ABSOLUTE  top 10")
                show(await rank_traders(conn, "absolute", category, top_n=10))

                hr(f"{category.upper()}  —  HYBRID  top 10")
                show(await rank_traders(conn, "hybrid", category, top_n=10))

            # Sanity: count differences between modes for one category
            abs_traders = await rank_traders(conn, "absolute", "overall", top_n=50)
            hyb_traders = await rank_traders(conn, "hybrid", "overall", top_n=50)
            abs_set = {t.proxy_wallet for t in abs_traders}
            hyb_set = {t.proxy_wallet for t in hyb_traders}

            hr("MODE OVERLAP — overall, top 50")
            print(f"  absolute count:        {len(abs_traders)}")
            print(f"  hybrid count:          {len(hyb_traders)}")
            print(f"  in both:               {len(abs_set & hyb_set)}")
            print(f"  only in absolute:      {len(abs_set - hyb_set)}")
            print(f"  only in hybrid:        {len(hyb_set - abs_set)}")

    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
