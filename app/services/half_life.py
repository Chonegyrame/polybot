"""B4 — signal price-snapshot collection + half-life analytics.

Two pieces:

  - `pick_offset_for_age` — pure mapping from minutes-since-fire to the
    canonical 5 / 15 / 30 / 60 / 120 min bucket (with a ±5 min tolerance).
    F7: added 5 + 15 min offsets so the short latency profiles (active
    1-3, responsive 5-10, casual 12-20) have real data behind them.
    Picks the CLOSEST canonical offset, with ties broken toward the
    smaller offset (so we capture early-time-horizon snapshots first).

  - `compute_half_life_summary` — turns a pile of (signal, fire_price,
    snapshot_at_offset, smart_money_entry_price) tuples into per-category
    convergence stats. n < 30 per category → flagged underpowered.

  F4: snapshots now carry both `bid_price` and `ask_price`. Half-life math
  uses mid = (bid + ask) / 2 when both available (falls back to bid for
  legacy rows). Comparing entry-side ask to snapshot-side bid baked a
  spread artifact into the convergence rate; mid is the honest comparison.

  F5: math is done in YES-space. fire_price and smart_money_entry are
  direction-space → translated via _to_yes_space. snapshot_price is
  already YES-space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

# Offsets we capture, in minutes since first_fired_at. Order matters for
# `pick_offset_for_age` — see helper for tie-break semantics.
# F7: added 5 + 15 min offsets (was just 120, 60, 30).
SNAPSHOT_OFFSETS_MIN: tuple[int, ...] = (120, 60, 30, 15, 5)
OFFSET_TOLERANCE_MIN = 5

# Minimum n per category before half-life numbers are considered honest.
MIN_HALF_LIFE_SAMPLE = 30


def pick_offset_for_age(
    age_minutes: float, exclude: Iterable[int] = (),
) -> int | None:
    """Map minutes-since-fire to the best canonical offset, or None.

    F7: picks the CLOSEST eligible offset (within ±OFFSET_TOLERANCE_MIN)
    that is NOT in `exclude`. Tie-breaks toward the smaller offset so we
    record early-time-horizon snapshots first. Pre-fix (when offsets were
    just 30/60/120 with no overlap) used `max` which was equivalent; with
    +5/+15 added there's now overlap at boundary ages and `closest` is the
    right semantic.

    `exclude` lets the caller pass already-snapshotted offsets so this
    helper picks the next-best one instead of repeatedly returning a
    duplicate.
    """
    excl = set(exclude)
    candidates = [
        (abs(age_minutes - off), off)
        for off in SNAPSHOT_OFFSETS_MIN
        if abs(age_minutes - off) <= OFFSET_TOLERANCE_MIN
        and off not in excl
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][1]


@dataclass(frozen=True)
class HalfLifeRow:
    """One signal + one offset's worth of data, post-join.

    F4: now carries both bid_price and ask_price. snapshot_price is kept
    for back-compat (mirrors bid). compute_half_life_summary uses mid
    when ask is available.
    """
    category: str | None              # market_category from events
    fire_price: float                 # signal_entry_offer at fire (direction-space)
    direction: str                    # 'YES' | 'NO'
    smart_money_entry: float | None   # first_top_trader_entry_price (direction-space)
    snapshot_price: float | None      # YES bid (back-compat); prefer bid_price/ask_price
    offset_min: int                   # 5 | 15 | 30 | 60 | 120
    # F4 additions — both nullable for legacy rows.
    bid_price: float | None = None    # YES-space bid at offset
    ask_price: float | None = None    # YES-space ask at offset


@dataclass
class HalfLifeBucket:
    category: str
    offset_min: int
    n: int
    convergence_rate: float | None    # fraction of rows that "moved toward smart money"
    underpowered: bool


def _to_yes_space(price: float, direction: str) -> float:
    """F5: Convert a direction-space price to YES-token-space.

    Direction-space: for a YES signal prices are YES-token prices; for a NO
    signal prices are NO-token prices. Snapshots are always stored as
    YES-token prices, so to compare them, translate direction-space inputs
    to YES-space via 1-x for NO signals.
    """
    if direction == "NO":
        return 1.0 - price
    return price


def _snapshot_yes_mid(row: "HalfLifeRow") -> float | None:
    """F4: prefer mid = (bid+ask)/2 when both available, fall back to bid.

    Pre-fix used bid only — entry was ask, so the spread baked an
    artificial "convergence" into every comparison. Using mid removes
    the spread bias.
    """
    bid = row.bid_price if row.bid_price is not None else row.snapshot_price
    ask = row.ask_price
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return bid  # falls through to None if both bid sources are None


def _moved_toward_smart_money(
    fire_price: float, snapshot_price: float, smart_money_entry: float,
) -> bool | None:
    """Did the market price move (over the offset window) closer to the
    cost basis of the top traders we were following?

    Returns:
      True  — snapshot is strictly closer to smart_money_entry than fire was.
      False — snapshot is strictly farther (or equal).
      None  — undefined (e.g. fire == smart_money_entry already, no gap to close).
    """
    fire_gap = abs(fire_price - smart_money_entry)
    snap_gap = abs(snapshot_price - smart_money_entry)
    if fire_gap == 0:
        return None
    return snap_gap < fire_gap


def compute_half_life_summary(
    rows: Iterable[HalfLifeRow],
) -> list[HalfLifeBucket]:
    """Group input rows by (category, offset) and compute convergence rate.

    Convergence rate = fraction of rows where the market price moved toward
    smart-money cost basis. n includes only rows where the comparison was
    definable (fire_gap > 0, snapshot_price available).

    F5: math done in YES-space (canonical storage of snapshot prices).
    F4: snapshot side is mid (bid+ask)/2 when ask available, else bid only.
    """
    by_bucket: dict[tuple[str, int], list[bool]] = {}

    for r in rows:
        if r.smart_money_entry is None:
            continue
        snap_yes = _snapshot_yes_mid(r)
        if snap_yes is None:
            continue
        # F5: translate direction-space inputs to YES-space; snapshot is
        # already YES-space so it passes through.
        fire_yes = _to_yes_space(r.fire_price, r.direction)
        sm_yes = _to_yes_space(r.smart_money_entry, r.direction)
        moved = _moved_toward_smart_money(fire_yes, snap_yes, sm_yes)
        if moved is None:
            continue
        cat = r.category or "uncategorized"
        by_bucket.setdefault((cat, r.offset_min), []).append(moved)

    out: list[HalfLifeBucket] = []
    for (cat, off), bools in sorted(by_bucket.items()):
        n = len(bools)
        rate = sum(1 for b in bools if b) / n if n > 0 else None
        out.append(HalfLifeBucket(
            category=cat,
            offset_min=off,
            n=n,
            convergence_rate=rate,
            underpowered=n < MIN_HALF_LIFE_SAMPLE,
        ))
    return out
