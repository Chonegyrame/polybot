"""Async token-bucket rate limiter. One instance shared across all calls to a host."""

from __future__ import annotations

import asyncio
import time


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
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
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
