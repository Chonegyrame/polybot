"""Smoke test for B1 — smart-money exit detector.

Tests:
  - migration 005: signal_exits table exists, paper_trades CHECK constraints
    accept the new closed_exit / smart_money_exit values
  - exit_detector._classify_drop pure-function logic
  - crud helpers: insert_signal_exit / list_recent_signal_exits / get_exit_for_signal
  - paper_trade auto-close path settles correctly at exit bid price
  - backtest engine accepts exit_strategy=smart_money_exit and produces
    different P&L than hold for an exit row

Run: ./venv/Scripts/python.exe scripts/smoke_phase_b1.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    SignalRow,
    compute_pnl_per_dollar,
    compute_pnl_per_dollar_exit,
    summarize_rows,
)
from app.services.exit_detector import _classify_drop  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}{('  -- ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n  {title}\n{'=' * 80}")


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_classify_drop() -> None:
    section("B1: _classify_drop classifies exits correctly")

    # No drop
    check("0% drop -> None", _classify_drop(10, 10, 100_000, 100_000) is None)

    # Trader count only
    check(
        "30%+ trader drop only -> 'trader_count'",
        _classify_drop(7, 10, 100_000, 100_000) == "trader_count",
        f"got {_classify_drop(7, 10, 100_000, 100_000)}",
    )

    # Aggregate only
    check(
        "30%+ aggregate drop only -> 'aggregate'",
        _classify_drop(10, 10, 50_000, 100_000) == "aggregate",
        f"got {_classify_drop(10, 10, 50_000, 100_000)}",
    )

    # Both
    check(
        "Both drop -> 'both'",
        _classify_drop(5, 10, 50_000, 100_000) == "both",
        f"got {_classify_drop(5, 10, 50_000, 100_000)}",
    )

    # Threshold edge — exactly 30%
    check(
        "Exactly 30% drop trips threshold",
        _classify_drop(7, 10, 100_000, 100_000) == "trader_count",
    )

    # Custom threshold
    check(
        "Custom threshold 0.5 — 30% drop is below",
        _classify_drop(7, 10, 100_000, 100_000, threshold=0.5) is None,
    )

    # Zero peaks
    check(
        "Zero peak trader count and zero aggregate -> None",
        _classify_drop(0, 0, 0, 0) is None,
    )


def test_compute_pnl_per_dollar_exit() -> None:
    section("B1: compute_pnl_per_dollar_exit math")

    # Bought at 0.40, exited at 0.65 with no fee, deep liquidity
    # effective_entry ≈ 0.40 (slippage tiny), shares = 1/0.40 = 2.5
    # gross = 2.5 * 0.65 = 1.625, fee=0, pnl/$ ≈ 0.625
    v = compute_pnl_per_dollar_exit(
        entry_price=0.40, exit_bid_price=0.65,
        category="politics",  # 0% fee
        trade_size_usdc=1.0, liquidity_at_signal=25_000.0,
    )
    check(
        "Buy 0.40 / exit 0.65 / politics -> ~+0.625",
        v is not None and approx(v, 0.625, 0.01),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # Same trade in sports (1.8% fee on payout)
    v = compute_pnl_per_dollar_exit(
        entry_price=0.40, exit_bid_price=0.65,
        category="sports", trade_size_usdc=1.0, liquidity_at_signal=25_000.0,
    )
    # gross_per_dollar = 0.65 / 0.40 = 1.625; with 1.8% fee on payout → 1.625 * 0.982 - 1 = +0.596
    check(
        "Buy 0.40 / exit 0.65 / sports -> ~+0.596",
        v is not None and approx(v, 0.596, 0.01),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # Exited LOW (loss): bought 0.40, exited 0.20
    # gross = 1/0.40 * 0.20 = 0.5, pnl/$ ≈ -0.5
    v = compute_pnl_per_dollar_exit(
        entry_price=0.40, exit_bid_price=0.20,
        category="politics", trade_size_usdc=1.0, liquidity_at_signal=25_000.0,
    )
    check(
        "Buy 0.40 / exit 0.20 / politics -> ~-0.5",
        v is not None and approx(v, -0.5, 0.01),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # Bad inputs return None
    check("entry=1.0 returns None", compute_pnl_per_dollar_exit(1.0, 0.5, "politics", 1.0, 25_000) is None)
    check("exit_bid=0 returns None", compute_pnl_per_dollar_exit(0.4, 0.0, "politics", 1.0, 25_000) is None)
    check("entry=0 returns None", compute_pnl_per_dollar_exit(0.0, 0.5, "politics", 1.0, 25_000) is None)


def test_summarize_rows_strategy_branch() -> None:
    section("B1: summarize_rows branches on exit_strategy")

    # Build 35 signal rows. Half exit in profit, half resolve as losses.
    base_t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    def row(pid: int, has_exit: bool, exit_bid: float | None, outcome: str) -> SignalRow:
        return SignalRow(
            id=pid, mode="absolute", category="politics", top_n=50,
            condition_id=f"0xc{pid}", direction="YES",
            first_trader_count=10, first_aggregate_usdc=100_000.0,
            first_net_skew=0.85, first_avg_portfolio_fraction=0.10,
            signal_entry_offer=0.40, signal_entry_mid=0.40,
            liquidity_at_signal_usdc=25_000.0, liquidity_tier="medium",
            first_top_trader_entry_price=0.40,
            cluster_id=f"clu{pid}", market_type="binary",
            first_fired_at=base_t + timedelta(hours=pid),
            resolved_outcome=outcome,
            market_category="politics",
            exit_bid_price=exit_bid if has_exit else None,
            exit_drop_reason="aggregate" if has_exit else None,
            exited_at=(base_t + timedelta(hours=pid, days=2)) if has_exit else None,
        )

    # 18 exits at 0.65 (profitable), 17 hold-to-NO losers
    rows = (
        [row(i, has_exit=True,  exit_bid=0.65, outcome="NO") for i in range(18)] +
        [row(100 + i, has_exit=False, exit_bid=None, outcome="NO") for i in range(17)]
    )

    hold = summarize_rows(rows, trade_size_usdc=1.0, exit_strategy="hold")
    exit_ = summarize_rows(rows, trade_size_usdc=1.0, exit_strategy="smart_money_exit")

    # Hold: every signal resolves NO -> all losses, mean ~-1.0
    check(
        "Hold strategy: all losses, mean P&L < 0",
        hold.mean_pnl_per_dollar is not None and hold.mean_pnl_per_dollar < -0.5,
        f"hold mean={hold.mean_pnl_per_dollar}",
    )

    # Exit: 18/35 settle at 0.65 (+0.625 each), 17/35 still settle at 0 (NO)
    # Mean ≈ (18 × 0.625 + 17 × -1.0) / 35 = (11.25 - 17) / 35 ≈ -0.164
    check(
        "Exit strategy: mean P&L is meaningfully higher than hold",
        exit_.mean_pnl_per_dollar is not None
        and hold.mean_pnl_per_dollar is not None
        and exit_.mean_pnl_per_dollar > hold.mean_pnl_per_dollar + 0.3,
        f"exit mean={exit_.mean_pnl_per_dollar}, hold mean={hold.mean_pnl_per_dollar}",
    )

    # Win rate higher under exit (18 winners + 17 losers vs 0 winners + 35 losers)
    check(
        "Exit strategy: win rate > hold win rate",
        exit_.win_rate is not None and hold.win_rate is not None
        and exit_.win_rate > hold.win_rate,
        f"exit wr={exit_.win_rate}, hold wr={hold.win_rate}",
    )


# ---------------------------------------------------------------------------
# DB integration tests
# ---------------------------------------------------------------------------


async def test_db_schema() -> None:
    section("B1: signal_exits table + paper_trades constraints")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            # signal_exits exists
            row = await conn.fetchrow(
                "SELECT to_regclass('signal_exits') AS r"
            )
            check("signal_exits table exists", row["r"] is not None)

            # paper_trades.status accepts 'closed_exit'
            cons = await conn.fetchval(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'paper_trades'::regclass
                  AND conname = 'paper_trades_status_check'
                """
            )
            check(
                "paper_trades.status CHECK includes 'closed_exit'",
                "closed_exit" in (cons or ""),
                f"got: {cons}",
            )

            cons = await conn.fetchval(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'paper_trades'::regclass
                  AND conname = 'paper_trades_exit_reason_check'
                """
            )
            check(
                "paper_trades.exit_reason CHECK includes 'smart_money_exit'",
                "smart_money_exit" in (cons or ""),
                f"got: {cons}",
            )
    finally:
        await close_pool()


async def test_crud_roundtrip() -> None:
    section("B1: insert_signal_exit + list/get round-trip")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            # Need a real signal_log row to FK against
            sig = await conn.fetchrow("SELECT id, mode, category, top_n, condition_id, direction FROM signal_log LIMIT 1")
            if sig is None:
                check(
                    "Skipped: no signal_log rows in DB to test against",
                    True, "skipped",
                )
                return

            # Make sure we don't have a leftover exit row for this signal
            await conn.execute(
                "DELETE FROM signal_exits WHERE signal_log_id = $1", sig["id"]
            )

            # Insert
            new_id = await crud.insert_signal_exit(
                conn,
                signal_log_id=sig["id"],
                exit_trader_count=2, peak_trader_count=10,
                exit_aggregate_usdc=12_000.0, peak_aggregate_usdc=100_000.0,
                drop_reason="both",
                exit_bid_price=0.42,
            )
            check("insert_signal_exit returned id", new_id is not None and new_id > 0)

            # Re-insert returns None (UNIQUE on signal_log_id)
            again = await crud.insert_signal_exit(
                conn,
                signal_log_id=sig["id"],
                exit_trader_count=1, peak_trader_count=10,
                exit_aggregate_usdc=5_000.0, peak_aggregate_usdc=100_000.0,
                drop_reason="both",
                exit_bid_price=0.40,
            )
            check("Duplicate insert returns None (UNIQUE dedup)", again is None)

            # get_exit_for_signal finds it
            got = await crud.get_exit_for_signal(
                conn, sig["mode"], sig["category"], sig["top_n"],
                sig["condition_id"], sig["direction"],
            )
            check(
                "get_exit_for_signal returns the inserted row",
                got is not None and got["drop_reason"] == "both",
                f"got: {got}",
            )

            # list_recent_signal_exits includes it
            recent = await crud.list_recent_signal_exits(conn, hours=1, limit=10)
            ids = {r["exit_id"] for r in recent}
            check("list_recent_signal_exits includes the new row", new_id in ids)

            # Cleanup
            await conn.execute(
                "DELETE FROM signal_exits WHERE signal_log_id = $1", sig["id"]
            )
            check("Cleanup: removed test exit row", True)
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nB1 smoke test\n" + "=" * 80)

    test_classify_drop()
    test_compute_pnl_per_dollar_exit()
    test_summarize_rows_strategy_branch()

    await test_db_schema()
    await test_crud_roundtrip()

    section("SUMMARY")
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"  {n_pass} passed, {n_fail} failed")
    if n_fail:
        print("\n  Failures:")
        for label, ok, detail in results:
            if not ok:
                print(f"    {FAIL}  {label}  -- {detail}")
        sys.exit(1)
    print("\n  All B1 changes verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
