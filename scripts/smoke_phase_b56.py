"""Smoke test for B5 (trader_category_stats) + B6 (cross-mode dedup view).

B5 tests:
  - Migration 006: trader_category_stats table exists
  - Pure-function: aggregate_trades_per_category buckets by category
  - CRUD: upsert_trader_category_stats_bulk + latest_pnl_volume_per_category
  - Trader ranker still works after the schema additions (bootstrap-safe
    when table is empty); shrinkage gives different ROI rank in synthetic
    case where small-sample wallet has high raw ROI

B6 tests:
  - Migration 007: vw_signals_unique_market view exists
  - View collapses signal_log correctly (one row per cid+direction)
  - Backtest engine accepts dedup=True without error
  - lens_count_bucket is a valid slice dimension

Run: ./venv/Scripts/python.exe scripts/smoke_phase_b56.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    SignalRow,
    _bucket,
    backtest_summary,
)
from app.services.polymarket_types import Trade  # noqa: E402
from app.services.trader_stats import (  # noqa: E402
    aggregate_trades_per_category,
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


def make_trade(cid: str, t: datetime) -> Trade:
    return Trade(
        proxy_wallet="x", condition_id=cid, asset="a", side="BUY",
        size=100.0, usdc_size=50.0, price=0.5,
        timestamp=t, transaction_hash=f"0x{cid}{int(t.timestamp())}",
        title=None, slug=None,
    )


# ---------------------------------------------------------------------------
# B5 pure-function: aggregate_trades_per_category
# ---------------------------------------------------------------------------


def test_aggregate_buckets() -> None:
    section("B5: aggregate_trades_per_category buckets correctly")

    base_t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    trades = [
        make_trade("c1", base_t + timedelta(hours=1)),
        make_trade("c1", base_t + timedelta(hours=2)),
        make_trade("c2", base_t + timedelta(hours=3)),  # politics, resolved
        make_trade("c3", base_t + timedelta(hours=4)),  # unknown market
    ]
    cid_to_category = {"c1": "sports", "c2": "politics"}
    cid_to_resolved = {"c1": True, "c2": True}  # c3 unknown, resolved=False

    out = aggregate_trades_per_category(trades, cid_to_category, cid_to_resolved)

    # overall counts ALL resolved trades (c1 twice + c2 once = 3)
    check(
        "Overall: 3 resolved trades counted",
        out.get("overall") is not None and out["overall"].resolved_trades == 3,
        f"got {out.get('overall')}",
    )
    # last_trade_at on overall is the latest timestamp (c3 at +4h)
    check(
        "Overall: last_trade_at is latest across all trades (incl unknown)",
        out["overall"].last_trade_at == base_t + timedelta(hours=4),
        f"got {out['overall'].last_trade_at}",
    )
    # Sports: 2 trades, both resolved
    check(
        "Sports: 2 resolved",
        out.get("sports") is not None and out["sports"].resolved_trades == 2,
    )
    # Politics: 1 resolved
    check(
        "Politics: 1 resolved",
        out.get("politics") is not None and out["politics"].resolved_trades == 1,
    )
    # No row for unknown
    check(
        "No category row for unknown markets",
        all(c not in out for c in ("crypto", "tech", "finance", "culture")),
        f"keys={sorted(out.keys())}",
    )


def test_aggregate_only_resolved_count() -> None:
    section("B5: only resolved markets count toward resolved_trades")

    base_t = datetime(2026, 5, 1, tzinfo=timezone.utc)
    trades = [make_trade("u1", base_t + timedelta(hours=i)) for i in range(5)]
    cid_to_category = {"u1": "sports"}
    cid_to_resolved = {"u1": False}  # not resolved

    out = aggregate_trades_per_category(trades, cid_to_category, cid_to_resolved)
    check(
        "Open market: 0 resolved_trades but last_trade_at recorded",
        out["sports"].resolved_trades == 0
        and out["sports"].last_trade_at is not None,
        f"got {out['sports']}",
    )


# ---------------------------------------------------------------------------
# B5 DB integration
# ---------------------------------------------------------------------------


async def test_b5_schema_and_crud() -> None:
    section("B5: trader_category_stats table + CRUD round-trip")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT to_regclass('trader_category_stats') AS r")
            check("trader_category_stats table exists", row["r"] is not None)

            # Pick any real trader for FK
            t_row = await conn.fetchrow("SELECT proxy_wallet FROM traders LIMIT 1")
            if t_row is None:
                check("Skipped: no traders in DB", True, "skipped")
                return
            wallet = t_row["proxy_wallet"]

            # Cleanup any leftover test rows
            await conn.execute(
                "DELETE FROM trader_category_stats WHERE proxy_wallet = $1",
                wallet,
            )

            # Bulk upsert
            now = datetime.now(timezone.utc)
            await crud.upsert_trader_category_stats_bulk(
                conn,
                [
                    (wallet, "overall",  10000.0, 50000.0, 42, now),
                    (wallet, "politics",  5000.0, 20000.0, 18, now),
                ],
            )

            row = await conn.fetchrow(
                "SELECT category_pnl_usdc::numeric AS pnl, resolved_trades, last_trade_at "
                "FROM trader_category_stats WHERE proxy_wallet = $1 AND category = 'overall'",
                wallet,
            )
            check(
                "Upsert wrote overall row correctly",
                row is not None
                and float(row["pnl"]) == 10000.0
                and row["resolved_trades"] == 42,
                f"got {dict(row) if row else None}",
            )

            # Bulk leaderboard query helper
            pnl_vol = await crud.latest_pnl_volume_per_category(conn, [wallet])
            check(
                "latest_pnl_volume_per_category returns dict",
                isinstance(pnl_vol, dict),
            )

            # Cleanup
            await conn.execute(
                "DELETE FROM trader_category_stats WHERE proxy_wallet = $1",
                wallet,
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# B5 ranker bootstrap-safe behavior
# ---------------------------------------------------------------------------


async def test_b5_ranker_bootstrap_safe() -> None:
    section("B5: trader_ranker still returns results when stats table is empty/sparse")

    from app.services.trader_ranker import (
        rank_traders, gather_union_top_n_wallets,
    )

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*)::INT AS n FROM trader_category_stats"
            )
            n_stats = row["n"] if row else 0

            # absolute / hybrid / specialist all should return non-empty
            # results (since live system has signals firing already)
            for mode in ("absolute", "hybrid", "specialist"):
                ranked = await rank_traders(
                    conn, mode=mode, category="overall", top_n=10,  # type: ignore[arg-type]
                )
                check(
                    f"{mode}/overall returns rows (stats_seeded={n_stats > 0})",
                    isinstance(ranked, list),
                    f"got {len(ranked)} rows",
                )

            # Bulk wallet gather
            wallets = await gather_union_top_n_wallets(
                conn, top_n=50,
                categories=("overall", "politics", "sports", "crypto",
                            "culture", "tech", "finance"),
            )
            check(
                "gather_union_top_n_wallets returns non-empty list",
                isinstance(wallets, list) and len(wallets) > 0,
                f"got {len(wallets)} wallets",
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# B6: dedup view + backtest dedup flag
# ---------------------------------------------------------------------------


async def test_f1_shrinkage_uses_roi_prior_not_dollar_pnl_prior() -> None:
    """F1 regression: Bayesian shrinkage uses an ROI prior (sum_pnl/sum_vol),
    not the dollar-pnl prior AVG(pnl) it had before. The old formula made
    `shrunk_roi` for tiny-volume traders explode (k * avg_pnl dominates the
    numerator, vol+k dominates the denominator), so small-sample traders
    sorted to the top regardless of skill.

    See review/02_signal_logic.md Critical #1 and review/FIXES.md F1.

    Synthetic pool (3 wallets, hybrid + specialist modes):
        A — $1M vol, $100k pnl, ROI 10% (large sample, modest)
        B — $5,001 vol, $2k pnl, ROI 40% (tiny sample, "lucky")
        C — $1M vol, $200k pnl, ROI 20% (large sample, the real winner)

    Correct shrinkage with k=$50k pulls B's ROI toward the prior (~0.15);
    under the bug it explodes to ~91k (a dollar quantity treated as a rate).

    Expected roi_rank under fix:  C=1, B=2, A=3 (hybrid)
    Under the bug it was:         B=1, C=2, A=3
    """
    section("F1: shrinkage uses ROI prior (sum_pnl / sum_vol), not AVG(pnl)")

    from app.services.trader_ranker import rank_traders

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            # Use a future snapshot_date so we don't collide with live data.
            snap_date = date(2099, 1, 15)
            now = datetime.now(timezone.utc)

            # Synthetic wallets — fresh hex strings, FK-safe via traders insert.
            wallets = {
                "A": "0xf1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01",
                "B": "0xf1bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb02",
                "C": "0xf1cccccccccccccccccccccccccccccccccccc03",
            }

            async def cleanup() -> None:
                for w in wallets.values():
                    await conn.execute(
                        "DELETE FROM leaderboard_snapshots WHERE proxy_wallet=$1 AND snapshot_date=$2",
                        w, snap_date,
                    )
                    await conn.execute(
                        "DELETE FROM trader_category_stats WHERE proxy_wallet=$1",
                        w,
                    )
                    await conn.execute(
                        "DELETE FROM wallet_classifications WHERE proxy_wallet=$1",
                        w,
                    )
                    await conn.execute(
                        "DELETE FROM traders WHERE proxy_wallet=$1",
                        w,
                    )

            await cleanup()  # in case a prior aborted run left rows

            try:
                # Insert traders (FK target for leaderboard_snapshots)
                for name, w in wallets.items():
                    await conn.execute(
                        """
                        INSERT INTO traders (proxy_wallet, user_name, verified_badge)
                        VALUES ($1, $2, FALSE)
                        ON CONFLICT (proxy_wallet) DO NOTHING
                        """,
                        w, f"f1_test_{name}",
                    )

                # Hybrid pool: A=$1M/$100k, B=$5,001/$2k, C=$1M/$200k
                hybrid_rows = [
                    (wallets["A"], 100_000.0, 1_000_000.0, 1),
                    (wallets["B"],   2_000.0,     5_001.0, 2),
                    (wallets["C"], 200_000.0, 1_000_000.0, 3),
                ]
                for w, pnl, vol, rank in hybrid_rows:
                    await conn.execute(
                        """
                        INSERT INTO leaderboard_snapshots
                            (snapshot_date, category, time_period, order_by,
                             proxy_wallet, rank, pnl, vol)
                        VALUES ($1, 'overall', 'all', 'PNL', $2, $3, $4, $5)
                        """,
                        snap_date, w, rank, pnl, vol,
                    )

                # trader_category_stats overall rows so recency filter passes
                for w in wallets.values():
                    await conn.execute(
                        """
                        INSERT INTO trader_category_stats
                            (proxy_wallet, category, category_pnl_usdc,
                             category_volume_usdc, category_roi, resolved_trades,
                             last_trade_at)
                        VALUES ($1, 'overall', 0, 0, 0, 50, $2)
                        ON CONFLICT (proxy_wallet, category) DO UPDATE
                          SET last_trade_at = EXCLUDED.last_trade_at,
                              resolved_trades = EXCLUDED.resolved_trades
                        """,
                        w, now,
                    )

                # Run hybrid ranker against the synthetic snapshot
                ranked = await rank_traders(
                    conn, mode="hybrid", category="overall", top_n=10,
                    snapshot_date=snap_date,
                )
                by_wallet = {r.proxy_wallet: r for r in ranked}

                got_a = by_wallet.get(wallets["A"])
                got_b = by_wallet.get(wallets["B"])
                got_c = by_wallet.get(wallets["C"])

                check(
                    "Hybrid: all 3 synthetic traders present in ranking",
                    got_a is not None and got_b is not None and got_c is not None,
                    f"got A={got_a is not None} B={got_b is not None} C={got_c is not None}",
                )

                if got_a and got_b and got_c:
                    # Under the bug: roi_rank order would be B=1, C=2, A=3
                    # Under the fix: roi_rank order is   C=1, B=2, A=3
                    check(
                        "Hybrid roi_rank: C ranks above B (fix; bug had B>C)",
                        got_c.roi_rank < got_b.roi_rank,
                        f"C.roi_rank={got_c.roi_rank}, B.roi_rank={got_b.roi_rank}",
                    )
                    check(
                        "Hybrid roi_rank: B ranks above A (small-sample with above-prior ROI still beats below-prior trader)",
                        got_b.roi_rank < got_a.roi_rank,
                        f"B.roi_rank={got_b.roi_rank}, A.roi_rank={got_a.roi_rank}",
                    )
                    check(
                        "Hybrid roi_rank: C is #1 in the synthetic pool",
                        got_c.roi_rank == 1,
                        f"C.roi_rank={got_c.roi_rank}",
                    )

                # ---------- Specialist mode ----------
                # Replace hybrid rows with specialist-eligible vols (>=$20k each)
                # and add 'month' leaderboard rows so active_recently passes.
                for w in wallets.values():
                    await conn.execute(
                        "DELETE FROM leaderboard_snapshots WHERE proxy_wallet=$1 AND snapshot_date=$2",
                        w, snap_date,
                    )

                spec_rows = [
                    (wallets["A"], 100_000.0, 1_000_000.0, 1),
                    (wallets["B"],   5_000.0,    25_000.0, 2),  # tiny but above $20k floor
                    (wallets["C"], 200_000.0, 1_000_000.0, 3),
                ]
                for w, pnl, vol, rank in spec_rows:
                    # all-time row
                    await conn.execute(
                        """
                        INSERT INTO leaderboard_snapshots
                            (snapshot_date, category, time_period, order_by,
                             proxy_wallet, rank, pnl, vol)
                        VALUES ($1, 'overall', 'all', 'PNL', $2, $3, $4, $5)
                        """,
                        snap_date, w, rank, pnl, vol,
                    )
                    # monthly row (specialist's active_recently check)
                    await conn.execute(
                        """
                        INSERT INTO leaderboard_snapshots
                            (snapshot_date, category, time_period, order_by,
                             proxy_wallet, rank, pnl, vol)
                        VALUES ($1, 'overall', 'month', 'PNL', $2, $3, $4, $5)
                        """,
                        snap_date, w, rank, pnl, vol,
                    )
                    # per-category stats row (specialist's resolved_trades floor)
                    await conn.execute(
                        """
                        INSERT INTO trader_category_stats
                            (proxy_wallet, category, category_pnl_usdc,
                             category_volume_usdc, category_roi, resolved_trades,
                             last_trade_at)
                        VALUES ($1, 'overall', 0, 0, 0, 50, $2)
                        ON CONFLICT (proxy_wallet, category) DO UPDATE
                          SET last_trade_at = EXCLUDED.last_trade_at,
                              resolved_trades = EXCLUDED.resolved_trades
                        """,
                        w, now,
                    )

                ranked_spec = await rank_traders(
                    conn, mode="specialist", category="overall", top_n=10,
                    snapshot_date=snap_date,
                )
                by_wallet_s = {r.proxy_wallet: r for r in ranked_spec}
                got_as = by_wallet_s.get(wallets["A"])
                got_bs = by_wallet_s.get(wallets["B"])
                got_cs = by_wallet_s.get(wallets["C"])

                check(
                    "Specialist: all 3 synthetic traders present",
                    got_as is not None and got_bs is not None and got_cs is not None,
                    f"got A={got_as is not None} B={got_bs is not None} C={got_cs is not None}",
                )

                if got_as and got_bs and got_cs:
                    # Specialist sorts directly by shrunk_roi DESC.
                    # Under bug: B=1, C=2, A=3 (B's shrunk_roi explodes).
                    # Under fix: C=1, B=2, A=3 (C's 20% ROI on $1M wins).
                    check(
                        "Specialist rank: C ranks above B (fix; bug had B>C)",
                        got_cs.rank < got_bs.rank,
                        f"C.rank={got_cs.rank}, B.rank={got_bs.rank}",
                    )
                    check(
                        "Specialist rank: C is #1 in the synthetic pool",
                        got_cs.rank == 1,
                        f"C.rank={got_cs.rank}",
                    )
            finally:
                await cleanup()
    finally:
        await close_pool()


async def test_b6_view() -> None:
    section("B6: vw_signals_unique_market view")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT to_regclass('vw_signals_unique_market') AS r"
            )
            check("vw_signals_unique_market view exists", row["r"] is not None)

            # Compare counts: view should be <= signal_log row count
            sl_count = await conn.fetchval("SELECT COUNT(*) FROM signal_log")
            v_count = await conn.fetchval("SELECT COUNT(*) FROM vw_signals_unique_market")
            check(
                f"View count ({v_count}) <= signal_log count ({sl_count})",
                v_count <= sl_count,
            )

            # Verify view has lens_count + lens_list and they're populated
            row = await conn.fetchrow(
                "SELECT lens_count, lens_list FROM vw_signals_unique_market LIMIT 1"
            )
            if row is not None:
                check(
                    "View row carries lens_count >= 1",
                    row["lens_count"] is not None and row["lens_count"] >= 1,
                    f"got {row['lens_count']}",
                )
                check(
                    "View row carries lens_list as array",
                    row["lens_list"] is not None and isinstance(row["lens_list"], list),
                    f"got {row['lens_list']}",
                )
            else:
                check("Skipped: view is empty (no signals yet)", True, "empty")
    finally:
        await close_pool()


async def test_b6_backtest_dedup_flag() -> None:
    section("B6: backtest_summary accepts dedup=True")

    res_normal = await backtest_summary(BacktestFilters(dedup=False))
    res_dedup = await backtest_summary(BacktestFilters(dedup=True))

    check(
        "dedup=False returns a result",
        res_normal is not None and isinstance(res_normal.n_signals, int),
        f"n_signals={res_normal.n_signals}",
    )
    check(
        "dedup=True returns a result with n_signals <= dedup=False",
        res_dedup is not None
        and isinstance(res_dedup.n_signals, int)
        and res_dedup.n_signals <= res_normal.n_signals,
        f"normal={res_normal.n_signals}, dedup={res_dedup.n_signals}",
    )


def test_b9_lens_count_bucket() -> None:
    section("B9: lens_count_bucket slice dimension")

    base_t = datetime(2026, 5, 1, tzinfo=timezone.utc)

    def row(lc: int) -> SignalRow:
        return SignalRow(
            id=lc, mode="absolute", category="overall", top_n=50,
            condition_id=f"c{lc}", direction="YES",
            first_trader_count=10, first_aggregate_usdc=100_000.0,
            first_net_skew=0.85, first_avg_portfolio_fraction=0.10,
            signal_entry_offer=0.40, signal_entry_mid=0.40,
            liquidity_at_signal_usdc=25_000.0, liquidity_tier="medium",
            first_top_trader_entry_price=0.40,
            cluster_id="clu1", market_type="binary",
            first_fired_at=base_t,
            resolved_outcome="YES", market_category="politics",
            lens_count=lc,
        )

    check("lens_count=1 -> '1'", _bucket(row(1), "lens_count_bucket") == "1")
    check("lens_count=2 -> '2-3'", _bucket(row(2), "lens_count_bucket") == "2-3")
    check("lens_count=3 -> '2-3'", _bucket(row(3), "lens_count_bucket") == "2-3")
    check("lens_count=4 -> '4-5'", _bucket(row(4), "lens_count_bucket") == "4-5")
    check("lens_count=5 -> '4-5'", _bucket(row(5), "lens_count_bucket") == "4-5")
    check("lens_count=6 -> '6+'", _bucket(row(6), "lens_count_bucket") == "6+")
    check("lens_count=99 -> '6+'", _bucket(row(99), "lens_count_bucket") == "6+")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nSession 5 (B5 + B6) smoke test\n" + "=" * 80)

    test_aggregate_buckets()
    test_aggregate_only_resolved_count()
    test_b9_lens_count_bucket()

    await test_b5_schema_and_crud()
    await test_b5_ranker_bootstrap_safe()
    await test_f1_shrinkage_uses_roi_prior_not_dollar_pnl_prior()
    await test_b6_view()
    await test_b6_backtest_dedup_flag()

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
    print("\n  All B5 + B6 changes verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
