"""Smoke tests for Phase B2 — B2 + B3 + B4 + B10 + B11 + B12.

Coverage:
  - Migration 008: counterparty_warning column, watchlist_signals,
    signal_price_snapshots, insider_wallets tables exist
  - B2 counterparty: pure match logic + maker-address normalization +
    set_counterparty_warning CRUD round-trip
  - B3 watchlist: detector floor logic + watchlist CRUD round-trip +
    dropout cleanup + mutual exclusion with signal_log
  - B4 half-life: pick_offset_for_age math + compute_half_life_summary
    convergence semantics + signal_price_snapshots CRUD
  - B10 latency: profile resolution + deterministic sampling + offset
    nearest-match + _apply_latency mutates signal_entry_offer
  - B11 edge decay: cohort grouping + decay_warning trigger conditions +
    insufficient_history flag
  - B12 insider: CRUD round-trip + insider_holdings_for_markets + override
    behavior of upsert_insider_wallet

Run: ./venv/Scripts/python.exe scripts/smoke_phase_b2.py
"""

from __future__ import annotations

import asyncio
import dataclasses
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
    LATENCY_PROFILES,
    LATENCY_SNAPSHOT_OFFSETS,
    LATENCY_OFFSET_TOLERANCE_MIN,
    _apply_latency,
    _nearest_snapshot_offset,
    _resolve_latency_window,
    _sampled_latency_minutes,
    compute_edge_decay,
    summarize_rows,
)
# R4+R7 (Pass 3): old fills-based counterparty helpers were removed and
# replaced with positions-based logic in app.services.counterparty
# (find_counterparty_wallets, is_counterparty). New tests live in
# scripts/smoke_phase_pass3_fixes.py under R4 + R7. Old tests for the
# removed _normalise_wallet, _is_counterparty_fill,
# _extract_counterparty_wallets, detect_counterparty_overlap, and the
# live data-api /trades shape probe have been deleted because they test
# removed code paths.
from app.services.polymarket import PolymarketClient  # noqa: E402
from app.services.half_life import (  # noqa: E402
    HalfLifeRow,
    MIN_HALF_LIFE_SAMPLE,
    SNAPSHOT_OFFSETS_MIN,
    compute_half_life_summary,
    pick_offset_for_age,
)
from app.services.signal_detector import (  # noqa: E402
    MIN_AGGREGATE_USDC,
    MIN_NET_DIRECTION_SKEW,
    MIN_TRADER_COUNT,
    WATCHLIST_MIN_AGGREGATE_USDC,
    WATCHLIST_MIN_NET_DIRECTION_SKEW,
    WATCHLIST_MIN_TRADER_COUNT,
)

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


def _make_row(
    cid: str = "0xabc",
    direction: str = "YES",
    resolved: str | None = "YES",
    entry: float = 0.60,
    fired_at: datetime | None = None,
    cluster_id: str | None = None,
    sid: int | None = None,
) -> SignalRow:
    t = fired_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SignalRow(
        id=sid if sid is not None else (hash(cid + direction) & 0xFFFF),
        mode="hybrid", category="overall", top_n=50,
        condition_id=cid, direction=direction,
        first_trader_count=10, first_aggregate_usdc=50_000.0,
        first_net_skew=0.70, first_avg_portfolio_fraction=0.05,
        signal_entry_offer=entry, signal_entry_mid=entry - 0.01,
        liquidity_at_signal_usdc=500_000.0, liquidity_tier="deep",
        first_top_trader_entry_price=entry - 0.05,
        cluster_id=cluster_id, market_type="binary",
        first_fired_at=t,
        resolved_outcome=resolved,
        market_category="politics",
    )


# ===========================================================================
# Migration 008 — schema sanity
# ===========================================================================


