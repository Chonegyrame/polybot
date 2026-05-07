"""Pass 5 Tier E #18 -- iter_trades paginator-mode flag.

Pre-fix: `iter_trades` called `get_trades` which used the silent
`_safe_list_or_empty` wrapper. A malformed mid-pagination response
(e.g. data-api returning a dict instead of a list because of an upstream
hiccup) silently returned [], iter_trades saw an empty page, and
ended iteration as if the wallet had no more trades. Downstream backtests
treated the partial dataset as complete and computed wrong P&L.

Post-fix: `get_trades` gains a `_paginator_mode: bool = False` kwarg.
When True, it uses `_safe_list_from_response` which raises
`ResponseShapeError` on a malformed payload. `iter_trades` passes True
and lets the exception propagate so the caller knows the dataset is
incomplete instead of silently mistaking a half-truncated stream for a
clean exhaustion.

The default (`_paginator_mode=False`) preserves back-compat: one-shot
callers continue to silently return [] on shape errors, which is the
right behavior for callers that can tolerate partial failure.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_iter_trades.py
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.polymarket import (  # noqa: E402
    PolymarketClient,
    ResponseShapeError,
    _safe_list_from_response,
    _safe_list_or_empty,
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
# Code-shape regression
# ---------------------------------------------------------------------------


def test_code_shape() -> None:
    section("#18 code-shape -- _paginator_mode kwarg + fail-loud path")

    sig = inspect.signature(PolymarketClient.get_trades)
    check(
        "#18: get_trades has _paginator_mode parameter",
        "_paginator_mode" in sig.parameters,
    )
    check(
        "#18: _paginator_mode defaults to False (back-compat)",
        sig.parameters["_paginator_mode"].default is False,
    )

    src = inspect.getsource(PolymarketClient.get_trades)
    check(
        "#18: get_trades uses _safe_list_from_response when _paginator_mode True",
        "_safe_list_from_response" in src,
    )
    check(
        "#18: get_trades still uses _safe_list_or_empty in default path",
        "_safe_list_or_empty" in src,
    )

    iter_src = inspect.getsource(PolymarketClient.iter_trades)
    check(
        "#18: iter_trades passes _paginator_mode=True",
        "_paginator_mode=True" in iter_src,
    )
    check(
        "#18: iter_trades catches ResponseShapeError and re-raises",
        "ResponseShapeError" in iter_src and "raise" in iter_src,
    )


# ---------------------------------------------------------------------------
# Behavioral: pure-function predicate verification
# ---------------------------------------------------------------------------


def test_safe_list_helpers_baseline() -> None:
    section("#18 _safe_list_or_empty vs _safe_list_from_response divergence")

    bad = {"error": "rate limited"}  # no list, no expected wrapper key

    # Silent path: returns [] (current default for get_trades)
    res = _safe_list_or_empty(bad, "test")
    check(
        "#18: _safe_list_or_empty returns [] on dict-without-wrapper",
        res == [],
    )

    # Loud path: raises (paginator-mode for iter_trades)
    raised = False
    try:
        _safe_list_from_response(bad, "test")
    except ResponseShapeError:
        raised = True
    check(
        "#18: _safe_list_from_response raises ResponseShapeError on same input",
        raised,
    )


# ---------------------------------------------------------------------------
# get_trades behavior with monkey-patched _get_json
# ---------------------------------------------------------------------------


async def _build_client() -> PolymarketClient:
    """Build a PolymarketClient without entering the async context manager.
    We monkey-patch _get_json so the HTTP layer is never touched.
    """
    return PolymarketClient.__new__(PolymarketClient)


async def test_get_trades_paginator_mode_raises() -> None:
    section("#18 get_trades(_paginator_mode=True) raises on malformed payload")

    client = await _build_client()

    async def fake_get_json_dict(*args, **kwargs):
        # Return a dict with no expected wrapper key -- shape error.
        return {"error": "rate limited"}

    with patch.object(PolymarketClient, "_get_json", new=fake_get_json_dict):
        # Default path: silent [].
        result = await client.get_trades("0xtest")
        check(
            "#18: default get_trades silently returns [] on shape error",
            result == [],
            f"got {result}",
        )

        # Paginator-mode: raises.
        raised = False
        try:
            await client.get_trades("0xtest", _paginator_mode=True)
        except ResponseShapeError:
            raised = True
        check(
            "#18: get_trades(_paginator_mode=True) raises on shape error",
            raised,
        )


async def test_get_trades_paginator_mode_passes_through_valid() -> None:
    section("#18 get_trades(_paginator_mode=True) returns trades on valid payload")

    client = await _build_client()

    async def fake_get_json_list(*args, **kwargs):
        return [
            {
                "proxyWallet": "0xtest",
                "conditionId": "0xcid_p5_18",
                "asset": "asset",
                "outcome": "Yes",
                "side": "BUY",
                "price": 0.4,
                "size": 100.0,
                "transactionHash": "0xhash_test_1",
                "timestamp": 1700000000,
            },
        ]

    with patch.object(PolymarketClient, "_get_json", new=fake_get_json_list):
        result = await client.get_trades("0xtest", _paginator_mode=True)
        check(
            "#18: paginator-mode + list payload -> trades parsed",
            len(result) == 1 and result[0].condition_id == "0xcid_p5_18",
            f"got {[r.condition_id for r in result]}",
        )


# ---------------------------------------------------------------------------
# iter_trades end-to-end: shape error mid-pagination propagates
# ---------------------------------------------------------------------------


async def test_iter_trades_propagates_shape_error_on_page2() -> None:
    section("#18 iter_trades: shape error on page 2 propagates (no silent truncation)")

    client = await _build_client()

    page_size = 3
    page1 = [
        {
            "proxyWallet": "0xtest",
            "conditionId": f"0xcid_p1_{i}",
            "asset": "asset",
            "outcome": "Yes",
            "side": "BUY",
            "price": 0.4,
            "size": 100.0,
            "transactionHash": f"0xhash_p1_{i}",
            "timestamp": 1700000000 + i,
        }
        for i in range(page_size)
    ]
    bad_page2 = {"error": "rate limited"}

    call_count = {"n": 0}

    async def fake_get_json(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return page1
        return bad_page2  # malformed on page 2

    with patch.object(PolymarketClient, "_get_json", new=fake_get_json):
        seen: list[str] = []
        raised = False
        try:
            async for t in client.iter_trades("0xtest", page_size=page_size):
                seen.append(t.condition_id)
        except ResponseShapeError:
            raised = True

        check(
            "#18: iter_trades raised ResponseShapeError on page 2",
            raised,
        )
        check(
            "#18: page 1 trades were yielded before the failure",
            len(seen) == page_size,
            f"got {len(seen)} trades from page 1",
        )
        # Pre-fix behavior: would have silently returned without error.
        # The key assertion is that the caller now KNOWS the iteration
        # was aborted instead of mistaking partial data for complete.


async def test_iter_trades_clean_exhaustion_unaffected() -> None:
    section("#18 iter_trades: clean exhaustion path unchanged")

    client = await _build_client()

    page_size = 3

    async def fake_get_json(*args, **kwargs):
        # Just one full page, then an empty list (legitimate end-of-iteration).
        offset = kwargs.get("params", {}).get("offset", 0) if kwargs else 0
        if offset == 0:
            return [
                {
                    "proxyWallet": "0xtest",
                    "conditionId": f"0xcid_clean_{i}",
                    "asset": "asset",
                    "outcome": "Yes",
                    "side": "BUY",
                    "price": 0.4,
                    "size": 100.0,
                    "transactionHash": f"0xhash_clean_{i}",
                    "timestamp": 1700000000 + i,
                }
                for i in range(page_size)
            ]
        return []  # empty page -> end of iteration (legitimate)

    # The `_get_json` call extracts `params` from kwargs differently in
    # production. To make the test robust, just track call number.
    call_count = {"n": 0}

    async def fake_get_json_seq(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [
                {
                    "proxyWallet": "0xtest",
                    "conditionId": f"0xcid_clean_{i}",
                    "asset": "asset",
                    "outcome": "Yes",
                    "side": "BUY",
                    "price": 0.4,
                    "size": 100.0,
                    "transactionHash": f"0xhash_clean_{i}",
                    "timestamp": 1700000000 + i,
                }
                for i in range(page_size)
            ]
        return []  # empty page = clean exhaustion

    with patch.object(PolymarketClient, "_get_json", new=fake_get_json_seq):
        seen: list[str] = []
        async for t in client.iter_trades("0xtest", page_size=page_size):
            seen.append(t.condition_id)
        check(
            "#18: clean exhaustion yields all trades from page 1",
            len(seen) == page_size,
            f"got {len(seen)}",
        )


async def test_iter_trades_partial_last_page() -> None:
    section("#18 iter_trades: partial last page terminates cleanly")

    client = await _build_client()

    page_size = 3
    call_count = {"n": 0}

    async def fake_get_json(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [
                {
                    "proxyWallet": "0xtest", "conditionId": f"0xcid_partial_{i}",
                    "asset": "asset", "outcome": "Yes", "side": "BUY",
                    "price": 0.4, "size": 100.0,
                    "transactionHash": f"0xhash_partial_{i}",
                    "timestamp": 1700000000 + i,
                }
                for i in range(page_size)
            ]
        # Page 2: only 1 trade -> partial -> terminate after yielding it.
        return [
            {
                "proxyWallet": "0xtest", "conditionId": "0xcid_partial_last",
                "asset": "asset", "outcome": "Yes", "side": "BUY",
                "price": 0.4, "size": 100.0,
                "transactionHash": "0xhash_partial_last",
                "timestamp": 1700000099,
            },
        ]

    with patch.object(PolymarketClient, "_get_json", new=fake_get_json):
        seen: list[str] = []
        async for t in client.iter_trades("0xtest", page_size=page_size):
            seen.append(t.condition_id)
        check(
            "#18: partial last page yields all trades and exits cleanly",
            len(seen) == page_size + 1,
            f"got {len(seen)}",
        )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    test_code_shape()
    test_safe_list_helpers_baseline()
    await test_get_trades_paginator_mode_raises()
    await test_get_trades_paginator_mode_passes_through_valid()
    await test_iter_trades_propagates_shape_error_on_page2()
    await test_iter_trades_clean_exhaustion_unaffected()
    await test_iter_trades_partial_last_page()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 #18 iter_trades fail-loud tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
