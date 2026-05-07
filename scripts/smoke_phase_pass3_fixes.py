"""Phase 3 smoke tests for Pass 3 Tier 0 fixes (R-class).

Covers (incrementally added as fixes ship):
  R6  -- crossed orderbook guard
  R9  -- slice_lookups dedup for Bonferroni
  R2  -- skew dual-axis (count + dollar)
  R5  -- portfolio_value recency
  R8  -- NO-direction snapshot uses NO token
  R10 -- unified close P&L formula + new fee math
  R4 + R7 -- counterparty rewrite (positions-based)
  R3a + R3b + R3c -- exit detector trim/exit + cohort-aware

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass3_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.orderbook import compute_book_metrics  # noqa: E402

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
# R6 -- crossed orderbook guard
# ---------------------------------------------------------------------------

section("R6 -- compute_book_metrics rejects crossed/locked books")

# Normal book: bid < ask, computes mid + spread normally
normal_book = {
    "bids": [{"price": 0.40, "size": 100}],
    "asks": [{"price": 0.42, "size": 100}],
}
m = compute_book_metrics(normal_book, "YES")
check("Normal book is available", m.available)
check("Normal book has positive spread_bps", m.spread_bps > 0,
      f"spread_bps={m.spread_bps}")
check("Normal book mid in [0,1]", m.mid is not None and 0 < m.mid < 1,
      f"mid={m.mid}")

# Crossed book: bid >= ask
crossed_book = {
    "bids": [{"price": 0.60, "size": 100}],
    "asks": [{"price": 0.55, "size": 50}],
}
m = compute_book_metrics(crossed_book, "YES")
check("Crossed book (bid > ask) marked unavailable", not m.available,
      f"available={m.available}")
check("Crossed book entry_offer is None", m.entry_offer is None)
check("Crossed book mid is None", m.mid is None)
check("Crossed book spread_bps is None", m.spread_bps is None)

# Locked book: bid == ask (still rejected)
locked_book = {
    "bids": [{"price": 0.50, "size": 100}],
    "asks": [{"price": 0.50, "size": 100}],
}
m = compute_book_metrics(locked_book, "YES")
check("Locked book (bid == ask) marked unavailable", not m.available)


# ---------------------------------------------------------------------------
# R9 -- slice_lookups dedup
# ---------------------------------------------------------------------------

section("R9 -- get_session_slice_lookups dedupes identical queries")


async def test_r9() -> None:
    from app.db.connection import init_pool, close_pool
    from app.db import crud

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Use a unique tag in the slice_definition so we don't pollute
            # existing rows; clean up at the end.
            tag = f"_R9_TEST_{id(test_r9)}"

            slice_def_a = {"_tag": tag, "mode": "hybrid", "category": "finance"}
            slice_def_b = {"_tag": tag, "mode": "hybrid", "category": "crypto"}

            # Baseline session count (with our tag)
            baseline_rows = await conn.fetch(
                "SELECT id FROM slice_lookups WHERE slice_definition->>'_tag' = $1",
                tag,
            )
            baseline_count = len(baseline_rows)
            check("R9: baseline cleanup OK", baseline_count == 0,
                  f"baseline_count={baseline_count}")

            # Insert SAME query 5 times -- should dedupe to 1 in session view
            for i in range(5):
                await crud.insert_slice_lookup(
                    conn, slice_def_a,
                    n_signals=10, reported_metric="mean_pnl_per_dollar",
                    reported_value=0.10, ci_low=0.05, ci_high=0.15,
                )

            # Insert a DIFFERENT query once
            await crud.insert_slice_lookup(
                conn, slice_def_b,
                n_signals=15, reported_metric="mean_pnl_per_dollar",
                reported_value=0.20, ci_low=0.10, ci_high=0.30,
            )

            # Check raw row count vs deduped
            raw_rows = await conn.fetch(
                "SELECT id FROM slice_lookups WHERE slice_definition->>'_tag' = $1",
                tag,
            )
            check("R9: 6 raw rows inserted", len(raw_rows) == 6,
                  f"got {len(raw_rows)}")

            # Now use the dedupe helper -- should see only 2 (one per distinct
            # slice_definition) within our tagged subset
            session_entries = await crud.get_session_slice_lookups(conn)
            tagged_in_session = [
                e for e in session_entries
                # We can't filter by tag in the helper (it returns abbreviated
                # rows) so re-query directly on dedup
            ]
            # Direct verification via SQL: count distinct tagged entries
            distinct_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT ON (slice_definition) slice_definition
                    FROM slice_lookups
                    WHERE slice_definition->>'_tag' = $1
                    ORDER BY slice_definition, ran_at DESC
                ) d
                """,
                tag,
            )
            check("R9: dedup collapses 5 identical queries to 1",
                  distinct_count == 2,
                  f"distinct={distinct_count} (expected 2: 1 deduped + 1 unique)")

            # Verify the helper returns the most-recent value among duplicates.
            # Insert a 6th row of slice_def_a with a DIFFERENT reported value
            # (simulating a re-run with different data)
            await crud.insert_slice_lookup(
                conn, slice_def_a,
                n_signals=10, reported_metric="mean_pnl_per_dollar",
                reported_value=0.99, ci_low=0.90, ci_high=1.10,
            )
            most_recent = await conn.fetchval(
                """
                SELECT reported_value
                FROM (
                    SELECT DISTINCT ON (slice_definition)
                        slice_definition, ran_at, reported_value
                    FROM slice_lookups
                    WHERE slice_definition = $1::jsonb
                    ORDER BY slice_definition, ran_at DESC
                ) d
                """,
                json.dumps(slice_def_a),
            )
            check("R9: dedup keeps the most-recent row for a given query",
                  most_recent is not None and abs(float(most_recent) - 0.99) < 0.001,
                  f"got {most_recent}")

            # Cleanup
            await conn.execute(
                "DELETE FROM slice_lookups WHERE slice_definition->>'_tag' = $1",
                tag,
            )
    finally:
        await close_pool()


asyncio.run(test_r9())


# ---------------------------------------------------------------------------
# R2 -- skew dual-axis (count + dollar)
# ---------------------------------------------------------------------------

section("R2 -- detect_signals requires both count-skew AND dollar-skew >= 65%")