async def test_migration_008_schema() -> None:
    section("Migration 008 — schema objects exist")
    pool = await init_pool()
    async with pool.acquire() as conn:
        col = await conn.fetchval(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'signal_log' AND column_name = 'counterparty_warning'
            """
        )
        check("signal_log.counterparty_warning column exists", col == "counterparty_warning")

        for table in (
            "watchlist_signals",
            "signal_price_snapshots",
            "insider_wallets",
        ):
            ok = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"public.{table}",
            )
            check(f"{table} table exists", bool(ok))


# ===========================================================================
# B2 — counterparty diagnostic (pure)
# ===========================================================================


# test_normalise_wallet() removed -- _normalise_wallet was a fills-only
# helper and the new positions-based path (R4+R7, Pass 3) doesn't need
# wallet-string normalization (positions table already has clean addresses).

# ===========================================================================
# B2 — set_counterparty_warning CRUD (DB)
# ===========================================================================


async def test_set_counterparty_warning_crud() -> None:
    section("B2 CRUD: set_counterparty_warning round-trip")
    pool = await init_pool()
    async with pool.acquire() as conn:
        # Find any signal_log row to test against — read-only against existing data.
        row = await conn.fetchrow(
            "SELECT id, counterparty_warning FROM signal_log LIMIT 1"
        )
        if row is None:
            check("(skipped — no signal_log rows yet)", True)
            return
        sid = int(row["id"])
        original = bool(row["counterparty_warning"])
        try:
            # Reset to FALSE first, then test UPDATE
            await conn.execute(
                "UPDATE signal_log SET counterparty_warning = FALSE WHERE id = $1", sid,
            )
            flipped = await crud.set_counterparty_warning(conn, sid)
            check("set_counterparty_warning returns True for fresh flip", flipped is True)
            after = await conn.fetchval(
                "SELECT counterparty_warning FROM signal_log WHERE id = $1", sid,
            )
            check("counterparty_warning persisted as TRUE", bool(after) is True)

            # Re-running on already-TRUE row is a no-op
            again = await crud.set_counterparty_warning(conn, sid)
            check("re-flip returns False (idempotent)", again is False)
        finally:
            # Restore original to keep test side-effect-free
            await conn.execute(
                "UPDATE signal_log SET counterparty_warning = $1 WHERE id = $2",
                original, sid,
            )


# ===========================================================================
# B3 — watchlist floors + mutual exclusion
# ===========================================================================


def test_watchlist_floor_constants() -> None:
    section("B3: watchlist floors are looser than official + share skew floor")
    check("watchlist trader floor < official", WATCHLIST_MIN_TRADER_COUNT < MIN_TRADER_COUNT)
    check("watchlist aggregate floor < official", WATCHLIST_MIN_AGGREGATE_USDC < MIN_AGGREGATE_USDC)
    check(
        "watchlist skew floor == official (only the headcount/$ change)",
        WATCHLIST_MIN_NET_DIRECTION_SKEW == MIN_NET_DIRECTION_SKEW,
    )


# ===========================================================================
# B3 — watchlist CRUD + cleanup (DB)
# ===========================================================================


async def test_watchlist_crud_and_cleanup() -> None:
    section("B3 CRUD: upsert_watchlist_signal + cleanup_watchlist_dropouts")
    pool = await init_pool()
    async with pool.acquire() as conn:
        # Need a real condition_id (foreign key). Pick any.
        cid = await conn.fetchval("SELECT condition_id FROM markets LIMIT 1")
        if cid is None:
            check("(skipped — no markets in DB)", True)
            return

        test_mode = "smoke_b3_test_mode"  # unique tag so we don't disturb real data
        try:
            # Insert two watchlist rows for the same lens
            for direction in ("YES", "NO"):
                await crud.upsert_watchlist_signal(
                    conn,
                    mode=test_mode, category="overall", top_n=50,
                    condition_id=cid, direction=direction,
                    trader_count=3, aggregate_usdc=10_000.0,
                    net_skew=0.65, avg_portfolio_fraction=0.02,
                )
            rows = await crud.list_watchlist_signals(
                conn, mode=test_mode, category="overall", top_n=50,
            )
            check("upsert -> 2 rows inserted", len(rows) == 2, f"got {len(rows)}")

            # Re-upsert one (idempotent)
            await crud.upsert_watchlist_signal(
                conn,
                mode=test_mode, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                trader_count=4, aggregate_usdc=12_000.0,
                net_skew=0.66, avg_portfolio_fraction=0.03,
            )
            rows2 = await crud.list_watchlist_signals(
                conn, mode=test_mode, category="overall", top_n=50,
            )
            check("re-upsert keeps row count at 2", len(rows2) == 2)
            updated = next(r for r in rows2 if r["direction"] == "YES")
            check(
                "re-upsert refreshed trader_count from 3 -> 4",
                updated["trader_count"] == 4,
                f"got {updated['trader_count']}",
            )

            # Cleanup: keep only YES, drop NO
            dropped = await crud.cleanup_watchlist_dropouts(
                conn, mode=test_mode, category="overall", top_n=50,
                keep_keys={(cid, "YES")},
            )
            check("cleanup reports 1 dropped", dropped == 1, f"got {dropped}")
            rows3 = await crud.list_watchlist_signals(
                conn, mode=test_mode, category="overall", top_n=50,
            )
            check(
                "after cleanup only the YES row remains",
                len(rows3) == 1 and rows3[0]["direction"] == "YES",
            )

            # Cleanup with empty keep_keys deletes everything for the lens
            await crud.cleanup_watchlist_dropouts(
                conn, mode=test_mode, category="overall", top_n=50,
                keep_keys=set(),
            )
            rows4 = await crud.list_watchlist_signals(
                conn, mode=test_mode, category="overall", top_n=50,
            )
            check("empty-keep_keys clears the lens", len(rows4) == 0)
        finally:
            # Belt and suspenders cleanup
            await conn.execute(
                "DELETE FROM watchlist_signals WHERE mode = $1", test_mode,
            )


async def test_f10_watchlist_skips_when_official_signal_exists() -> None:
    """F10 regression: watchlist upsert must skip when (cid, direction) is
    already an official signal in any lens. And the per-cycle cleanup must
    remove any pre-existing watchlist row that's been promoted to an
    official signal in another lens.

    See review/FIXES.md F10.
    """
    section("F10 + R14: watchlist mutual exclusion (scoped to recent signals)")
    pool = await init_pool()
    async with pool.acquire() as conn:
        # R14 (Pass 3): the F10 cleanup + upsert NOT EXISTS check are now
        # scoped to last 24h. Find a real (cid, direction) that's RECENT
        # (last_seen_at within window). Failing that, bump one for the test.
        official_row = await conn.fetchrow(
            """
            SELECT condition_id, direction FROM signal_log
            WHERE direction IN ('YES', 'NO')
              AND last_seen_at >= NOW() - INTERVAL '24 hours'
            LIMIT 1
            """
        )
        if official_row is None:
            # Bump the first signal_log row's last_seen_at to NOW so we have
            # something to test against. (Test is read-only on production
            # data otherwise; this is a controlled mutation we restore below.)
            any_row = await conn.fetchrow(
                "SELECT id, condition_id, direction, last_seen_at FROM signal_log "
                "WHERE direction IN ('YES', 'NO') LIMIT 1"
            )
            if any_row is None:
                check("(skipped -- no signal_log rows at all)", True)
                return
            await conn.execute(
                "UPDATE signal_log SET last_seen_at = NOW() WHERE id = $1",
                any_row["id"],
            )
            official_cid = any_row["condition_id"]
            official_dir = any_row["direction"]
            _restore_last_seen_id = any_row["id"]
            _restore_last_seen_at = any_row["last_seen_at"]
        else:
            official_cid = official_row["condition_id"]
            official_dir = official_row["direction"]
            _restore_last_seen_id = None
            _restore_last_seen_at = None

        test_mode = "smoke_f10_test_mode"
        try:
            # Try to insert a watchlist row for the SAME (cid, direction)
            # that exists as an official signal in some other lens.
            inserted = await crud.upsert_watchlist_signal(
                conn,
                mode=test_mode, category="overall", top_n=50,
                condition_id=official_cid, direction=official_dir,
                trader_count=3, aggregate_usdc=10_000.0,
                net_skew=0.65, avg_portfolio_fraction=0.02,
            )
            check(
                "F10: upsert SKIPS when (cid, direction) is in signal_log "
                "(returns False)",
                inserted is False,
                f"got inserted={inserted}",
            )
            rows = await crud.list_watchlist_signals(
                conn, mode=test_mode, category="overall", top_n=50,
            )
            check(
                "F10: no watchlist row inserted",
                len(rows) == 0,
                f"got {len(rows)} rows; expected 0",
            )

            # Now find a market that's NOT in signal_log to verify normal
            # path still works.
            non_official_cid = await conn.fetchval(
                "SELECT m.condition_id FROM markets m "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM signal_log s WHERE s.condition_id = m.condition_id"
                ") LIMIT 1"
            )
            if non_official_cid:
                inserted2 = await crud.upsert_watchlist_signal(
                    conn,
                    mode=test_mode, category="overall", top_n=50,
                    condition_id=non_official_cid, direction="YES",
                    trader_count=3, aggregate_usdc=10_000.0,
                    net_skew=0.65, avg_portfolio_fraction=0.02,
                )
                check(
                    "F10: normal upsert still works for non-signal market "
                    "(returns True)",
                    inserted2 is True,
                    f"got inserted={inserted2}",
                )

            # Test the bulk cleanup helper
            # Pre-insert a stale row directly via raw SQL (bypassing the
            # write-time check) to simulate a row that became stale because
            # an official signal fired in another lens after the watchlist
            # row was already there.
            await conn.execute(
                """
                INSERT INTO watchlist_signals
                    (mode, category, top_n, condition_id, direction,
                     trader_count, aggregate_usdc, net_skew, avg_portfolio_fraction)
                VALUES ($1, 'overall', 50, $2, $3, 3, 10000, 0.65, 0.02)
                ON CONFLICT DO NOTHING
                """,
                test_mode, official_cid, official_dir,
            )
            # Quick sanity check: the row exists
            n_before = await conn.fetchval(
                "SELECT COUNT(*) FROM watchlist_signals "
                "WHERE mode = $1 AND condition_id = $2 AND direction = $3",
                test_mode, official_cid, official_dir,
            )

            cleared = await crud.cleanup_watchlist_promoted_to_signal(conn)
            check(
                "F10: cleanup_watchlist_promoted_to_signal removes promoted rows",
                cleared >= n_before,  # may delete more than ours if other stale rows exist
                f"cleared={cleared}, n_before={n_before}",
            )
            n_after = await conn.fetchval(
                "SELECT COUNT(*) FROM watchlist_signals "
                "WHERE mode = $1 AND condition_id = $2 AND direction = $3",
                test_mode, official_cid, official_dir,
            )
            check(
                "F10: stale promoted row is gone post-cleanup",
                n_after == 0,
                f"n_after={n_after}",
            )
        finally:
            await conn.execute(
                "DELETE FROM watchlist_signals WHERE mode = $1", test_mode,
            )
            # R14: restore signal_log.last_seen_at if we bumped it
            if _restore_last_seen_id is not None:
                await conn.execute(
                    "UPDATE signal_log SET last_seen_at = $1 WHERE id = $2",
                    _restore_last_seen_at, _restore_last_seen_id,
                )


# ===========================================================================
# B4 — pick_offset_for_age + half_life summary
# ===========================================================================


def test_pick_offset_for_age() -> None:
    """F7-updated: snapshot offsets now include +5 and +15. Tie-break is
    closest-first; ties go to the smaller offset."""
    section("B4/F7: pick_offset_for_age within tolerance, with +5/+15 added")
    # Original 30/60/120 cases still hold
    check("age=30 -> offset 30", pick_offset_for_age(30) == 30)
    check("age=32 (within +5) -> 30", pick_offset_for_age(32) == 30)
    check("age=27 (within -5) -> 30", pick_offset_for_age(27) == 30)
    check("age=60 -> 60", pick_offset_for_age(60) == 60)
    check("age=64 -> 60", pick_offset_for_age(64) == 60)
    check("age=120 -> 120", pick_offset_for_age(120) == 120)
    check("age=125 -> 120", pick_offset_for_age(125) == 120)
    check("age=45 (between buckets) -> None", pick_offset_for_age(45) is None)
    check("age=200 (past last bucket) -> None", pick_offset_for_age(200) is None)
    # F7: +5 and +15 offsets
    check("age=5 -> offset 5 (F7 new)", pick_offset_for_age(5) == 5)
    check("age=3 (close to 5) -> 5", pick_offset_for_age(3) == 5)
    check("age=15 -> 15 (F7 new)", pick_offset_for_age(15) == 15)
    check("age=12 (close to 15) -> 15", pick_offset_for_age(12) == 15)
    check("age=18 (close to 15) -> 15", pick_offset_for_age(18) == 15)
    # Tie-break at boundary: age=10 is equidistant from 5 and 15 -> closer
    # picks 5 (smaller) per F7 tie-break rule
    check(
        "age=10 (tied 5 vs 15) -> 5 (smaller wins)",
        pick_offset_for_age(10) == 5,
    )
    # age=0 used to return None — now within tolerance of +5
    check("age=0 (within tolerance of +5) -> 5", pick_offset_for_age(0) == 5)
    # F7: exclude semantics
    check(
        "age=10, exclude={5} -> 15 (next-best)",
        pick_offset_for_age(10, exclude={5}) == 15,
    )
    check(
        "age=10, exclude={5, 15} -> None (all eligible offsets taken)",
        pick_offset_for_age(10, exclude={5, 15}) is None,
    )
    check(
        "age=30, exclude={30} -> None (no other in tolerance)",
        pick_offset_for_age(30, exclude={30}) is None,
    )


def test_compute_half_life_summary() -> None:
    section("B4: compute_half_life_summary convergence semantics")
    # YES signal: smart_money entered at 0.50, fire price 0.60 (gap 0.10).
    # If snapshot 0.55 -> closer (moved toward smart money) -> True
    # If snapshot 0.62 -> farther -> False
    rows = [
        HalfLifeRow(category="politics", fire_price=0.60, direction="YES",
                    smart_money_entry=0.50, snapshot_price=0.55, offset_min=30),
        HalfLifeRow(category="politics", fire_price=0.60, direction="YES",
                    smart_money_entry=0.50, snapshot_price=0.62, offset_min=30),
        HalfLifeRow(category="politics", fire_price=0.60, direction="YES",
                    smart_money_entry=0.50, snapshot_price=0.51, offset_min=30),
    ]
    out = compute_half_life_summary(rows)
    check("one bucket emitted for (politics, 30)", len(out) == 1)
    b = out[0]
    check("convergence_rate = 2/3", abs(b.convergence_rate - 2/3) < 1e-9)
    check("n=3", b.n == 3)
    check("underpowered=True at n<30", b.underpowered is True)

    # Skip rows with smart_money_entry == fire_price (undefined gap)
    rows2 = [
        HalfLifeRow(category="x", fire_price=0.50, direction="YES",
                    smart_money_entry=0.50, snapshot_price=0.55, offset_min=60),
    ]
    out2 = compute_half_life_summary(rows2)
    check("zero-gap row skipped (no bucket emitted)", len(out2) == 0)

    # F5: NO signal — fire_price and smart_money_entry are direction-space
    # (NO-token prices); snapshot_price is YES-space. The fix converts
    # fire/sm to YES-space and compares everything in YES-space.
    rows3 = [
        HalfLifeRow(category="y", fire_price=0.40, direction="NO",
                    smart_money_entry=0.30, snapshot_price=0.65, offset_min=30),
    ]
    # YES-space: fire_yes=1-0.40=0.60, sm_yes=1-0.30=0.70, snap_yes=0.65
    # gap_fire = |0.60-0.70| = 0.10; gap_snap = |0.65-0.70| = 0.05 -> closer
    out3 = compute_half_life_summary(rows3)
    check(
        "F5: NO direction comparison done in YES-space (snap_yes=0.65 is closer to sm_yes=0.70 than fire_yes=0.60)",
        len(out3) == 1 and out3[0].convergence_rate == 1.0,
        f"got {out3[0].convergence_rate if out3 else 'no buckets'}",
    )

    check("MIN_HALF_LIFE_SAMPLE = 30", MIN_HALF_LIFE_SAMPLE == 30)


def test_f4_half_life_uses_mid_when_ask_present() -> None:
    """F4 regression: HalfLifeRow.snapshot_price was the bid (which is what
    the legacy yes_price stored). After F4 we capture both bid + ask, and
    half-life math uses mid = (bid+ask)/2 when ask is available — the
    spread artifact in the convergence rate is the bug being fixed.

    See review/PROBE_FINDINGS.md, review/FIXES.md F4.
    """
    section("F4: half-life uses mid when ask present, falls back to bid otherwise")

    # Construct a NO signal where:
    #   fire_yes  = 1 - 0.55 = 0.45
    #   sm_yes    = 1 - 0.40 = 0.60
    #   gap_fire  = 0.15
    # If we use BID only (= 0.50), gap_snap = |0.50 - 0.60| = 0.10 -> moved=True
    # If we use MID = (0.50+0.56)/2 = 0.53, gap_snap = |0.53 - 0.60| = 0.07 -> moved=True
    # The TEST case: use mid that pushes outside fire_gap to flip the answer.
    #
    # bid=0.40, ask=0.60 -> mid=0.50 exactly equals sm_yes -> moved=False (gap=0 < 0.15)
    # but bid alone (0.40) -> gap=0.20 > 0.15 -> moved=False also.
    # Need a case where bid < ask and mid moves the comparison.
    #
    # Better: bid=0.55, ask=0.65 -> mid=0.60 (= sm_yes)
    # snapshot_price legacy (= bid = 0.55): |0.55 - 0.60| = 0.05 < 0.15 -> moved=True
    # mid (= 0.60): |0.60 - 0.60| = 0 -> _moved returns None (gap=0) — skipped
    # That's a tricky boundary. Let me use a clearer differentiator.
    #
    # bid=0.30, ask=0.70 -> mid=0.50.
    # snapshot_price (bid=0.30): for NO direction sm_yes=0.60, gap=|0.30-0.60|=0.30 > 0.15 -> moved=False
    # mid (0.50): gap=|0.50-0.60|=0.10 < 0.15 -> moved=True
    # Different answers — proves the fix uses mid.

    rows_with_ask = [
        HalfLifeRow(
            category="ask_test", fire_price=0.55, direction="NO",
            smart_money_entry=0.40, snapshot_price=0.30, offset_min=30,
            bid_price=0.30, ask_price=0.70,
        ),
    ]
    out = compute_half_life_summary(rows_with_ask)
    check(
        "F4: when ask available, uses mid -> reports moved=True (rate=1.0)",
        len(out) == 1 and out[0].convergence_rate == 1.0,
        f"got {out[0].convergence_rate if out else 'no buckets'}",
    )

    # Same scenario but no ask captured (legacy row): falls back to bid
    rows_bid_only = [
        HalfLifeRow(
            category="bid_only", fire_price=0.55, direction="NO",
            smart_money_entry=0.40, snapshot_price=0.30, offset_min=30,
            bid_price=0.30, ask_price=None,
        ),
    ]
    out2 = compute_half_life_summary(rows_bid_only)
    check(
        "F4: when ask=None (legacy), falls back to bid-only -> moved=False (rate=0.0)",
        len(out2) == 1 and out2[0].convergence_rate == 0.0,
        f"got {out2[0].convergence_rate if out2 else 'no buckets'}",
    )

    # snapshot_price still works as a fallback when bid_price not provided
    # (back-compat for older HalfLifeRow constructors)
    rows_legacy = [
        HalfLifeRow(
            category="legacy", fire_price=0.55, direction="NO",
            smart_money_entry=0.40, snapshot_price=0.30, offset_min=30,
            # no bid_price, no ask_price
        ),
    ]
    out3 = compute_half_life_summary(rows_legacy)
    check(
        "F4: legacy HalfLifeRow with snapshot_price only still works",
        len(out3) == 1 and out3[0].convergence_rate == 0.0,
        f"got {out3[0].convergence_rate if out3 else 'no buckets'}",
    )


def test_f7_latency_unavailable_flag() -> None:
    """F7 regression: the latency_unavailable() helper flips True when
    fallback dominates. Pass 5 #12 lowered the threshold from 0.5 to 0.2
    (LATENCY_FALLBACK_WARN_FRACTION) — anything over 20% fallback now
    trips the warning. Pre-fix the engine reported 'latency simulated'
    even when every row had fallen back to the optimistic baseline
    (because the short profiles' windows didn't
    intersect any captured snapshot offset).

    See review/FIXES.md F7.
    """
    section("F7: latency_unavailable flag triggers when fallback > 50%")
    from app.services.backtest_engine import latency_unavailable

    check(
        "0 rows -> False (no info)",
        latency_unavailable(0, 0) is False,
    )
    check(
        "all adjusted -> False",
        latency_unavailable(10, 0) is False,
    )
    check(
        "all fallback -> True",
        latency_unavailable(0, 10) is True,
    )
    check(
        "50% fallback -> True (above 0.2 threshold)",
        latency_unavailable(5, 5) is True,
    )
    check(
        "60% fallback -> True",
        latency_unavailable(4, 6) is True,
    )
    check(
        "40% fallback -> True (above 0.2 threshold)",
        latency_unavailable(6, 4) is True,
    )
    check(
        "20% fallback -> False (boundary; > not >=)",
        latency_unavailable(8, 2) is False,
    )
    check(
        "25% fallback -> True (just above 0.2 threshold)",
        latency_unavailable(75, 25) is True,
    )


def test_f7_latency_snapshot_offsets_include_5_and_15() -> None:
    """F7: LATENCY_SNAPSHOT_OFFSETS in backtest_engine must mirror
    SNAPSHOT_OFFSETS_MIN in half_life. Both must include the 5 + 15 min
    offsets so the short latency profiles work."""
    section("F7: latency snapshot offsets include 5 + 15 min")
    from app.services.backtest_engine import LATENCY_SNAPSHOT_OFFSETS
    from app.services.half_life import SNAPSHOT_OFFSETS_MIN
    check(
        "LATENCY_SNAPSHOT_OFFSETS contains 5",
        5 in LATENCY_SNAPSHOT_OFFSETS,
        f"got {LATENCY_SNAPSHOT_OFFSETS}",
    )
    check(
        "LATENCY_SNAPSHOT_OFFSETS contains 15",
        15 in LATENCY_SNAPSHOT_OFFSETS,
    )
    check(
        "SNAPSHOT_OFFSETS_MIN matches LATENCY_SNAPSHOT_OFFSETS (set-equal)",
        set(LATENCY_SNAPSHOT_OFFSETS) == set(SNAPSHOT_OFFSETS_MIN),
        f"latency={LATENCY_SNAPSHOT_OFFSETS}, halflife={SNAPSHOT_OFFSETS_MIN}",
    )


def test_f5_half_life_no_direction_price_space() -> None:
    """F5 regression: half-life convergence math must compare prices in a
    single canonical space. Pre-fix: `_yes_price_for_direction` was called on
    all three inputs (fire, smart_money, snapshot), but only `snapshot_price`
    was actually stored in YES-space — `fire_price` and `smart_money_entry`
    were already direction-space. The function double-translated two of the
    three inputs for NO signals, mixing YES-space and NO-space and producing
    garbage convergence rates for half the table.

    See review/03_backtest_stats.md Critical #1, review/FIXES.md F5.
    """
    section("F5: half-life NO-direction prices compared in single space")

    # Case 1: NO signal where YES-space math says CONVERGED (1.0) but the
    # buggy code gave DIVERGED (0.0). The price actually moved toward smart
    # money's cost basis, but the bug reported the opposite.
    rows_converge = [
        HalfLifeRow(category="conv", fire_price=0.55, direction="NO",
                    smart_money_entry=0.40, snapshot_price=0.55, offset_min=30),
    ]
    # YES-space: fire_yes=0.45, sm_yes=0.60, snap_yes=0.55
    #   gap_fire = |0.45-0.60| = 0.15; gap_snap = |0.55-0.60| = 0.05 -> True
    # Bug: fire_in_dir=0.45 (YES-space), sm_in_dir=0.60 (YES-space),
    #      snap_in_dir=0.45 (NO-space)
    #   gap_fire = 0.15; gap_snap = 0.15 -> False
    out_c = compute_half_life_summary(rows_converge)
    check(
        "F5: NO signal converging — YES-space math says moved (post-fix)",
        len(out_c) == 1 and out_c[0].convergence_rate == 1.0,
        f"got {out_c[0].convergence_rate if out_c else 'no buckets'}",
    )

    # Case 2: NO signal where bug claimed convergence (1.0) but YES-space
    # math correctly says DIVERGED (0.0). This is the more dangerous case —
    # bug gives false confidence that smart-money cost basis is "magnetic".
    rows_diverge = [
        HalfLifeRow(category="div", fire_price=0.55, direction="NO",
                    smart_money_entry=0.40, snapshot_price=0.30, offset_min=30),
    ]
    # YES-space: fire_yes=0.45, sm_yes=0.60, snap_yes=0.30
    #   gap_fire = 0.15; gap_snap = |0.30-0.60| = 0.30 -> False (diverged)
    # Bug: snap_in_dir=0.70, sm_in_dir=0.60
    #   gap_fire = 0.15; gap_snap = |0.70-0.60| = 0.10 -> True (claimed converged)
    out_d = compute_half_life_summary(rows_diverge)
    check(
        "F5: NO signal diverging — YES-space math correctly reports diverged (bug used to claim converged)",
        len(out_d) == 1 and out_d[0].convergence_rate == 0.0,
        f"got {out_d[0].convergence_rate if out_d else 'no buckets'}",
    )

    # Case 3: YES signal — both pre-fix and post-fix should give same answer.
    # Sanity check that we didn't break YES signals while fixing NO.
    rows_yes = [
        HalfLifeRow(category="yes_sanity", fire_price=0.45, direction="YES",
                    smart_money_entry=0.60, snapshot_price=0.55, offset_min=60),
    ]
    # YES-space: fire=0.45, sm=0.60, snap=0.55
    #   gap_fire = 0.15; gap_snap = 0.05 -> True
    out_y = compute_half_life_summary(rows_yes)
    check(
        "F5: YES signal still converges (regression check — fix didn't break YES)",
        len(out_y) == 1 and out_y[0].convergence_rate == 1.0,
        f"got {out_y[0].convergence_rate if out_y else 'no buckets'}",
    )

    # Case 4: Mixed bag — 1 NO converging + 1 NO diverging in same bucket
    # → convergence_rate should be 0.5
    rows_mixed = [
        HalfLifeRow(category="mix", fire_price=0.55, direction="NO",
                    smart_money_entry=0.40, snapshot_price=0.55, offset_min=120),
        HalfLifeRow(category="mix", fire_price=0.55, direction="NO",
                    smart_money_entry=0.40, snapshot_price=0.30, offset_min=120),
    ]
    out_m = compute_half_life_summary(rows_mixed)
    check(
        "F5: mixed NO bucket -- 1 converged + 1 diverged -> rate=0.5",
        len(out_m) == 1 and out_m[0].convergence_rate == 0.5 and out_m[0].n == 2,
        f"got rate={out_m[0].convergence_rate if out_m else 'none'}, n={out_m[0].n if out_m else 'none'}",
    )


async def test_signal_price_snapshot_crud() -> None:
    """F4-updated CRUD test: insert with bid + ask, fetch returns dict with
    bid + ask + computed mid."""
    section("B4 CRUD: signal_price_snapshots round-trip (F4 bid + ask)")
    pool = await init_pool()
    async with pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM signal_log LIMIT 1")
        if sid is None:
            check("(skipped — no signal_log rows)", True)
            return
        sid = int(sid)
        try:
            inserted = await crud.insert_signal_price_snapshot(
                conn, signal_log_id=sid, snapshot_offset_min=30,
                bid_price=0.5432, ask_price=0.5500,
                token_id="smoke_b4_token",
            )
            check("F4: insert with bid+ask returns True for fresh row", inserted is True)
            again = await crud.insert_signal_price_snapshot(
                conn, signal_log_id=sid, snapshot_offset_min=30,
                bid_price=0.6, ask_price=0.61,
                token_id="smoke_b4_token",
            )
            check("re-insert at same (sid, offset) returns False", again is False)

            existing = await crud.existing_price_snapshot_offsets(conn, sid)
            check("existing_price_snapshot_offsets includes 30", 30 in existing)

            snaps = await crud.fetch_signal_price_snapshots(conn, [sid])
            snap_dict = snaps.get((sid, 30))
            check(
                "F4: fetch returns dict with bid + ask + mid keys",
                isinstance(snap_dict, dict)
                and "bid" in snap_dict and "ask" in snap_dict and "mid" in snap_dict,
                f"got {snap_dict}",
            )
            check(
                "F4: bid_price round-trips correctly",
                snap_dict is not None
                and snap_dict["bid"] is not None
                and abs(snap_dict["bid"] - 0.5432) < 1e-6,
                f"got bid={snap_dict['bid'] if snap_dict else None}",
            )
            check(
                "F4: ask_price round-trips correctly",
                snap_dict is not None
                and snap_dict["ask"] is not None
                and abs(snap_dict["ask"] - 0.5500) < 1e-6,
                f"got ask={snap_dict['ask'] if snap_dict else None}",
            )
            expected_mid = (0.5432 + 0.5500) / 2.0
            check(
                "F4: mid is computed as (bid+ask)/2",
                snap_dict is not None
                and snap_dict["mid"] is not None
                and abs(snap_dict["mid"] - expected_mid) < 1e-6,
                f"got mid={snap_dict['mid'] if snap_dict else None}",
            )
        finally:
            await conn.execute(
                "DELETE FROM signal_price_snapshots WHERE signal_log_id = $1 AND token_id = 'smoke_b4_token'",
                sid,
            )


# ===========================================================================
# B10 — latency simulation
# ===========================================================================


def test_latency_window_resolution() -> None:
    section("B10: _resolve_latency_window")
    check(
        "profile=None -> None",
        _resolve_latency_window(BacktestFilters()) is None,
    )
    check(
        "profile=responsive -> (5, 10)",
        _resolve_latency_window(BacktestFilters(latency_profile="responsive"))
            == LATENCY_PROFILES["responsive"],
    )
    check(
        "profile=delayed -> (30, 60)",
        _resolve_latency_window(BacktestFilters(latency_profile="delayed"))
            == LATENCY_PROFILES["delayed"],
    )
    # Custom requires both bounds
    check(
        "profile=custom without bounds -> None",
        _resolve_latency_window(BacktestFilters(latency_profile="custom")) is None,
    )
    check(
        "profile=custom with bounds -> the bounds",
        _resolve_latency_window(BacktestFilters(
            latency_profile="custom", latency_min_min=8, latency_max_min=15,
        )) == (8.0, 15.0),
    )
    check(
        "profile=custom with reversed bounds -> None",
        _resolve_latency_window(BacktestFilters(
            latency_profile="custom", latency_min_min=20, latency_max_min=5,
        )) is None,
    )


def test_sampled_latency_deterministic() -> None:
    section("B10: _sampled_latency_minutes is deterministic per condition_id")
    a1 = _sampled_latency_minutes("0xabc", (30.0, 60.0))
    a2 = _sampled_latency_minutes("0xabc", (30.0, 60.0))
    b1 = _sampled_latency_minutes("0xdef", (30.0, 60.0))
    check("same cid -> same value", a1 == a2)
    check("different cid -> usually different value", a1 != b1)
    check("value within window", 30.0 <= a1 <= 60.0)


def test_nearest_snapshot_offset() -> None:
    """F7-updated: snapshot offsets now include +5 and +15."""
    section("B10/F7: _nearest_snapshot_offset")
    check("32 -> 30 (within tolerance)", _nearest_snapshot_offset(32) == 30)
    check("57 -> 60", _nearest_snapshot_offset(57) == 60)
    check("117 -> 120", _nearest_snapshot_offset(117) == 120)
    check("45 (midway 30/60, outside tolerance) -> None", _nearest_snapshot_offset(45) is None)
    # F7: 8 is now within tolerance of +5 (|8-5|=3 <= 5) — used to be None.
    check("F7: 8 (active profile) -> 5 (was None pre-F7)", _nearest_snapshot_offset(8) == 5)
    check("F7: 3 -> 5", _nearest_snapshot_offset(3) == 5)
    check("F7: 12 -> 15", _nearest_snapshot_offset(12) == 15)
    check("F7: 22 (midway 15/30, outside tolerance) -> None", _nearest_snapshot_offset(22) is None)
    check("LATENCY_OFFSET_TOLERANCE_MIN = 5", LATENCY_OFFSET_TOLERANCE_MIN == 5.0)
    check(
        "LATENCY_SNAPSHOT_OFFSETS == half_life SNAPSHOT_OFFSETS",
        set(LATENCY_SNAPSHOT_OFFSETS) == set(SNAPSHOT_OFFSETS_MIN),
    )


def test_apply_latency() -> None:
    """F4 + R8: snapshots are {bid, ask, mid, direction} dicts. Latency
    uses the ASK (true buy-cross price) when available; branches on the
    snapshot's direction-space ('YES' | 'NO' | None=legacy YES-space)."""
    section("B10/F4/R8: _apply_latency uses ASK and branches on snapshot direction")
    # Row: NO direction, fire_price=0.55 (NO ask), id=42
    row = _make_row("0xdelayed", direction="NO", entry=0.55, sid=42)
    f = BacktestFilters(latency_profile="delayed")  # 30-60 min window

    # R8 post-fix: NO-direction snapshot stored in NO-space. ASK is
    # already in NO-space, so latency must NOT translate via 1-x.
    snaps_no_space = {
        (42, 30): {"bid": 0.40, "ask": 0.42, "mid": 0.41, "direction": "NO"},
        (42, 60): {"bid": 0.30, "ask": 0.32, "mid": 0.31, "direction": "NO"},
    }
    new_rows, adjusted, fallback = _apply_latency([row], f, snaps_no_space)
    check("returns one row", len(new_rows) == 1)
    new_row = new_rows[0]
    # Post-R8: snapshot already direction-space, use ask as-is.
    expected_no = {0.42, 0.32}
    check(
        "R8: NO-signal + NO-space snapshot uses ask directly (no translation)",
        any(abs(new_row.signal_entry_offer - e) < 1e-9 for e in expected_no),
        f"got {new_row.signal_entry_offer}, expected one of {expected_no}",
    )
    check("adjusted=1, fallback=0", adjusted == 1 and fallback == 0)

    # Legacy (pre-R8) NO snapshots: direction=None, snapshot in YES-space.
    # Latency must translate via 1-x for NO signals.
    snaps_legacy_space = {
        (42, 30): {"bid": 0.40, "ask": 0.42, "mid": 0.41, "direction": None},
        (42, 60): {"bid": 0.30, "ask": 0.32, "mid": 0.31, "direction": None},
    }
    new_rows_legacy, adjusted_legacy, _ = _apply_latency([row], f, snaps_legacy_space)
    expected_legacy = {1.0 - 0.42, 1.0 - 0.32}
    check(
        "Legacy: NO-signal + YES-space snapshot translates via 1-x",
        adjusted_legacy == 1
        and any(abs(new_rows_legacy[0].signal_entry_offer - e) < 1e-9 for e in expected_legacy),
        f"got {new_rows_legacy[0].signal_entry_offer}, expected one of {expected_legacy}",
    )

    # F4 fallback: legacy snapshot with ask=None and direction=None falls
    # back to bid AND translates via 1-x (legacy YES-space NO signal).
    snaps_legacy_bid = {
        (42, 30): {"bid": 0.40, "ask": None, "mid": 0.40, "direction": None},
        (42, 60): {"bid": 0.30, "ask": None, "mid": 0.30, "direction": None},
    }
    new_rows3, adjusted3, _ = _apply_latency([row], f, snaps_legacy_bid)
    expected_legacy_bid = {1.0 - 0.40, 1.0 - 0.30}
    check(
        "F4: legacy ask=None falls back to bid (still YES-space, still translates)",
        adjusted3 == 1
        and any(abs(new_rows3[0].signal_entry_offer - e) < 1e-9 for e in expected_legacy_bid),
        f"got {new_rows3[0].signal_entry_offer}",
    )

    # YES signal: snapshot in YES-space (either direction='YES' or legacy)
    # — use ask as-is, no translation. Reuse cid/sid so the deterministic
    # RNG picks the same offset as the NO branch above (so the (sid, off)
    # lookup hits one of the seeded offsets).
    yes_row = _make_row("0xdelayed", direction="YES", entry=0.45, sid=42)
    snaps_yes = {
        (42, 30): {"bid": 0.50, "ask": 0.52, "mid": 0.51, "direction": "YES"},
        (42, 60): {"bid": 0.55, "ask": 0.57, "mid": 0.56, "direction": "YES"},
    }
    new_rows_yes, adjusted_yes, _ = _apply_latency([yes_row], f, snaps_yes)
    expected_yes = {0.52, 0.57}
    check(
        "YES-signal + YES-space snapshot uses ask directly",
        adjusted_yes == 1
        and any(abs(new_rows_yes[0].signal_entry_offer - e) < 1e-9 for e in expected_yes),
        f"got {new_rows_yes[0].signal_entry_offer}, expected one of {expected_yes}",
    )

    # No snapshot present -> fallback (entry unchanged)
    new_rows2, adjusted2, fallback2 = _apply_latency([row], f, {})
    check("no snapshot -> fallback to original entry", new_rows2[0].signal_entry_offer == 0.55)
    check("adjusted=0, fallback=1", adjusted2 == 0 and fallback2 == 1)

    # Profile=None -> rows unchanged + zero counts
    new_rows4, adjusted4, fallback4 = _apply_latency([row], BacktestFilters(), snaps_no_space)
    check("profile=None -> no-op", new_rows4 is [row] or new_rows4[0] is row or new_rows4[0].signal_entry_offer == 0.55)
    check("profile=None -> 0/0 counters", adjusted4 == 0 and fallback4 == 0)


# ===========================================================================
# B11 — edge_decay
# ===========================================================================


def test_edge_decay_grouping_and_warning() -> None:
    # D4 (Pass 3): rolling 3-vs-3 comparison with min 6 weeks history.
    # Pre-Pass-3 used 4-week minimum + recent-3-vs-all-time-prior comparison.
    section("B11 + D4: compute_edge_decay rolling 3-vs-3")

    # Build 6 weekly cohorts of 30 rows each. First 3 strong (winners),
    # last 3 weak (losers) -> recent_3_avg << preceding_3_avg -> decay_warning.
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)  # Monday
    rows: list[SignalRow] = []
    # Three strong weeks: rows mostly resolve in signal direction (winners)
    for week_idx in range(3):
        for i in range(30):
            rows.append(_make_row(
                cid=f"strong_w{week_idx}_{i}",
                direction="YES", resolved="YES",
                entry=0.50 + (i * 0.001),
                fired_at=base + timedelta(weeks=week_idx),
                cluster_id=f"cluster_strong_{week_idx}_{i}",
                sid=10_000 + week_idx * 100 + i,
            ))
    # Three weak weeks: signals lose
    for week_idx in range(3, 6):
        for i in range(30):
            rows.append(_make_row(
                cid=f"weak_w{week_idx}_{i}",
                direction="YES", resolved="NO",
                entry=0.50,
                fired_at=base + timedelta(weeks=week_idx),
                cluster_id=f"cluster_weak_{week_idx}_{i}",
                sid=20_000 + week_idx * 100 + i,
            ))

    res = compute_edge_decay(rows, min_n_per_cohort=5)
    check("6 cohorts emitted (weekly)", res.weeks_of_data == 6, f"got {res.weeks_of_data}")
    check("insufficient_history=False at >=6 weeks (D4 raised from 4)",
          res.insufficient_history is False)
    check(
        "decay_warning=True (recent 3 << preceding 3, drop > 20%)",
        res.decay_warning is True,
    )
    means = [c.mean_pnl_per_dollar for c in res.cohorts]
    check(
        "first 3 cohorts mean > last 3 cohorts mean",
        sum(means[:3]) / 3 > sum(means[3:]) / 3,
    )

    # Insufficient history case: 4 weeks (was enough pre-D4, now isn't)
    short_rows = [r for r in rows if r.first_fired_at < base + timedelta(weeks=4)]
    res2 = compute_edge_decay(short_rows, min_n_per_cohort=5)
    check("4 cohorts -> insufficient_history=True (D4 needs 6)",
          res2.insufficient_history is True)
    check("with insufficient_history, decay_warning stays False", res2.decay_warning is False)

    # Min n cohort filter
    res3 = compute_edge_decay(rows, min_n_per_cohort=100)
    check("min_n_per_cohort=100 -> no cohorts (each only has 30)", len(res3.cohorts) == 0)


