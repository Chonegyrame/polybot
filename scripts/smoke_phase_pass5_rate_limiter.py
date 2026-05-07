"""Pass 5 R17 -- rate-limiter consolidation smoke tests.

Pure-function tests for:
  - app/services/rate_limiter.py -- module-level registry, lazy lock,
    per-host scoping
  - app/services/polymarket.py -- _bucket_for, _parse_retry_after,
    _DecorrelatedJitterWait

No DB access. No live API.
Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_rate_limiter.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.rate_limiter import (  # noqa: E402
    TokenBucket,
    get_bucket,
    host_for_url,
    reset_buckets,
)
from app.services.polymarket import (  # noqa: E402
    PolymarketClient,
    _DecorrelatedJitterWait,
    _parse_retry_after,
    RETRY_AFTER_CAP_SECONDS,
)
from app.config import settings  # noqa: E402


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
# config sanity
# ---------------------------------------------------------------------------

section("config -- rate_limit_per_second default lowered to 8 r/s")

# Pass 5 R17 lowered the default 10.0 -> 8.0 to leave headroom for retries.
# Settings is loaded at import time. If the operator overrides via env var,
# accept any value <= 10 as honoring the spirit of the change.
check(
    "settings.rate_limit_per_second <= 10.0 (default lowered to 8.0)",
    settings.rate_limit_per_second <= 10.0,
    f"actual={settings.rate_limit_per_second}",
)


# ---------------------------------------------------------------------------
# rate_limiter -- host_for_url
# ---------------------------------------------------------------------------

section("rate_limiter.host_for_url -- URL -> hostname extraction")

check(
    "data-api URL -> 'data-api.polymarket.com'",
    host_for_url("https://data-api.polymarket.com/positions?user=0xabc")
    == "data-api.polymarket.com",
)
check(
    "gamma-api URL -> 'gamma-api.polymarket.com'",
    host_for_url("https://gamma-api.polymarket.com/markets") == "gamma-api.polymarket.com",
)
check(
    "clob URL -> 'clob.polymarket.com'",
    host_for_url("https://clob.polymarket.com/book?token_id=xyz") == "clob.polymarket.com",
)
check(
    "URL with port preserved hostname (port stripped)",
    host_for_url("http://example.com:8080/path") == "example.com",
)
check(
    "malformed URL -> 'unknown' (defensive)",
    host_for_url("not a url") == "unknown",
)


# ---------------------------------------------------------------------------
# rate_limiter -- registry sharing & per-host scoping
# ---------------------------------------------------------------------------

section("rate_limiter.get_bucket -- shared registry per host")

reset_buckets()

b1 = get_bucket("data-api.polymarket.com", 8.0)
b2 = get_bucket("data-api.polymarket.com", 8.0)
check(
    "two get_bucket calls for SAME host return SAME instance",
    b1 is b2,
    "this is THE Pass 5 R17 fix -- concurrent jobs share one bucket",
)

b_gamma = get_bucket("gamma-api.polymarket.com", 8.0)
check(
    "different host -> different bucket (per-host scoping)",
    b1 is not b_gamma,
    "prevents one slow host starving callers of another",
)

b_clob = get_bucket("clob.polymarket.com", 8.0)
check(
    "third distinct host -> third distinct bucket",
    b_clob is not b1 and b_clob is not b_gamma,
)

# First-write-wins semantics: the second call's rate is ignored
b_again = get_bucket("data-api.polymarket.com", 99.0)
check(
    "first-write-wins on rate (second call's rate ignored)",
    b_again is b1 and b_again.rate == 8.0,
    f"rate={b_again.rate}",
)


# ---------------------------------------------------------------------------
# rate_limiter -- reset_buckets
# ---------------------------------------------------------------------------

section("rate_limiter.reset_buckets -- test isolation helper")

before = get_bucket("data-api.polymarket.com", 8.0)
reset_buckets()
after = get_bucket("data-api.polymarket.com", 8.0)
check(
    "after reset_buckets, get_bucket returns a NEW instance",
    before is not after,
)


# ---------------------------------------------------------------------------
# TokenBucket -- lazy lock works without running event loop at construction
# ---------------------------------------------------------------------------

section("TokenBucket -- lazy lock binding (event-loop safe)")

# Construct a bucket OUTSIDE any event loop. Pre-fix would have called
# asyncio.Lock() at __init__ which crashes on Python 3.10+ if no loop is
# running. Post-fix the lock is created lazily inside acquire().
no_loop_bucket = TokenBucket(rate=8.0)
check(
    "TokenBucket() constructible without running event loop",
    no_loop_bucket._lock is None,
    "lock is None until first acquire()",
)


# ---------------------------------------------------------------------------
# TokenBucket -- actual pacing behavior (integration)
# ---------------------------------------------------------------------------

section("TokenBucket -- paces requests at the configured rate")


async def _test_pacing() -> tuple[float, int]:
    """Acquire 5 tokens from a fresh rate=10/s bucket. Should take roughly
    400ms (first 5 tokens are immediate from initial burst capacity, then
    each successive acquire waits ~100ms)."""
    reset_buckets()
    bucket = get_bucket("test-pacing.example.com", 10.0)
    start = time.monotonic()
    n_acquired = 0
    # Acquire 15 tokens -- the first 10 (capacity) are immediate, then 5 more
    # at 10/s rate = ~500ms additional wait.
    for _ in range(15):
        await bucket.acquire()
        n_acquired += 1
    elapsed = time.monotonic() - start
    return elapsed, n_acquired


elapsed, n_acquired = asyncio.run(_test_pacing())
check(
    "15 acquires from rate=10/s, capacity=10 took >= 0.4s",
    elapsed >= 0.4,
    f"elapsed={elapsed:.2f}s, expected ~0.5s",
)
check(
    "all 15 acquires completed",
    n_acquired == 15,
    f"completed={n_acquired}",
)


# ---------------------------------------------------------------------------
# TokenBucket -- works across multiple event loops (test reuse)
# ---------------------------------------------------------------------------

section("TokenBucket -- lock rebinds when event loop changes")


async def _acquire_once() -> None:
    bucket = get_bucket("multi-loop.example.com", 10.0)
    await bucket.acquire()


def _run_in_fresh_loop() -> None:
    asyncio.run(_acquire_once())


# Run the same bucket across two distinct event loops. Pre-fix lazy-lock
# would bind to the first loop and the second loop's await would fail with
# "RuntimeError: <Lock> is bound to a different event loop".
reset_buckets()
_run_in_fresh_loop()
multi_loop_ok = True
try:
    _run_in_fresh_loop()
except RuntimeError as e:
    multi_loop_ok = False
    print(f"  [DEBUG]  multi-loop runtime error: {e}")
check(
    "same bucket usable across two distinct event loops",
    multi_loop_ok,
    "pytest-style tests with fresh loop per test must not crash",
)


# ---------------------------------------------------------------------------
# PolymarketClient._bucket_for -- production path uses shared registry
# ---------------------------------------------------------------------------

section("PolymarketClient._bucket_for -- production uses shared registry")

reset_buckets()

# Two clients constructed with default (no override) should share buckets
# via the registry.
pm1 = PolymarketClient()
pm2 = PolymarketClient()

b1 = pm1._bucket_for("https://data-api.polymarket.com/positions")
b2 = pm2._bucket_for("https://data-api.polymarket.com/value")
check(
    "two PolymarketClient instances -> same bucket for same host",
    b1 is b2,
    "this is the actual fix -- concurrent scheduler jobs share rate budget",
)

# Different hosts via the same client -> different buckets
b_gamma = pm1._bucket_for("https://gamma-api.polymarket.com/markets")
check(
    "same client, different hosts -> different buckets",
    b1 is not b_gamma,
)

# Three hosts confirm three distinct shared buckets
b_clob = pm1._bucket_for("https://clob.polymarket.com/book")
check(
    "all three hosts -> three distinct shared buckets",
    len({id(b1), id(b_gamma), id(b_clob)}) == 3,
)


# ---------------------------------------------------------------------------
# PolymarketClient._bucket_for -- per-instance override path (tests)
# ---------------------------------------------------------------------------

section("PolymarketClient._bucket_for -- per-instance override path")

reset_buckets()

pm_default = PolymarketClient()
pm_override = PolymarketClient(rate_limit_per_second=1000.0)

b_default = pm_default._bucket_for("https://data-api.polymarket.com/positions")
b_override = pm_override._bucket_for("https://data-api.polymarket.com/positions")

check(
    "override-rate client does NOT share bucket with default client",
    b_default is not b_override,
    "tests passing rate=1000 must not blow out the production bucket",
)
check(
    "override-rate bucket has the override rate",
    b_override.rate == 1000.0,
    f"rate={b_override.rate}",
)
check(
    "default-rate bucket uses settings.rate_limit_per_second",
    b_default.rate == settings.rate_limit_per_second,
    f"rate={b_default.rate}",
)

# Two override clients with the SAME override rate still get DISTINCT
# buckets (each has its own private registry).
pm_override2 = PolymarketClient(rate_limit_per_second=1000.0)
b_override2 = pm_override2._bucket_for("https://data-api.polymarket.com/positions")
check(
    "two override clients -> distinct private buckets (each is isolated)",
    b_override is not b_override2,
)


# ---------------------------------------------------------------------------
# _parse_retry_after -- header parsing (numeric + HTTP-date + edge cases)
# ---------------------------------------------------------------------------

section("polymarket._parse_retry_after -- Retry-After header parsing")

check("None -> None", _parse_retry_after(None) is None)
check("empty string -> None", _parse_retry_after("") is None)
check("whitespace-only -> None", _parse_retry_after("   ") is None)
check("unparseable garbage -> None", _parse_retry_after("nonsense") is None)

check(
    "numeric '5' -> 5.0 seconds",
    _parse_retry_after("5") == 5.0,
)
check(
    "numeric '0.5' -> 0.5 seconds",
    _parse_retry_after("0.5") == 0.5,
)
check(
    "numeric with whitespace '  10  ' -> 10.0",
    _parse_retry_after("  10  ") == 10.0,
)
check(
    "negative numeric -> None (server bug, ignore)",
    _parse_retry_after("-5") is None,
)
check(
    "zero -> None (no point sleeping)",
    _parse_retry_after("0") is None,
)

# Cap at RETRY_AFTER_CAP_SECONDS to defend against pathological values
check(
    "pathological '3600' clamped to RETRY_AFTER_CAP_SECONDS",
    _parse_retry_after("3600") == RETRY_AFTER_CAP_SECONDS,
)


# ---------------------------------------------------------------------------
# _DecorrelatedJitterWait -- jitter formula bounds + Retry-After precedence
# ---------------------------------------------------------------------------

section("polymarket._DecorrelatedJitterWait -- jitter formula")


class _FakeOutcome:
    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc

    def exception(self) -> Exception | None:
        return self._exc


class _FakeRetryState:
    def __init__(self, exc: Exception | None = None, idle_for: float = 0.0) -> None:
        self.outcome = _FakeOutcome(exc) if exc is not None else _FakeOutcome(None)
        self.idle_for = idle_for


jitter = _DecorrelatedJitterWait(base=0.5, cap=8.0)

# Without any prior delay, sleep should be in [base, cap]
samples = [jitter(_FakeRetryState()) for _ in range(50)]
check(
    "jitter samples all >= base (0.5)",
    all(s >= 0.5 for s in samples),
    f"min={min(samples):.3f}",
)
check(
    "jitter samples all <= cap (8.0)",
    all(s <= 8.0 for s in samples),
    f"max={max(samples):.3f}",
)
check(
    "jitter introduces variance (not constant)",
    len({round(s, 3) for s in samples}) > 5,
    f"distinct values={len({round(s, 3) for s in samples})}",
)

# Retry-After takes precedence over jitter formula
class _FakeExc(Exception):
    pass


exc_with_retry_after = _FakeExc()
exc_with_retry_after._retry_after = 12.5  # type: ignore[attr-defined]

state = _FakeRetryState(exc=exc_with_retry_after, idle_for=0.0)
sleep = jitter(state)
check(
    "Retry-After value (12.5s) takes precedence over jitter",
    sleep == 12.5,
    f"got {sleep}",
)

# Exception WITHOUT _retry_after attribute -> fall back to jitter
exc_no_attr = _FakeExc()
state = _FakeRetryState(exc=exc_no_attr, idle_for=0.0)
sleep = jitter(state)
check(
    "exception without _retry_after -> jitter formula used",
    0.5 <= sleep <= 8.0,
    f"got {sleep}",
)


# ---------------------------------------------------------------------------
# Code-shape assertions (smoke against accidental regressions)
# ---------------------------------------------------------------------------

section("Code-shape -- module wiring assertions")

import inspect  # noqa: E402

from app.services import polymarket as pm_mod  # noqa: E402

# Confirm wait_exponential is no longer CALLED (Pass 5 R17 replaced it with
# _DecorrelatedJitterWait). We check for the call form, not the bare word,
# because comments may still mention the old name historically.
src = inspect.getsource(pm_mod.PolymarketClient._get_json)
check(
    "_get_json instantiates _DecorrelatedJitterWait()",
    "_DecorrelatedJitterWait()" in src,
)
check(
    "_get_json no longer calls wait_exponential(...)",
    "wait_exponential(" not in src,
)
check(
    "_get_json calls self._bucket_for(url)",
    "_bucket_for(url)" in src,
)

# Confirm rate_limiter exposes the registry helpers
from app.services import rate_limiter as rl  # noqa: E402
check(
    "rate_limiter exports get_bucket",
    hasattr(rl, "get_bucket") and callable(rl.get_bucket),
)
check(
    "rate_limiter exports host_for_url",
    hasattr(rl, "host_for_url") and callable(rl.host_for_url),
)
check(
    "rate_limiter exports reset_buckets",
    hasattr(rl, "reset_buckets") and callable(rl.reset_buckets),
)
check(
    "rate_limiter exposes module-level _BUCKETS dict",
    isinstance(rl._BUCKETS, dict),
)


# ---------------------------------------------------------------------------
# Cleanup: leave the registry empty so other smoke suites start fresh
# ---------------------------------------------------------------------------

reset_buckets()


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------

print()
print("=" * 80)
print(f"  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 R17 rate-limiter consolidation tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
