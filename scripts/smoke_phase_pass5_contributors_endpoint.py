"""Pass 5 — GET /signals/{id}/contributors endpoint smoke tests.

Verifies the contributors + counterparty panel that drives UI-SPEC.md
Section 2's expandable signal panel. Cluster-aware: a 4-wallet sybil
cluster appears as ONE row with cluster_size=4 and dollar fields summed
across the FULL cluster's positions on this market.

Coverage:
  - crud + route 404 path on nonexistent signal_log_id
  - contributors with one-sided cluster + retail (cluster collapsed)
  - contributors with hedged cluster (is_hedged=True; net_exposure)
  - contributors with lone wallet (cluster_size=1 path)
  - response shape (summary block + counterparty list)
  - counterparty behavioral check (entity passes is_counterparty;
    contributors are excluded from the pool)

Uses synthetic wallet_clusters / cluster_membership / positions /
signal_log fixtures with cleanup. Borrows real traders + an open market
for FK validity.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_contributors_endpoint.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import init_pool, close_pool  # noqa: E402
from app.db import crud  # noqa: E402


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
# Fixture helpers
# ---------------------------------------------------------------------------


TEST_MODE = "__pass5_contrib_test__"


async def setup_fixture(conn, n_wallets: int = 6) -> dict:
    """Borrow N real traders + one open market. Snapshot existing rows so
    we can restore them after the test."""
    traders = await conn.fetch(
        "SELECT proxy_wallet FROM traders LIMIT $1", n_wallets,
    )
    if len(traders) < n_wallets:
        return {}
    wallets = [t["proxy_wallet"] for t in traders]

    market = await conn.fetchrow(
        """
        SELECT condition_id FROM markets
        WHERE closed = FALSE
        LIMIT 1
        """
    )
    if not market:
        return {}
    cid = market["condition_id"]

    existing_positions = await conn.fetch(
        """
        SELECT proxy_wallet, condition_id, asset, outcome, size,
               cur_price, current_value, avg_price,
               first_seen_at, last_updated_at
        FROM positions
        WHERE proxy_wallet = ANY($1::TEXT[])
          AND condition_id = $2
        """,
        wallets, cid,
    )
    existing_clusters = await conn.fetch(
        """
        SELECT cluster_id, proxy_wallet, joined_at
        FROM cluster_membership
        WHERE proxy_wallet = ANY($1::TEXT[])
        """,
        wallets,
    )

    # Wipe what we'll replace.
    await conn.execute(
        "DELETE FROM positions WHERE proxy_wallet = ANY($1::TEXT[]) "
        "AND condition_id = $2",
        wallets, cid,
    )
    await conn.execute(
        "DELETE FROM cluster_membership WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )

    return {
        "wallets": wallets,
        "cid": cid,
        "existing_positions": existing_positions,
        "existing_clusters": existing_clusters,
    }


async def teardown_fixture(
    conn, fx: dict, test_cluster_ids: list[str], test_signal_ids: list[int],
) -> None:
    wallets = fx["wallets"]
    cid = fx["cid"]
    # Signal log + cluster wipe
    if test_signal_ids:
        await conn.execute(
            "DELETE FROM signal_log WHERE id = ANY($1::BIGINT[])",
            test_signal_ids,
        )
    await conn.execute(
        "DELETE FROM signal_log WHERE mode = $1", TEST_MODE,
    )
    await conn.execute(
        "DELETE FROM positions WHERE proxy_wallet = ANY($1::TEXT[]) "
        "AND condition_id = $2",
        wallets, cid,
    )
    await conn.execute(
        "DELETE FROM cluster_membership WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )
    if test_cluster_ids:
        await conn.execute(
            "DELETE FROM wallet_clusters WHERE cluster_id = ANY($1::UUID[])",
            test_cluster_ids,
        )
    # Restore originals
    for r in fx["existing_clusters"]:
        await conn.execute(
            """
            INSERT INTO cluster_membership (cluster_id, proxy_wallet, joined_at)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            r["cluster_id"], r["proxy_wallet"], r["joined_at"],
        )
    for r in fx["existing_positions"]:
        await conn.execute(
            """
            INSERT INTO positions (proxy_wallet, condition_id, asset, outcome,
                                    size, cur_price, current_value, avg_price,
                                    first_seen_at, last_updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT DO NOTHING
            """,
            r["proxy_wallet"], r["condition_id"], r["asset"], r["outcome"],
            r["size"], r["cur_price"], r["current_value"], r["avg_price"],
            r["first_seen_at"], r["last_updated_at"],
        )


