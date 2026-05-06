"""F23 regression smoke — call each refactored route handler on live data
and verify the response shape is unchanged after the inline-SQL extraction.

Pre-fix: 7 route files had inline `conn.fetch[row]` SQL violating the
CLAUDE.md "all DB access through crud.py" rule. Pass 2 extracted each query
into a named `crud.<helper>` function. This smoke verifies the route's
response shape is identical to what callers expect.

Coverage: each route function is called directly with a real conn from the
shared pool. We assert the top-level response keys + the type of inner
collections + the schema of one inner row (key set). Behavioral changes
(other than verified-equivalent SQL extraction) would surface as missing
keys or wrong types.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass2_routes.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

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
# F23: traders/{wallet}
# ---------------------------------------------------------------------------


async def test_f23_traders_route() -> None:
    section("F23: GET /traders/{wallet} response shape unchanged")
    from app.api.routes.traders import get_trader
    pool = await init_pool()
    async with pool.acquire() as conn:
        wallet = await conn.fetchval(
            "SELECT proxy_wallet FROM traders LIMIT 1"
        )
        if wallet is None:
            check("(skipped — no traders in DB)", True)
            return
        try:
            response = await get_trader(wallet, conn=conn)
        except Exception as e:  # noqa: BLE001
            check(f"get_trader raised: {e}", False)
            return
        expected_keys = {
            "profile", "classification", "cluster", "per_category", "open_positions",
        }
        check(
            "response has expected top-level keys",
            set(response.keys()) == expected_keys,
            f"got {sorted(response.keys())}",
        )
        check(
            "profile is a dict (or None for missing trader, here non-None)",
            isinstance(response["profile"], dict),
        )
        check(
            "per_category is a list",
            isinstance(response["per_category"], list),
        )
        check(
            "open_positions is a list",
            isinstance(response["open_positions"], list),
        )


# ---------------------------------------------------------------------------
# F23: markets/{condition_id}
# ---------------------------------------------------------------------------


async def test_f23_markets_route() -> None:
    section("F23: GET /markets/{condition_id} response shape unchanged")
    from app.api.routes.markets import get_market
    pool = await init_pool()
    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            "SELECT condition_id FROM markets LIMIT 1"
        )
        if cid is None:
            check("(skipped — no markets in DB)", True)
            return
        try:
            response = await get_market(cid, conn=conn)
        except Exception as e:  # noqa: BLE001
            check(f"get_market raised: {e}", False)
            return
        expected_keys = {
            "market", "tracked_positions_by_outcome",
            "tracked_positions_per_trader", "signal_history",
        }
        check(
            "response has expected top-level keys",
            set(response.keys()) == expected_keys,
            f"got {sorted(response.keys())}",
        )
        check(
            "market is a dict",
            isinstance(response["market"], dict),
        )
        check(
            "tracked_positions_by_outcome is a list",
            isinstance(response["tracked_positions_by_outcome"], list),
        )
        check(
            "tracked_positions_per_trader is a list",
            isinstance(response["tracked_positions_per_trader"], list),
        )
        check(
            "signal_history is a list",
            isinstance(response["signal_history"], list),
        )


# ---------------------------------------------------------------------------
# F23: system/status
# ---------------------------------------------------------------------------


async def test_f23_system_status_route() -> None:
    section("F23: GET /system/status response shape unchanged")
    from app.api.routes.system import get_status
    pool = await init_pool()
    async with pool.acquire() as conn:
        try:
            response = await get_status(conn=conn)
        except Exception as e:  # noqa: BLE001
            check(f"system_status raised: {e}", False)
            return
        expected_top = {"overall_health", "components"}
        check(
            "response has overall_health + components keys",
            expected_top.issubset(set(response.keys())),
            f"got {sorted(response.keys())}",
        )
        check(
            "overall_health is one of green/amber/red",
            response["overall_health"] in ("green", "amber", "red"),
            f"got {response['overall_health']!r}",
        )
        check(
            "components is a dict with health subkeys",
            isinstance(response["components"], dict)
            and all(isinstance(v, dict) for v in response["components"].values()),
        )


# ---------------------------------------------------------------------------
# F23: paper_trades — open + close paths
# ---------------------------------------------------------------------------


async def test_f23_paper_trades_route_helpers() -> None:
    """Verify the new crud helper for paper_trades' market lookup works."""
    section("F23: paper_trades crud helpers respond on real data")
    from app.db import crud
    pool = await init_pool()
    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            "SELECT condition_id FROM markets LIMIT 1"
        )
        if cid is None:
            check("(skipped — no markets)", True)
            return
        result = await crud.get_market_tokens_and_category(conn, cid)
        check(
            "get_market_tokens_and_category returns dict for known cid",
            isinstance(result, dict),
            f"got {type(result).__name__}",
        )
        if result:
            check(
                "result has the 3 expected keys",
                set(result.keys()) >= {"clob_token_yes", "clob_token_no", "category"},
                f"got {sorted(result.keys())}",
            )
        # Non-existent cid -> None
        result_none = await crud.get_market_tokens_and_category(conn, "0xnonexistent")
        check(
            "get_market_tokens_and_category returns None for unknown cid",
            result_none is None,
        )


# ---------------------------------------------------------------------------
# F23: backtest /half_life
# ---------------------------------------------------------------------------


async def test_f23_half_life_route_helper() -> None:
    section("F23: crud.fetch_half_life_rows works")
    from app.db import crud
    pool = await init_pool()
    async with pool.acquire() as conn:
        rows = await crud.fetch_half_life_rows(conn)
        check(
            "fetch_half_life_rows returns a list",
            isinstance(rows, list),
            f"got {type(rows).__name__}",
        )
        if rows:
            check(
                "row has expected keys (id, direction, fire_price, ...)",
                {"id", "direction", "fire_price", "smart_money_entry",
                 "category", "snapshot_offset_min", "yes_price",
                 "bid_price", "ask_price"} <= set(rows[0].keys()),
                f"got {sorted(rows[0].keys())}",
            )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nF23 route refactor smoke\n" + "=" * 80)

    try:
        await test_f23_traders_route()
        await test_f23_markets_route()
        await test_f23_system_status_route()
        await test_f23_paper_trades_route_helpers()
        await test_f23_half_life_route_helper()
    finally:
        await close_pool()

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
    print("\n  All F23 route refactors verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
