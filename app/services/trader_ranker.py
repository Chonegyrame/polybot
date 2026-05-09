"""Trader ranker — turns leaderboard snapshots into a ranked top-N list per
(mode, category, top_n) selection.

Two modes (locked decisions in CLAUDE.md / UI-SPEC.md):

  Absolute PnL
    Rank ALL traders in the category by lifetime dollar profit. No filters.
    Lets through low-frequency, big-size traders.

  Hybrid (PnL + ROI)
    Filter to traders with cumulative volume >= HYBRID_MIN_VOLUME.
    Rank that pool twice: by PnL desc and by ROI desc (ROI = pnl/vol).
    Sort by the average of the two ranks (lowest combined rank = best).
    Excludes lucky one-shot wonders and tiny accounts.

We always read the latest snapshot for `time_period = 'all'` and `order_by = 'PNL'`.
Each row carries both pnl and vol so a single source row supports both modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

import asyncpg

from app.services.polymarket import LeaderboardCategory

log = logging.getLogger(__name__)

RankingMode = Literal["absolute", "hybrid", "specialist"]

HYBRID_MIN_VOLUME = 5_000.0       # USDC; floor for Hybrid mode
SPECIALIST_MIN_VOLUME = 20_000.0  # USDC; floor for Specialist mode (tighter)

# B5 recency + sample-size + shrinkage parameters
RECENCY_MAX_DAYS = 60             # absolute & hybrid: drop traders inactive >this
SPECIALIST_MIN_RESOLVED_TRADES = 30
BAYESIAN_K_USDC = 50_000.0        # shrinkage prior: equivalent volume at category mean

# SQL fragment shared by both ranking modes — excludes wallets the classifier
# has flagged as market_maker/arbitrage/likely_sybil. Wallets not yet
# classified pass through (NULL is not in the set).
_EXCLUDE_CONTAMINATED_SQL = """
    AND t.proxy_wallet NOT IN (
        SELECT proxy_wallet FROM wallet_classifications
        WHERE wallet_class IN ('market_maker','arbitrage','likely_sybil')
    )
