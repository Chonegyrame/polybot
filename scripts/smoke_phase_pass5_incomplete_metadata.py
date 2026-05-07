"""Pass 5 Tier C #17 -- zombie filter incomplete-metadata predicate.

Pass 4 shipped 4 zombie-drop predicates (redeemable, market_closed,
dust_size, resolved_price_past) at the API boundary in
PolymarketClient.get_positions. The audit found a residual fall-open
path: positions where Polymarket has stopped maintaining metadata
entirely -- no `redeemable`, no `closed`, no `curPrice` -- AND the
endDate is in the past. Each of the 4 base predicates fails open
because each individual signal is *missing* rather than affirmatively
resolved. The position then gets persisted into the live tables.

Pass 5 #17 adds a 5th predicate as a residual sweep: when ALL four
metadata signals are missing AND end_date is in the past, drop with
reason 'incomplete_metadata_resolved'. The conjunction prevents
false-positives on live markets where the API briefly returned partial
metadata. Reads `redeemable` and `closed` from the raw dict (not the
dataclass field) so we can distinguish "API didn't send" from
"explicitly returned False."

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_incomplete_metadata.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.polymarket_types import Position  # noqa: E402
from app.services import health_counters  # noqa: E402
from app.services.polymarket import _ZOMBIE_DROP_COUNTERS  # noqa: E402


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
# Code-shape regressions
# ---------------------------------------------------------------------------


def test_code_shape() -> None:
    section("Code-shape -- counter, predicate, counter-map, /system/status")

    check(
        "#17: ZOMBIE_DROP_INCOMPLETE_METADATA constant present",
        hasattr(health_counters, "ZOMBIE_DROP_INCOMPLETE_METADATA"),
    )
    check(
        "#17: snapshot() returns the new counter key",
        health_counters.ZOMBIE_DROP_INCOMPLETE_METADATA in health_counters.snapshot(),
    )

    src = inspect.getsource(Position.drop_reason)
    check(
        "#17: drop_reason returns 'incomplete_metadata_resolved'",
        "'incomplete_metadata_resolved'" in src
        or '"incomplete_metadata_resolved"' in src,
    )
    # Predicate reads raw dict for 'redeemable' and 'closed' (so it can
    # distinguish missing-from-API vs explicitly-False).
    check(
        "#17: predicate reads raw.get('redeemable') (not coerced field)",
        "raw.get(\"redeemable\")" in src or "raw.get('redeemable')" in src,
    )
    check(
        "#17: predicate reads raw.get('closed')",
        "raw.get(\"closed\")" in src or "raw.get('closed')" in src,
    )
    check(
        "#17: predicate uses _end_date_in_past()",
        "_end_date_in_past()" in src,
    )

    check(
        "#17: counter map includes 'incomplete_metadata_resolved'",
        "incomplete_metadata_resolved" in _ZOMBIE_DROP_COUNTERS,
    )
    check(
        "#17: counter map points to the new counter constant",
        _ZOMBIE_DROP_COUNTERS.get("incomplete_metadata_resolved")
        == health_counters.ZOMBIE_DROP_INCOMPLETE_METADATA,
    )

    # /system/status surfaces incomplete_metadata under zombie_drops_last_24h
    sys_src = (ROOT / "app" / "api" / "routes" / "system.py").read_text(
        encoding="utf-8",
    )
    check(
        "#17: /system/status zombie_drops_last_24h surfaces 'incomplete_metadata'",
        '"incomplete_metadata"' in sys_src,
    )
    check(
        "#17: /system/status total includes the new counter",
        "zombie_drop_incomplete_metadata" in sys_src,
    )


# ---------------------------------------------------------------------------
# Behavioral tests
# ---------------------------------------------------------------------------


def _bare(**overrides):
    """Minimal /positions row with optional fields explicitly absent.

    This builder OMITS curPrice, redeemable, and closed unless overridden,
    simulating the Polymarket "stale-metadata" response shape: the keys
    are simply not in the dict.
    """
    base = {
        "proxyWallet": "0xabc",
        "conditionId": "0xcid_test_17",
        "asset": "asset_test_17",
        "outcome": "Yes",
        "size": 100.0,
        "avgPrice": 0.45,
        "currentValue": 55.0,
    }
    base.update(overrides)
    return base


PAST_DATE = "2020-01-01T00:00:00Z"
FUTURE_DATE = "2099-01-01T00:00:00Z"


def test_predicate_drops_full_blank_past_endDate() -> None:
    section("#17 drops: all metadata blank + past endDate")

    p = Position.from_dict(_bare(endDate=PAST_DATE))
    # raw has no redeemable, no closed, no curPrice; size=100; endDate is past.
    check(
        "#17: blank metadata + past endDate -> 'incomplete_metadata_resolved'",
        p.drop_reason() == "incomplete_metadata_resolved",
        f"got {p.drop_reason()!r}",
    )


def test_predicate_keeps_full_blank_future_endDate() -> None:
    section("#17 keeps: all metadata blank but future endDate (fail-open)")

    p = Position.from_dict(_bare(endDate=FUTURE_DATE))
    check(
        "#17: blank metadata + FUTURE endDate -> None (live market, kept)",
        p.drop_reason() is None,
        f"got {p.drop_reason()!r}",
    )


def test_predicate_keeps_full_blank_no_endDate() -> None:
    section("#17 keeps: all metadata blank + endDate missing (fail-open)")

    p = Position.from_dict(_bare())  # no endDate at all
    check(
        "#17: blank metadata + missing endDate -> None (kept; can't prove resolved)",
        p.drop_reason() is None,
    )


def test_predicate_keeps_when_curPrice_present() -> None:
    section("#17 keeps: curPrice present (live signal -> not residual sweep)")

    p = Position.from_dict(_bare(curPrice=0.5, endDate=PAST_DATE))
    # curPrice 0.5 isn't in RESOLVED_PRICES so resolved_price_past doesn't
    # match either. Other 3 predicates miss too (redeemable absent, closed
    # absent, size > 1). The new predicate requires curPrice IS None,
    # so this position is kept.
    check(
        "#17: curPrice=0.5 + past endDate (other signals blank) -> None",
        p.drop_reason() is None,
        f"got {p.drop_reason()!r}",
    )


def test_predicate_keeps_when_redeemable_explicitly_False() -> None:
    section("#17 keeps: redeemable explicitly False (API affirms not resolved)")

    p = Position.from_dict(_bare(redeemable=False, endDate=PAST_DATE))
    # `redeemable: False` in the raw dict is not None -- API explicitly
    # said "not redeemable yet". The predicate's first conjunct fails.
    # Resolved_price_past also doesn't fire (no curPrice).
    check(
        "#17: raw.redeemable=False + past endDate -> None (API affirmed)",
        p.drop_reason() is None,
        f"got {p.drop_reason()!r}",
    )


def test_predicate_keeps_when_closed_explicitly_False() -> None:
    section("#17 keeps: closed explicitly False (API affirms still trading)")

    p = Position.from_dict(_bare(closed=False, endDate=PAST_DATE))
    check(
        "#17: raw.closed=False + past endDate -> None (API affirmed)",
        p.drop_reason() is None,
        f"got {p.drop_reason()!r}",
    )


def test_priority_existing_predicates_still_win() -> None:
    section("#17 priority: existing predicates still win over the new one")

    # If `closed=True` is set, market_closed still wins (priority order).
    p = Position.from_dict(_bare(closed=True, endDate=PAST_DATE))
    check(
        "#17: closed=True + past endDate -> 'market_closed' (priority over new path)",
        p.drop_reason() == "market_closed",
    )

    # redeemable=True still wins.
    p = Position.from_dict(_bare(redeemable=True, endDate=PAST_DATE))
    check(
        "#17: redeemable=True + past endDate -> 'redeemable' (priority)",
        p.drop_reason() == "redeemable",
    )

    # dust_size still wins when size <= 1.
    p = Position.from_dict(_bare(size=0.5, endDate=PAST_DATE))
    check(
        "#17: size=0.5 + past endDate -> 'dust_size' (priority)",
        p.drop_reason() == "dust_size",
    )


def test_real_world_stale_metadata_shape() -> None:
    section("#17 realistic shape: position dict with absolute minimum fields")

    # Real Polymarket positions sometimes return bare-bones rows like:
    #   {proxyWallet, conditionId, asset, outcome, size, avgPrice, endDate}
    # No closed, no redeemable, no curPrice. End-date in the past.
    real_shape = {
        "proxyWallet": "0xreal",
        "conditionId": "0xreal_cid_zombie_remnant",
        "asset": "asset_remnant",
        "outcome": "No",
        "size": 12.0,
        "avgPrice": 0.30,
        "currentValue": None,
        "endDate": PAST_DATE,
    }
    p = Position.from_dict(real_shape)
    check(
        "#17: real-world stale-metadata shape -> dropped via new predicate",
        p.drop_reason() == "incomplete_metadata_resolved",
        f"got {p.drop_reason()!r}",
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


test_code_shape()
test_predicate_drops_full_blank_past_endDate()
test_predicate_keeps_full_blank_future_endDate()
test_predicate_keeps_full_blank_no_endDate()
test_predicate_keeps_when_curPrice_present()
test_predicate_keeps_when_redeemable_explicitly_False()
test_predicate_keeps_when_closed_explicitly_False()
test_priority_existing_predicates_still_win()
test_real_world_stale_metadata_shape()


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 #17 incomplete-metadata predicate tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
