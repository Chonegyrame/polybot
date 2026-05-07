"""Phase 2 smoke tests for Pass 3 foundation helpers.

Pure-function tests for:
  - app/services/fees.py — Polymarket taker fee math (D1)
  - app/services/backtest_engine.py:compute_kish_n_eff (D3)
  - app/services/polymarket.py:ResponseShapeError + _safe_list_from_response (R15)

No DB access. No live API. Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass3_helpers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.fees import (  # noqa: E402
    DEFAULT_FEE_RATE,
    TAKER_FEE_RATES,
    compute_taker_fee_per_dollar,
    compute_taker_fee_usdc,
)
from app.services.backtest_engine import compute_kish_n_eff  # noqa: E402
from app.services.polymarket import (  # noqa: E402
    PolymarketClient,
    ResponseShapeError,
    _safe_list_from_response,
    _safe_list_or_empty,
)
from app.services import health_counters  # noqa: E402
from app.services.polymarket_types import (  # noqa: E402
    DUST_SIZE_THRESHOLD,
    Position,
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
# fees.py — Polymarket taker fee math
# ---------------------------------------------------------------------------

section("fees.compute_taker_fee_usdc — formula validation")

# Polymarket formula: fee = stake × rate × (1 - price)
# Verify against the reference table in their docs:
#   100 shares @ $0.40 in Politics (rate=0.04) = $0.96
#   That's $40 trade value × 0.04 × 0.60 = $0.96 ✓

fee = compute_taker_fee_usdc(40.0, 0.40, "Politics")
check("Politics $40 stake @ $0.40 = $0.96", abs(fee - 0.96) < 0.0001, f"got {fee:.4f}")

# Symmetry: $0.40 and $0.60 incur the SAME USDC fee
fee_low = compute_taker_fee_usdc(40.0, 0.40, "Politics")
fee_high = compute_taker_fee_usdc(60.0, 0.60, "Politics")  # 100 shares @ 0.60 = $60
check("Symmetry: 100 shares @ 0.40 == 100 shares @ 0.60", abs(fee_low - fee_high) < 0.0001,
      f"low={fee_low:.4f} high={fee_high:.4f}")

# Per-category differential -- Crypto is 0.07, Sports is 0.03
# $100 stake at $0.50 in Crypto = 100 * 0.07 * (1-0.50) = $3.50
# $100 stake at $0.50 in Sports = 100 * 0.03 * (1-0.50) = $1.50
fee_crypto = compute_taker_fee_usdc(100.0, 0.50, "Crypto")
fee_sports = compute_taker_fee_usdc(100.0, 0.50, "Sports")
check("Crypto $100 stake @ 0.50 == $3.50", abs(fee_crypto - 3.50) < 0.0001, f"got {fee_crypto:.4f}")
check("Sports $100 stake @ 0.50 == $1.50", abs(fee_sports - 1.50) < 0.0001, f"got {fee_sports:.4f}")
check("Crypto fee > Sports fee (0.07 vs 0.03)", fee_crypto > fee_sports * 2)

# Geopolitics is FREE
fee_geo = compute_taker_fee_usdc(1000.0, 0.50, "Geopolitics")
check("Geopolitics fee is always 0", fee_geo == 0.0, f"got {fee_geo:.4f}")

# Edge: 0 stake -> 0 fee
check("Zero stake -> zero fee", compute_taker_fee_usdc(0.0, 0.50, "Politics") == 0.0)

# Edge: invalid prices (0 or >= 1) -> 0 fee, no crash
check("Price=0 -> 0 fee", compute_taker_fee_usdc(100.0, 0.0, "Politics") == 0.0)
check("Price=1.0 -> 0 fee", compute_taker_fee_usdc(100.0, 1.0, "Politics") == 0.0)
check("Negative stake -> 0 fee", compute_taker_fee_usdc(-100.0, 0.50, "Politics") == 0.0)

# Unknown category falls through to default
fee_unknown = compute_taker_fee_usdc(100.0, 0.50, "Foobar")
fee_default = compute_taker_fee_usdc(100.0, 0.50, None)
check("Unknown category uses DEFAULT_FEE_RATE",
      abs(fee_unknown - 100.0 * DEFAULT_FEE_RATE * 0.5) < 0.0001,
      f"got {fee_unknown:.4f}")
check("None category uses DEFAULT_FEE_RATE",
      abs(fee_default - 100.0 * DEFAULT_FEE_RATE * 0.5) < 0.0001,
      f"got {fee_default:.4f}")

# Case-insensitive category matching
fee_lower = compute_taker_fee_usdc(100.0, 0.50, "crypto")
fee_proper = compute_taker_fee_usdc(100.0, 0.50, "Crypto")
check("Case-insensitive: 'crypto' == 'Crypto'", abs(fee_lower - fee_proper) < 0.0001,
      f"lower={fee_lower:.4f} proper={fee_proper:.4f}")

# Per-dollar convenience matches per-stake calculation
fpd = compute_taker_fee_per_dollar(0.40, "Politics")
expected_fpd = 1.0 * 0.04 * (1 - 0.40)
check("compute_taker_fee_per_dollar matches stake=1 calculation",
      abs(fpd - expected_fpd) < 0.0001, f"got {fpd:.6f}")

section("fees — full reference table sanity check")

# Spot-check several rows of the Polymarket reference tables
# (100 shares = stake = price * 100)
test_cases = [
    # (category, price, expected_fee_per_100_shares_usdc)
    ("Crypto",   0.10, 0.63),
    ("Crypto",   0.50, 1.75),
    ("Crypto",   0.90, 0.63),
    ("Sports",   0.30, 0.63),
    ("Sports",   0.50, 0.75),
    ("Politics", 0.25, 0.75),
    ("Politics", 0.50, 1.00),
    ("Tech",     0.50, 1.00),  # Tech, Mentions, Politics, Finance all = 0.04
    ("Mentions", 0.50, 1.00),
    ("Finance",  0.50, 1.00),
    ("Culture",  0.50, 1.25),  # Culture, Economics, Weather, Other all = 0.05
    ("Economics", 0.50, 1.25),
    ("Other",    0.50, 1.25),
]
for cat, price, expected in test_cases:
    stake_for_100_shares = price * 100
    fee = compute_taker_fee_usdc(stake_for_100_shares, price, cat)
    check(f"{cat} 100 shares @ ${price:.2f} = ${expected:.2f}",
          abs(fee - expected) < 0.01, f"got ${fee:.4f}")


# ---------------------------------------------------------------------------
# backtest_engine.compute_kish_n_eff — effective sample size
# ---------------------------------------------------------------------------

section("compute_kish_n_eff — clustered sample size")

# All singletons: n_eff equals n
keys_singletons = [f"k{i}" for i in range(50)]
neff = compute_kish_n_eff(keys_singletons)
check("50 singleton clusters -> n_eff = 50", abs(neff - 50.0) < 0.01, f"got {neff:.2f}")

# Single big cluster of n: n_eff = 1
keys_one_cluster = ["A"] * 100
neff = compute_kish_n_eff(keys_one_cluster)
check("100 obs all in one cluster -> n_eff = 1", abs(neff - 1.0) < 0.01, f"got {neff:.2f}")

# Trump example from the audit: 1 cluster of 200 + 50 singletons
# Expected: 250^2 / (200^2 + 50) = 62500 / 40050 ~ 1.56
keys_trump = ["TRUMP"] * 200 + [f"singleton_{i}" for i in range(50)]
neff = compute_kish_n_eff(keys_trump)
expected = (250 ** 2) / (200 ** 2 + 50)
check("Trump example (200+50 singletons) -> n_eff ~ 1.56",
      abs(neff - expected) < 0.01, f"got {neff:.4f} expected {expected:.4f}")

# Balanced clusters: 50 clusters of 5 each = 250 total
# Expected: 250^2 / (50 × 25) = 62500 / 1250 = 50
keys_balanced = []
for c in range(50):
    keys_balanced.extend([f"cluster_{c}"] * 5)
neff = compute_kish_n_eff(keys_balanced)
check("50 balanced clusters of 5 -> n_eff = 50", abs(neff - 50.0) < 0.01, f"got {neff:.2f}")

# None keys count as singletons (one per observation, distinct)
keys_with_none = [None, None, None, "A", "A"]
neff = compute_kish_n_eff(keys_with_none)
# 5 distinct (3 singletons + 1 cluster of 2): sizes [1,1,1,2], n=5, sum_sq=1+1+1+4=7
# n_eff = 25/7 ~ 3.57
expected = 25.0 / 7
check("None keys treated as singletons", abs(neff - expected) < 0.01,
      f"got {neff:.4f} expected {expected:.4f}")

# Empty input
check("Empty input -> n_eff = 0", compute_kish_n_eff([]) == 0.0)


# ---------------------------------------------------------------------------
# polymarket._safe_list_from_response + ResponseShapeError
# ---------------------------------------------------------------------------

section("_safe_list_from_response — shape parsing")

# Real list passes through
result = _safe_list_from_response([{"a": 1}, {"b": 2}], "test")
check("Real list returns list", result == [{"a": 1}, {"b": 2}])

# Empty list passes through (legit empty)
result = _safe_list_from_response([], "test")
check("Empty list returns empty list (no error)", result == [])

# List with non-dict entries: filtered out
result = _safe_list_from_response([{"a": 1}, "garbage", 42, {"b": 2}], "test")
check("Non-dict entries filtered out", result == [{"a": 1}, {"b": 2}])

# Wrapped list
result = _safe_list_from_response({"data": [{"x": 1}]}, "test", list_keys=("data",))
check("Wrapped list unwrapped via list_keys", result == [{"x": 1}])

# Dict with no expected wrapper key — RAISES ResponseShapeError
try:
    _safe_list_from_response({"error": "rate limited"}, "test", list_keys=("data",))
    check("Dict-with-no-wrapper-key raises ResponseShapeError", False, "did NOT raise")
except ResponseShapeError as e:
    check("Dict-with-no-wrapper-key raises ResponseShapeError", True,
          f"endpoint={e.endpoint!r}")

# Non-list, non-dict (None, str, int) — RAISES
for bad in (None, "garbage", 42, 3.14):
    try:
        _safe_list_from_response(bad, "test")
        check(f"Bad type {type(bad).__name__} raises", False, "did NOT raise")
    except ResponseShapeError:
        check(f"Bad type {type(bad).__name__} raises ResponseShapeError", True)

section("_safe_list_or_empty — silent wrapper for non-paginator callers")

# Real list passes through
result = _safe_list_or_empty([{"a": 1}], "test")
check("List passes through silent wrapper", result == [{"a": 1}])

# Empty list passes through
result = _safe_list_or_empty([], "test")
check("Empty list passes through silent wrapper", result == [])

# Wrapped list passes through
result = _safe_list_or_empty({"data": [{"x": 1}]}, "test", list_keys=("data",))
check("Wrapped list passes through silent wrapper", result == [{"x": 1}])

# Dict with no wrapper -> silent empty (NOT an exception)
result = _safe_list_or_empty({"error": "rate limited"}, "test", list_keys=("data",))
check("Silent wrapper returns [] on shape error (no exception)", result == [])

# Non-list, non-dict -> silent empty
result = _safe_list_or_empty(None, "test")
check("Silent wrapper returns [] on None", result == [])


# ---------------------------------------------------------------------------
# Zombie/dust position filter — Position.from_dict + drop_reason()
# ---------------------------------------------------------------------------

section("Position.from_dict — redeemable parsing")


def _position_dict(**overrides):
    """Minimal valid /positions API row. Override fields as needed."""
    base = {
        "proxyWallet": "0xabc",
        "conditionId": "0xcid_default",
        "asset": "asset_default",
        "outcome": "Yes",
        "size": 100.0,
        "avgPrice": 0.45,
        "curPrice": 0.55,
        "currentValue": 55.0,
    }
    base.update(overrides)
    return base


# Parsing — redeemable=True
p = Position.from_dict(_position_dict(redeemable=True))
check("from_dict parses redeemable=True", p.redeemable is True)

# Parsing — redeemable=False explicit
p = Position.from_dict(_position_dict(redeemable=False))
check("from_dict parses redeemable=False explicit", p.redeemable is False)

# Parsing — redeemable missing -> default False (fail-open)
p = Position.from_dict(_position_dict())
check("from_dict redeemable defaults to False when key missing", p.redeemable is False)

# Parsing — redeemable=None -> bool(None) = False
p = Position.from_dict(_position_dict(redeemable=None))
check("from_dict redeemable=None coerces to False", p.redeemable is False)


section("Position.drop_reason — multi-signal predicate")

# Reason 1: redeemable=True wins (highest priority)
p = Position.from_dict(_position_dict(redeemable=True, size=100.0, curPrice=0.5))
check("drop_reason: redeemable=True returns 'redeemable'",
      p.drop_reason() == "redeemable")

# Reason 2: market_closed via raw['closed']=True
p = Position.from_dict(_position_dict(closed=True))
check("drop_reason: raw.closed=True returns 'market_closed'",
      p.drop_reason() == "market_closed")

# Reason 3: dust_size at exactly the threshold (1.0)
p = Position.from_dict(_position_dict(size=1.0))
check("drop_reason: size=1.0 (at threshold) returns 'dust_size'",
      p.drop_reason() == "dust_size")

# Reason 3: dust_size below threshold (0.5)
p = Position.from_dict(_position_dict(size=0.5))
check("drop_reason: size=0.5 (below threshold) returns 'dust_size'",
      p.drop_reason() == "dust_size")

# Reason 3: just above the threshold -> kept
p = Position.from_dict(_position_dict(size=1.01))
check("drop_reason: size=1.01 (just above threshold) returns None (kept)",
      p.drop_reason() is None)

# Reason 4: extreme price + past end_date -> resolved_price_past
past_date = "2020-01-01T00:00:00Z"
p = Position.from_dict(_position_dict(size=100, curPrice=1.0, endDate=past_date))
check("drop_reason: cur_price=1.0 + past end_date returns 'resolved_price_past'",
      p.drop_reason() == "resolved_price_past")

# Reason 4: extreme price BUT future end_date -> kept (live market just at extreme)
future_date = "2099-01-01T00:00:00Z"
p = Position.from_dict(_position_dict(size=100, curPrice=0.99, endDate=future_date))
check("drop_reason: cur_price=0.99 + future end_date returns None (live market)",
      p.drop_reason() is None)

# Reason 4: cur_price at 1.0 + future end_date -> kept (heavy favorite, live)
p = Position.from_dict(_position_dict(size=100, curPrice=1.0, endDate=future_date))
check("drop_reason: cur_price=1.0 + future end_date returns None (live extreme)",
      p.drop_reason() is None)

# Normal live position -> kept
p = Position.from_dict(_position_dict(size=100, curPrice=0.45))
check("drop_reason: normal position (size=100, mid-price) returns None",
      p.drop_reason() is None)

# Priority: redeemable beats every other signal (so the counter attributes correctly)
p = Position.from_dict(_position_dict(
    redeemable=True, closed=True, size=0.5, curPrice=1.0, endDate=past_date,
))
check("drop_reason: priority order -> redeemable wins over closed+dust+price",
      p.drop_reason() == "redeemable")

# Priority: market_closed beats dust + price-past (when redeemable False)
p = Position.from_dict(_position_dict(
    closed=True, size=0.5, curPrice=1.0, endDate=past_date,
))
check("drop_reason: priority order -> market_closed wins over dust + price-past",
      p.drop_reason() == "market_closed")


section("Position._end_date_in_past — date parsing edge cases")

p_past = Position.from_dict(_position_dict(endDate="2020-01-01T00:00:00Z"))
check("_end_date_in_past: 2020 is past", p_past._end_date_in_past() is True)

p_future = Position.from_dict(_position_dict(endDate="2099-01-01T00:00:00Z"))
check("_end_date_in_past: 2099 is future", p_future._end_date_in_past() is False)

p_none = Position.from_dict(_position_dict())  # no endDate key -> None
check("_end_date_in_past: None end_date returns False",
      p_none._end_date_in_past() is False)

p_malformed = Position.from_dict(_position_dict(endDate="not-a-date"))
check("_end_date_in_past: malformed string returns False (no crash)",
      p_malformed._end_date_in_past() is False)


# ---------------------------------------------------------------------------
# get_positions integration: filter applied at the API seam
# ---------------------------------------------------------------------------

section("PolymarketClient.get_positions — zombie filter at API boundary")


class _StubClient(PolymarketClient):
    """PolymarketClient with _get_json overridden to return canned data.

    Bypasses httpx + rate limiter so we can test the filter logic in
    isolation. Required: `await client.__aenter__()` is NOT called, so
    the rate limiter / httpx client never touch the network.
    """

    def __init__(self, canned_response):
        super().__init__()
        self._canned = canned_response

    async def _get_json(self, url, params=None):
        return self._canned


import asyncio  # noqa: E402

# Reset counters so we can assert exact deltas
health_counters.reset()

# Canned API response: 5 rows, 4 should drop, 1 should survive
mixed_response = [
    _position_dict(conditionId="0xc1_redeemable", redeemable=True),
    _position_dict(conditionId="0xc2_closed", closed=True),
    _position_dict(conditionId="0xc3_dust", size=0.5),
    _position_dict(conditionId="0xc4_resolved_price",
                   curPrice=1.0, endDate="2020-01-01T00:00:00Z"),
    _position_dict(conditionId="0xc5_live", size=100, curPrice=0.5),
]

stub = _StubClient(canned_response=mixed_response)
result = asyncio.run(stub.get_positions("0xabc"))

check("get_positions default filters zombies/dust (5 in -> 1 out)",
      len(result) == 1, f"got {len(result)} positions")
check("get_positions: surviving position is the live one",
      len(result) == 1 and result[0].condition_id == "0xc5_live")

# Counters should reflect 1 of each reason
snap = health_counters.snapshot()
check("counter incremented: zombie_drop_redeemable=1",
      snap["zombie_drop_redeemable"] == 1, f"got {snap['zombie_drop_redeemable']}")
check("counter incremented: zombie_drop_market_closed=1",
      snap["zombie_drop_market_closed"] == 1, f"got {snap['zombie_drop_market_closed']}")
check("counter incremented: zombie_drop_dust_size=1",
      snap["zombie_drop_dust_size"] == 1, f"got {snap['zombie_drop_dust_size']}")
check("counter incremented: zombie_drop_resolved_price_past=1",
      snap["zombie_drop_resolved_price_past"] == 1,
      f"got {snap['zombie_drop_resolved_price_past']}")

# include_resolved=True bypasses the filter entirely
health_counters.reset()
stub = _StubClient(canned_response=mixed_response)
result_unfiltered = asyncio.run(stub.get_positions("0xabc", include_resolved=True))
check("get_positions(include_resolved=True) returns ALL rows (5 in -> 5 out)",
      len(result_unfiltered) == 5)

# include_resolved=True must NOT increment counters (no filter ran)
snap = health_counters.snapshot()
total_zombie_drops = (
    snap["zombie_drop_redeemable"]
    + snap["zombie_drop_market_closed"]
    + snap["zombie_drop_dust_size"]
    + snap["zombie_drop_resolved_price_past"]
)
check("include_resolved=True does NOT increment zombie counters",
      total_zombie_drops == 0, f"got {total_zombie_drops}")

# Empty response -> empty list, no counter increments
health_counters.reset()
stub = _StubClient(canned_response=[])
result_empty = asyncio.run(stub.get_positions("0xabc"))
check("get_positions on empty response returns []", result_empty == [])
snap = health_counters.snapshot()
check("empty response increments no counters",
      snap["zombie_drop_redeemable"] == 0
      and snap["zombie_drop_market_closed"] == 0)


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
    print("  All Phase 2 helpers verified.")
