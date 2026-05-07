"""D5 (Pass 3) -- in-memory health counters surfaced on /system/status.

Tracks operational signals the user wants to see at a glance:
  - rate_limit_hits_last_hour: how often Polymarket returned 429
  - cycle_duration_warnings_last_24h: cycles that exceeded 9 min
  - api_failures_last_hour: unrecoverable API errors

In-memory by design: V1 has one Python process, restart-on-deploy is fine.
If we later need persistence (multi-process, post-mortem analysis), upgrade
to a small `health_events` table with rolling-window queries.

Each counter stores a deque of timestamps; read-side queries count entries
within a window. Old entries get pruned lazily on read.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Final

# Internal storage. Each value is a deque of UTC timestamps (newest at right).
_lock = threading.Lock()
_events: dict[str, deque[datetime]] = {}

# Counter names (use constants to avoid typos at call sites).
RATE_LIMIT_HIT: Final[str] = "rate_limit_hit"
CYCLE_DURATION_WARNING: Final[str] = "cycle_duration_warning"
API_FAILURE: Final[str] = "api_failure"

# How long to keep each event class. Events older than this get pruned.
_RETENTION: dict[str, timedelta] = {
    RATE_LIMIT_HIT: timedelta(hours=1),
    CYCLE_DURATION_WARNING: timedelta(hours=24),
    API_FAILURE: timedelta(hours=1),
}


def record(counter: str) -> None:
    """Increment a counter (record one event at NOW)."""
    now = datetime.now(timezone.utc)
    with _lock:
        if counter not in _events:
            _events[counter] = deque()
        _events[counter].append(now)


def count_since(counter: str, since: datetime) -> int:
    """Count events for `counter` that occurred at or after `since`.
    Lazily prunes events older than the counter's retention window."""
    with _lock:
        dq = _events.get(counter)
        if not dq:
            return 0
        # Lazy prune by retention
        cutoff = datetime.now(timezone.utc) - _RETENTION.get(counter, timedelta(hours=24))
        while dq and dq[0] < cutoff:
            dq.popleft()
        # Count remaining at or after `since`
        return sum(1 for ts in dq if ts >= since)


def snapshot() -> dict[str, int]:
    """Return a dict of {counter_name: count_within_retention_window}."""
    now = datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for name in (RATE_LIMIT_HIT, CYCLE_DURATION_WARNING, API_FAILURE):
        retention = _RETENTION.get(name, timedelta(hours=1))
        out[name] = count_since(name, now - retention)
    return out


def reset() -> None:
    """Clear all counters. For tests + clean-slate startup."""
    with _lock:
        _events.clear()