"""


@dataclass(frozen=True)
class RankedTrader:
    """One trader after our local ranking — what the signal detector consumes."""

    rank: int                        # 1-based final rank in the chosen mode
    proxy_wallet: str
    user_name: str | None
    verified_badge: bool
    pnl: float
    vol: float
    roi: float                       # pnl / vol (0 if vol is 0)
    pnl_rank: int                    # rank by PnL within the filtered pool
    roi_rank: int | None             # rank by ROI; None for Absolute mode


async def _latest_snapshot_date_for(
    conn: asyncpg.Connection, category: LeaderboardCategory
) -> date | None:
    row = await conn.fetchrow(
        """
        SELECT MAX(snapshot_date) AS d
        FROM leaderboard_snapshots
        WHERE category = $1 AND time_period = 'all' AND order_by = 'PNL'
        """,
        category,
    )
    return row["d"] if row and row["d"] else None


async def _record_stats_staleness_if_needed(conn: asyncpg.Connection) -> None:
    """Pass 5 #6: record STATS_STALE counter when stats are seeded but
    >7 days old (= the nightly trader-stats job is stuck).

    Best-effort. The freshness check is a single-row SELECT; if it fails
    we swallow the error rather than blocking the ranker -- the SQL gate
    inside each ranker still bypasses correctly on staleness, this is
    purely the operator-visible signal.
    """
    try:
        from app.db import crud
        from app.services import health_counters

        freshness = await crud.get_stats_freshness(conn)
        if freshness.get("seeded") and not freshness.get("fresh"):
            health_counters.record(health_counters.STATS_STALE)
            log.warning(
                "stats_stale: trader_category_stats last_trade_at is older "
                "than %d days (last_refresh=%s) -- nightly job may be dead",
                crud.STATS_FRESHNESS_MAX_DAYS,
                freshness.get("last_refresh"),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("stats-staleness probe failed: %s", e)


async def rank_traders(
    conn: asyncpg.Connection,
    mode: RankingMode,
    category: LeaderboardCategory,
    top_n: int,
    snapshot_date: date | None = None,
) -> list[RankedTrader]:
    """Return the top-N ranked traders for the (mode, category, top_n) selection.

    Uses the most recent snapshot unless `snapshot_date` is given explicitly.
    Returns an empty list (not an error) if no snapshot exists yet.
    """
    if top_n <= 0:
        return []

    target_date = snapshot_date or await _latest_snapshot_date_for(conn, category)
    if target_date is None:
        log.warning("no snapshot available for category=%s — returning empty", category)
        return []

    # Pass 5 #6: detect-and-record stats staleness so the operator sees it
    # in /system/status. The SQL gate inside each ranker independently
    # bypasses the recency filter -- this Python-side check is purely
    # observability.
    await _record_stats_staleness_if_needed(conn)

    if mode == "absolute":
        rows = await _rank_absolute(conn, target_date, category, top_n)
    elif mode == "hybrid":
        rows = await _rank_hybrid(conn, target_date, category, top_n)
    elif mode == "specialist":
        rows = await _rank_specialist(conn, target_date, category, top_n)
    else:  # pragma: no cover — Literal narrows this in callers
        raise ValueError(f"unknown ranking mode: {mode!r}")

    return [_row_to_ranked(r, mode) for r in rows]


async def _rank_absolute(
    conn: asyncpg.Connection,
    snapshot_date: date,
    category: LeaderboardCategory,
    top_n: int,
) -> list[asyncpg.Record]:
    """Top-N by lifetime PnL within the category, with MM/arb wallets excluded.

    B5: applies a recency filter — drop traders whose `last_trade_at` (across
    ALL categories, i.e. the 'overall' row) is older than RECENCY_MAX_DAYS.
    Bootstrap-safe: if trader_category_stats is empty, the filter is a no-op
    (rather than silently excluding everyone).
    """
    return await conn.fetch(
        f"""
        WITH stats_seeded AS (
            SELECT EXISTS (SELECT 1 FROM trader_category_stats LIMIT 1) AS has_data
        ),
        -- Pass 5 #6: freshness gate. If the nightly trader-stats job dies,
        -- every row's last_trade_at ages past the recency threshold and
        -- the recency filter would silently exclude every wallet. Once
        -- stats are >7 days stale, behave as if not yet seeded -- skip
        -- the recency filter. The Python wrapper records a STATS_STALE
        -- health counter when this triggers so the operator sees it.
        stats_fresh AS (
            SELECT (
                COALESCE(MAX(last_trade_at), 'epoch'::TIMESTAMPTZ)
                >= NOW() - INTERVAL '7 days'
            ) AS is_fresh
            FROM trader_category_stats
        ),
        base AS (
            SELECT
                t.proxy_wallet,
                t.user_name,
                t.verified_badge,
                ls.pnl,
                ls.vol,
                CASE WHEN ls.vol > 0 THEN ls.pnl / ls.vol ELSE 0 END AS roi
            FROM leaderboard_snapshots ls
            JOIN traders t USING (proxy_wallet)
            LEFT JOIN trader_category_stats tcs
                   ON tcs.proxy_wallet = ls.proxy_wallet
                  AND tcs.category = 'overall'
            CROSS JOIN stats_seeded
            CROSS JOIN stats_fresh
            WHERE ls.snapshot_date = $1
              AND ls.category = $2
              AND ls.time_period = 'all'
              AND ls.order_by = 'PNL'
              -- Recency filter: skip when stats not yet seeded OR when
              -- seeded-but-stale (Pass 5 #6); otherwise enforce.
              AND (
                  NOT stats_seeded.has_data
                  OR NOT stats_fresh.is_fresh
                  OR tcs.last_trade_at >= NOW() - make_interval(days => $4)
              )
              {_EXCLUDE_CONTAMINATED_SQL}
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY pnl DESC, proxy_wallet ASC) AS rank,
            proxy_wallet, user_name, verified_badge, pnl, vol, roi,
            ROW_NUMBER() OVER (ORDER BY pnl DESC, proxy_wallet ASC) AS pnl_rank
        FROM base
        ORDER BY pnl DESC, proxy_wallet ASC
        LIMIT $3
        """,
        snapshot_date,
        category,
        top_n,
        RECENCY_MAX_DAYS,
    )


async def _rank_hybrid(
    conn: asyncpg.Connection,
    snapshot_date: date,
    category: LeaderboardCategory,
    top_n: int,
) -> list[asyncpg.Record]:
    """Top-N by rank-average of (PnL rank, ROI rank) within the eligible pool.

    Eligibility: vol >= HYBRID_MIN_VOLUME + recency filter (overall last_trade_at
    within RECENCY_MAX_DAYS). ROI is Bayesian-shrunk toward the pool's average
    PnL, so a 3-trade 40% ROI trader doesn't outrank a 200-trade 36% ROI trader.

    Lower combined rank wins.
    """
    return await conn.fetch(
        f"""
        WITH stats_seeded AS (
            SELECT EXISTS (SELECT 1 FROM trader_category_stats LIMIT 1) AS has_data
        ),
        stats_fresh AS (
            -- Pass 5 #6: see _rank_absolute for rationale.
            SELECT (
                COALESCE(MAX(last_trade_at), 'epoch'::TIMESTAMPTZ)
                >= NOW() - INTERVAL '7 days'
            ) AS is_fresh
            FROM trader_category_stats
        ),
        base AS (
            SELECT
                t.proxy_wallet,
                t.user_name,
                t.verified_badge,
                ls.pnl,
                ls.vol
            FROM leaderboard_snapshots ls
            JOIN traders t USING (proxy_wallet)
            LEFT JOIN trader_category_stats tcs
                   ON tcs.proxy_wallet = ls.proxy_wallet
                  AND tcs.category = 'overall'
            CROSS JOIN stats_seeded
            CROSS JOIN stats_fresh
            WHERE ls.snapshot_date = $1
              AND ls.category = $2
              AND ls.time_period = 'all'
              AND ls.order_by = 'PNL'
              AND ls.vol >= $3
              AND (
                  NOT stats_seeded.has_data
                  OR NOT stats_fresh.is_fresh
                  OR tcs.last_trade_at >= NOW() - make_interval(days => $5)
              )
              {_EXCLUDE_CONTAMINATED_SQL}
        ),
        cat_avg AS (
            -- F1: Bayesian prior is the pool's ROI rate (sum_pnl / sum_vol),
            -- NOT AVG(pnl). The shrinkage formula expects a dimensionless
            -- rate; using a dollar quantity here used to make small-vol
            -- traders' shrunk_roi explode and rank #1 regardless of skill.
            SELECT COALESCE(SUM(pnl)::NUMERIC / NULLIF(SUM(vol), 0), 0)
                   AS prior_roi FROM base
        ),
        shrunk AS (
            SELECT
                b.*,
                CASE WHEN b.vol > 0 THEN b.pnl / b.vol ELSE 0 END AS roi,
                -- (pnl + k*prior_roi) / (vol + k) — pulls outliers toward
                -- the pool ROI; large-sample winners barely move.
                (b.pnl + $6 * COALESCE(c.prior_roi, 0))
                  / NULLIF(b.vol + $6, 0) AS shrunk_roi
            FROM base b
            CROSS JOIN cat_avg c
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (ORDER BY pnl DESC, proxy_wallet ASC) AS pnl_rank,
                ROW_NUMBER() OVER (ORDER BY shrunk_roi DESC NULLS LAST, proxy_wallet ASC) AS roi_rank
            FROM shrunk
        )
        SELECT
            ROW_NUMBER() OVER (
                -- R11 (Pass 3): tiebreak on roi_rank (skill) instead of
                -- pnl DESC (whale bias). Pre-fix re-introduced the
                -- whale-bias that Hybrid mode exists to dampen.
                ORDER BY (pnl_rank + roi_rank) ASC, roi_rank ASC, proxy_wallet ASC
            ) AS rank,
            proxy_wallet, user_name, verified_badge, pnl, vol, roi,
            pnl_rank, roi_rank
        FROM ranked
        ORDER BY (pnl_rank + roi_rank) ASC, roi_rank ASC, proxy_wallet ASC
        LIMIT $4
        """,
        snapshot_date,
        category,
        HYBRID_MIN_VOLUME,
        top_n,
        RECENCY_MAX_DAYS,
        BAYESIAN_K_USDC,
    )


async def _rank_specialist(
    conn: asyncpg.Connection,
    snapshot_date: date,
    category: LeaderboardCategory,
    top_n: int,
) -> list[asyncpg.Record]:
    """Top-N category specialists.

    A specialist is a trader who has SUSTAINED per-category presence (≥$20k
    volume), POSITIVE per-category PnL (net winner), and RECENT activity
    (appears in the latest 'month' leaderboard for the category). Ranked by
    ROI = pnl/vol descending — surfaces small-bankroll, high-conviction
    traders that absolute (whales-only) and hybrid (rank-averaged globally)
    structurally miss.

    Uses ONLY data we already collect in `leaderboard_snapshots` — no extra
    API calls or nightly batch needed.
    """
    return await conn.fetch(
        f"""
        WITH stats_seeded AS (
            SELECT EXISTS (SELECT 1 FROM trader_category_stats LIMIT 1) AS has_data
        ),
        stats_fresh AS (
            -- Pass 5 #6: see _rank_absolute for rationale. When stats are
            -- seeded but stale (>7 days), the B5 sample-size floor and the
            -- F9 last_trade_at gate both bypass -- the alternative is
            -- silently returning [] forever until the operator notices.
            SELECT (
                COALESCE(MAX(last_trade_at), 'epoch'::TIMESTAMPTZ)
                >= NOW() - INTERVAL '7 days'
            ) AS is_fresh
            FROM trader_category_stats
        ),
        active_recently AS (
            -- Wallets present in the most recent monthly per-category leaderboard
            SELECT DISTINCT proxy_wallet
            FROM leaderboard_snapshots
            WHERE category = $1
              AND time_period = 'month'
              AND order_by = 'PNL'
              AND snapshot_date = $2
        ),
        -- Pass 5 #3: honest category baseline for the Bayesian shrinkage
        -- target. Pre-fix `cat_avg` was computed from `base`, which is
        -- restricted to PnL>0 winners + active_recently + resolved_trades
        -- floor. So the prior the shrinkage pulled toward was the average
        -- ROI of qualifying winners -- structurally inflated. Lucky tiny-
        -- volume traders got promoted (the F1 bug, relocated here).
        --
        -- prior_pool drops the candidate-restricting filters and keeps
        -- only the per-snapshot data-quality filters: same date / category
        -- / time_period / order_by, the specialist volume floor (defines
        -- the specialist-eligible universe), and contamination exclusion
        -- (MM/arb/sybils don't represent the population we want to shrink
        -- toward). pnl>0, active_recently, resolved_trades floor, and the
        -- F9 last_trade_at gate are all dropped here.
        prior_pool AS (
            SELECT ls.pnl, ls.vol
            FROM leaderboard_snapshots ls
            JOIN traders t USING (proxy_wallet)
            WHERE ls.snapshot_date = $2
              AND ls.category = $1
              AND ls.time_period = 'all'
              AND ls.order_by = 'PNL'
              AND ls.vol >= $3
              {_EXCLUDE_CONTAMINATED_SQL}
        ),
        base AS (
            SELECT
                t.proxy_wallet,
                t.user_name,
                t.verified_badge,
                ls.pnl,
                ls.vol,
                COALESCE(tcs.resolved_trades, 0) AS resolved_trades
            FROM leaderboard_snapshots ls
            JOIN traders t USING (proxy_wallet)
            LEFT JOIN trader_category_stats tcs
                   ON tcs.proxy_wallet = ls.proxy_wallet
                  AND tcs.category = ls.category
            CROSS JOIN stats_seeded
            CROSS JOIN stats_fresh
            WHERE ls.snapshot_date = $2
              AND ls.category = $1
              AND ls.time_period = 'all'
              AND ls.order_by = 'PNL'
              AND ls.vol >= $3
              AND ls.pnl > 0
              -- Pass 6: drop the monthly-leaderboard recency cut when
              -- trader_category_stats is seeded AND fresh -- the F9
              -- last_trade_at gate below provides a proper 60d recency check
              -- with much better coverage than Polymarket's 30-day monthly
              -- leaderboard slice (which capped pool sizes at 7-20 per
              -- category). Until stats seed, fall back to the old
              -- monthly-presence filter so Specialist never runs without
              -- ANY recency check.
              AND (
                  (stats_seeded.has_data AND stats_fresh.is_fresh)
                  OR ls.proxy_wallet IN (SELECT proxy_wallet FROM active_recently)
              )
              -- B5 sample-size floor: only enforce once stats are seeded
              -- AND fresh (Pass 5 #6).
              AND (
                  NOT stats_seeded.has_data
                  OR NOT stats_fresh.is_fresh
                  OR COALESCE(tcs.resolved_trades, 0) >= $5
              )
              -- F9: also enforce the same per-category recency check that
              -- gather_union_top_n_wallets uses (overall last_trade_at within
              -- RECENCY_MAX_DAYS via $7). Pre-fix Specialist relied only on
              -- monthly-leaderboard presence, which let traders qualify on
              -- one huge old trade dominating the monthly view. Layering the
              -- last_trade_at filter on top tightens correctness without
              -- removing the monthly-presence requirement. Pass 5 #6:
              -- bypassed when stats are stale (the EXISTS clause would
              -- structurally fail).
              AND (
                  NOT stats_seeded.has_data
                  OR NOT stats_fresh.is_fresh
                  OR EXISTS (
                      SELECT 1 FROM trader_category_stats tcs2
                      WHERE tcs2.proxy_wallet = ls.proxy_wallet
                        AND tcs2.category = 'overall'
                        AND tcs2.last_trade_at >= NOW() - make_interval(days => $7)
                  )
              )
              {_EXCLUDE_CONTAMINATED_SQL}
        ),
        cat_avg AS (
            -- F1 + Pass 5 #3: ROI prior over the FULL specialist-eligible
            -- pool (winners and losers alike), not over `base` (winners
            -- only). See prior_pool comment above.
            SELECT COALESCE(SUM(pnl)::NUMERIC / NULLIF(SUM(vol), 0), 0)
                   AS prior_roi FROM prior_pool
        ),
        shrunk AS (
            SELECT
                b.*,
                CASE WHEN b.vol > 0 THEN b.pnl / b.vol ELSE 0 END AS roi,
                -- Bayesian-shrunk ROI for ranking: small-sample winners get
                -- pulled toward the per-category ROI rate; the raw `roi`
                -- column above is preserved for display.
                (b.pnl + $6 * COALESCE(c.prior_roi, 0))
                  / NULLIF(b.vol + $6, 0) AS shrunk_roi
            FROM base b
            CROSS JOIN cat_avg c
        )
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY shrunk_roi DESC NULLS LAST, pnl DESC, proxy_wallet ASC
            ) AS rank,
            proxy_wallet, user_name, verified_badge, pnl, vol, roi,
            ROW_NUMBER() OVER (ORDER BY pnl DESC, proxy_wallet ASC) AS pnl_rank,
            ROW_NUMBER() OVER (
                ORDER BY shrunk_roi DESC NULLS LAST, proxy_wallet ASC
            ) AS roi_rank
        FROM shrunk
        ORDER BY shrunk_roi DESC NULLS LAST, pnl DESC, proxy_wallet ASC
        LIMIT $4
        """,
        category, snapshot_date, SPECIALIST_MIN_VOLUME, top_n,
        SPECIALIST_MIN_RESOLVED_TRADES, BAYESIAN_K_USDC,
        RECENCY_MAX_DAYS,
    )


async def gather_union_top_n_wallets(
    conn: asyncpg.Connection,
    top_n: int,
    categories: tuple[str, ...],
) -> list[str]:
    """Bulk equivalent of looping rank_traders() across (mode, category) and
    de-duping. Returns the union as a sorted wallet list in ONE query.

    Replaces the 21-iteration N+1 (3 modes × 7 categories) the position-refresh
    cycle used to do at start. Same exclusion rules as the per-mode rankers
    (MM/arb/likely_sybil filtered via wallet_classifications) and same
    eligibility floors per mode.

    Each mode's per-category ranking runs as a partitioned ROW_NUMBER over a
    shared `base` CTE, then the three rank lists are UNIONed and DISTINCT'd.
    """
    if top_n <= 0 or not categories:
        return []

    # Pass 5 #6: same staleness detector as `rank_traders`. The SQL gates
    # below already bypass on staleness -- this records the counter so the
    # operator sees it in /system/status. Idempotent (latching counter).
    await _record_stats_staleness_if_needed(conn)

    sql = """
    WITH stats_seeded AS (
        SELECT EXISTS (SELECT 1 FROM trader_category_stats LIMIT 1) AS has_data
    ),
    stats_fresh AS (
        -- Pass 5 #6: bypass recency filter when stats are >7 days stale.
        -- See _rank_absolute for rationale.
        SELECT (
            COALESCE(MAX(last_trade_at), 'epoch'::TIMESTAMPTZ)
            >= NOW() - INTERVAL '7 days'
        ) AS is_fresh
        FROM trader_category_stats
    ),
    latest_per_category AS (
        SELECT category, MAX(snapshot_date) AS d
        FROM leaderboard_snapshots
        WHERE time_period = 'all' AND order_by = 'PNL'
        GROUP BY category
    ),
    contaminated AS (
        SELECT proxy_wallet FROM wallet_classifications
        WHERE wallet_class IN ('market_maker','arbitrage','likely_sybil')
    ),
    active_recently AS (
        SELECT DISTINCT ls.category, ls.proxy_wallet
        FROM leaderboard_snapshots ls
        JOIN (
            SELECT category, MAX(snapshot_date) AS d
            FROM leaderboard_snapshots
            WHERE time_period = 'month' AND order_by = 'PNL'
            GROUP BY category
        ) lm USING (category)
        WHERE ls.time_period = 'month' AND ls.order_by = 'PNL'
          AND ls.snapshot_date = lm.d
    ),
    -- Recent activity per (wallet, category) from trader_category_stats
    recent_overall AS (
        SELECT proxy_wallet
        FROM trader_category_stats
        WHERE category = 'overall'
          AND last_trade_at >= NOW() - make_interval(days => $5)
    ),
    -- Pass 5 #3 (gather_union variant): broader pool for the per-category
    -- shrinkage prior. `base` is recency-filtered (recent_overall), so a
    -- prior computed from base reflects the active subset only. After
    -- this fix, `prior_pool` includes inactive traders too -- the right
    -- baseline because the prior should represent "what does an average
    -- specialist-eligible trader earn?", not "what does an average active
    -- trader earn?". Contamination exclusion stays; vol floor stays.
    -- Mirrors the same pattern applied in _rank_specialist's prior_pool.
    prior_pool AS (
        SELECT
            ls.category,
            ls.pnl,
            ls.vol
        FROM leaderboard_snapshots ls
        JOIN latest_per_category lpc
          ON lpc.category = ls.category AND lpc.d = ls.snapshot_date
        JOIN traders t USING (proxy_wallet)
        WHERE ls.time_period = 'all'
          AND ls.order_by = 'PNL'
          AND ls.category = ANY($1::TEXT[])
          AND ls.proxy_wallet NOT IN (SELECT proxy_wallet FROM contaminated)
    ),
    base AS (
        SELECT
            ls.category,
            t.proxy_wallet,
            ls.pnl,
            ls.vol,
            COALESCE(tcs.resolved_trades, 0) AS resolved_trades,
            CASE WHEN ls.vol > 0 THEN ls.pnl / ls.vol ELSE 0 END AS roi
        FROM leaderboard_snapshots ls
        JOIN latest_per_category lpc
          ON lpc.category = ls.category AND lpc.d = ls.snapshot_date
        JOIN traders t USING (proxy_wallet)
        LEFT JOIN trader_category_stats tcs
               ON tcs.proxy_wallet = ls.proxy_wallet
              AND tcs.category = ls.category
        CROSS JOIN stats_seeded
        CROSS JOIN stats_fresh
        WHERE ls.time_period = 'all'
          AND ls.order_by = 'PNL'
          AND ls.category = ANY($1::TEXT[])
          AND ls.proxy_wallet NOT IN (SELECT proxy_wallet FROM contaminated)
          -- Recency filter on Absolute & Hybrid (overall last-trade-at).
          -- Pass 5 #6: bypassed when stats are seeded-but-stale; otherwise
          -- the entire pool empties out the moment the nightly job dies.
          AND (
              NOT stats_seeded.has_data
              OR NOT stats_fresh.is_fresh
              OR ls.proxy_wallet IN (SELECT proxy_wallet FROM recent_overall)
          )
    ),
    -- F1 + Pass 5 #3: Per-category Bayesian prior for shrinkage. ROI rate
    -- (sum_pnl / sum_vol), and computed from prior_pool (full eligible
    -- universe) rather than `base` (recency-filtered subset).
    cat_avg AS (
        SELECT category,
               COALESCE(SUM(pnl)::NUMERIC / NULLIF(SUM(vol), 0), 0)
                 AS prior_roi
        FROM prior_pool GROUP BY category
    ),
    shrunk AS (
        SELECT b.*,
               (b.pnl + $6 * COALESCE(c.prior_roi, 0))
                 / NULLIF(b.vol + $6, 0) AS shrunk_roi
        FROM base b
        LEFT JOIN cat_avg c USING (category)
    ),
    ranked_absolute AS (
        SELECT category, proxy_wallet,
               ROW_NUMBER() OVER (
                   PARTITION BY category
                   ORDER BY pnl DESC, proxy_wallet ASC
               ) AS rn
        FROM shrunk
    ),
    hybrid_pool AS (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY category ORDER BY pnl DESC, proxy_wallet ASC
               ) AS pnl_rank,
               ROW_NUMBER() OVER (
                   PARTITION BY category
                   ORDER BY shrunk_roi DESC NULLS LAST, proxy_wallet ASC
               ) AS roi_rank
        FROM shrunk
        WHERE vol >= $2
    ),
    ranked_hybrid AS (
        SELECT category, proxy_wallet,
               ROW_NUMBER() OVER (
                   PARTITION BY category
                   -- R11 (Pass 3): tiebreak on roi_rank (skill) not pnl
                   ORDER BY (pnl_rank + roi_rank) ASC, roi_rank ASC, proxy_wallet ASC
               ) AS rn
        FROM hybrid_pool
    ),
    ranked_specialist AS (
        SELECT b.category, b.proxy_wallet,
               ROW_NUMBER() OVER (
                   PARTITION BY b.category
                   ORDER BY b.shrunk_roi DESC NULLS LAST, b.pnl DESC, b.proxy_wallet ASC
               ) AS rn
        FROM shrunk b
        CROSS JOIN stats_seeded
        CROSS JOIN stats_fresh
        WHERE b.vol >= $3 AND b.pnl > 0
          AND EXISTS (
              SELECT 1 FROM active_recently a
              WHERE a.category = b.category AND a.proxy_wallet = b.proxy_wallet
          )
          -- B5 sample-size floor for Specialist (skip until stats seeded
          -- AND fresh, Pass 5 #6).
          AND (
              NOT stats_seeded.has_data
              OR NOT stats_fresh.is_fresh
              OR b.resolved_trades >= $7
          )
    )
    SELECT proxy_wallet FROM (
        SELECT proxy_wallet FROM ranked_absolute   WHERE rn <= $4
        UNION
        SELECT proxy_wallet FROM ranked_hybrid     WHERE rn <= $4
        UNION
        SELECT proxy_wallet FROM ranked_specialist WHERE rn <= $4
    ) u
    GROUP BY proxy_wallet
    ORDER BY proxy_wallet
    """
    rows = await conn.fetch(
        sql,
        list(categories),
        HYBRID_MIN_VOLUME,
        SPECIALIST_MIN_VOLUME,
        top_n,
        RECENCY_MAX_DAYS,
        BAYESIAN_K_USDC,
        SPECIALIST_MIN_RESOLVED_TRADES,
    )
    return [r["proxy_wallet"] for r in rows]


def _row_to_ranked(r: asyncpg.Record, mode: RankingMode) -> RankedTrader:
    return RankedTrader(
        rank=int(r["rank"]),
        proxy_wallet=str(r["proxy_wallet"]),
        user_name=r["user_name"],
        verified_badge=bool(r["verified_badge"]),
        pnl=float(r["pnl"]),
        vol=float(r["vol"]),
        roi=float(r["roi"]) if r["roi"] is not None else 0.0,
        pnl_rank=int(r["pnl_rank"]),
        roi_rank=int(r["roi_rank"]) if mode in ("hybrid", "specialist") else None,
    )