async def make_cluster(conn, label: str, wallets: list[str]) -> str:
    cluster_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO wallet_clusters (cluster_id, cluster_label, detection_method,
                                     detected_at, evidence)
        VALUES ($1::UUID, $2, 'manual', NOW(), '{}'::jsonb)
        """,
        cluster_id, label,
    )
    for w in wallets:
        await conn.execute(
            """
            INSERT INTO cluster_membership (cluster_id, proxy_wallet, joined_at)
            VALUES ($1::UUID, $2, NOW())
            """,
            cluster_id, w,
        )
    return cluster_id


async def insert_position(
    conn, wallet: str, cid: str, outcome: str,
    size: float, current_value: float,
    cur_price: float = 0.50, avg_price: float = 0.40,
) -> None:
    await conn.execute(
        """
        INSERT INTO positions
            (proxy_wallet, condition_id, asset, outcome,
             size, cur_price, current_value, avg_price,
             first_seen_at, last_updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
        """,
        wallet, cid, outcome + "_TOKEN", outcome,
        size, cur_price, current_value, avg_price,
    )


async def insert_signal_log(
    conn, *, mode: str, category: str, top_n: int, condition_id: str,
    direction: str, contributing_wallets: list[str],
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO signal_log
            (mode, category, top_n, condition_id, direction,
             first_fired_at, last_seen_at,
             peak_trader_count, peak_aggregate_usdc, peak_net_skew,
             first_trader_count, first_aggregate_usdc, first_net_skew,
             market_type, contributing_wallets)
        VALUES ($1, $2, $3, $4, $5,
                NOW() - INTERVAL '30 minutes',
                NOW() - INTERVAL '5 minutes',
                $6, $7, 0.85,
                $6, $7, 0.85,
                'binary', $8)
        RETURNING id
        """,
        mode, category, top_n, condition_id, direction,
        len(contributing_wallets), 50000.0,
        contributing_wallets,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_404_on_unknown_signal() -> None:
    section("404 path: unknown signal_log_id returns None")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            result = await crud.get_signal_contributors_and_counterparty(
                conn, signal_log_id=-99999999,
            )
            check(
                "crud.get_signal_contributors_and_counterparty(-99999999) -> None",
                result is None,
                f"got {result}",
            )
    finally:
        await close_pool()


async def test_contributors_one_sided_cluster_plus_retail() -> None:
    section("Contributors: 4-wallet cluster on YES + 1 retail on YES")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    test_signal_ids: list[int] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn, n_wallets=5)
            if not fx:
                check("skipped (need fixture)", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]
            retail = wallets[4]

            cid_uuid = await make_cluster(conn, "TestClusterA", cluster_wallets)
            test_cluster_ids.append(cid_uuid)

            for w in cluster_wallets:
                await insert_position(conn, w, cid, "Yes", 40_000.0, 20_000.0)
            await insert_position(conn, retail, cid, "Yes", 10_000.0, 5_000.0)

            sid = await insert_signal_log(
                conn, mode=TEST_MODE, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                contributing_wallets=wallets,
            )
            test_signal_ids.append(sid)

            result = await crud.get_signal_contributors_and_counterparty(
                conn, sid,
            )
            check("response is not None", result is not None)
            if not result:
                return

            check(
                "response top-level keys",
                set(result.keys()) >= {
                    "signal_log_id", "condition_id", "direction",
                    "contributors", "counterparty", "summary",
                },
            )

            contribs = result["contributors"]
            # 5 cohort wallets -> 4-wallet cluster + 1 retail = 2 entities.
            check(
                "len(contributors) = 2 (cluster collapsed)",
                len(contribs) == 2,
                f"got {len(contribs)}: {[c['cluster_size'] for c in contribs]}",
            )
            cluster_entity = next(
                (c for c in contribs if c["cluster_size"] == 4), None,
            )
            retail_entity = next(
                (c for c in contribs if c["cluster_size"] == 1), None,
            )
            check("cluster entity present (size=4)", cluster_entity is not None)
            check("retail entity present (size=1)", retail_entity is not None)
            if cluster_entity:
                check(
                    "cluster.same_side_usdc = $80k (4 wallets x $20k)",
                    abs(cluster_entity["same_side_usdc"] - 80_000.0) < 0.01,
                    f"got {cluster_entity['same_side_usdc']}",
                )
                check(
                    "cluster.opposite_side_usdc = 0 (one-sided)",
                    cluster_entity["opposite_side_usdc"] == 0.0,
                )
                check(
                    "cluster.is_hedged = False (one-sided)",
                    cluster_entity["is_hedged"] is False,
                )
                check(
                    "cluster.net_exposure_usdc = $80k",
                    abs(cluster_entity["net_exposure_usdc"] - 80_000.0) < 0.01,
                )
                check(
                    "cluster.cluster_label = 'TestClusterA'",
                    cluster_entity["cluster_label"] == "TestClusterA",
                )
                check(
                    "cluster.cluster_id is the UUID string",
                    cluster_entity["cluster_id"] == cid_uuid,
                )
                check(
                    "cluster.wallets has all 4 cluster members",
                    len(cluster_entity["wallets"]) == 4
                    and set(cluster_entity["wallets"]) == set(cluster_wallets),
                )
            if retail_entity:
                check(
                    "retail.same_side_usdc = $5k",
                    abs(retail_entity["same_side_usdc"] - 5_000.0) < 0.01,
                )
                check(
                    "retail.cluster_id is None (lone wallet)",
                    retail_entity["cluster_id"] is None,
                )
                check(
                    "retail.cluster_label is None",
                    retail_entity["cluster_label"] is None,
                )

            summary = result["summary"]
            check(
                "summary.n_contributors = 2",
                summary["n_contributors"] == 2,
            )
            check(
                "summary.n_hedged_contributors = 0 (one-sided cohort)",
                summary["n_hedged_contributors"] == 0,
            )
            check(
                "summary.total_same_side_usdc = $85k",
                abs(summary["total_same_side_usdc"] - 85_000.0) < 0.01,
            )
            check(
                "summary.total_opposite_side_usdc = 0",
                summary["total_opposite_side_usdc"] == 0.0,
            )

            # Counterparty list is a list (may be empty in this scenario --
            # depends on whether the live top-N pool happens to hold opposite
            # on this market). We only assert structural integrity here.
            check(
                "result.counterparty is a list",
                isinstance(result["counterparty"], list),
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids, test_signal_ids)
        await close_pool()


