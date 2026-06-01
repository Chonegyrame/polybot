"""Run the smart-money backtest under several parameter sets and print a
comparison. Read-only — no writes. Run: python -m scripts.backtest_explore
"""

from __future__ import annotations

import asyncio

from app.db.connection import close_pool, init_pool
from app.services.backtest_engine import BacktestFilters, backtest_summary


def fmt(r) -> str:
    if r.mean_pnl_per_dollar is None:
        pnl = "n/a"
    else:
        pnl = f"{r.mean_pnl_per_dollar:+.3f} [{r.pnl_ci_lo:+.3f}, {r.pnl_ci_hi:+.3f}]"
    if r.win_rate is None:
        wr = "n/a"
    else:
        wr = f"{r.win_rate:.0%} [{r.win_rate_ci_lo:.0%}, {r.win_rate_ci_hi:.0%}]"
    flag = "UNDERPOWERED" if r.underpowered else "ok"
    pf = "n/a" if r.profit_factor is None else f"{r.profit_factor:.2f}"
    p = "n/a" if r.pnl_bootstrap_p is None else f"{r.pnl_bootstrap_p:.3f}"
    return (
        f"n={r.n_signals} resolved={r.n_resolved} n_eff={r.n_eff:.1f} [{flag}]\n"
        f"       PnL/$1 = {pnl}   (bootstrap p={p})\n"
        f"       win_rate = {wr}   profit_factor={pf}\n"
        f"       by_resolution={r.by_resolution}  by_direction={r.by_direction}"
    )


async def run(label: str, **kw) -> None:
    try:
        r = await backtest_summary(BacktestFilters(**kw))
        print(f"\n### {label}\n       {fmt(r)}")
    except Exception as e:  # noqa: BLE001
        print(f"\n### {label}\n       ERROR: {e!r}")


async def main() -> None:
    await init_pool()
    print("=== Smart-money backtest sweep (after resolution backfill) ===")
    await run("1) ALL signals, hold-to-resolution (baseline)")
    await run("2) DEDUP - one row per market (honest headline)", dedup=True)
    await run("3) DEDUP + direction = YES only", dedup=True, direction="YES")
    await run("4) DEDUP + direction = NO only", dedup=True, direction="NO")
    await run("5) DEDUP + high-conviction (>=8 traders)", dedup=True, min_trader_count=8)
    await run("6) DEDUP + smart_money_exit", dedup=True, exit_strategy="smart_money_exit")
    await run("7) Non-dedup + smart_money_exit", exit_strategy="smart_money_exit")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
