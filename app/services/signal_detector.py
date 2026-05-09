"""Signal detector — turns the (mode, category, top_n) selection into a list
of firing consensus signals.

Pipeline:
  1. trader_ranker.rank_traders(mode, category, top_n) -> list of wallets
  2. Aggregate those wallets' open positions per (condition_id, direction)
  3. Apply the eligibility floors (≥5 traders, ≥$25k aggregate, ≥60% direction skew)
  4. Return one Signal per (market, direction) that passes

Floors live in MIN_TRADER_COUNT / MIN_AGGREGATE_USDC / MIN_NET_DIRECTION_SKEW
and are tunable. UI never sees signals that don't pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg

from app.services.polymarket import LeaderboardCategory
from app.services.trader_ranker import RankingMode, rank_traders

log = logging.getLogger(__name__)

# Eligibility floors -- every firing signal satisfies all four.
# R2 (Pass 3): "skew" is now BOTH count-skew AND dollar-skew. Pre-fix used
# headcount only, so 6 minnows on YES + 1 whale on NO would fire YES even
# though dollar consensus was 99% NO. Requiring both axes to clear catches
# whale-vs-retail mismatches.
MIN_TRADER_COUNT = 5
MIN_AGGREGATE_USDC = 25_000.0
MIN_NET_DIRECTION_SKEW = 0.65         # headcount fraction
MIN_NET_DIRECTION_DOLLAR_SKEW = 0.65  # USDC-weighted fraction

# B3: watchlist floors -- looser pre-signal threshold for markets building
# consensus. A watchlist row is mutually exclusive with signal_log: a market
# crossing the official floors is a signal, NOT a watchlist row.
# R2: watchlist also requires dual-axis but at the same 0.65 threshold
# (we don't want noisy watchlist hits on markets with dollar mismatch).
WATCHLIST_MIN_TRADER_COUNT = 2
WATCHLIST_MIN_AGGREGATE_USDC = 5_000.0
WATCHLIST_MIN_NET_DIRECTION_SKEW = 0.65
WATCHLIST_MIN_NET_DIRECTION_DOLLAR_SKEW = 0.65

# Map outcome string from Polymarket to our canonical direction label.
# Polymarket markets are binary; outcomes are typically "Yes"/"No" but
# multi-outcome markets exist (e.g. team names). For V1 we only treat the
# canonical Yes/No case as a signal — anything else is filtered out.
Direction = Literal["YES", "NO"]


def _outcome_to_direction(outcome: str | None) -> Direction | None:
    if not outcome:
        return None
    o = outcome.strip().lower()
    if o == "yes":
        return "YES"
    if o == "no":
        return "NO"
    return None


@dataclass(frozen=True)
class Signal:
    """One firing signal -- a (market x direction) pair with strong consensus."""

    condition_id: str
    market_question: str | None
    market_slug: str | None
    market_category: str | None
    event_id: str | None

    direction: Direction
    direction_skew: float           # headcount fraction on this direction (0..1)
    direction_dollar_skew: float    # R2: dollar-weighted fraction on this direction (0..1)
    trader_count: int               # distinct top-N traders on this direction
    aggregate_usdc: float           # sum of current_value for those traders' positions
    avg_portfolio_fraction: float   # mean of (position_current_value / portfolio_total)

    current_price: float | None     # latest cur_price observed across the involved positions
    first_top_trader_first_seen_at: datetime | None  # earliest first_seen_at -- proxy entry time
    avg_entry_price: float | None   # mean avg_price on this direction (cost basis approximation)

    # R3b (Pass 3): which wallet addresses contributed to this signal at fire
    # time. Persisted on first-fire so the exit detector can recompute against
    # the original cohort instead of the current top-N pool (which churns).
    contributing_wallets: tuple[str, ...] = ()


def _row_to_signal(
    r: asyncpg.Record,
    direction: Direction,
    skew: float,
    dollar_skew: float,
) -> Signal:
    contributing = r.get("contributing_wallets") if hasattr(r, "get") else None
    if contributing is None:
        try:
            contributing = r["contributing_wallets"]
        except (KeyError, IndexError):
            contributing = None
    contributing_tuple: tuple[str, ...] = (
        tuple(contributing) if contributing else ()
    )
    return Signal(
        condition_id=r["condition_id"],
        market_question=r["question"],
        market_slug=r["slug"],
        market_category=r["category"],
        event_id=r["event_id"],
        direction=direction,
        direction_skew=skew,
        direction_dollar_skew=dollar_skew,
        trader_count=int(r["trader_count"]),
        aggregate_usdc=float(r["aggregate_usdc"] or 0.0),
        avg_portfolio_fraction=float(r["avg_portfolio_fraction"] or 0.0),
        current_price=float(r["current_price"]) if r["current_price"] is not None else None,
        first_top_trader_first_seen_at=r["earliest_first_seen_at"],
        avg_entry_price=float(r["avg_entry_price"]) if r["avg_entry_price"] is not None else None,
        contributing_wallets=contributing_tuple,
    )


@dataclass(frozen=True)
class SignalDetectionResult:
    """B3: detect_signals_and_watchlist returns both feeds from one DB pass.

    `official` and `watchlist` are mutually exclusive (any (cid, direction)
    that passes the official floors is in `official` and removed from
    `watchlist`).
    """
    official: list[Signal]
    watchlist: list[Signal]


async def detect_signals_and_watchlist(
    conn: asyncpg.Connection,
    mode: RankingMode,
    category: LeaderboardCategory,
    top_n: int,
) -> SignalDetectionResult:
    """One pass over the position aggregation; emit official + watchlist sets.

    Watchlist applies looser floors (≥2 traders, ≥$5k aggregate, same skew),
    then any (cid, direction) that ALSO passes the official floors is removed
    from watchlist (mutual exclusion).
    """
    traders = await rank_traders(conn, mode=mode, category=category, top_n=top_n)
    if not traders:
        return SignalDetectionResult(official=[], watchlist=[])
    wallets = [t.proxy_wallet for t in traders]

    rows = await _aggregate_positions(
        conn,
        wallets=wallets,
        market_category=None if category == "overall" else category,
    )
    if not rows:
        return SignalDetectionResult(official=[], watchlist=[])

    official: list[Signal] = []
    watchlist: list[Signal] = []

    for r in rows:
        direction = _outcome_to_direction(r["outcome"])
        if direction is None:
            continue
        total_traders_in_market = int(r["traders_any_direction"])
        if total_traders_in_market == 0:
            continue

        trader_count = int(r["trader_count"])
        aggregate = float(r["aggregate_usdc"] or 0.0)
        # R2: dual-axis skew. Headcount ratio + dollar ratio. Both must
        # clear floor for the signal to fire.
        total_dollars_in_market = float(r["total_dollars_in_market"] or 0.0)
        skew = trader_count / total_traders_in_market
        dollar_skew = (
            aggregate / total_dollars_in_market
            if total_dollars_in_market > 0 else 0.0
        )

        # Below the watchlist threshold on EITHER axis -> drop entirely
        if (skew < WATCHLIST_MIN_NET_DIRECTION_SKEW
                or dollar_skew < WATCHLIST_MIN_NET_DIRECTION_DOLLAR_SKEW):
            continue

        passes_official = (
            trader_count >= MIN_TRADER_COUNT
            and aggregate >= MIN_AGGREGATE_USDC
            and skew >= MIN_NET_DIRECTION_SKEW
            and dollar_skew >= MIN_NET_DIRECTION_DOLLAR_SKEW
        )
        passes_watchlist = (
            trader_count >= WATCHLIST_MIN_TRADER_COUNT
            and aggregate >= WATCHLIST_MIN_AGGREGATE_USDC
        )

        if passes_official:
            official.append(_row_to_signal(r, direction, skew, dollar_skew))
        elif passes_watchlist:
            watchlist.append(_row_to_signal(r, direction, skew, dollar_skew))

    official.sort(key=lambda s: s.direction_skew, reverse=True)
    watchlist.sort(key=lambda s: s.direction_skew, reverse=True)
    return SignalDetectionResult(official=official, watchlist=watchlist)


async def detect_signals(
    conn: asyncpg.Connection,
    mode: RankingMode,
    category: LeaderboardCategory,
    top_n: int,
) -> list[Signal]:
    """Active signal set for one UI selection. Returns only the OFFICIAL feed.

    Sorted by direction_skew descending (strongest consensus first). For the
    looser watchlist tier (B3), call `detect_signals_and_watchlist` instead.
    """
    res = await detect_signals_and_watchlist(conn, mode=mode, category=category, top_n=top_n)
    return res.official


async def _aggregate_positions(
    conn: asyncpg.Connection,
    wallets: list[str],
    market_category: str | None,
) -> list[asyncpg.Record]:
    """For the given wallet pool, aggregate open positions by (market, direction).

    Joins markets/events for filter + display fields, and computes each trader's
    portfolio fraction by joining the latest portfolio_value_snapshots row.

    Returns rows like:
      condition_id, outcome, question, slug, category, event_id,
      trader_count (this direction), aggregate_usdc, avg_portfolio_fraction,
      traders_any_direction (across both YES and NO of the market),
      current_price, earliest_first_seen_at, avg_entry_price.
    """
    sql = """
    WITH wallet_pool AS (
        SELECT proxy_wallet
        FROM unnest($1::TEXT[]) AS proxy_wallet
    ),
    -- Map each wallet to its sybil-cluster identity (defaults to wallet itself
    -- if not in any cluster). Used so the trader_count below counts entities,
    -- not raw wallets — Théo's 4 wallets count as 1.
    wallet_identity AS (
        SELECT
            w.proxy_wallet,
            COALESCE(cm.cluster_id::text, w.proxy_wallet) AS identity
        FROM wallet_pool w
        LEFT JOIN cluster_membership cm USING (proxy_wallet)
    ),
    -- Latest portfolio value per wallet, REQUIRED to be fresh.
    -- R5 (Pass 3): pre-fix had no recency filter, so a wallet that briefly
    -- went flat (no positions) and stopped getting a new PV row would keep
    -- returning a weeks-old portfolio value. Their next $20k position then
    -- got divided by an obsolete denominator -- the headline avg_portfolio_
    -- fraction metric was lying on those wallets. Now: only PV rows from
    -- the last hour count; pair this with jobs.py always-write-PV (so a
    -- wallet that goes flat still gets a fresh row with their cash value).
    latest_pv AS (
        SELECT DISTINCT ON (proxy_wallet)
            proxy_wallet, value AS portfolio_value
        FROM portfolio_value_snapshots
        WHERE proxy_wallet IN (SELECT proxy_wallet FROM wallet_pool)
          AND fetched_at >= NOW() - INTERVAL '1 hour'
        ORDER BY proxy_wallet, fetched_at DESC
    ),
    -- Tracked positions for the pool, joined to market/event for filter/display.
    -- TTL filter (last_updated_at >= NOW() - 20min) excludes stale positions
    -- from failed/skipped fetches — covers ~2 cycles of slack. Without this,
    -- a wallet whose fetch failed mid-cycle would contribute its OLD positions
    -- as if they were live, producing phantom signals.
    pool_positions AS (
        SELECT
            p.proxy_wallet, wi.identity, p.condition_id, p.outcome, p.size,
            p.cur_price, p.avg_price, p.current_value, p.first_seen_at,
            m.question, m.slug, m.event_id,
            e.category,
            COALESCE(pv.portfolio_value, 0)::numeric AS portfolio_value
        FROM positions p
        JOIN wallet_identity wi USING (proxy_wallet)
        JOIN markets m ON m.condition_id = p.condition_id
        LEFT JOIN events e ON e.id = m.event_id
        LEFT JOIN latest_pv pv ON pv.proxy_wallet = p.proxy_wallet
        WHERE m.closed = FALSE
          AND p.size > 0
          AND p.last_updated_at >= NOW() - INTERVAL '20 minutes'
          AND ($2::TEXT IS NULL OR e.category = $2::TEXT)
          -- "Effectively resolved" filter: drop markets that Polymarket hasn't
          -- formally closed but are dead in practice (waiting on UMA, sat at
          -- the price extreme for weeks). Two heuristics, OR'd:
          --   (a) end_date passed by 7+ days regardless of `closed` flag
          --   (b) cur_price outside [0.02, 0.92] — no tradeable depth, no edge.
          -- Both sides of a binary fail (b) together because YES at 0.99 ↔ NO at 0.01.
          AND (m.end_date IS NULL OR m.end_date >= NOW() - INTERVAL '7 days')
          AND (p.cur_price IS NULL OR p.cur_price BETWEEN 0.02 AND 0.92)
    ),
    -- Pass 5 #1: identity-collapse the per-wallet positions before the
    -- per-direction aggregate. A 4-wallet sybil cluster with $20k on each
    -- wallet collapses to one row per (identity, market, outcome) with
    -- current_value = $80k. Downstream aggregations now operate on entity-
    -- level rows, so:
    --   - avg_portfolio_fraction is per-ENTITY (cluster's total $$ vs
    --     cluster's max wallet portfolio_value), not per-wallet -- the
    --     cluster's real "% of capital deployed" instead of the dilution
    --     of averaging across its sybils.
    --   - avg_entry_price is identity-weighted (size-weighted across
    --     identities), so the cost basis attributes one weight per entity
    --     rather than one per wallet -- mathematically equivalent for
    --     pure size-weighted averaging because it factors associatively.
    --   - aggregate_usdc and total_dollars_in_market are unchanged
    --     numerically (sum across wallets == sum across identity-summed
    --     wallets), but conceptually attributed at entity level so they
    --     stay consistent with the COUNT(DISTINCT identity) logic.
    -- portfolio_value uses MAX rather than SUM at the identity level on
    -- the assumption that sybil wallets often share funding (SUM would
    -- double-count); MAX gives the upper-bound on entity capital.
    identity_positions AS (
        SELECT
            identity, condition_id, outcome,
            SUM(current_value)                          AS current_value,
            SUM(size)                                   AS size,
            AVG(cur_price)                              AS cur_price,
            MIN(first_seen_at)                          AS first_seen_at,
            CASE WHEN SUM(size) > 0
                 THEN SUM(avg_price * size) / SUM(size)
                 ELSE NULL
            END                                         AS avg_entry_price,
            MAX(portfolio_value)                        AS portfolio_value,
            ANY_VALUE(question)                         AS question,
            ANY_VALUE(slug)                             AS slug,
            ANY_VALUE(category)                         AS category,
            ANY_VALUE(event_id)                         AS event_id
        FROM pool_positions
        GROUP BY identity, condition_id, outcome
    ),
    -- R3b (Pass 3): contributing wallet addresses per (cid, direction)
    -- so the exit detector can recompute against the original cohort.
    -- Computed from raw pool_positions (not identity_positions) so the
    -- output is the underlying wallet list -- the exit detector resolves
    -- clusters at recompute time via cluster_membership.
    direction_wallets AS (
        SELECT
            condition_id, outcome,
            ARRAY_AGG(DISTINCT proxy_wallet ORDER BY proxy_wallet)
                AS contributing_wallets
        FROM pool_positions
        GROUP BY condition_id, outcome
    ),
    -- Per (market, direction) totals -- one input row per identity, so
    -- every aggregate counts entities not wallets. avg_entry_price is
    -- size-weighted across identities (still single-position-dominant).
    direction_agg AS (
        SELECT
            condition_id, outcome,
            ANY_VALUE(question)            AS question,
            ANY_VALUE(slug)                AS slug,
            ANY_VALUE(category)            AS category,
            ANY_VALUE(event_id)            AS event_id,
            COUNT(DISTINCT identity)       AS trader_count,
            SUM(current_value)             AS aggregate_usdc,
            AVG(CASE WHEN portfolio_value > 0 THEN current_value / portfolio_value ELSE NULL END)
                                            AS avg_portfolio_fraction,
            AVG(cur_price)                 AS current_price,
            MIN(first_seen_at)             AS earliest_first_seen_at,
            CASE WHEN SUM(size) > 0
                 THEN SUM(avg_entry_price * size) / SUM(size)
                 ELSE NULL
            END                            AS avg_entry_price
        FROM identity_positions
        GROUP BY condition_id, outcome
    ),
    -- F17: Total distinct identities per market across YES/NO outcomes only
    -- (headcount denominator for skew). Pre-fix counted across EVERY outcome,
    -- so stray non-YES/NO position rows on a binary market inflated the
    -- denominator and legitimate signals fell below the threshold.
    -- R2 (Pass 3): also expose total_dollars_in_market for dollar-weighted
    -- skew. Pass 5 #1: rows here are per-identity not per-wallet so the
    -- denominator counts entities consistently with the numerator.
    market_totals AS (
        SELECT
            condition_id,
            COUNT(DISTINCT identity)         AS traders_any_direction,
            SUM(current_value)               AS total_dollars_in_market
        FROM identity_positions
        WHERE LOWER(outcome) IN ('yes', 'no')
        GROUP BY condition_id
    )
    SELECT
        d.condition_id, d.outcome, d.question, d.slug, d.category, d.event_id,
        d.trader_count, d.aggregate_usdc, d.avg_portfolio_fraction,
        d.current_price, d.earliest_first_seen_at, d.avg_entry_price,
        dw.contributing_wallets,
        m.traders_any_direction,
        m.total_dollars_in_market
    FROM direction_agg d
    JOIN direction_wallets dw USING (condition_id, outcome)
    JOIN market_totals m ON m.condition_id = d.condition_id
    ORDER BY d.aggregate_usdc DESC
    """
    return await conn.fetch(sql, wallets, market_category)
