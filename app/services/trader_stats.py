"""B5 — Per-trader, per-category trade-derived statistics.

Aggregates a trader's `/trades` history into per-category counts +
last-trade-at, attributing each trade to a category via a (cid → category)
lookup against our `markets` + `events` tables. Combined with leaderboard
PnL / Volume, this gives `trader_category_stats` everything trader_ranker
needs to apply recency filters, sample-size floors, and Bayesian shrinkage.

The pure-function core (`aggregate_trades_per_category`) is no-DB so it's
unit-testable with synthetic inputs. The DB-bound `compute_for_wallet`
wrapper does the lookup + aggregation in one call site.

Categories: same 7 as `SNAPSHOT_CATEGORIES`. "overall" is a virtual category
that counts EVERY trade regardless of attribution. Trades whose market is
unknown to us, or whose event has no category, are counted in "overall" only.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import asyncpg

from app.services.polymarket_types import Trade

log = logging.getLogger(__name__)


# Standard 7 — must match SNAPSHOT_CATEGORIES in jobs.py.
ALL_CATEGORIES: tuple[str, ...] = (
    "overall", "politics", "sports", "crypto", "culture", "tech", "finance",
)


@dataclass(frozen=True)
class CategoryStats:
    category: str
    resolved_trades: int
    last_trade_at: datetime | None


def aggregate_trades_per_category(
    trades: Iterable[Trade],
    cid_to_category: dict[str, str | None],
    cid_to_resolved: dict[str, bool],
) -> dict[str, CategoryStats]:
    """Bucket a trader's trades into per-category aggregates.

    `cid_to_category[cid]` returns the category for a market, or None when
    we don't know the category (market not in DB, or event has no category).

    `cid_to_resolved[cid]` returns True iff the market has resolved
    (markets.resolved_outcome IS NOT NULL AND IN ('YES','NO','50_50','VOID')).
    Only resolved trades count toward `resolved_trades` — we want a
    sample-size signal that reflects settled outcomes, not pending bets.

    Every trade contributes to the synthetic "overall" category. Trades on
    known markets ALSO contribute to their specific category. Result is a
    dict keyed by category, containing one CategoryStats per category that
    has at least one trade.
    """
    counts: dict[str, int] = defaultdict(int)
    last_at: dict[str, datetime | None] = defaultdict(lambda: None)

    for t in trades:
        if not t.condition_id or t.timestamp is None:
            continue

        # Track last-trade-at per category regardless of resolution status —
        # recency is "did this trader trade at all recently," not "did they
        # resolve a trade recently."
        ts = t.timestamp
        prev_overall = last_at["overall"]
        if prev_overall is None or ts > prev_overall:
            last_at["overall"] = ts

        cat = cid_to_category.get(t.condition_id)
        if cat:
            prev = last_at[cat]
            if prev is None or ts > prev:
                last_at[cat] = ts

        # Resolved-trade counts attribute only when the market has settled.
        if cid_to_resolved.get(t.condition_id, False):
            counts["overall"] += 1
            if cat:
                counts[cat] += 1

    # Always emit a row per category we saw, even if resolved count is 0
    # (so last_trade_at is recorded). Skip categories with no activity at all.
    out: dict[str, CategoryStats] = {}
    seen_cats = set(counts.keys()) | set(last_at.keys())
    for cat in seen_cats:
        out[cat] = CategoryStats(
            category=cat,
            resolved_trades=counts.get(cat, 0),
            last_trade_at=last_at.get(cat),
        )
    return out


async def fetch_cid_lookups(
    conn: asyncpg.Connection, condition_ids: list[str]
) -> tuple[dict[str, str | None], dict[str, bool]]:
    """One query that returns (cid_to_category, cid_to_resolved) maps for a
    set of condition_ids. Missing cids implicitly map to category=None,
    resolved=False — caller's `aggregate_trades_per_category` handles that.
    """
    if not condition_ids:
        return {}, {}
    rows = await conn.fetch(
        """
        SELECT m.condition_id,
               e.category,
               (m.resolved_outcome IS NOT NULL
                AND m.resolved_outcome IN ('YES','NO','50_50','VOID')) AS resolved
        FROM markets m
        LEFT JOIN events e ON e.id = m.event_id
        WHERE m.condition_id = ANY($1::TEXT[])
        """,
        condition_ids,
    )
    cid_to_category: dict[str, str | None] = {}
    cid_to_resolved: dict[str, bool] = {}
    for r in rows:
        cid_to_category[r["condition_id"]] = r["category"]
        cid_to_resolved[r["condition_id"]] = bool(r["resolved"])
    return cid_to_category, cid_to_resolved
