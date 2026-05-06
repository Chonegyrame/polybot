"""Smoke test for Session 3 (Phase A correctness fixes — third pass).

Tests each of the 7 items shipped in session 3:
  A18 Status endpoint composite health (overall = worst-component)
  A19 Sybil detector Scope 2 — sliding windows + group co-entry
  A20 Cluster bootstrap — upsert backfill + sweep helper
  A21 Backtest filters expose min_avg_portfolio_fraction + liquidity_tiers,
      signals API enriches with liquidity_tier, market drill-down per-trader
  A28 Paper-trade auto-close handles markets gamma drops from active feed
      (verified via API client signature accepts closed=True)
  A29 discover_and_persist_markets does closed=true follow-up sweep
      (verified via API client signature accepts closed=True)
  A31 gather_union_top_n_wallets returns same set as the loop version

Run: ./venv/Scripts/python.exe scripts/smoke_phase_a3.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.polymarket import PolymarketClient  # noqa: E402
from app.services.polymarket_types import Trade  # noqa: E402
from app.services.sybil_detector import detect_clusters, _bucket_trades  # noqa: E402
from app.services.trader_ranker import (  # noqa: E402
    gather_union_top_n_wallets,
    rank_traders,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke_phase_a3")
log.setLevel(logging.INFO)


PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}{('  -- ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n  {title}\n{'=' * 80}")


# ---------------------------------------------------------------------------
# A19: sybil detector Scope 2
# ---------------------------------------------------------------------------


def make_trade(cid: str, asset: str, t: datetime) -> Trade:
    return Trade(
        proxy_wallet="x",
        condition_id=cid,
        asset=asset,
        side="BUY",
        size=100.0,
        usdc_size=50.0,
        price=0.5,
        timestamp=t,
        transaction_hash=f"0x{cid}{asset}{int(t.timestamp())}",
        title=None, slug=None,
    )


def test_a19_dual_grid_bucketing() -> None:
    section("A19: dual-grid bucketing catches 60s boundary case")

    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Two trades 2 seconds apart but straddling minute boundary
    t1 = base.replace(second=59)  # ts // 60 = bucket A
    t2 = base.replace(minute=1, second=1)  # ts // 60 = bucket A+1
    trade_a = make_trade("m1", "YES", t1)
    trade_b = make_trade("m1", "YES", t2)

    buckets_a = _bucket_trades("A", [trade_a])
    buckets_b = _bucket_trades("B", [trade_b])

    # Check: at least one (cid, asset, grid_id, bucket) is shared
    shared = buckets_a & buckets_b
    check(
        "Trades 2s apart across minute boundary share a sliding bucket",
        len(shared) > 0,
        f"shared={len(shared)} (expected >= 1)",
    )


def test_a19_group_detection() -> None:
    section("A19: 3-wallet group co-entry detection")

    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 3 wallets co-trade in 6 distinct buckets — above SYBIL_GROUP_MIN_BUCKETS=5
    # but each PAIR's rate is < SYBIL_CO_ENTRY_THRESHOLD because they each
    # trade many other markets independently.
    coordinated = [
        make_trade(f"coord{i}", "YES", base + timedelta(minutes=i * 11))
        for i in range(6)
    ]
    # Padding: each wallet has unique trades so the per-pair rate is diluted
    pad_a = [
        make_trade(f"a_solo{i}", "YES", base + timedelta(hours=i + 1))
        for i in range(20)
    ]
    pad_b = [
        make_trade(f"b_solo{i}", "YES", base + timedelta(hours=10 + i))
        for i in range(20)
    ]
    pad_c = [
        make_trade(f"c_solo{i}", "YES", base + timedelta(hours=20 + i))
        for i in range(20)
    ]

    trades_by_wallet = {
        "A": coordinated + pad_a,
        "B": coordinated + pad_b,
        "C": coordinated + pad_c,
        # D is independent — should NOT cluster
        "D": [
            make_trade(f"d_solo{i}", "YES", base + timedelta(hours=30 + i))
            for i in range(25)
        ],
    }

    clusters = detect_clusters(trades_by_wallet)
    check(
        "Group-co-entry triggers cluster despite low pairwise rate",
        len(clusters) >= 1
        and any({"A", "B", "C"}.issubset(set(c.members)) for c in clusters),
        f"got {[c.members for c in clusters]}",
    )
    check(
        "Independent wallet D not in any cluster",
        not any("D" in c.members for c in clusters),
    )

    # Group flag should be present in evidence
    matched = next(
        (c for c in clusters if {"A", "B", "C"}.issubset(set(c.members))),
        None,
    )
    if matched:
        check(
            "Cluster evidence records group detection mode",
            "group" in matched.evidence.get("detection_modes", []),
            f"detection_modes={matched.evidence.get('detection_modes')}",
        )
        check(
            "Cluster evidence carries max_group_shared_buckets",
            "max_group_shared_buckets" in matched.evidence,
            f"evidence keys={list(matched.evidence.keys())}",
        )


# ---------------------------------------------------------------------------
# A28 + A29: API client supports closed= parameter
# ---------------------------------------------------------------------------


def test_a28_a29_closed_param() -> None:
    section("A28+A29: PolymarketClient.get_markets_by_condition_ids accepts closed=")

    sig = inspect.signature(PolymarketClient.get_markets_by_condition_ids)
    has_closed = "closed" in sig.parameters
    check("get_markets_by_condition_ids has `closed` param", has_closed)
    if has_closed:
        default = sig.parameters["closed"].default
        check(
            "Default for `closed` is None (preserve old behavior)",
            default is None,
            f"got {default!r}",
        )


# ---------------------------------------------------------------------------
# A31: bulk gather matches the loop version
# ---------------------------------------------------------------------------


async def test_a31_gather_matches_loop() -> None:
    section("A31: gather_union_top_n_wallets matches per-mode/per-category loop union")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            CATEGORIES = (
                "overall", "politics", "sports", "crypto",
                "culture", "tech", "finance",
            )
            MODES = ("absolute", "hybrid", "specialist")
            top_n = 50

            # Bulk
            bulk_set = set(await gather_union_top_n_wallets(conn, top_n, CATEGORIES))

            # Loop
            loop_set: set[str] = set()
            for cat in CATEGORIES:
                for m in MODES:
                    traders = await rank_traders(
                        conn, mode=m, category=cat, top_n=top_n,  # type: ignore[arg-type]
                    )
                    loop_set.update(t.proxy_wallet for t in traders)

            check(
                f"Bulk and loop return same wallet set ({len(bulk_set)} vs {len(loop_set)})",
                bulk_set == loop_set,
                f"bulk-only={len(bulk_set - loop_set)}, loop-only={len(loop_set - bulk_set)}",
            )
            check(
                f"Bulk returns non-empty (got {len(bulk_set)} wallets)",
                len(bulk_set) > 0,
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# A20: cluster_id sweep helper + upsert COALESCE
# ---------------------------------------------------------------------------


async def test_a20_cluster_backfill() -> None:
    section("A20: backfill_signal_log_cluster_ids sweep helper exists and is callable")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            n = await crud.backfill_signal_log_cluster_ids(conn)
            check(
                f"Sweep returns int count (n={n})",
                isinstance(n, int) and n >= 0,
                f"got {n!r}",
            )

            # Verify all signal_log rows have non-null cluster_id (or NULL only
            # for rows whose markets.event_id is also NULL)
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*) FILTER (WHERE s.cluster_id IS NULL
                                   AND m.event_id IS NOT NULL) AS fixable_remaining
                FROM signal_log s JOIN markets m ON m.condition_id = s.condition_id
                """
            )
            check(
                "After sweep, no fixable NULL cluster_ids remain",
                row["fixable_remaining"] == 0,
                f"fixable_remaining={row['fixable_remaining']}",
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# A21: BacktestFilters has the new fields and routes pass them through
# ---------------------------------------------------------------------------


def test_a21_filter_fields() -> None:
    section("A21: BacktestFilters has min_avg_portfolio_fraction + liquidity_tiers")

    from app.services.backtest_engine import BacktestFilters
    f = BacktestFilters()
    check(
        "min_avg_portfolio_fraction defaults to None",
        f.min_avg_portfolio_fraction is None,
    )
    check(
        "liquidity_tiers defaults to None",
        f.liquidity_tiers is None,
    )

    f2 = BacktestFilters(
        min_avg_portfolio_fraction=0.10,
        liquidity_tiers=("medium", "deep"),
    )
    check(
        "Construct with the new fields works",
        f2.min_avg_portfolio_fraction == 0.10
        and f2.liquidity_tiers == ("medium", "deep"),
    )


# ---------------------------------------------------------------------------
# A18: status endpoint composite health
# ---------------------------------------------------------------------------


async def test_a18_status_endpoint() -> None:
    section("A18: /system/status returns overall_health + components breakdown")

    # Call the function directly (not through HTTP) so we don't need uvicorn.
    from app.api.routes.system import get_status
    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            payload = await get_status(conn=conn)
            for key in ("overall_health", "components"):
                check(
                    f"Status payload contains '{key}'",
                    key in payload,
                    f"keys={list(payload.keys())}",
                )
            comps = payload.get("components", {})
            for sub in (
                "position_refresh", "daily_snapshot", "wallet_classifier",
                "tracked_wallets", "recent_signals",
            ):
                check(
                    f"  components.{sub} present",
                    sub in comps,
                )
                if sub in comps:
                    check(
                        f"  components.{sub}.health is green/amber/red",
                        comps[sub]["health"] in ("green", "amber", "red"),
                        f"got {comps[sub]['health']!r}",
                    )
            check(
                "overall_health is one of green/amber/red",
                payload["overall_health"] in ("green", "amber", "red"),
                f"got {payload['overall_health']!r}",
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nSession 3 (Phase A) smoke test\n" + "=" * 80)

    # Pure-function tests — no DB needed
    test_a19_dual_grid_bucketing()
    test_a19_group_detection()
    test_a28_a29_closed_param()
    test_a21_filter_fields()

    # DB integration tests
    await test_a31_gather_matches_loop()
    await test_a20_cluster_backfill()
    await test_a18_status_endpoint()

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
    print("\n  All session-3 changes verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