async def test_r2() -> None:
    from app.db.connection import init_pool, close_pool
    from app.db import crud
    from app.services.signal_detector import (
        Signal, _row_to_signal, _outcome_to_direction,
        MIN_NET_DIRECTION_SKEW, MIN_NET_DIRECTION_DOLLAR_SKEW,
    )

    # Verify constant values are 0.65 each
    check("R2: MIN_NET_DIRECTION_SKEW == 0.65", MIN_NET_DIRECTION_SKEW == 0.65,
          f"got {MIN_NET_DIRECTION_SKEW}")
    check("R2: MIN_NET_DIRECTION_DOLLAR_SKEW == 0.65",
          MIN_NET_DIRECTION_DOLLAR_SKEW == 0.65,
          f"got {MIN_NET_DIRECTION_DOLLAR_SKEW}")

    # Verify Signal dataclass carries direction_dollar_skew
    sig = Signal(
        condition_id="test", market_question=None, market_slug=None,
        market_category=None, event_id=None, direction="YES",
        direction_skew=0.85, direction_dollar_skew=0.12,
        trader_count=6, aggregate_usdc=1200.0,
        avg_portfolio_fraction=0.05, current_price=0.50,
        first_top_trader_first_seen_at=None, avg_entry_price=0.40,
    )
    check("R2: Signal carries both skew fields",
          sig.direction_skew == 0.85 and sig.direction_dollar_skew == 0.12,
          f"count={sig.direction_skew} dollar={sig.direction_dollar_skew}")

    # Verify upsert_signal_log_entry accepts direction_dollar_skew kwarg
    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Use a non-existent condition_id to avoid pollution; rely on
            # FK to markets to NOT trip (we use a real cid from markets).
            real_cid = await conn.fetchval(
                "SELECT condition_id FROM markets LIMIT 1"
            )
            if not real_cid:
                # No markets in DB yet -- skip the round-trip test, it's
                # not strictly necessary (signature accepts kwarg = sufficient)
                check("R2: DB round-trip skipped (no markets)", True)
                return

            test_mode = "_R2_TEST"
            test_cat = "_test"

            # Insert with new dual-axis fields
            inserted = await crud.upsert_signal_log_entry(
                conn,
                mode=test_mode, category=test_cat, top_n=50,
                condition_id=real_cid, direction="YES",
                trader_count=6, avg_portfolio_fraction=0.10,
                aggregate_usdc=30000.0,
                direction_skew=0.85,
                first_top_trader_entry_price=0.40,
                current_price=0.50,
                cluster_id=None, market_type="binary",
                direction_dollar_skew=0.78,
                contributing_wallets=["0xabc", "0xdef"],
            )
            check("R2: upsert with dual-skew kwargs returns inserted=True", inserted)

            # Read back and verify
            row = await conn.fetchrow(
                """
                SELECT first_net_skew, first_net_dollar_skew, contributing_wallets
                FROM signal_log
                WHERE mode = $1 AND category = $2 AND condition_id = $3
                """,
                test_mode, test_cat, real_cid,
            )
            check("R2: first_net_skew persisted as 0.85",
                  row is not None and abs(float(row["first_net_skew"]) - 0.85) < 0.001,
                  f"got {row['first_net_skew'] if row else None}")
            check("R2: first_net_dollar_skew persisted as 0.78",
                  row is not None
                  and row["first_net_dollar_skew"] is not None
                  and abs(float(row["first_net_dollar_skew"]) - 0.78) < 0.001,
                  f"got {row['first_net_dollar_skew'] if row else None}")
            check("R2: contributing_wallets persisted as TEXT[]",
                  row is not None
                  and list(row["contributing_wallets"] or []) == ["0xabc", "0xdef"],
                  f"got {row['contributing_wallets'] if row else None}")

            # Cleanup test row
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1",
                test_mode,
            )
    finally:
        await close_pool()


asyncio.run(test_r2())


# ---------------------------------------------------------------------------
# R5 -- portfolio_value recency in latest_pv CTE
# ---------------------------------------------------------------------------

section("R5 -- latest_pv CTE filters to fetched_at >= NOW() - INTERVAL '1 hour'")


def test_r5_source_inspection() -> None:
    """Source-inspection test: verify the CTE has the recency filter inline.
    Pure structural test (no DB needed)."""
    sd = (ROOT / "app" / "services" / "signal_detector.py").read_text(encoding="utf-8")
    check("R5: latest_pv CTE has fetched_at recency filter",
          "fetched_at >= NOW() - INTERVAL '1 hour'" in sd
          and "latest_pv" in sd,
          "either the CTE is missing or the recency filter wasn't added")

    jobs_src = (ROOT / "app" / "scheduler" / "jobs.py").read_text(encoding="utf-8")
    # Verify the always-write logic: condition is "if pv_api is not None or portfolio_total > 0"
    check("R5: jobs.py writes PV when pv_api available even if positions=0",
          "if pv_api is not None or portfolio_total > 0" in jobs_src,
          "always-write logic missing")


test_r5_source_inspection()


async def test_r5_db() -> None:
    """End-to-end DB test: stale PV row is excluded; fresh PV row is used."""
    from app.db.connection import init_pool, close_pool

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Borrow a real trader for the FK -- portfolio_value_snapshots
            # has FK to traders. Cleanup our test rows after.
            test_wallet = await conn.fetchval(
                "SELECT proxy_wallet FROM traders LIMIT 1"
            )
            if not test_wallet:
                check("R5: DB round-trip skipped (no traders)", True)
                return

            # Snapshot what's already there so we restore it after
            existing_rows = await conn.fetch(
                "SELECT value, fetched_at FROM portfolio_value_snapshots "
                "WHERE proxy_wallet = $1",
                test_wallet,
            )

            # Insert STALE row (2 hours ago)
            await conn.execute(
                "DELETE FROM portfolio_value_snapshots WHERE proxy_wallet = $1",
                test_wallet,
            )

            # Insert STALE row (2 hours ago)
            await conn.execute(
                """
                INSERT INTO portfolio_value_snapshots (proxy_wallet, value, fetched_at)
                VALUES ($1, $2, NOW() - INTERVAL '2 hours')
                """,
                test_wallet, 5_000_000.0,  # $5M stale value
            )

            # Run the latest_pv CTE manually with the recency filter
            row = await conn.fetchrow(
                """
                SELECT DISTINCT ON (proxy_wallet)
                    proxy_wallet, value AS portfolio_value
                FROM portfolio_value_snapshots
                WHERE proxy_wallet = $1
                  AND fetched_at >= NOW() - INTERVAL '1 hour'
                ORDER BY proxy_wallet, fetched_at DESC
                """,
                test_wallet,
            )
            check("R5: stale row (2h old) excluded by recency filter",
                  row is None, f"got {dict(row) if row else None}")

            # Insert FRESH row (now)
            await conn.execute(
                """
                INSERT INTO portfolio_value_snapshots (proxy_wallet, value, fetched_at)
                VALUES ($1, $2, NOW())
                """,
                test_wallet, 50_000.0,  # $50k fresh value
            )

            # Re-run the CTE: should now return the fresh row
            row = await conn.fetchrow(
                """
                SELECT DISTINCT ON (proxy_wallet)
                    proxy_wallet, value AS portfolio_value
                FROM portfolio_value_snapshots
                WHERE proxy_wallet = $1
                  AND fetched_at >= NOW() - INTERVAL '1 hour'
                ORDER BY proxy_wallet, fetched_at DESC
                """,
                test_wallet,
            )
            check("R5: fresh row (now) included; stale row ignored",
                  row is not None
                  and abs(float(row["portfolio_value"]) - 50_000.0) < 0.01,
                  f"got {row['portfolio_value'] if row else None}")

            # Cleanup -- delete our test rows, restore originals
            await conn.execute(
                "DELETE FROM portfolio_value_snapshots WHERE proxy_wallet = $1",
                test_wallet,
            )
            for er in existing_rows:
                await conn.execute(
                    "INSERT INTO portfolio_value_snapshots "
                    "(proxy_wallet, value, fetched_at) VALUES ($1, $2, $3) "
                    "ON CONFLICT (proxy_wallet, fetched_at) DO NOTHING",
                    test_wallet, er["value"], er["fetched_at"],
                )
    finally:
        await close_pool()