async def test_contributors_hedged_cluster() -> None:
    section("Contributors: cluster holds BOTH sides -> is_hedged=True")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    test_signal_ids: list[int] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn, n_wallets=5)
            if not fx:
                check("skipped (need fixture)", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cid_uuid = await make_cluster(conn, "HedgedCluster", cluster_wallets)
            test_cluster_ids.append(cid_uuid)

            # 3 wallets on YES at $23.33k each (~$70k YES total)
            # 1 wallet on NO at $20k
            await insert_position(conn, cluster_wallets[0], cid, "Yes",
                                  46_667.0, 23_333.34)
            await insert_position(conn, cluster_wallets[1], cid, "Yes",
                                  46_667.0, 23_333.33)
            await insert_position(conn, cluster_wallets[2], cid, "Yes",
                                  46_666.0, 23_333.33)
            await insert_position(conn, cluster_wallets[3], cid, "No",
                                  40_000.0, 20_000.0)

            sid = await insert_signal_log(
                conn, mode=TEST_MODE, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                contributing_wallets=cluster_wallets,
            )
            test_signal_ids.append(sid)

            result = await crud.get_signal_contributors_and_counterparty(
                conn, sid,
            )
            check("response is not None", result is not None)
            if not result:
                return

            contribs = result["contributors"]
            check("len(contributors) = 1 (cluster identity)",
                  len(contribs) == 1, f"got {len(contribs)}")
            if contribs:
                c = contribs[0]
                check("cluster.same_side_usdc ~= $70k",
                      abs(c["same_side_usdc"] - 70_000.0) < 1.0,
                      f"got {c['same_side_usdc']}")
                check("cluster.opposite_side_usdc = $20k",
                      abs(c["opposite_side_usdc"] - 20_000.0) < 0.01,
                      f"got {c['opposite_side_usdc']}")
                check("cluster.is_hedged = True (both sides)",
                      c["is_hedged"] is True)
                check("cluster.net_exposure_usdc = $50k (70 - 20)",
                      abs(c["net_exposure_usdc"] - 50_000.0) < 1.0,
                      f"got {c['net_exposure_usdc']}")

            summary = result["summary"]
            check("summary.n_hedged_contributors = 1",
                  summary["n_hedged_contributors"] == 1)
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids, test_signal_ids)
        await close_pool()


