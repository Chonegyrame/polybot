"""Async token-bucket rate limiter.

Pass 5 R17 (rate-limiter consolidation):
    Buckets are kept in a process-wide registry keyed by host. Every code
    path that hits the same Polymarket host shares the same bucket — no
    matter how many `PolymarketClient` instances are alive concurrently
    (12 distinct call sites in this codebase). Pre-fix every
    `PolymarketClient.__init__` created its own bucket, so two scheduler
    jobs overlapping by seconds (e.g. `record_signal_price_snapshots` +
    `refresh_and_log` on the same 10-min cron) emitted 2× the configured
    rate, cascading into 429s.

Per-host scoping (rather than one global bucket) protects against one slow
host (e.g. CLOB) starving callers of an unrelated host (e.g. data-api).

Lazy lock binding:
    `asyncio.Lock` instances are bound to the event loop they're created
    on. We can't create the lock at construction time because the
    registry is populated lazily and may first be touched from a test
    fixture or a fresh loop. The lock is created on first `acquire()`
    inside an async context, and recreated if the loop changes (covers
    pytest-style tests where each test gets a fresh loop).
"""

from __future__ import annotations

import asyncio
import threading
import time
from urllib.parse import urlparse


class TokenBucket:
    """Fair async rate limiter. `acquire()` waits until a token is available.

    rate     — tokens added per second (i.e. max sustained req/s)
    capacity — burst size (defaults to rate, so a 1-second burst is allowed)
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self.tokens = self.capacity
        self._last = time.monotonic()
        # Lock and its bound loop are lazily created on first acquire so
        # construction does not require a running event loop, and tests
        # using multiple loops don't get a "lock bound to different loop"
        # error.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _ensure_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def acquire(self, tokens: float = 1.0) -> None:
        lock = self._ensure_lock()
        async with lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                wait = (tokens - self.tokens) / self.rate
                await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Module-level registry — Pass 5 R17
# ---------------------------------------------------------------------------

# Buckets keyed by host (e.g. "data-api.polymarket.com"). One process-wide
# bucket per host shared by every PolymarketClient instance. Mutated only
# under _REGISTRY_LOCK so concurrent first-acquire calls from different
# threads (rare but possible if FastAPI runs threadpool offloads) don't
# create duplicate buckets.
_BUCKETS: dict[str, TokenBucket] = {}
_REGISTRY_LOCK = threading.Lock()


def get_bucket(
    host: str, rate: float, capacity: float | None = None
) -> TokenBucket:
    """Return the shared TokenBucket for a host, creating it on first call.

    The first caller for a host wins the rate/capacity values. Subsequent
    calls receive the same bucket regardless of the rate they pass — so
    the registry is intentionally first-write-wins for those parameters.
    Use `reset_buckets()` between tests if you need to override.
    """
    bucket = _BUCKETS.get(host)
    if bucket is not None:
        return bucket
    with _REGISTRY_LOCK:
        bucket = _BUCKETS.get(host)
        if bucket is None:
            bucket = TokenBucket(rate=rate, capacity=capacity)
            _BUCKETS[host] = bucket
        return bucket


def host_for_url(url: str) -> str:
    """Extract the hostname for bucket scoping.

    Returns 'unknown' if the URL has no parseable hostname (defensive —
    would only happen with malformed URLs that are bugs elsewhere).
    """
    return urlparse(url).hostname or "unknown"


def reset_buckets() -> None:
    """Test helper: clear the registry so each test starts with fresh buckets.

    Production code never calls this. Smoke tests should call it in setup
    so per-test bucket state doesn't leak across cases.
    """
    with _REGISTRY_LOCK:
        _BUCKETS.clear()
