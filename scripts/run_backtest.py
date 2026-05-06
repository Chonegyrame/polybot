"""Run backtest queries against the local signal_log.

Examples:
    ./venv/Scripts/python.exe scripts/run_backtest.py
    ./venv/Scripts/python.exe scripts/run_backtest.py --include-pre-fix
    ./venv/Scripts/python.exe scripts/run_backtest.py --slice gap_bucket
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    BacktestResult,
    backtest_slice,
    backtest_summary,
)

logging.basicConfig(level=logging.WARNING)


def fmt_pct(x: float | None) -> str:
    return f"{x*100:+.1f}%" if x is not None else "  n/a"


def fmt_pnl(x: float | None) -> str:
    return f"{x*100:+7.2f}%" if x is not None else "    n/a"


def print_result(label: str, r: BacktestResult) -> None:
    flag = " [UNDERPOWERED]" if r.underpowered else ""
    print(f"\n{label}{flag}")
    print(f"  signals : {r.n_signals:>4}  resolved: {r.n_resolved:>4}  n_eff: {r.n_eff:>5.1f}")
    if r.mean_pnl_per_dollar is None:
        print("  (no resolved signals with computable P&L)")
        if r.by_resolution:
            print(f"  resolution mix: {r.by_resolution}")
        return

    print(f"  mean P&L per $1 invested : {fmt_pnl(r.mean_pnl_per_dollar)}"
          f"   CI [{fmt_pnl(r.pnl_ci_lo)}, {fmt_pnl(r.pnl_ci_hi)}]")
    print(f"  win rate                 : {fmt_pct(r.win_rate)}"
          f"        CI [{fmt_pct(r.win_rate_ci_lo)}, {fmt_pct(r.win_rate_ci_hi)}]")
    pf = "∞" if r.profit_factor == float("inf") else (
        f"{r.profit_factor:.2f}" if r.profit_factor is not None else "n/a"
    )
    print(f"  profit factor            : {pf}")
    print(f"  max drawdown (1% sizing) : {fmt_pct(r.max_drawdown)}")
    print(f"  median entry price       : "
          f"{r.median_entry_price:.3f}" if r.median_entry_price is not None else "  n/a")
    if r.median_gap_to_smart_money is not None:
        print(f"  median gap to smart $    : {fmt_pct(r.median_gap_to_smart_money)}")


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--include-pre-fix", action="store_true",
                   help="Include rows with signal_entry_source='unavailable'")
    p.add_argument("--mode", choices=("absolute", "hybrid"))
    p.add_argument("--category")
    p.add_argument("--slice", dest="slice_dim",
                   help="Dimension to slice on (mode, category, direction, "
                        "market_category, liquidity_tier, skew_bucket, "
                        "trader_count_bucket, aggregate_bucket, "
                        "entry_price_bucket, gap_bucket)")
    p.add_argument("--trade-size", type=float, default=100.0)
    args = p.parse_args()

    f = BacktestFilters(
        mode=args.mode,
        category=args.category,
        include_pre_fix=args.include_pre_fix,
        trade_size_usdc=args.trade_size,
    )

    try:
        if args.slice_dim:
            results = await backtest_slice(args.slice_dim, f)
            print(f"=== Slice by {args.slice_dim!r} (trade_size=${args.trade_size:.0f}) ===")
            for bucket, r in sorted(results.items()):
                print_result(f"[{bucket}]", r)
        else:
            r = await backtest_summary(f)
            print(f"=== Headline (trade_size=${args.trade_size:.0f}) ===")
            print_result("ALL signals matching filters", r)
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