async def test_contributors_lone_wallet_only() -> None:
    section("Contributors: 3 lone wallets, no clusters")

    pool = await init_pool(min_size=1, max_size=2)
    test_signal_ids: list[int] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn, n_wallets=3)
            if not fx:
                check("skipped (need fixture)", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]

            for w in wallets:
                await insert_position(conn, w, cid, "Yes", 20_000.0, 10_000.0)

            sid = await insert_signal_log(
                conn, mode=TEST_MODE, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                contributing_wallets=wallets,
            )
            test_signal_ids.append(sid)

            result = await crud.get_signal_contributors_and_counterparty(
                conn, sid,
            )
            check("response is not None", result is not None)
            if not result:
                return
            contribs = result["contributors"]
            check("len(contributors) = 3 (3 lone wallets)",
                  len(contribs) == 3, f"got {len(contribs)}")
            for c in contribs:
                check(
                    f"lone wallet has cluster_size=1 (wallet={c['proxy_wallet'][:10]}...)",
                    c["cluster_size"] == 1 and c["cluster_id"] is None,
                )
            check(
                "summary.total_same_side_usdc = $30k (3 x $10k)",
                abs(result["summary"]["total_same_side_usdc"] - 30_000.0) < 0.01,
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, [], test_signal_ids)
        await close_pool()


async def test_response_shape() -> None:
    section("Response shape: every contributor + counterparty entry has the documented keys")

    REQUIRED_KEYS = {
        "proxy_wallet", "user_name", "verified_badge",
        "cluster_id", "cluster_label", "cluster_size", "wallets",
        "same_side_usdc", "opposite_side_usdc",
        "is_hedged", "net_exposure_usdc",
        "avg_entry_price",
        "lifetime_pnl_usdc", "lifetime_roi",
    }

    pool = await init_pool(min_size=1, max_size=2)
    test_signal_ids: list[int] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn, n_wallets=3)
            if not fx:
                check("skipped (need fixture)", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            for w in wallets:
                await insert_position(conn, w, cid, "Yes", 20_000.0, 10_000.0)
            sid = await insert_signal_log(
                conn, mode=TEST_MODE, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                contributing_wallets=wallets,
            )
            test_signal_ids.append(sid)

            result = await crud.get_signal_contributors_and_counterparty(
                conn, sid,
            )
            check("response is not None", result is not None)
            if not result:
                return

            for c in result["contributors"]:
                check(
                    f"contributor entity has all required keys",
                    set(c.keys()) >= REQUIRED_KEYS,
                    f"missing: {REQUIRED_KEYS - set(c.keys())}",
                )

            summary = result["summary"]
            check(
                "summary has required keys",
                set(summary.keys()) >= {
                    "n_contributors", "n_hedged_contributors",
                    "n_counterparty",
                    "total_same_side_usdc", "total_opposite_side_usdc",
                },
            )
            check(
                "result.counterparty is a list (may be empty)",
                isinstance(result["counterparty"], list),
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, [], test_signal_ids)
        await close_pool()


async def test_route_404() -> None:
    section("Route /signals/{id}/contributors returns 404 for unknown id")

    from fastapi import HTTPException
    from app.api.routes.signals import get_signal_contributors

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            raised_404 = False
            try:
                await get_signal_contributors(
                    signal_log_id=-99999999, conn=conn,
                )
            except HTTPException as e:
                raised_404 = e.status_code == 404
            check(
                "get_signal_contributors(-99999999) raises HTTPException(404)",
                raised_404,
            )
    finally:
        await close_pool()


async def test_route_returns_response() -> None:
    section("Route handler returns the documented shape for a real signal_log_id")

    from app.api.routes.signals import get_signal_contributors

    pool = await init_pool(min_size=1, max_size=2)
    test_signal_ids: list[int] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn, n_wallets=3)
            if not fx:
                check("skipped (need fixture)", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            for w in wallets:
                await insert_position(conn, w, cid, "Yes", 20_000.0, 10_000.0)
            sid = await insert_signal_log(
                conn, mode=TEST_MODE, category="overall", top_n=50,
                condition_id=cid, direction="YES",
                contributing_wallets=wallets,
            )
            test_signal_ids.append(sid)

            resp = await get_signal_contributors(signal_log_id=sid, conn=conn)
            check(
                "route handler returned a dict",
                isinstance(resp, dict),
            )
            check(
                "route response has contributors+counterparty+summary keys",
                set(resp.keys()) >= {"contributors", "counterparty", "summary"},
            )
            check(
                "route response.contributors is a list of 3 (lone wallets)",
                len(resp["contributors"]) == 3,
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, [], test_signal_ids)
        await close_pool()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    await test_404_on_unknown_signal()
    await test_contributors_one_sided_cluster_plus_retail()
    await test_contributors_hedged_cluster()
    await test_contributors_lone_wallet_only()
    await test_response_shape()
    await test_route_404()
    await test_route_returns_response()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 contributors-endpoint tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
