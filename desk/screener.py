"""Golden-cross EMA screener — pure math + detection (no I/O).

Golden cross = fast EMA crosses ABOVE slow EMA (bullish). Death cross = fast
crosses below (bearish). Defaults are the textbook 50/200; configurable via env
(DESK_EMA_FAST / DESK_EMA_SLOW) and the "recent" window (DESK_CROSS_WINDOW, in
trading days) that decides how fresh a cross must be to count as a signal.

EMA uses the standard seed: SMA of the first `period` closes, then the recursive
multiplier 2/(period+1). Detection scans the full EMA series and reports the most
recent crossover, its direction, and how many trading days ago it happened.
"""

from __future__ import annotations

import os
from typing import Optional


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


EMA_FAST = _int_env("DESK_EMA_FAST", 50)
EMA_SLOW = _int_env("DESK_EMA_SLOW", 200)
CROSS_WINDOW = _int_env("DESK_CROSS_WINDOW", 10)  # trading days a cross stays "fresh"


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """EMA series aligned to `values`; first period-1 entries are None.

    Seeded with the simple average of the first `period` values (the common
    convention), then values[i]*k + prev*(1-k) with k = 2/(period+1).
    """
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if n < period or period <= 0:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def detect_cross(closes: list[float], fast: int = EMA_FAST, slow: int = EMA_SLOW) -> dict:
    """Find the most recent fast/slow EMA crossover in `closes`.

    Returns a dict with:
      state       : 'golden' | 'death' | 'none'   (most recent crossover type)
      days_since  : trading days since that crossover (0 = happened on last bar)
      cross_index : index in `closes` of the crossover bar, or None
      above       : is fast currently above slow? (current trend posture)
      fast_ema, slow_ema, last_close : latest values
      enough_data : False if not enough bars to compute the slow EMA
    """
    n = len(closes)
    result = {
        "state": "none", "days_since": None, "cross_index": None,
        "above": None, "fast_ema": None, "slow_ema": None,
        "last_close": closes[-1] if n else None, "enough_data": False,
    }
    if n < slow + 1:
        return result

    ef = ema(closes, fast)
    es = ema(closes, slow)
    result["enough_data"] = True
    result["fast_ema"] = ef[-1]
    result["slow_ema"] = es[-1]
    if ef[-1] is not None and es[-1] is not None:
        result["above"] = ef[-1] > es[-1]

    # Walk backward to the most recent bar where (fast - slow) flipped sign.
    # A small deadband (eps, relative to price) ignores float noise when the two
    # EMAs are essentially equal, so a flat/near-flat series fires no phantom cross.
    for i in range(n - 1, 0, -1):
        a, b = ef[i], es[i]
        pa, pb = ef[i - 1], es[i - 1]
        if a is None or b is None or pa is None or pb is None:
            break
        eps = abs(b) * 1e-7 + 1e-12
        prev_diff = pa - pb
        cur_diff = a - b
        if prev_diff <= eps and cur_diff > eps:
            result["state"] = "golden"
            result["cross_index"] = i
            result["days_since"] = (n - 1) - i
            break
        if prev_diff >= -eps and cur_diff < -eps:
            result["state"] = "death"
            result["cross_index"] = i
            result["days_since"] = (n - 1) - i
            break
    return result


def is_fresh_golden(det: dict, window: int = CROSS_WINDOW) -> bool:
    """A golden cross that happened within the recent window = a signal to fire."""
    return (
        det.get("state") == "golden"
        and det.get("days_since") is not None
        and det["days_since"] <= window
    )