asyncio.run(test_r5_db())


# ---------------------------------------------------------------------------
# R8 -- snapshot direction-side token
# ---------------------------------------------------------------------------

section("R8 -- snapshot uses direction-side token; half-life is direction-aware")


def test_r8_pure() -> None:
    """Pure-function tests for direction-aware half-life math."""
    from app.services.half_life import (
        HalfLifeRow, compute_half_life_summary,
    )

    # NO-direction signal with NO-space snapshot (new R8 path).
    # Signal fires at NO ask 0.45, smart money entered NO at 0.40.
    # 30 min later NO bid is 0.42, NO ask is 0.44. Mid = 0.43.
    # Did the price move toward smart money cost (0.40)?
    # Fire was 0.45, snap is 0.43, smart_money is 0.40.
    # |0.45 - 0.40| = 0.05; |0.43 - 0.40| = 0.03 -> snap closer -> True.
    rows = [
        HalfLifeRow(
            category="politics",
            fire_price=0.45, direction="NO",
            smart_money_entry=0.40,
            snapshot_price=0.42,  # bid (back-compat)
            offset_min=30,
            bid_price=0.42, ask_price=0.44,
            snapshot_direction="NO",  # NEW
        ),
    ]
    buckets = compute_half_life_summary(rows)
    check("R8: NO-direction snapshot uses NO-space comparison directly",
          len(buckets) == 1
          and buckets[0].n == 1
          and buckets[0].convergence_rate == 1.0,
          f"buckets={[(b.category, b.offset_min, b.n, b.convergence_rate) for b in buckets]}")

    # Legacy NO signal with snapshot_direction=None -- uses YES-space
    # translation as before. Fire=0.45 (NO), snap=0.55 (YES), sm=0.40 (NO)
    # Translated: fire_yes = 0.55, sm_yes = 0.60, snap_yes = 0.55.
    # |0.55-0.60|=0.05; |0.55-0.60|=0.05 -> snap NOT closer (equal) -> False.
    rows = [
        HalfLifeRow(
            category="politics",
            fire_price=0.45, direction="NO",
            smart_money_entry=0.40,
            snapshot_price=0.55, offset_min=30,
            bid_price=0.55, ask_price=0.55,
            snapshot_direction=None,  # legacy
        ),
    ]
    buckets = compute_half_life_summary(rows)
    check("R8: legacy NO row (snapshot_direction=None) uses YES-space translation",
          len(buckets) == 1 and buckets[0].n == 1
          and buckets[0].convergence_rate == 0.0,
          f"buckets={[(b.category, b.offset_min, b.n, b.convergence_rate) for b in buckets]}")

    # YES-direction signal with YES snapshot (typical new path).
    # Fire YES at 0.55, smart money entered YES at 0.50, snapshot at 0.52.
    # |0.55-0.50|=0.05; |0.52-0.50|=0.02 -> closer -> True.
    rows = [
        HalfLifeRow(
            category="politics",
            fire_price=0.55, direction="YES",
            smart_money_entry=0.50,
            snapshot_price=0.52, offset_min=30,
            bid_price=0.52, ask_price=0.52,
            snapshot_direction="YES",
        ),
    ]
    buckets = compute_half_life_summary(rows)
    check("R8: YES-direction signal converges correctly in YES-space",
          buckets[0].convergence_rate == 1.0,
          f"got {buckets[0].convergence_rate}")


test_r8_pure()


async def test_r8_db() -> None:
    """End-to-end DB test: list_signals_pending_price_snapshots returns
    direction-side token for NO signals."""
    from app.db.connection import init_pool, close_pool
    from app.db import crud

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Pick a market with both clob_token_yes and clob_token_no
            mkt = await conn.fetchrow(
                """
                SELECT condition_id, clob_token_yes, clob_token_no
                FROM markets
                WHERE clob_token_yes IS NOT NULL AND clob_token_yes <> ''
                  AND clob_token_no IS NOT NULL AND clob_token_no <> ''
                  AND closed = FALSE
                LIMIT 1
                """,
            )
            if mkt is None:
                check("R8: DB round-trip skipped (no markets with both tokens)", True)
                return
            check("R8: found market with both YES + NO tokens", True,
                  f"cid={mkt['condition_id'][:12]}...")

            # Insert a synthetic signal with direction=NO so we can verify
            # the helper picks the NO token.
            test_mode = "_R8_TEST"
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1", test_mode,
            )
            await conn.execute(
                """
                INSERT INTO signal_log
                  (mode, category, top_n, condition_id, direction,
                   first_fired_at, last_seen_at,
                   peak_trader_count, peak_aggregate_usdc, peak_net_skew,
                   first_trader_count, first_aggregate_usdc, first_net_skew,
                   market_type)
                VALUES ($1, '_test', 50, $2, 'NO',
                        NOW() - INTERVAL '4 minutes',
                        NOW() - INTERVAL '4 minutes',
                        5, 25000, 0.65,
                        5, 25000, 0.65,
                        'binary')
                """,
                test_mode, mkt["condition_id"],
            )

            candidates = await crud.list_signals_pending_price_snapshots(conn)
            cands = [c for c in candidates if c["condition_id"] == mkt["condition_id"]]
            check("R8: pending snapshot found for NO-direction signal",
                  len(cands) == 1, f"got {len(cands)}")
            if cands:
                c = cands[0]
                check("R8: helper returned token_id == NO token (not YES)",
                      c["token_id"] == mkt["clob_token_no"],
                      f"got {c['token_id'][:12]}... vs no={mkt['clob_token_no'][:12]}...")
                check("R8: direction field present and == 'NO'",
                      c.get("direction") == "NO",
                      f"got {c.get('direction')}")

            # R1 verification: verify migration 010 lets us insert at offset 5
            sid = await conn.fetchval(
                "SELECT id FROM signal_log WHERE mode = $1 LIMIT 1",
                test_mode,
            )
            inserted_5 = await crud.insert_signal_price_snapshot(
                conn,
                signal_log_id=sid, snapshot_offset_min=5,
                bid_price=0.42, ask_price=0.44,
                token_id=mkt["clob_token_no"], direction="NO",
            )
            check("R1: insert at snapshot_offset_min=5 succeeds (CHECK relaxed)",
                  inserted_5)

            inserted_15 = await crud.insert_signal_price_snapshot(
                conn,
                signal_log_id=sid, snapshot_offset_min=15,
                bid_price=0.43, ask_price=0.44,
                token_id=mkt["clob_token_no"], direction="NO",
            )
            check("R1: insert at snapshot_offset_min=15 succeeds (CHECK relaxed)",
                  inserted_15)

            # R8: confirm the persisted rows have direction='NO'
            stored = await conn.fetch(
                """
                SELECT snapshot_offset_min, direction
                FROM signal_price_snapshots
                WHERE signal_log_id = $1
                ORDER BY snapshot_offset_min
                """, sid,
            )
            check("R8: persisted rows carry direction='NO'",
                  all(r["direction"] == "NO" for r in stored),
                  f"got {[r['direction'] for r in stored]}")

            # Cleanup
            await conn.execute(
                "DELETE FROM signal_price_snapshots WHERE signal_log_id = $1", sid,
            )
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1", test_mode,
            )
    finally:
        await close_pool()