# ===========================================================================
# B12 — insider wallet CRUD + holdings query
# ===========================================================================


async def test_insider_wallet_crud() -> None:
    section("B12 CRUD: insider_wallets round-trip")
    pool = await init_pool()
    test_addr = "0x" + "f" * 40
    async with pool.acquire() as conn:
        try:
            row = await crud.upsert_insider_wallet(
                conn, proxy_wallet=test_addr, label="smoke", notes="b12 test",
            )
            check("upsert returns row", row["proxy_wallet"] == test_addr)
            check("label stored", row["label"] == "smoke")

            got = await crud.get_insider_wallet(conn, test_addr)
            check("get_insider_wallet finds it", got is not None and got["proxy_wallet"] == test_addr)

            proxies = await crud.list_insider_wallet_proxies(conn)
            check("list_insider_wallet_proxies includes test addr", test_addr in proxies)

            # Re-upsert with new notes — COALESCE should keep label, update notes
            row2 = await crud.upsert_insider_wallet(
                conn, proxy_wallet=test_addr, label=None, notes="updated",
            )
            check("re-upsert preserves existing label via COALESCE", row2["label"] == "smoke")
            check("re-upsert updates notes", row2["notes"] == "updated")

            deleted = await crud.delete_insider_wallet(conn, test_addr)
            check("delete returns True", deleted is True)
            after = await crud.get_insider_wallet(conn, test_addr)
            check("after delete -> None", after is None)
            redel = await crud.delete_insider_wallet(conn, test_addr)
            check("re-delete returns False", redel is False)
        finally:
            await conn.execute(
                "DELETE FROM insider_wallets WHERE proxy_wallet = $1", test_addr,
            )


