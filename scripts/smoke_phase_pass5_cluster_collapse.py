"""Pass 5 Tier B #1+#2+#5 -- cluster-collapse family smoke tests.

Three SQL hotspots collapse to one conceptual fix: when a sybil cluster
holds positions across multiple wallets, downstream aggregation must
treat the cluster as ONE entity.

  #1A signal_detector._aggregate_positions
       avg_portfolio_fraction is now per-ENTITY (cluster total $$ vs
       cluster max wallet PV) instead of averaged across raw wallets.
       trader_count and aggregate_usdc unchanged numerically but
       structurally attributed to identities.

  #1B counterparty.find_counterparty_wallets
       The MIN_OPPOSITE_USDC ($5k) floor and concentration threshold
       (0.75) are evaluated at the entity level. A 4-wallet cluster
       with $20k each on the opposite side counts as 1 counterparty,
       not 4. A 4-wallet cluster with $4k each ($16k entity total)
       clears the floor as one entity (was: false-negative because
       each wallet was below $5k).

  #1C exit_detector._recompute_one_signal_aggregates_for_cohort
       trader_count and aggregate_usdc both derive from an inner
       per-identity aggregate, so they stay consistent when cluster
       composition shifts.

DB-backed. Sets up traders/clusters/positions then cleans up everything.
Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_cluster_collapse.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import init_pool, close_pool  # noqa: E402
from app.services.signal_detector import _aggregate_positions  # noqa: E402
from app.services.counterparty import (  # noqa: E402
    find_counterparty_wallets,
    is_counterparty,
    MIN_OPPOSITE_USDC,
    CONCENTRATION_THRESHOLD,
)
from app.services.exit_detector import (  # noqa: E402
    _recompute_one_signal_aggregates_for_cohort,
)


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
# Setup helpers
# ---------------------------------------------------------------------------


TEST_TAG = "__pass5_cc_test__"


async def setup_fixture(conn) -> dict:
    """Pick 5 real traders + 1 open market; snapshot what's there for restore."""
    traders = await conn.fetch("SELECT proxy_wallet FROM traders LIMIT 5")
    if len(traders) < 5:
        return {}
    wallets = [t["proxy_wallet"] for t in traders]

    market = await conn.fetchrow(
        """
        SELECT condition_id FROM markets
        WHERE closed = FALSE
          AND clob_token_yes IS NOT NULL AND clob_token_yes <> ''
          AND clob_token_no  IS NOT NULL AND clob_token_no  <> ''
        LIMIT 1
        """
    )
    if not market:
        return {}

    cid = market["condition_id"]

    # Snapshot existing positions / PV rows for these wallets so we can
    # restore them after we wipe-and-replace.
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
    existing_pv = await conn.fetch(
        """
        SELECT proxy_wallet, value, fetched_at
        FROM portfolio_value_snapshots
        WHERE proxy_wallet = ANY($1::TEXT[])
        """,
        wallets,
    )
    existing_clusters = await conn.fetch(
        """
        SELECT cluster_id, proxy_wallet, joined_at
        FROM cluster_membership
        WHERE proxy_wallet = ANY($1::TEXT[])
        """,
        wallets,
    )

    # Wipe everything we'll replace.
    await conn.execute(
        """
        DELETE FROM positions
        WHERE proxy_wallet = ANY($1::TEXT[])
          AND condition_id = $2
        """,
        wallets, cid,
    )
    await conn.execute(
        "DELETE FROM portfolio_value_snapshots WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )
    await conn.execute(
        "DELETE FROM cluster_membership WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )

    return {
        "wallets": wallets,
        "cid": cid,
        "existing_positions": existing_positions,
        "existing_pv": existing_pv,
        "existing_clusters": existing_clusters,
    }


async def teardown_fixture(conn, fx: dict, test_cluster_ids: list[str]) -> None:
    wallets = fx["wallets"]
    cid = fx["cid"]
    # Wipe test data
    await conn.execute(
        """
        DELETE FROM positions
        WHERE proxy_wallet = ANY($1::TEXT[])
          AND condition_id = $2
        """,
        wallets, cid,
    )
    await conn.execute(
        "DELETE FROM portfolio_value_snapshots WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )
    await conn.execute(
        "DELETE FROM cluster_membership WHERE proxy_wallet = ANY($1::TEXT[])",
        wallets,
    )
    # Cluster CASCADE: delete the test wallet_clusters row to drop any
    # surviving membership rows.
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
    for r in fx["existing_pv"]:
        await conn.execute(
            """
            INSERT INTO portfolio_value_snapshots (proxy_wallet, value, fetched_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (proxy_wallet, fetched_at) DO NOTHING
            """,
            r["proxy_wallet"], r["value"], r["fetched_at"],
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
    """Insert wallet_clusters + cluster_membership; return cluster_id."""
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


async def insert_pv(conn, wallet: str, value: float) -> None:
    await conn.execute(
        """
        INSERT INTO portfolio_value_snapshots (proxy_wallet, value, fetched_at)
        VALUES ($1, $2, NOW())
        """,
        wallet, value,
    )


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


# ---------------------------------------------------------------------------
# Section A -- signal_detector identity-collapse (#1A)
# ---------------------------------------------------------------------------


async def test_signal_detector_one_sided_cluster() -> None:
    section("#1A signal_detector -- one-sided cluster + retail")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1A: skipped (need 5 traders + open market)", True)
                return

            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]
            retail = wallets[4]

            cluster_id = await make_cluster(conn, "pass5_test_A", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # PV: each cluster wallet $200k; retail $50k.
            for w in cluster_wallets:
                await insert_pv(conn, w, 200_000.0)
            await insert_pv(conn, retail, 50_000.0)

            # Positions: cluster $20k YES on each wallet (= $80k entity).
            #            retail $5k YES.
            for w in cluster_wallets:
                await insert_position(conn, w, cid, "Yes", 40_000.0, 20_000.0)
            await insert_position(conn, retail, cid, "Yes", 10_000.0, 5_000.0)

            rows = await _aggregate_positions(
                conn, wallets=wallets, market_category=None,
            )
            yes_row = next(
                (r for r in rows if r["condition_id"] == cid
                 and r["outcome"].lower() == "yes"),
                None,
            )
            check("#1A: YES row present", yes_row is not None)
            if yes_row is None:
                return

            # trader_count = 2 entities (cluster + retail)
            check(
                "#1A: trader_count counts cluster as 1 entity (= 2)",
                int(yes_row["trader_count"]) == 2,
                f"got {yes_row['trader_count']}",
            )
            # aggregate_usdc = $85k
            check(
                "#1A: aggregate_usdc = $85k (cluster $80k + retail $5k)",
                abs(float(yes_row["aggregate_usdc"]) - 85_000.0) < 0.01,
                f"got {yes_row['aggregate_usdc']}",
            )
            # traders_any_direction = 2 (cluster as 1, retail as 1)
            check(
                "#1A: traders_any_direction = 2 (cluster collapsed)",
                int(yes_row["traders_any_direction"]) == 2,
                f"got {yes_row['traders_any_direction']}",
            )
            # total_dollars_in_market = $85k (only YES present)
            check(
                "#1A: total_dollars_in_market = $85k",
                abs(float(yes_row["total_dollars_in_market"]) - 85_000.0) < 0.01,
                f"got {yes_row['total_dollars_in_market']}",
            )
            # avg_portfolio_fraction is per-IDENTITY:
            # cluster fraction = $80k / max(PV)=$200k = 0.40
            # retail fraction = $5k / $50k = 0.10
            # AVG over identities = (0.40 + 0.10) / 2 = 0.25
            apf = float(yes_row["avg_portfolio_fraction"])
            check(
                "#1A: avg_portfolio_fraction is per-identity (~0.25)",
                abs(apf - 0.25) < 0.01,
                f"got {apf:.4f} (pre-fix would be ~0.10 averaging 5 wallet rows)",
            )
            # contributing_wallets returns ALL 5 raw wallets (not collapsed)
            cw = list(yes_row["contributing_wallets"] or [])
            check(
                "#1A: contributing_wallets contains all 5 raw wallets",
                len(cw) == 5 and set(cw) == set(wallets),
                f"got {len(cw)} wallets: {cw}",
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_signal_detector_pure_wash_cluster() -> None:
    section("#1A signal_detector -- pure wash-trading cluster (cluster on both sides only)")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1A wash: skipped (need fixture)", True)
                return

            wallets = fx["wallets"]
            cid = fx["cid"]
            # Cluster is just 4 wallets, on BOTH sides equally.
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_wash", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            for w in cluster_wallets:
                await insert_pv(conn, w, 200_000.0)
            # Each wallet has $25k YES + $25k NO -> cluster $100k on each side.
            for w in cluster_wallets:
                await insert_position(conn, w, cid, "Yes", 50_000.0, 25_000.0)
                await insert_position(conn, w, cid, "No", 50_000.0, 25_000.0)

            rows = await _aggregate_positions(
                conn, wallets=cluster_wallets, market_category=None,
            )
            yes_row = next(
                (r for r in rows if r["condition_id"] == cid and r["outcome"].lower() == "yes"),
                None,
            )
            no_row = next(
                (r for r in rows if r["condition_id"] == cid and r["outcome"].lower() == "no"),
                None,
            )
            check("#1A wash: YES row present", yes_row is not None)
            check("#1A wash: NO row present", no_row is not None)
            if yes_row and no_row:
                check(
                    "#1A wash: trader_count_YES = 1 (cluster as 1 entity)",
                    int(yes_row["trader_count"]) == 1,
                    f"got {yes_row['trader_count']}",
                )
                check(
                    "#1A wash: trader_count_NO = 1 (cluster as 1 entity)",
                    int(no_row["trader_count"]) == 1,
                    f"got {no_row['trader_count']}",
                )
                check(
                    "#1A wash: traders_any_direction = 1 (cluster on both sides "
                    "collapses to one identity)",
                    int(yes_row["traders_any_direction"]) == 1,
                    f"got {yes_row['traders_any_direction']}",
                )
                # Below the 5-trader floor on either side -- cannot fire an
                # official signal (the audit's expected outcome).
                check(
                    "#1A wash: cluster alone falls below 5-trader signal floor",
                    int(yes_row["trader_count"]) < 5,
                )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


# ---------------------------------------------------------------------------
# Section B -- counterparty cluster-aware count (#1B)
# ---------------------------------------------------------------------------


async def test_counterparty_cluster_collapses_to_one() -> None:
    section("#1B counterparty -- 4-wallet cluster on opposite side -> count=1 not 4")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1B: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]
            retail = wallets[4]

            cluster_id = await make_cluster(conn, "pass5_test_B", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # Cluster: 4 wallets each $20k NO (= $80k entity, opposite of YES signal)
            for w in cluster_wallets:
                await insert_position(conn, w, cid, "No", 40_000.0, 20_000.0)
            # Retail wallet (NOT in cluster): $10k NO -> independent counterparty
            await insert_position(conn, retail, cid, "No", 20_000.0, 10_000.0)

            results = await find_counterparty_wallets(
                conn,
                condition_id=cid, signal_direction="YES",
                tracked_pool=wallets,
            )
            # 2 entities: cluster (1) + retail (1)
            check(
                "#1B: cluster + retail -> 2 counterparty entities",
                len(results) == 2,
                f"got {len(results)} entries: {[r['wallet'] for r in results]}",
            )
            # The cluster entity's wallets list has 4 wallets
            cluster_entity = next(
                (r for r in results if len(r["wallets"]) == 4), None,
            )
            check(
                "#1B: cluster entity has 4 wallets in `wallets` field",
                cluster_entity is not None,
            )
            if cluster_entity:
                check(
                    "#1B: cluster entity opposite_usdc = $80k (entity-summed)",
                    abs(float(cluster_entity["opposite_usdc"]) - 80_000.0) < 0.01,
                    f"got {cluster_entity['opposite_usdc']}",
                )
                check(
                    "#1B: cluster entity `wallet` field is one of the cluster wallets",
                    cluster_entity["wallet"] in cluster_wallets,
                )
            # The retail entity has 1 wallet
            retail_entity = next(
                (r for r in results if r["wallet"] == retail), None,
            )
            check(
                "#1B: retail entity present, opposite_usdc = $10k",
                retail_entity is not None
                and abs(float(retail_entity["opposite_usdc"]) - 10_000.0) < 0.01,
            )
            if retail_entity:
                check(
                    "#1B: retail entity wallets list has 1 wallet",
                    retail_entity["wallets"] == [retail],
                )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_counterparty_small_cluster_clears_floor() -> None:
    section("#1B counterparty -- cluster $4k each (=$16k entity) clears $5k floor")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1B small: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_B_small", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # 4 wallets each at $4k NO (each individually below $5k floor;
            # cluster total $16k clears it).
            for w in cluster_wallets:
                await insert_position(conn, w, cid, "No", 8_000.0, 4_000.0)

            results = await find_counterparty_wallets(
                conn, condition_id=cid, signal_direction="YES",
                tracked_pool=wallets,
            )
            # Pre-fix: 0 (each per-wallet below $5k floor -> false negative).
            # Post-fix: 1 entity at $16k.
            check(
                "#1B small: 1 counterparty entity at $16k (pre-fix would be 0)",
                len(results) == 1,
                f"got {len(results)}",
            )
            if results:
                check(
                    "#1B small: entity opposite_usdc = $16k",
                    abs(float(results[0]["opposite_usdc"]) - 16_000.0) < 0.01,
                    f"got {results[0]['opposite_usdc']}",
                )
                check(
                    "#1B small: entity wallets list has all 4",
                    len(results[0]["wallets"]) == 4,
                )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_counterparty_below_floor_at_entity_level() -> None:
    section("#1B counterparty -- cluster $1k each (=$4k entity) below floor -> count=0")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1B floor: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_B_floor", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            for w in cluster_wallets:
                await insert_position(conn, w, cid, "No", 2_000.0, 1_000.0)

            results = await find_counterparty_wallets(
                conn, condition_id=cid, signal_direction="YES",
                tracked_pool=wallets,
            )
            check(
                "#1B floor: cluster $4k entity below $5k floor -> 0 counterparty",
                len(results) == 0,
                f"got {len(results)}",
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_counterparty_lone_wallet_unchanged() -> None:
    section("#1B counterparty -- lone-wallet path unchanged from pre-fix")

    pool = await init_pool(min_size=1, max_size=2)
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1B lone: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            # No cluster created. All 5 wallets are independent identities.
            # 2 wallets on NO, 3 not positioned.
            await insert_position(conn, wallets[0], cid, "No", 20_000.0, 10_000.0)
            await insert_position(conn, wallets[1], cid, "No", 16_000.0, 8_000.0)

            results = await find_counterparty_wallets(
                conn, condition_id=cid, signal_direction="YES",
                tracked_pool=wallets,
            )
            check(
                "#1B lone: 2 lone-wallet counterparties",
                len(results) == 2,
                f"got {len(results)}",
            )
            # `wallet` field equals the actual proxy_wallet in lone case (back-compat)
            wallets_flagged = {r["wallet"] for r in results}
            check(
                "#1B lone: `wallet` field uses proxy_wallet for lone entities",
                wallets_flagged == {wallets[0], wallets[1]},
                f"got {wallets_flagged}",
            )
            for r in results:
                check(
                    f"#1B lone: lone `wallets` list has 1 entry == proxy_wallet",
                    r["wallets"] == [r["wallet"]],
                    f"wallet={r['wallet']} wallets={r['wallets']}",
                )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, [])
        await close_pool()


# ---------------------------------------------------------------------------
# Section C -- exit_detector identity-summed cohort recompute (#1C)
# ---------------------------------------------------------------------------


async def test_exit_detector_cluster_full_holds() -> None:
    section("#1C exit_detector -- cluster fully holds: trader_count=1, agg=$80k")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1C full: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_C_full", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # All 4 wallets hold $20k YES.
            for w in cluster_wallets:
                await insert_position(conn, w, cid, "Yes", 40_000.0, 20_000.0)

            tc, agg = await _recompute_one_signal_aggregates_for_cohort(
                conn,
                contributing_wallets=cluster_wallets,
                condition_id=cid,
                direction="YES",
            )
            check(
                "#1C full: trader_count = 1 (cluster as 1 identity)",
                tc == 1, f"got {tc}",
            )
            check(
                "#1C full: aggregate_usdc = $80k (identity-summed)",
                abs(agg - 80_000.0) < 0.01, f"got {agg}",
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_exit_detector_cluster_partial_dropout() -> None:
    section("#1C exit_detector -- 1 of 4 cluster wallets flat: count stays 1, agg drops")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1C partial: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_C_partial", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # 3 of 4 still hold $20k. 1 wallet has no position row at all
            # (= dropped out / sold to zero).
            for w in cluster_wallets[:3]:
                await insert_position(conn, w, cid, "Yes", 40_000.0, 20_000.0)
            # cluster_wallets[3] has no position.

            tc, agg = await _recompute_one_signal_aggregates_for_cohort(
                conn,
                contributing_wallets=cluster_wallets,
                condition_id=cid,
                direction="YES",
            )
            # COUNT(DISTINCT identity) where HAVING SUM > 0 -> the cluster
            # identity still has $60k > 0, so count stays 1.
            check(
                "#1C partial: trader_count = 1 (cluster identity still alive)",
                tc == 1, f"got {tc}",
            )
            check(
                "#1C partial: aggregate_usdc = $60k (3 of 4 wallets active)",
                abs(agg - 60_000.0) < 0.01, f"got {agg}",
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_exit_detector_cluster_full_dropout() -> None:
    section("#1C exit_detector -- entire cluster flat: count=0, agg=0")

    pool = await init_pool(min_size=1, max_size=2)
    test_cluster_ids: list[str] = []
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1C drop: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            cluster_wallets = wallets[:4]

            cluster_id = await make_cluster(conn, "pass5_test_C_drop", cluster_wallets)
            test_cluster_ids.append(cluster_id)

            # No positions at all. Cluster fully flat.
            tc, agg = await _recompute_one_signal_aggregates_for_cohort(
                conn,
                contributing_wallets=cluster_wallets,
                condition_id=cid,
                direction="YES",
            )
            check("#1C drop: trader_count = 0", tc == 0, f"got {tc}")
            check("#1C drop: aggregate_usdc = 0", abs(agg) < 0.01, f"got {agg}")
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, test_cluster_ids)
        await close_pool()


async def test_exit_detector_two_independent_traders() -> None:
    section("#1C exit_detector -- two lone wallets (no cluster) -> count=2")

    pool = await init_pool(min_size=1, max_size=2)
    fx: dict = {}
    try:
        async with pool.acquire() as conn:
            fx = await setup_fixture(conn)
            if not fx:
                check("#1C two: skipped", True)
                return
            wallets = fx["wallets"]
            cid = fx["cid"]
            await insert_position(conn, wallets[0], cid, "Yes", 40_000.0, 20_000.0)
            await insert_position(conn, wallets[1], cid, "Yes", 60_000.0, 30_000.0)

            tc, agg = await _recompute_one_signal_aggregates_for_cohort(
                conn,
                contributing_wallets=[wallets[0], wallets[1]],
                condition_id=cid,
                direction="YES",
            )
            check("#1C two: trader_count = 2 (lone wallets)", tc == 2, f"got {tc}")
            check(
                "#1C two: aggregate_usdc = $50k",
                abs(agg - 50_000.0) < 0.01, f"got {agg}",
            )
    finally:
        async with pool.acquire() as conn:
            if fx:
                await teardown_fixture(conn, fx, [])
        await close_pool()


# ---------------------------------------------------------------------------
# Code-shape regression checks (cheap; catches future drift)
# ---------------------------------------------------------------------------


def test_code_shape() -> None:
    section("Code-shape regression checks")

    import inspect
    from app.services import signal_detector as sd_mod
    from app.services import counterparty as cp_mod
    from app.services import exit_detector as ed_mod

    sd_src = inspect.getsource(sd_mod._aggregate_positions)
    check(
        "signal_detector: identity_positions CTE present",
        "identity_positions AS (" in sd_src,
    )
    check(
        "signal_detector: market_totals reads from identity_positions",
        "FROM identity_positions" in sd_src,
    )
    check(
        "signal_detector: direction_wallets CTE collects wallets from pool_positions",
        "direction_wallets AS (" in sd_src,
    )

    cp_src = inspect.getsource(cp_mod.find_counterparty_wallets)
    check(
        "counterparty: wallet_identity CTE present",
        "wallet_identity AS (" in cp_src,
    )
    check(
        "counterparty: SQL groups by identity (not raw proxy_wallet)",
        "GROUP BY wi.identity" in cp_src,
    )
    check(
        "counterparty: cluster_membership join present",
        "cluster_membership cm" in cp_src,
    )

    ed_src = inspect.getsource(ed_mod._recompute_one_signal_aggregates_for_cohort)
    check(
        "exit_detector: identity_agg CTE present",
        "identity_agg AS (" in ed_src,
    )
    check(
        "exit_detector: outer SELECT counts identity_agg rows (not raw positions)",
        "FROM identity_agg" in ed_src,
    )


# ---------------------------------------------------------------------------
# Pure-function counterparty dual-axis sanity (regression on is_counterparty)
# ---------------------------------------------------------------------------


def test_is_counterparty_pure() -> None:
    section("is_counterparty -- pure-function regression")

    # Floor + concentration both met
    check(
        "is_counterparty: $80k opp + $0 same -> True (full concentration)",
        is_counterparty(0.0, 80_000.0) is True,
    )
    # Below floor
    check(
        "is_counterparty: $4k opp + $0 same -> False (below floor)",
        is_counterparty(0.0, 4_000.0) is False,
    )
    # Above floor but mixed (concentration too low)
    check(
        "is_counterparty: $20k opp + $20k same (50% conc) -> False",
        is_counterparty(20_000.0, 20_000.0) is False,
    )
    # Right at concentration threshold (75%)
    check(
        "is_counterparty: $5k same + $15k opp (75% conc) -> True",
        is_counterparty(5_000.0, 15_000.0) is True,
    )
    # Constants are documented values
    check("is_counterparty: MIN_OPPOSITE_USDC = $5k", MIN_OPPOSITE_USDC == 5_000.0)
    check("is_counterparty: CONCENTRATION_THRESHOLD = 0.75", CONCENTRATION_THRESHOLD == 0.75)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    test_code_shape()
    test_is_counterparty_pure()
    await test_signal_detector_one_sided_cluster()
    await test_signal_detector_pure_wash_cluster()
    await test_counterparty_cluster_collapses_to_one()
    await test_counterparty_small_cluster_clears_floor()
    await test_counterparty_below_floor_at_entity_level()
    await test_counterparty_lone_wallet_unchanged()
    await test_exit_detector_cluster_full_holds()
    await test_exit_detector_cluster_partial_dropout()
    await test_exit_detector_cluster_full_dropout()
    await test_exit_detector_two_independent_traders()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 Tier B cluster-collapse tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
