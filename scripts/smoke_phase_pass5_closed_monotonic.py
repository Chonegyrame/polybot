"""Pass 5 Tier C #14 -- markets.closed and events.closed monotonic.

Pre-fix: `upsert_market`/`upsert_event` did `closed = EXCLUDED.closed`
on conflict. A transient gamma response with `closed=false` (stale
cache during a reorg, brief blip while resolving disputes) could flip
a closed=true row back to false. signal_detector filters
`WHERE m.closed = FALSE`, so the flip would re-admit a resolved market
into the live signal pool until the next sync corrected it. F18
acknowledged this risk but didn't address it.

Post-fix: `closed = (markets.closed OR EXCLUDED.closed)` /
`closed = (events.closed OR EXCLUDED.closed)`. Once true, stays true.

The reverse-flip risk (gamma incorrectly flags a still-live market as
closed=true) is the rarer failure mode -- the audit explicitly accepts
it. Manual recovery is one SQL:
  UPDATE markets SET closed = FALSE WHERE condition_id = '...';
  UPDATE events  SET closed = FALSE WHERE id           = '...';

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_closed_monotonic.py
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from datetime import datetime, timezone, timedelta
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
# Code-shape regression
# ---------------------------------------------------------------------------


def test_code_shape() -> None:
    section("#14 code-shape -- ON CONFLICT uses OR-merge for closed")

    src = inspect.getsource(crud.upsert_market)
    check(
        "upsert_market: closed = (markets.closed OR EXCLUDED.closed)",
        "(markets.closed OR EXCLUDED.closed)" in src,
    )
    check(
        "upsert_market: no longer uses bare `EXCLUDED.closed` for closed",
        "closed           = EXCLUDED.closed" not in src
        and "closed = EXCLUDED.closed" not in src.split("(markets.closed")[0],
    )

    src_e = inspect.getsource(crud.upsert_event)
    check(
        "upsert_event: closed = (events.closed OR EXCLUDED.closed)",
        "(events.closed OR EXCLUDED.closed)" in src_e,
    )


# ---------------------------------------------------------------------------
# DB integration: insert + flip-back attempt
# ---------------------------------------------------------------------------


# Use a unique synthetic condition_id and event_id so cleanup is exact
# and we can't collide with real data.
TEST_CID = "0xpass5_14_test_market_condition_id_aaaaaaaaaaaaaaaaaaaaaaaa"
TEST_EVENT_ID = "__pass5_14_test_event__"


async def cleanup(conn) -> None:
    # markets has FK to events.id. Delete in reverse order.
    await conn.execute(
        "DELETE FROM markets WHERE condition_id = $1", TEST_CID,
    )
    await conn.execute(
        "DELETE FROM events WHERE id = $1", TEST_EVENT_ID,
    )


async def test_market_closed_monotonic() -> None:
    section("#14 markets.closed: closed=true survives gamma blip with closed=false")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await cleanup(conn)
            try:
                end = datetime.now(timezone.utc) + timedelta(days=30)

                # Initial upsert: closed=true (e.g. resolved market discovered)
                await crud.upsert_market(
                    conn,
                    condition_id=TEST_CID,
                    gamma_id="g_pass5_14",
                    event_id=None,
                    slug="pass5-14-test",
                    question="pass5 14 test market",
                    clob_token_yes="tok_y", clob_token_no="tok_n",
                    outcomes=["Yes", "No"],
                    end_date=end, closed=True,
                    resolved_outcome="YES",
                )
                row = await conn.fetchrow(
                    "SELECT closed, resolved_outcome FROM markets WHERE condition_id = $1",
                    TEST_CID,
                )
                check(
                    "#14 market: initial upsert sets closed=TRUE",
                    row is not None and row["closed"] is True,
                    f"got closed={row['closed'] if row else None}",
                )

                # Gamma blip: same row, closed=false (stale cache)
                await crud.upsert_market(
                    conn,
                    condition_id=TEST_CID,
                    gamma_id="g_pass5_14",
                    event_id=None,
                    slug="pass5-14-test",
                    question="pass5 14 test market",
                    clob_token_yes="tok_y", clob_token_no="tok_n",
                    outcomes=["Yes", "No"],
                    end_date=end, closed=False,
                    resolved_outcome=None,
                )
                row = await conn.fetchrow(
                    "SELECT closed, resolved_outcome FROM markets WHERE condition_id = $1",
                    TEST_CID,
                )
                check(
                    "#14 market: re-upsert with closed=FALSE does NOT flip back",
                    row is not None and row["closed"] is True,
                    f"got closed={row['closed'] if row else None}",
                )
                # F11 / pre-Pass-5 invariant: resolved_outcome stays via
                # COALESCE (existing fix). Sanity check.
                check(
                    "#14 market: resolved_outcome survived (existing COALESCE)",
                    row is not None and row["resolved_outcome"] == "YES",
                    f"got resolved_outcome={row['resolved_outcome'] if row else None}",
                )
            finally:
                await cleanup(conn)
    finally:
        await close_pool()


async def test_market_closed_can_still_flip_to_true() -> None:
    section("#14 markets.closed: false -> true still works (forward direction)")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await cleanup(conn)
            try:
                end = datetime.now(timezone.utc) + timedelta(days=30)
                # Initial upsert: closed=false (open market)
                await crud.upsert_market(
                    conn,
                    condition_id=TEST_CID,
                    gamma_id="g_pass5_14",
                    event_id=None,
                    slug="pass5-14-test",
                    question="pass5 14 test market",
                    clob_token_yes="tok_y", clob_token_no="tok_n",
                    outcomes=["Yes", "No"],
                    end_date=end, closed=False,
                    resolved_outcome=None,
                )
                row = await conn.fetchrow(
                    "SELECT closed FROM markets WHERE condition_id = $1", TEST_CID,
                )
                check(
                    "#14 market: initial closed=FALSE",
                    row is not None and row["closed"] is False,
                )

                # Market resolves: closed=true (legitimate transition)
                await crud.upsert_market(
                    conn,
                    condition_id=TEST_CID,
                    gamma_id="g_pass5_14",
                    event_id=None,
                    slug="pass5-14-test",
                    question="pass5 14 test market",
                    clob_token_yes="tok_y", clob_token_no="tok_n",
                    outcomes=["Yes", "No"],
                    end_date=end, closed=True,
                    resolved_outcome="NO",
                )
                row = await conn.fetchrow(
                    "SELECT closed, resolved_outcome FROM markets WHERE condition_id = $1",
                    TEST_CID,
                )
                check(
                    "#14 market: false -> true flip succeeds (forward only)",
                    row is not None and row["closed"] is True,
                )
                check(
                    "#14 market: resolved_outcome populated on the legitimate close",
                    row is not None and row["resolved_outcome"] == "NO",
                )
            finally:
                await cleanup(conn)
    finally:
        await close_pool()


async def test_event_closed_monotonic() -> None:
    section("#14 events.closed: closed=true survives gamma blip with closed=false")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await cleanup(conn)
            try:
                end = datetime.now(timezone.utc) + timedelta(days=30)
                # Initial: closed=true
                await crud.upsert_event(
                    conn,
                    event_id=TEST_EVENT_ID,
                    slug="pass5-14-event",
                    title="pass5 14 test event",
                    category="overall",
                    tags=None,
                    end_date=end,
                    closed=True,
                )
                row = await conn.fetchrow(
                    "SELECT closed FROM events WHERE id = $1", TEST_EVENT_ID,
                )
                check(
                    "#14 event: initial upsert sets closed=TRUE",
                    row is not None and row["closed"] is True,
                )

                # Blip: closed=false
                await crud.upsert_event(
                    conn,
                    event_id=TEST_EVENT_ID,
                    slug="pass5-14-event",
                    title="pass5 14 test event",
                    category="overall",
                    tags=None,
                    end_date=end,
                    closed=False,
                )
                row = await conn.fetchrow(
                    "SELECT closed FROM events WHERE id = $1", TEST_EVENT_ID,
                )
                check(
                    "#14 event: re-upsert with closed=FALSE does NOT flip back",
                    row is not None and row["closed"] is True,
                    f"got closed={row['closed'] if row else None}",
                )
            finally:
                await cleanup(conn)
    finally:
        await close_pool()


async def test_event_closed_can_still_flip_to_true() -> None:
    section("#14 events.closed: false -> true still works")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await cleanup(conn)
            try:
                end = datetime.now(timezone.utc) + timedelta(days=30)
                await crud.upsert_event(
                    conn, event_id=TEST_EVENT_ID,
                    slug="pass5-14-event", title="pass5 14 test event",
                    category="overall", tags=None,
                    end_date=end, closed=False,
                )
                row = await conn.fetchrow(
                    "SELECT closed FROM events WHERE id = $1", TEST_EVENT_ID,
                )
                check(
                    "#14 event: initial closed=FALSE",
                    row is not None and row["closed"] is False,
                )

                await crud.upsert_event(
                    conn, event_id=TEST_EVENT_ID,
                    slug="pass5-14-event", title="pass5 14 test event",
                    category="overall", tags=None,
                    end_date=end, closed=True,
                )
                row = await conn.fetchrow(
                    "SELECT closed FROM events WHERE id = $1", TEST_EVENT_ID,
                )
                check(
                    "#14 event: false -> true flip succeeds",
                    row is not None and row["closed"] is True,
                )
            finally:
                await cleanup(conn)
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    test_code_shape()
    await test_market_closed_monotonic()
    await test_market_closed_can_still_flip_to_true()
    await test_event_closed_monotonic()
    await test_event_closed_can_still_flip_to_true()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 #14 closed-monotonic tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