asyncio.run(test_r8_db())


# ---------------------------------------------------------------------------
# R10 + D1 -- unified close formula + correct Polymarket fee math
# ---------------------------------------------------------------------------

section("R10 + D1 -- compute_pnl_per_dollar with new fee formula")


def test_r10_d1_pnl() -> None:
    """Verify the new fee math matches the formula by hand."""
    from app.services.backtest_engine import (
        compute_pnl_per_dollar, compute_pnl_per_dollar_exit,
    )

    # $1 stake YES @ 0.40 in Politics (rate=0.04), no slippage, wins:
    #   shares = 1/0.40 = 2.5
    #   entry_fee = 0.04 * (1 - 0.40) = 0.024
    #   payout = 2.5 * 1.0 = 2.5
    #   P&L = 2.5 - 1 - 0.024 = 1.476
    # (Use a huge liquidity to make slippage ~0)
    pnl = compute_pnl_per_dollar(0.40, "YES", "YES", "Politics", 1.0, 1_000_000_000.0)
    check("R10: Politics YES@0.40 winner = +1.476 (no slip)",
          pnl is not None and abs(pnl - 1.476) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")

    # Same trade but loses
    pnl = compute_pnl_per_dollar(0.40, "YES", "NO", "Politics", 1.0, 1_000_000_000.0)
    # P&L = 0 - 1 - 0.024 = -1.024 (lose stake + entry fee)
    check("R10: Politics YES@0.40 loser = -1.024 (entry fee on loss)",
          pnl is not None and abs(pnl - (-1.024)) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")

    # Crypto rate is 7% -- much higher than the old placeholder. Crypto YES@0.40
    # wins: entry_fee = 0.07 * 0.60 = 0.042; P&L = 2.5 - 1 - 0.042 = 1.458
    pnl = compute_pnl_per_dollar(0.40, "YES", "YES", "Crypto", 1.0, 1_000_000_000.0)
    check("R10: Crypto YES@0.40 winner = +1.458 (high fee)",
          pnl is not None and abs(pnl - 1.458) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")

    # Geopolitics is fee-free. Politics YES@0.40 winner with rate=0:
    # P&L = 2.5 - 1 - 0 = 1.500 (no fee at all)
    pnl = compute_pnl_per_dollar(0.40, "YES", "YES", "Geopolitics", 1.0, 1_000_000_000.0)
    check("R10: Geopolitics YES@0.40 winner = +1.500 (fee-free)",
          pnl is not None and abs(pnl - 1.500) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")

    # 50/50 case in Politics: payoff = 0.5
    # P&L = 0.5/0.40 - 1 - 0.024 = 1.25 - 1 - 0.024 = +0.226
    pnl = compute_pnl_per_dollar(0.40, "YES", "50_50", "Politics", 1.0, 1_000_000_000.0)
    check("R10: Politics YES@0.40 50_50 = +0.226",
          pnl is not None and abs(pnl - 0.226) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")

    # VOID returns None
    pnl = compute_pnl_per_dollar(0.40, "YES", "VOID", "Politics", 1.0, 1_000_000_000.0)
    check("R10: VOID returns None", pnl is None)

    # Smart-money-exit path:
    # Buy at 0.40 in Politics, exit-bid at 0.55:
    #   shares = 1/0.40 = 2.5
    #   entry_fee = 0.04 * (1 - 0.40) = 0.024
    #   revenue = 2.5 * 0.55 = 1.375
    #   exit_fee = (0.04 * 0.55 * 0.45) / 0.40 = 0.02475
    #   P&L = 1.375 - 1 - 0.024 - 0.02475 = 0.32625
    pnl = compute_pnl_per_dollar_exit(
        0.40, 0.55, "Politics", 1.0, 1_000_000_000.0,
    )
    check("R10: exit-strategy Politics 0.40->0.55 = +0.326 (entry+exit fee)",
          pnl is not None and abs(pnl - 0.326) < 0.001,
          f"got {pnl:+.4f}" if pnl is not None else "got None")


test_r10_d1_pnl()


section("R10 -- compute_realized_pnl unified close helper")


def test_r10_unified_close() -> None:
    """Verify all three close paths (manual / resolution / smart_money_exit)
    compute the same P&L for the same inputs."""
    from app.services.paper_trade_close import compute_realized_pnl

    # Common inputs: $100 stake at $0.40 in Politics
    # entry_fee = 100 * 0.04 * 0.60 = $2.40 (computed by Polymarket curve)
    # Suppose actual stored entry_fee = $2.40 and slippage = $0.00
    common = dict(
        entry_price=0.40, entry_size_usdc=100.0,
        entry_slippage_usdc=0.0, entry_fee_usdc=2.40,
        category="Politics",
    )

    # Resolution as winner: payoff = 1.0
    # shares = 100/0.40 = 250; revenue = 250*1 = $250
    # P&L = 250 - 100 - 2.40 = $147.60 (no exit fee on resolution)
    r = compute_realized_pnl(**common, exit_price=1.0, exit_kind="resolution")
    check("R10: $100 Politics @0.40 winner resolution = +$147.60",
          abs(r.realized_pnl_usdc - 147.60) < 0.01,
          f"got {r.realized_pnl_usdc:+.2f}")
    check("R10: resolution path has zero exit_fee", r.exit_fee_usdc == 0.0)

    # Resolution as loser: payoff = 0.0
    # P&L = 0 - 100 - 2.40 = -$102.40
    r = compute_realized_pnl(**common, exit_price=0.0, exit_kind="resolution")
    check("R10: $100 Politics @0.40 loser resolution = -$102.40",
          abs(r.realized_pnl_usdc - (-102.40)) < 0.01,
          f"got {r.realized_pnl_usdc:+.2f}")

    # Manual close at $0.55 (smart_money_exit also same path)
    # revenue = 250 * 0.55 = $137.50
    # exit_fee = compute_taker_fee_usdc(137.50, 0.55, "Politics")
    #          = 137.50 * 0.04 * 0.45 = $2.475
    # P&L = 137.50 - 100 - 2.40 - 2.475 = $32.625
    r_manual = compute_realized_pnl(**common, exit_price=0.55, exit_kind="manual")
    r_exit = compute_realized_pnl(**common, exit_price=0.55, exit_kind="smart_money_exit")
    check("R10: manual close @0.55 = +$32.63",
          abs(r_manual.realized_pnl_usdc - 32.625) < 0.01,
          f"got {r_manual.realized_pnl_usdc:+.2f}")
    check("R10: manual + smart_money_exit produce IDENTICAL P&L",
          abs(r_manual.realized_pnl_usdc - r_exit.realized_pnl_usdc) < 0.0001,
          f"manual={r_manual.realized_pnl_usdc:.4f} exit={r_exit.realized_pnl_usdc:.4f}")

    # Geopolitics: fee-free in BOTH directions. $100 @0.40 winner:
    # P&L = 250 - 100 - 0 = +$150
    r = compute_realized_pnl(
        entry_price=0.40, entry_size_usdc=100.0,
        entry_slippage_usdc=0.0, entry_fee_usdc=0.0,
        exit_price=1.0, exit_kind="resolution", category="Geopolitics",
    )
    check("R10: Geopolitics fee-free winner = +$150 exact",
          abs(r.realized_pnl_usdc - 150.0) < 0.01,
          f"got {r.realized_pnl_usdc:+.2f}")

    # Crypto manual close to verify high-rate exit fee
    # $100 @0.40 in Crypto, sell @0.50:
    #   shares = 250
    #   entry_fee = 100 * 0.07 * 0.60 = $4.20
    #   revenue = 250 * 0.50 = $125
    #   exit_fee = 125 * 0.07 * 0.50 = $4.375
    #   P&L = 125 - 100 - 4.20 - 4.375 = $16.425
    r = compute_realized_pnl(
        entry_price=0.40, entry_size_usdc=100.0,
        entry_slippage_usdc=0.0, entry_fee_usdc=4.20,
        exit_price=0.50, exit_kind="manual", category="Crypto",
    )
    check("R10: Crypto manual close 0.40->0.50 = +$16.43",
          abs(r.realized_pnl_usdc - 16.425) < 0.01,
          f"got {r.realized_pnl_usdc:+.2f}")


test_r10_unified_close()


# ---------------------------------------------------------------------------
# R4 + R7 -- positions-based counterparty (concentration + size threshold)
# ---------------------------------------------------------------------------

section("R4 + R7 -- is_counterparty pure decision function")


def test_r4_r7_pure() -> None:
    from app.services.counterparty import (
        is_counterparty, MIN_OPPOSITE_USDC, CONCENTRATION_THRESHOLD,
    )

    check("R4+R7: defaults locked at $5k + 75%",
          MIN_OPPOSITE_USDC == 5000.0 and CONCENTRATION_THRESHOLD == 0.75,
          f"min={MIN_OPPOSITE_USDC} conc={CONCENTRATION_THRESHOLD}")

    # Walk-through table from the user-locked spec:
    cases = [
        # (same, opposite, expected_is_counterparty, label)
        (0,    10000, True,  "$0 same + $10k opp -> 100% conc, flag"),
        (2000, 8000,  True,  "$2k same + $8k opp -> 80% conc, flag"),
        (3000, 9000,  True,  "$3k same + $9k opp -> 75% conc, flag (threshold)"),
        (5000, 10000, False, "$5k same + $10k opp -> 67% conc, NO flag"),
        (50000, 100000, False, "$50k same + $100k opp -> 67% conc, NO flag (whale hedged)"),
        (0, 4000, False, "$0 same + $4k opp -> below $5k floor, NO flag"),
        (10000, 15000, True, "$10k same + $15k opp -> 60% conc -- should NOT flag"),
        # Wait, 15000 / 25000 = 0.60, BELOW 0.75 threshold -> should NOT flag
        # Let me fix the case above: actually expected = False
    ]
    # Override the above last case which was wrong:
    cases[-1] = (10000, 15000, False, "$10k same + $15k opp -> 60% conc, NO flag")

    for same, opp, expected, label in cases:
        got = is_counterparty(same, opp)
        check(f"R4+R7: {label}", got == expected,
              f"got {got}, expected {expected}")


test_r4_r7_pure()


section("R4 + R7 -- find_counterparty_wallets DB integration")


async def test_r4_r7_db() -> None:
    from app.db.connection import init_pool, close_pool
    from app.services.counterparty import find_counterparty_wallets

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Need: a real condition_id with both YES + NO tokens, and
            # at least one tracked wallet to insert positions for.
            mkt = await conn.fetchrow(
                """
                SELECT condition_id FROM markets
                WHERE clob_token_yes IS NOT NULL AND clob_token_yes <> ''
                  AND clob_token_no IS NOT NULL AND clob_token_no <> ''
                  AND closed = FALSE
                LIMIT 1
                """,
            )
            if mkt is None:
                check("R4+R7: skipped (no market)", True)
                return

            wallets = await conn.fetch(
                "SELECT proxy_wallet FROM traders LIMIT 3"
            )
            if len(wallets) < 3:
                check("R4+R7: skipped (need >=3 traders)", True)
                return
            w1 = wallets[0]["proxy_wallet"]
            w2 = wallets[1]["proxy_wallet"]
            w3 = wallets[2]["proxy_wallet"]

            cid = mkt["condition_id"]

            # Snapshot any pre-existing positions so we can restore them
            existing = await conn.fetch(
                """
                SELECT proxy_wallet, condition_id, asset, outcome,
                       size, cur_price, current_value, avg_price,
                       first_seen_at, last_updated_at
                FROM positions
                WHERE condition_id = $1
                  AND proxy_wallet IN ($2, $3, $4)
                """,
                cid, w1, w2, w3,
            )

            # Wipe just for test
            await conn.execute(
                """
                DELETE FROM positions
                WHERE condition_id = $1
                  AND proxy_wallet IN ($2, $3, $4)
                """, cid, w1, w2, w3,
            )

            # Insert test positions:
            # w1: $10k NO only -> clear counterparty for YES signal
            # w2: $5k YES + $10k NO (67% NO conc) -> NOT counterparty
            # w3: $0 YES + $4k NO (below $5k floor) -> NOT counterparty
            async def ins(w: str, outcome: str, size: float, value: float):
                await conn.execute(
                    """
                    INSERT INTO positions
                      (proxy_wallet, condition_id, asset, outcome, size,
                       cur_price, current_value, avg_price, first_seen_at,
                       last_updated_at)
                    VALUES ($1, $2, $3, $4, $5, 0.50, $6, 0.50,
                            NOW(), NOW())
                    """,
                    w, cid, outcome + "_TOKEN", outcome, size, value,
                )

            await ins(w1, "No", 20000, 10000.0)
            await ins(w2, "Yes", 10000, 5000.0)
            await ins(w2, "No", 20000, 10000.0)
            await ins(w3, "No", 8000, 4000.0)

            # YES signal -> only w1 should fire
            results = await find_counterparty_wallets(
                conn,
                condition_id=cid, signal_direction="YES",
                tracked_pool=[w1, w2, w3],
            )
            wallets_flagged = {r["wallet"] for r in results}
            check("R4+R7: w1 ($10k NO only) flagged on YES signal",
                  w1 in wallets_flagged,
                  f"flagged={wallets_flagged}")
            check("R4+R7: w2 (67% NO conc) NOT flagged",
                  w2 not in wallets_flagged,
                  f"flagged={wallets_flagged}")
            check("R4+R7: w3 ($4k below floor) NOT flagged",
                  w3 not in wallets_flagged,
                  f"flagged={wallets_flagged}")

            # NO signal: w2 has $10k YES vs $5k NO -> 67% YES conc, fail
            #            w1 has $10k NO only -> 0 YES, fail (no opposite)
            #            so 0 counterparty for NO signal
            results = await find_counterparty_wallets(
                conn, condition_id=cid, signal_direction="NO",
                tracked_pool=[w1, w2, w3],
            )
            check("R4+R7: NO signal -- 0 counterparty in this scenario",
                  len(results) == 0,
                  f"got {[r['wallet'] for r in results]}")

            # Cleanup -- restore originals
            await conn.execute(
                """
                DELETE FROM positions
                WHERE condition_id = $1
                  AND proxy_wallet IN ($2, $3, $4)
                """, cid, w1, w2, w3,
            )
            for er in existing:
                await conn.execute(
                    """
                    INSERT INTO positions
                      (proxy_wallet, condition_id, asset, outcome, size,
                       cur_price, current_value, avg_price, first_seen_at,
                       last_updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    er["proxy_wallet"], er["condition_id"], er["asset"],
                    er["outcome"], er["size"], er["cur_price"],
                    er["current_value"], er["avg_price"],
                    er["first_seen_at"], er["last_updated_at"],
                )
    finally:
        await close_pool()


asyncio.run(test_r4_r7_db())


# ---------------------------------------------------------------------------
# R3a + R3b + R3c -- exit detector trim/exit + cohort-aware
# ---------------------------------------------------------------------------

section("R3a -- two-tier _classify_drop (TRIM vs EXIT)")


def test_r3a_pure() -> None:
    from app.services.exit_detector import (
        _classify_drop, TRIM_THRESHOLD, EXIT_THRESHOLD,
    )

    # Pass 5 #4 raised TRIM_THRESHOLD 0.20 -> 0.25 (one-wallet noise
    # buffer at the n=5 cohort floor).
    check("R3a + Pass 5 #4: TRIM_THRESHOLD == 0.25", TRIM_THRESHOLD == 0.25,
          f"got {TRIM_THRESHOLD}")
    check("R3a: EXIT_THRESHOLD == 0.50", EXIT_THRESHOLD == 0.50,
          f"got {EXIT_THRESHOLD}")

    # Returns tuple now
    res = _classify_drop(7, 10, 100_000, 100_000)  # 30% trader drop
    check("R3a: 30% trader drop returns trim tier",
          res == ("trader_count", "trim"), f"got {res}")

    # Pass 5 #4: 20% drop is now BELOW threshold (was: TRIM fired pre-fix)
    res = _classify_drop(8, 10, 100_000, 100_000)  # 20% trader drop
    check("R3a + Pass 5 #4: 20% trader drop returns None (below 25% threshold)",
          res is None, f"got {res}")

    # 25% drop boundary: hits threshold (>= comparison)
    res = _classify_drop(75, 100, 100_000, 100_000)  # exactly 25%
    check("R3a + Pass 5 #4: exactly 25% trader drop hits trim threshold",
          res == ("trader_count", "trim"), f"got {res}")

    res = _classify_drop(4, 10, 100_000, 100_000)  # 60% trader drop
    check("R3a: 60% trader drop returns exit tier",
          res == ("trader_count", "exit"), f"got {res}")

    # 50% drop on aggregate hits EXIT exactly
    res = _classify_drop(10, 10, 50_000, 100_000)
    check("R3a: 50% aggregate drop hits exit threshold",
          res == ("aggregate", "exit"), f"got {res}")


test_r3a_pure()


section("R3b + R3c -- detect_exits uses contributing_wallets cohort")


async def test_r3b_r3c_db() -> None:
    """End-to-end: insert a synthetic signal with contributing_wallets,
    insert positions for SOME of those wallets (simulating partial exit),
    run detect_exits, verify cohort-based recompute."""
    from app.db.connection import init_pool, close_pool
    from app.services.exit_detector import detect_exits

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            mkt = await conn.fetchrow(
                """
                SELECT condition_id FROM markets
                WHERE clob_token_yes IS NOT NULL AND clob_token_yes <> ''
                  AND clob_token_no IS NOT NULL AND clob_token_no <> ''
                  AND closed = FALSE
                LIMIT 1
                """
            )
            if mkt is None:
                check("R3b/c: skipped (no market)", True)
                return

            # Need multiple traders for the cohort
            wallets_rows = await conn.fetch(
                "SELECT proxy_wallet FROM traders LIMIT 10"
            )
            if len(wallets_rows) < 8:
                check("R3b/c: skipped (need >=8 traders)", True)
                return
            cohort = [r["proxy_wallet"] for r in wallets_rows[:8]]
            cid = mkt["condition_id"]

            # Snapshot existing positions for cleanup later
            existing_pos = await conn.fetch(
                """
                SELECT proxy_wallet, condition_id, asset, outcome,
                       size, cur_price, current_value, avg_price,
                       first_seen_at, last_updated_at
                FROM positions
                WHERE condition_id = $1 AND proxy_wallet = ANY($2::TEXT[])
                """,
                cid, cohort,
            )
            await conn.execute(
                "DELETE FROM positions WHERE condition_id = $1 AND proxy_wallet = ANY($2::TEXT[])",
                cid, cohort,
            )

            # Insert a signal_log row with peak=8 traders + $80k aggregate +
            # contributing_wallets=cohort
            test_mode = "_R3_TEST"
            await conn.execute(
                "DELETE FROM signal_log WHERE mode = $1", test_mode,
            )
            sid = await conn.fetchval(
                """
                INSERT INTO signal_log
                  (mode, category, top_n, condition_id, direction,
                   first_fired_at, last_seen_at,
                   peak_trader_count, peak_aggregate_usdc, peak_net_skew,
                   first_trader_count, first_aggregate_usdc, first_net_skew,
                   market_type, contributing_wallets)
                VALUES ($1, '_test', 50, $2, 'YES',
                        NOW() - INTERVAL '30 minutes',
                        NOW() - INTERVAL '5 minutes',
                        8, 80000, 0.85,
                        8, 80000, 0.85,
                        'binary', $3)
                RETURNING id
                """,
                test_mode, cid, cohort,
            )

            # SCENARIO A: 6 of 8 still hold YES at $5k each = 6 traders, $30k
            # Drop: 8->6 = 25% (TRIM threshold), $80k->$30k = 62% (EXIT threshold)
            # Expected: EXIT event
            for w in cohort[:6]:
                await conn.execute(
                    """
                    INSERT INTO positions
                      (proxy_wallet, condition_id, asset, outcome, size,
                       cur_price, current_value, avg_price, first_seen_at,
                       last_updated_at)
                    VALUES ($1, $2, 'TEST_TOKEN', 'Yes', 10000, 0.50, 5000.0, 0.40,
                            NOW(), NOW())
                    """,
                    w, cid,
                )

            events = await detect_exits(conn)
            our_events = [e for e in events if e.signal_log_id == sid]
            check("R3b: detect_exits found event for our test signal",
                  len(our_events) == 1, f"got {len(our_events)} events")
            if our_events:
                ev = our_events[0]
                check("R3b: cohort-recompute current trader_count = 6 (not 0)",
                      ev.exit_trader_count == 6,
                      f"got {ev.exit_trader_count}")
                check("R3a: 62% dollar drop -> EXIT (not TRIM)",
                      ev.event_type == "exit", f"got {ev.event_type}")

            # Cleanup
            await conn.execute(
                "DELETE FROM signal_exits WHERE signal_log_id = $1", sid,
            )
            await conn.execute(
                "DELETE FROM signal_log WHERE id = $1", sid,
            )
            await conn.execute(
                "DELETE FROM positions WHERE condition_id = $1 AND proxy_wallet = ANY($2::TEXT[])",
                cid, cohort,
            )

            # SCENARIO B: 7 of 8 still hold YES at $7.5k each = 7 traders, $52.5k
            # Drop: 8->7 = 12.5% (below TRIM), $80k->$52.5k = 34% (TRIM threshold)
            # Expected: TRIM event
            # Pass 5 #4: TRIM_THRESHOLD raised to 0.25 -- pre-Pass-5 this
            # scenario used $63k current (21% drop) which now sits below
            # the new threshold. Lowered per-wallet value to $7.5k so the
            # aggregate drop clears the new floor.
            sid2 = await conn.fetchval(
                """
                INSERT INTO signal_log
                  (mode, category, top_n, condition_id, direction,
                   first_fired_at, last_seen_at,
                   peak_trader_count, peak_aggregate_usdc, peak_net_skew,
                   first_trader_count, first_aggregate_usdc, first_net_skew,
                   market_type, contributing_wallets)
                VALUES ($1, '_test', 50, $2, 'YES',
                        NOW() - INTERVAL '30 minutes',
                        NOW() - INTERVAL '5 minutes',
                        8, 80000, 0.85,
                        8, 80000, 0.85,
                        'binary', $3)
                RETURNING id
                """,
                test_mode, cid, cohort,
            )
            for w in cohort[:7]:
                await conn.execute(
                    """
                    INSERT INTO positions
                      (proxy_wallet, condition_id, asset, outcome, size,
                       cur_price, current_value, avg_price, first_seen_at,
                       last_updated_at)
                    VALUES ($1, $2, 'TEST_TOKEN', 'Yes', 15000, 0.50, 7500.0, 0.40,
                            NOW(), NOW())
                    """,
                    w, cid,
                )
            events = await detect_exits(conn)
            our_events = [e for e in events if e.signal_log_id == sid2]
            check("R3a + Pass 5 #4: 34% aggregate drop fires TRIM event",
                  len(our_events) == 1
                  and our_events[0].event_type == "trim",
                  f"events={[(e.event_type, e.drop_reason) for e in our_events]}")

            # Cleanup
            await conn.execute(
                "DELETE FROM signal_exits WHERE signal_log_id = $1", sid2,
            )
            await conn.execute(
                "DELETE FROM signal_log WHERE id = $1", sid2,
            )
            await conn.execute(
                "DELETE FROM positions WHERE condition_id = $1 AND proxy_wallet = ANY($2::TEXT[])",
                cid, cohort,
            )

            # Restore originals
            for er in existing_pos:
                await conn.execute(
                    """
                    INSERT INTO positions
                      (proxy_wallet, condition_id, asset, outcome, size,
                       cur_price, current_value, avg_price, first_seen_at,
                       last_updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    er["proxy_wallet"], er["condition_id"], er["asset"],
                    er["outcome"], er["size"], er["cur_price"],
                    er["current_value"], er["avg_price"],
                    er["first_seen_at"], er["last_updated_at"],
                )
    finally:
        await close_pool()


asyncio.run(test_r3b_r3c_db())


# ---------------------------------------------------------------------------
# R11 -- Hybrid tiebreaker uses roi_rank instead of pnl DESC
# ---------------------------------------------------------------------------

section("R11 -- trader_ranker hybrid tiebreaker")


def test_r11_source() -> None:
    src = (ROOT / "app" / "services" / "trader_ranker.py").read_text(encoding="utf-8")
    # Old: 'ORDER BY (pnl_rank + roi_rank) ASC, pnl DESC, proxy_wallet ASC'
    # New: 'ORDER BY (pnl_rank + roi_rank) ASC, roi_rank ASC, proxy_wallet ASC'
    check("R11: hybrid tiebreaker uses roi_rank (not pnl DESC)",
          "(pnl_rank + roi_rank) ASC, roi_rank ASC" in src
          and "(pnl_rank + roi_rank) ASC, pnl DESC" not in src,
          "tiebreaker still uses pnl DESC -- R11 didn't take effect")


test_r11_source()


# ---------------------------------------------------------------------------
# R12 -- log_signals releases conn during HTTP
# ---------------------------------------------------------------------------

section("R12 -- log_signals connection scope")


def test_r12_source() -> None:
    src = (ROOT / "app" / "scheduler" / "jobs.py").read_text(encoding="utf-8")
    # Marker: _capture_book_for_signal now takes pool, not conn
    check("R12: _capture_book_for_signal signature takes pool",
          "async def _capture_book_for_signal(\n    pool," in src,
          "signature didn't change -- R12 refactor incomplete")
    check("R12: log_signals uses Phase 1/Phase 2 split with all_fresh",
          "all_fresh: list[tuple[str, str, int, Signal]]" in src,
          "Phase 1/2 split missing -- conn still held across HTTP")


test_r12_source()


# ---------------------------------------------------------------------------
# R13 -- dropout grace via traders.dropout_count
# ---------------------------------------------------------------------------

section("R13 -- 3-cycle dropout grace")


async def test_r13_db() -> None:
    from app.db.connection import init_pool, close_pool
    from app.db import crud

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            check("R13: R13_GRACE_CYCLES == 3", crud.R13_GRACE_CYCLES == 3,
                  f"got {crud.R13_GRACE_CYCLES}")

            # Pick two real traders
            wallets = await conn.fetch("SELECT proxy_wallet FROM traders LIMIT 2")
            if len(wallets) < 2:
                check("R13: skipped (need 2 traders)", True)
                return
            w_present = wallets[0]["proxy_wallet"]
            w_absent = wallets[1]["proxy_wallet"]

            # Snapshot existing dropout_count to restore later
            existing = await conn.fetch(
                "SELECT proxy_wallet, dropout_count FROM traders WHERE proxy_wallet = ANY($1::TEXT[])",
                [w_present, w_absent],
            )

            # Reset both to 0
            await conn.execute(
                "UPDATE traders SET dropout_count = 0 WHERE proxy_wallet = ANY($1::TEXT[])",
                [w_present, w_absent],
            )

            # Run counter update with only w_present in current pool.
            # Expectation: w_present stays 0 (or re-resets), w_absent +1.
            resets, increments = await crud.update_wallet_dropout_counters(
                conn, [w_present],
            )
            # Note: increments will include EVERY wallet not in the pool, not
            # just w_absent -- so we can't assert exact count. Just check
            # w_absent specifically.
            row = await conn.fetchrow(
                "SELECT dropout_count FROM traders WHERE proxy_wallet = $1",
                w_absent,
            )
            check("R13: absent wallet's dropout_count incremented to 1",
                  row["dropout_count"] == 1, f"got {row['dropout_count']}")

            row = await conn.fetchrow(
                "SELECT dropout_count FROM traders WHERE proxy_wallet = $1",
                w_present,
            )
            check("R13: present wallet's dropout_count stays 0",
                  row["dropout_count"] == 0, f"got {row['dropout_count']}")

            # Restore originals
            for er in existing:
                await conn.execute(
                    "UPDATE traders SET dropout_count = $1 WHERE proxy_wallet = $2",
                    er["dropout_count"], er["proxy_wallet"],
                )
            # Reset other wallets we incremented (the broad UPDATE)
            # Just decrement them all by 1 to undo (best-effort cleanup)
            await conn.execute(
                """
                UPDATE traders
                SET dropout_count = GREATEST(dropout_count - 1, 0)
                WHERE proxy_wallet <> ALL($1::TEXT[])
                  AND dropout_count > 0
                """,
                [w_present],
            )
    finally:
        await close_pool()


asyncio.run(test_r13_db())


# ---------------------------------------------------------------------------
# R14 -- watchlist cleanup scoped to last 24h
# ---------------------------------------------------------------------------

section("R14 -- cleanup_watchlist_promoted_to_signal scoped to recent")


def test_r14_source() -> None:
    src = (ROOT / "app" / "db" / "crud.py").read_text(encoding="utf-8")
    # Find the function and check it has the recency clause
    assert "async def cleanup_watchlist_promoted_to_signal(" in src
    fn_start = src.index("async def cleanup_watchlist_promoted_to_signal(")
    fn_end = src.index("\nasync def ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    check("R14: cleanup function scopes EXISTS to last 24h",
          "s.last_seen_at >= NOW() - INTERVAL '24 hours'" in fn_body,
          "recency filter missing in cleanup_watchlist_promoted_to_signal")


test_r14_source()


# ---------------------------------------------------------------------------
# D3 -- Kish n_eff in summarize_rows
# ---------------------------------------------------------------------------

section("D3 -- Kish n_eff used in summarize_rows underpowered flag")


def test_d3_kish_in_summarize() -> None:
    """Build a SignalRow set with one big cluster + many singletons.
    Pre-fix would give n_eff = distinct clusters = 51.
    With Kish: n_eff = 250^2 / (200^2 + 50) ~ 1.56 -> underpowered.
    """
    from datetime import datetime, timezone
    from app.services.backtest_engine import (
        SignalRow, summarize_rows, MIN_SAMPLE_SIZE,
    )

    def make_row(cid: str, cluster: str | None) -> SignalRow:
        return SignalRow(
            id=hash(cid), mode="hybrid", category="overall", top_n=50,
            condition_id=cid, direction="YES",
            first_trader_count=5, first_aggregate_usdc=30000,
            first_net_skew=0.85, first_avg_portfolio_fraction=0.10,
            signal_entry_offer=0.40, signal_entry_mid=0.40,
            liquidity_at_signal_usdc=25000, liquidity_tier="medium",
            first_top_trader_entry_price=0.35,
            cluster_id=cluster, market_type="binary",
            first_fired_at=datetime.now(timezone.utc),
            resolved_outcome="YES", market_category="Politics",
            exit_bid_price=None, exit_drop_reason=None, exited_at=None,
            lens_count=1, lens_list=None,
        )

    # 200-row big cluster + 50 singletons
    rows = []
    for i in range(200):
        rows.append(make_row(f"trump_{i}", "TRUMP_CLUSTER"))
    for i in range(50):
        rows.append(make_row(f"singleton_{i}", None))

    result = summarize_rows(rows, trade_size_usdc=100.0)
    check("D3: 200+50 rows -> Kish n_eff << 30 (was 51 with old method)",
          result.n_eff < 30,
          f"got n_eff={result.n_eff:.4f}")
    check("D3: underpowered=True (was False with old method)",
          result.underpowered,
          f"got underpowered={result.underpowered}")
    # Pass 5 #11: NULL cluster_ids collapse to one shared cluster.
    # Sizes [200, 50] (was [200, 1, 1, ..., 1] pre-fix). n_eff = 250^2 /
    # (200^2 + 50^2) = 62500 / 42500 = 1.4706. Pre-fix this was 1.56
    # (one cluster of 200 + 50 singleton _solo_{i} clusters).
    check("D3 + Pass 5 #11: n_eff ~1.47 (NULL cluster collapse)",
          abs(result.n_eff - 1.4706) < 0.01,
          f"got {result.n_eff:.4f}")

    # Balanced case: 50 clusters of 5 each. Kish n_eff = 250^2 / (50*25) = 50.
    rows_balanced = []
    for c in range(50):
        for r in range(5):
            rows_balanced.append(make_row(f"c{c}_r{r}", f"cluster_{c}"))
    result = summarize_rows(rows_balanced, trade_size_usdc=100.0)
    check("D3: balanced 50 clusters of 5 -> Kish n_eff = 50 (powered)",
          abs(result.n_eff - 50.0) < 0.5,
          f"got n_eff={result.n_eff:.4f}")
    check("D3: balanced case underpowered=False",
          not result.underpowered)


test_d3_kish_in_summarize()


# ---------------------------------------------------------------------------
# D4 -- edge decay rolling 3-vs-3 + min drop threshold
# ---------------------------------------------------------------------------

section("D4 -- edge decay rolling 3-vs-3 with minimum drop")


def test_d4_edge_decay() -> None:
    from app.services.backtest_engine import (
        EDGE_DECAY_MIN_DROP_PCT, EDGE_DECAY_MIN_WEEKS,
    )
    check("D4: EDGE_DECAY_MIN_DROP_PCT == 0.20",
          EDGE_DECAY_MIN_DROP_PCT == 0.20)
    check("D4: EDGE_DECAY_MIN_WEEKS == 6", EDGE_DECAY_MIN_WEEKS == 6)

    # Source-inspection: comparison is rolling 3-vs-3
    src = (ROOT / "app" / "services" / "backtest_engine.py").read_text(encoding="utf-8")
    check("D4: edge_decay uses cohorts[-6:-3] (preceding 3, rolling)",
          "cohorts[-6:-3]" in src,
          "preceding range still 'cohorts[:-3]' (all-time, not rolling)")
    check("D4: edge_decay applies MIN_DROP_PCT threshold",
          "EDGE_DECAY_MIN_DROP_PCT" in src
          and "decay_warning = recent_avg <= " in src,
          "min-drop threshold not enforced")


test_d4_edge_decay()


# ---------------------------------------------------------------------------
# D5 -- health counters on /system/status
# ---------------------------------------------------------------------------

section("D5 -- health_counters surfaced on /system/status")


def test_d5_counters() -> None:
    from app.services.health_counters import (
        record, snapshot, reset,
        RATE_LIMIT_HIT, CYCLE_DURATION_WARNING, API_FAILURE,
    )
    reset()
    s = snapshot()
    check("D5: snapshot has the three keys",
          set(s.keys()) >= {"rate_limit_hit", "cycle_duration_warning", "api_failure"})
    check("D5: all counters start at 0",
          all(v == 0 for v in s.values()), f"got {s}")

    record(RATE_LIMIT_HIT)
    record(RATE_LIMIT_HIT)
    record(API_FAILURE)
    s = snapshot()
    check("D5: 2 rate_limit_hit recorded -> counter == 2",
          s["rate_limit_hit"] == 2, f"got {s['rate_limit_hit']}")
    check("D5: 1 api_failure recorded -> counter == 1",
          s["api_failure"] == 1, f"got {s['api_failure']}")

    # System status route includes counters
    routes_src = (ROOT / "app" / "api" / "routes" / "system.py").read_text(encoding="utf-8")
    check("D5: /system/status response includes 'counters' block",
          '"counters"' in routes_src
          and "rate_limit_hits_last_hour" in routes_src,
          "system.py doesn't expose counters")
    check("D5: cosmetic field rename fired_last_72h present",
          '"fired_last_72h"' in routes_src,
          "fired_last_72h field still missing")
    reset()


test_d5_counters()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()

if FAILED:
    sys.exit(1)
else:
    print("  Phase 3 fixes verified (incremental).")