async def test_insider_holdings_for_markets() -> None:
    section("B12: insider_holdings_for_markets — empty + safe-handling")
    pool = await init_pool()
    async with pool.acquire() as conn:
        # Empty input -> empty result
        out = await crud.insider_holdings_for_markets(conn, [])
        check("empty cid list -> empty set", out == set())

        # Nonexistent cid -> empty result (no false matches)
        out2 = await crud.insider_holdings_for_markets(conn, ["definitely_not_a_real_cid"])
        check("non-existent cid -> empty set", out2 == set())


# ===========================================================================
# Runner
# ===========================================================================


async def run_all() -> None:
    # Pure-function tests
    # R4+R7 (Pass 3): test_normalise_wallet + test_f2_f12_counterparty_uses_
    # outcome_and_side removed (tested deleted fills-based code; R4+R7 tests
    # in smoke_phase_pass3_fixes.py cover the new positions-based logic).
    test_watchlist_floor_constants()
    test_pick_offset_for_age()
    test_compute_half_life_summary()
    test_f4_half_life_uses_mid_when_ask_present()
    test_f7_latency_unavailable_flag()
    test_f7_latency_snapshot_offsets_include_5_and_15()
    test_f5_half_life_no_direction_price_space()
    test_latency_window_resolution()
    test_sampled_latency_deterministic()
    test_nearest_snapshot_offset()
    test_apply_latency()
    test_edge_decay_grouping_and_warning()

    # DB-backed tests
    await test_migration_008_schema()
    # R4+R7 (Pass 3): test_f12_live_data_api_trades_shape removed (the
    # data-api/trades?market endpoint is no longer used; counterparty is
    # positions-based now).
    await test_set_counterparty_warning_crud()
    await test_watchlist_crud_and_cleanup()
    await test_f10_watchlist_skips_when_official_signal_exists()
    await test_signal_price_snapshot_crud()
    await test_insider_wallet_crud()
    await test_insider_holdings_for_markets()

    await close_pool()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    summary = f"\n{'=' * 80}\n  Results: {passed}/{total} passed"
    if failed:
        summary += f", {failed} FAILED\n"
    else:
        summary += "  -- ALL PASS\n"
    summary += "=" * 80
    print(summary)
    if failed:
        for label, ok, detail in results:
            if not ok:
                print(f"  FAIL: {label}{('  -- ' + detail) if detail else ''}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
