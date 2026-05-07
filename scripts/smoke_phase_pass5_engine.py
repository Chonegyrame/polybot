"""Pass 5 Tier B #9 + #10 -- backtest engine integration.

Two engine-level fixes share this file because both touch
app/services/backtest_engine.py.

  #9 dedup view skips unavailable first-fires (the view itself was
     fixed by migration 019 in commit 1; this file adds the engine-
     consumer integration test the migration smoke didn't cover).

  #10 compute_pnl_per_dollar_exit applies symmetric exit-side
      slippage. Pre-fix the entry was bumped up by the slippage
      impact but the exit was sold at the displayed bid -- a $100
      trade on a $50k-deep book overstated P&L by ~0.0022 per dollar.

Pure-function tests for #10 + a DB integration test for #9 calling
_fetch_signals directly with dedup=True.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_engine.py
"""

from __future__ import annotations

import asyncio
import inspect
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import init_pool, close_pool  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    SLIPPAGE_K,
    _fetch_signals,
    _slippage_per_dollar,
    compute_pnl_per_dollar,
    compute_pnl_per_dollar_exit,
)
from app.services.fees import _resolve_rate  # noqa: E402


PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS]  {label}" + (f"  -- {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL]  {label}" + (f"  -- {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# #10 pure-function tests
# ---------------------------------------------------------------------------


def test_code_shape_exit_slippage() -> None:
    section("#10 code-shape: effective_exit + post-slippage fee")

    src = inspect.getsource(compute_pnl_per_dollar_exit)
    check(
        "#10: effective_exit defined via max(0.001, exit - slip)",
        "effective_exit" in src and "exit_bid_price - slip" in src,
    )
    check(
        "#10: revenue uses effective_exit (not raw exit_bid_price)",
        "effective_exit / effective_entry" in src,
    )
    check(
        "#10: exit_fee runs over effective_exit",
        "rate * effective_exit * (1.0 - effective_exit)" in src,
    )
    # Resolution-path P&L untouched -- no exit slippage.
    res_src = inspect.getsource(compute_pnl_per_dollar)
    check(
        "#10: compute_pnl_per_dollar (resolution path) does not introduce effective_exit",
        "effective_exit" not in res_src,
    )


def _expected_pnl_pre_fix(
    entry_price: float, exit_bid: float, rate: float, slip: float,
) -> float:
    """Pre-fix formula: only entry was slipped, exit was the raw bid."""
    effective_entry = min(0.999, entry_price + slip)
    revenue = exit_bid / effective_entry
    entry_fee = rate * (1.0 - effective_entry)
    exit_fee = (rate * exit_bid * (1.0 - exit_bid)) / effective_entry
    return revenue - 1.0 - entry_fee - exit_fee


def _expected_pnl_post_fix(
    entry_price: float, exit_bid: float, rate: float, slip: float,
) -> float:
    """Post-fix formula: symmetric slippage on both legs."""
    effective_entry = min(0.999, entry_price + slip)
    effective_exit = max(0.001, exit_bid - slip)
    revenue = effective_exit / effective_entry
    entry_fee = rate * (1.0 - effective_entry)
    exit_fee = (rate * effective_exit * (1.0 - effective_exit)) / effective_entry
    return revenue - 1.0 - entry_fee - exit_fee


def test_exit_slippage_thick_book() -> None:
    section("#10 exit slippage: $100 trade, $50k liquidity, entry 0.40 -> 0.55")

    trade = 100.0
    liq = 50_000.0
    entry = 0.40
    exit_bid = 0.55
    rate = _resolve_rate("Politics")  # 0.04

    slip = _slippage_per_dollar(trade, liq, None)
    expected_post = _expected_pnl_post_fix(entry, exit_bid, rate, slip)
    expected_pre = _expected_pnl_pre_fix(entry, exit_bid, rate, slip)

    actual = compute_pnl_per_dollar_exit(
        entry_price=entry, exit_bid_price=exit_bid, category="Politics",
        trade_size_usdc=trade, liquidity_at_signal=liq,
    )
    check(
        "#10 thick: actual P&L matches symmetric-slippage formula",
        actual is not None and abs(actual - expected_post) < 1e-6,
        f"actual={actual:.6f} expected_post={expected_post:.6f}",
    )
    diff = expected_pre - expected_post
    check(
        "#10 thick: post-fix P&L is LOWER than pre-fix by ~0.0022 per dollar",
        diff > 0.0019 and diff < 0.0026,
        f"diff={diff:.6f} (plan target ~0.0022)",
    )


def test_exit_slippage_thin_book() -> None:
    section("#10 exit slippage: $100 trade, $5k liquidity (thinner)")

    trade = 100.0
    liq = 5_000.0
    entry = 0.40
    exit_bid = 0.55
    rate = _resolve_rate("Politics")

    slip = _slippage_per_dollar(trade, liq, None)
    expected_post = _expected_pnl_post_fix(entry, exit_bid, rate, slip)
    expected_pre = _expected_pnl_pre_fix(entry, exit_bid, rate, slip)
    actual = compute_pnl_per_dollar_exit(
        entry_price=entry, exit_bid_price=exit_bid, category="Politics",
        trade_size_usdc=trade, liquidity_at_signal=liq,
    )
    check(
        "#10 thin: actual P&L matches symmetric-slippage formula",
        actual is not None and abs(actual - expected_post) < 1e-6,
        f"actual={actual:.6f} expected_post={expected_post:.6f}",
    )
    diff = expected_pre - expected_post
    check(
        "#10 thin: thinner book => larger P&L drop (~0.007 per dollar)",
        diff > 0.0060 and diff < 0.0080,
        f"diff={diff:.6f} (plan target ~0.007)",
    )


def test_exit_slippage_resolution_path_unchanged() -> None:
    section("#10 resolution path (compute_pnl_per_dollar) unaffected")

    # Resolution: market settled. YES bought at 0.40, resolved YES -> $1 per share.
    # No exit-side book impact -- settlement is at $1, not on a book.
    pnl = compute_pnl_per_dollar(
        entry_price=0.40,
        direction="YES",
        resolved_outcome="YES",
        category="Politics",
        trade_size_usdc=100.0,
        liquidity_at_signal=50_000.0,
    )
    check(
        "#10 resolution: returns a sensible P&L > 0 for winning settled trade",
        pnl is not None and pnl > 0.0,
        f"got {pnl}",
    )
    # Settlement value is hard-coded $1 internally; no exit_bid is involved.
    # Sanity: same call twice returns identical P&L (deterministic).
    pnl2 = compute_pnl_per_dollar(
        entry_price=0.40,
        direction="YES",
        resolved_outcome="YES",
        category="Politics",
        trade_size_usdc=100.0,
        liquidity_at_signal=50_000.0,
    )
    check("#10 resolution: deterministic", abs(pnl - pnl2) < 1e-12)


def test_exit_slippage_invalid_inputs() -> None:
    section("#10 exit slippage: invalid input handling preserved")

    # Pre-fix early returns -- regression sanity that we kept them.
    check(
        "#10: returns None for entry_price <= 0",
        compute_pnl_per_dollar_exit(
            entry_price=0.0, exit_bid_price=0.5, category="Politics",
            trade_size_usdc=100.0, liquidity_at_signal=50_000.0,
        ) is None,
    )
    check(
        "#10: returns None for entry_price >= 1.0",
        compute_pnl_per_dollar_exit(
            entry_price=1.0, exit_bid_price=0.5, category="Politics",
            trade_size_usdc=100.0, liquidity_at_signal=50_000.0,
        ) is None,
    )
    check(
        "#10: returns None for exit_bid_price <= 0",
        compute_pnl_per_dollar_exit(
            entry_price=0.4, exit_bid_price=0.0, category="Politics",
            trade_size_usdc=100.0, liquidity_at_signal=50_000.0,
        ) is None,
    )


def test_exit_slippage_lower_bound_clamp() -> None:
    section("#10 exit slippage: max(0.001, ...) clamps near-zero exit")

    # Slip larger than the bid -> raw value would go negative, clamp to 0.001.
    # Set up an extreme scenario: trade is large vs liquidity.
    # liquidity = $100, trade = $100 -> slip ~= min(0.10, 0.02 * sqrt(1)) = 0.02
    # exit_bid 0.005 -> 0.005 - 0.02 = -0.015 -> clamped to 0.001
    actual = compute_pnl_per_dollar_exit(
        entry_price=0.40, exit_bid_price=0.005, category="Politics",
        trade_size_usdc=100.0, liquidity_at_signal=100.0,
    )
    check(
        "#10 clamp: extreme slip on tiny exit_bid -> finite P&L (no division blowup)",
        actual is not None and math.isfinite(actual),
        f"got {actual}",
    )


# ---------------------------------------------------------------------------
# #9 engine-consumer integration test
# ---------------------------------------------------------------------------


async def test_engine_consumes_dedup_view() -> None:
    section("#9 engine consumer: _fetch_signals(dedup=True) skips unavailable first-fires")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            tag_mode = "__pass5_9_engine_test"
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1", tag_mode,
            )

            real_cid = await conn.fetchval(
                """
                SELECT condition_id FROM markets
                WHERE closed = FALSE
                LIMIT 1
                """
            )
            if real_cid is None:
                check("#9: skipped (no open binary market)", True)
                return

            t_unavail = datetime.now(timezone.utc) - timedelta(hours=2)
            t_clean = t_unavail + timedelta(minutes=10)

            # Earlier row: signal_entry_source='unavailable'
            await conn.execute(
                """
                INSERT INTO signal_log
                    (mode, category, top_n, condition_id, direction,
                     first_fired_at, last_seen_at,
                     peak_trader_count, peak_avg_portfolio_fraction,
                     peak_aggregate_usdc, peak_net_skew,
                     first_trader_count, first_avg_portfolio_fraction,
                     first_aggregate_usdc, first_net_skew,
                     market_type, signal_entry_source, signal_entry_offer,
                     liquidity_tier)
                VALUES
                    ($1, 'unavail_lens', 50, $2, 'YES',
                     $3, $3, 7, 0.10,
                     30000.0, 0.85, 7, 0.10,
                     30000.0, 0.85,
                     'binary', 'unavailable', NULL,
                     'unknown')
                """,
                tag_mode, real_cid, t_unavail,
            )
            # Later row: signal_entry_source='clob_l2', clean book
            await conn.execute(
                """
                INSERT INTO signal_log
                    (mode, category, top_n, condition_id, direction,
                     first_fired_at, last_seen_at,
                     peak_trader_count, peak_avg_portfolio_fraction,
                     peak_aggregate_usdc, peak_net_skew,
                     first_trader_count, first_avg_portfolio_fraction,
                     first_aggregate_usdc, first_net_skew,
                     market_type, signal_entry_source, signal_entry_offer,
                     liquidity_tier)
                VALUES
                    ($1, 'clean_lens', 50, $2, 'YES',
                     $3, $3, 9, 0.12,
                     45000.0, 0.88, 9, 0.12,
                     45000.0, 0.88,
                     'binary', 'clob_l2', 0.42,
                     'medium')
                """,
                tag_mode, real_cid, t_clean,
            )

            # Direct view query: post-migration the view should expose only
            # the clean row for this (cid, YES) pair (limited to our tag rows).
            view_rows = await conn.fetch(
                """
                SELECT v.signal_entry_source, v.category
                FROM vw_signals_unique_market v
                JOIN signal_log s ON s.id = v.id
                WHERE s.mode = $1 AND v.condition_id = $2 AND v.direction = 'YES'
                """,
                tag_mode, real_cid,
            )
            check(
                "#9: view exposes exactly one row for our test (cid, YES)",
                len(view_rows) == 1,
                f"got {len(view_rows)} rows: {[dict(r) for r in view_rows]}",
            )
            if view_rows:
                check(
                    "#9: view's canonical row is the clean (clob_l2) one, NOT unavailable",
                    view_rows[0]["signal_entry_source"] == "clob_l2"
                    and view_rows[0]["category"] == "clean_lens",
                    f"got source={view_rows[0]['signal_entry_source']} "
                    f"cat={view_rows[0]['category']}",
                )

            # Engine consumer: call _fetch_signals with dedup=True. Filter to
            # mode=tag_mode so we don't drown in real signal_log rows.
            #
            # _fetch_signals applies its own filter
            # `WHERE COALESCE(s.signal_entry_source, '') != 'unavailable'`
            # which is now a no-op redundancy on the dedup path (the view
            # already filters). On the dedup=False path it's load-bearing.
            filters_dedup = BacktestFilters(
                mode=tag_mode,  # narrows to our test rows; both share this mode
                dedup=True,
                include_pre_fix=False,
            )
            rows_dedup = await _fetch_signals(conn, filters_dedup)
            # Both our rows had mode=tag_mode but the view only exposes the
            # 'clean_lens' canonical row -- so the engine sees exactly one.
            our_dedup = [
                r for r in rows_dedup if r.condition_id == real_cid
                and r.direction == "YES"
            ]
            check(
                "#9: engine on dedup path returns exactly 1 row for our test pair",
                len(our_dedup) == 1,
                f"got {len(our_dedup)} rows",
            )
            if our_dedup:
                # SignalRow doesn't surface signal_entry_source -- the engine
                # filtered it at SQL level. Use `category` as a proxy: our
                # clean row has category='clean_lens', the unavailable one
                # has 'unavail_lens'.
                check(
                    "#9: engine row is the clean lens (clean_lens), NOT unavail_lens",
                    our_dedup[0].category == "clean_lens",
                    f"got category={our_dedup[0].category}",
                )

            # Sanity: with dedup=False, the engine queries signal_log directly
            # and its OWN filter drops the unavailable row -- so we still see
            # 1 row (the clean one). This proves the engine-side filter is
            # load-bearing in the non-dedup path (NOT redundant).
            filters_nondedup = BacktestFilters(
                mode=tag_mode,
                dedup=False,
                include_pre_fix=False,
            )
            rows_nondedup = await _fetch_signals(conn, filters_nondedup)
            our_nondedup = [
                r for r in rows_nondedup if r.condition_id == real_cid
                and r.direction == "YES"
            ]
            check(
                "#9: engine on non-dedup path also returns 1 (its own WHERE filter "
                "kills the unavailable row)",
                len(our_nondedup) == 1,
                f"got {len(our_nondedup)} rows",
            )

            # And with include_pre_fix=True we'd see BOTH (the engine-side
            # filter is gated on include_pre_fix=False).
            filters_with_pre = BacktestFilters(
                mode=tag_mode,
                dedup=False,
                include_pre_fix=True,
            )
            rows_with_pre = await _fetch_signals(conn, filters_with_pre)
            our_with_pre = [
                r for r in rows_with_pre if r.condition_id == real_cid
                and r.direction == "YES"
            ]
            check(
                "#9: include_pre_fix=True restores the unavailable row on non-dedup path",
                len(our_with_pre) == 2,
                f"got {len(our_with_pre)} rows",
            )

            # Cleanup
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1", tag_mode,
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    test_code_shape_exit_slippage()
    test_exit_slippage_thick_book()
    test_exit_slippage_thin_book()
    test_exit_slippage_resolution_path_unchanged()
    test_exit_slippage_invalid_inputs()
    test_exit_slippage_lower_bound_clamp()
    await test_engine_consumes_dedup_view()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 #9 + #10 engine tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
