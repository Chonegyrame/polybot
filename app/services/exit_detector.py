"""Smart-money exit detector (B1) -- Pass 3 rewrite for R3a + R3b + R3c.

For every signal in `signal_log` whose underlying market is still open AND
at least one of its ORIGINAL contributing wallets still holds a position,
recompute the CURRENT trader_count + aggregate_usdc using the historical
cohort (not the current top-N pool), and classify the drop:

  TRIM event (>=20% drop, <50% drop on either metric):
    Notification only -- paper trades stay open. Captures the
    "smart money is taking profit but still holds material position"
    case that pre-fix was misclassified as a full exit.

  EXIT event (>=50% drop on either metric):
    Notification + auto-close paper trades at current bid. The
    "smart money truly fled" event.

Pass 3 changes (R3a + R3b + R3c):

  R3a: split single ">=30% drop" into two-tier (TRIM 20-50%, EXIT >=50%).
       Migration 013 added signal_exits.event_type ('trim' | 'exit').
       paper-trade auto-close runs ONLY for EXIT, not TRIM.

  R3b: recompute aggregates against the HISTORICAL contributing wallets
       (signal_log.contributing_wallets, populated at fire time -- migration
       011), not the current top-N pool. Pre-fix used the current pool, so
       a wallet that briefly fell off top-N had its positions "deleted" by
       the dropout sweep (R13 also relevant here), causing false exit fires.
       With this fix, falling-off-top-N is invisible to exit detection.

  R3c: replace the F18 "activity guard" (last_seen_at >= 2h) which silently
       suppressed real exits when signals dropped below detection floors.
       New rule: track a signal as long as the market is open AND at least
       one original contributor still holds a position there. When all
       original contributors have closed -> that IS the exit, fire it now.

Exits are durable rows in `signal_exits` keyed by `signal_log_id` UNIQUE --
once a signal has its first exit row written (TRIM or EXIT), subsequent
recomputes are no-ops until the row gets purged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg

log = logging.getLogger(__name__)

# Window cap (still bounded to last 24h to avoid evaluating ancient signals).
EXIT_WINDOW_HOURS = 24

# R3a thresholds. TRIM applies on either metric in [TRIM_THRESHOLD, EXIT_THRESHOLD).
# Pass 5 #4: raised 0.20 -> 0.25 to leave a one-wallet noise buffer at n=5
# (the cohort floor for an official signal). Pre-fix a 5-wallet cohort
# would fire TRIM on a single wallet aging past the 30-min TTL during a
# transient API blip -- a 20% drop on trader_count looked like
# "smart money trimming" but no real exit had happened. 25% requires
# at least 2 of 5 wallets to actually go flat before TRIM fires.
# EXIT applies on either metric >= EXIT_THRESHOLD.
TRIM_THRESHOLD = 0.25
EXIT_THRESHOLD = 0.50

DropReason = Literal["trader_count", "aggregate", "both"]
EventType = Literal["trim", "exit"]


@dataclass(frozen=True)
class ExitEvent:
    """One detected trim or exit. Mirrors the columns of the signal_exits row."""
    signal_log_id: int
    condition_id: str
    direction: str  # "YES" or "NO"
    exit_trader_count: int
    peak_trader_count: int
    exit_aggregate_usdc: float
    peak_aggregate_usdc: float
    drop_reason: DropReason
    event_type: EventType  # 'trim' (notification only) or 'exit' (auto-closes)


def _classify_drop(
    cur_traders: int, peak_traders: int,
    cur_agg: float, peak_agg: float,
    trim_threshold: float = TRIM_THRESHOLD,
    exit_threshold: float = EXIT_THRESHOLD,
) -> tuple[DropReason, EventType] | None:
    """R3a (Pass 3): two-tier classifier.

    Returns (reason, event_type) or None.

    Pre-fix returned just the reason as a single-tier "exit at >=30%". Now:
      - drop on trader_count >= exit_threshold OR drop on aggregate >= exit_threshold
        -> EXIT event (auto-closes paper trades)
      - drop on either metric in [trim_threshold, exit_threshold)
        -> TRIM event (notification only)
      - all metrics below trim_threshold -> None

    Pure function; no DB access.
    """
    traders_drop = (
        (peak_traders - cur_traders) / peak_traders if peak_traders > 0 else 0.0
    )
    agg_drop = (peak_agg - cur_agg) / peak_agg if peak_agg > 0 else 0.0

    traders_exit = traders_drop >= exit_threshold
    agg_exit = agg_drop >= exit_threshold
    if traders_exit and agg_exit:
        return ("both", "exit")
    if traders_exit:
        return ("trader_count", "exit")
    if agg_exit:
        return ("aggregate", "exit")

    traders_trim = traders_drop >= trim_threshold
    agg_trim = agg_drop >= trim_threshold
    if traders_trim and agg_trim:
        return ("both", "trim")
    if traders_trim:
        return ("trader_count", "trim")
    if agg_trim:
        return ("aggregate", "trim")
    return None


async def _recompute_one_signal_aggregates_for_cohort(
    conn: asyncpg.Connection,
    contributing_wallets: list[str],
    condition_id: str,
    direction: str,
) -> tuple[int, float]:
    """R3b (Pass 3): recompute current trader_count + aggregate_usdc using
    the HISTORICAL contributing wallets, not the current top-N pool.

    This is the key fix that prevents false exits caused by leaderboard
    churn (a wallet falling from rank 50 to 105 had its positions wiped
    by the dropout sweep, looking like an exit even though the wallet
    still holds the position).

    Returns (trader_count, aggregate_usdc).
    """
    if not contributing_wallets:
        return 0, 0.0
    row = await conn.fetchrow(
        """
        WITH cohort AS (
            SELECT proxy_wallet
            FROM unnest($1::TEXT[]) AS proxy_wallet
        ),
        wallet_identity AS (
            SELECT
                c.proxy_wallet,
                COALESCE(cm.cluster_id::text, c.proxy_wallet) AS identity
            FROM cohort c
            LEFT JOIN cluster_membership cm USING (proxy_wallet)
        ),
        -- Pass 5 #5: collapse positions to one row per identity before
        -- the outer aggregate. The HAVING clause filters identities
        -- whose net direction-side exposure is zero, so an entity that
        -- has fully flattened on this side (regardless of how many of
        -- its wallets are still alive) drops out of the count and the
        -- sum together. Without this, an entity that closed its
        -- position would still appear as `trader_count = 1` if any of
        -- its wallets had stale (>30min) zero-size rows -- the COUNT
        -- and SUM stayed inconsistent. Pre-existing signal_log rows
        -- have peak_aggregate_usdc written with the old raw-wallet SUM;
        -- legacy peak vs identity-collapsed current can differ slightly
        -- on cluster-active markets but the TRIM threshold absorbs it.
        identity_agg AS (
            SELECT
                wi.identity,
                SUM(p.current_value) AS identity_usdc
            FROM positions p
            JOIN wallet_identity wi USING (proxy_wallet)
            JOIN markets m ON m.condition_id = p.condition_id
            WHERE p.condition_id = $2
              AND p.size > 0
              AND p.last_updated_at >= NOW() - INTERVAL '30 minutes'
              AND m.closed = FALSE
              AND UPPER(p.outcome) = UPPER($3)
            GROUP BY wi.identity
            HAVING SUM(p.current_value) > 0
        )
        SELECT
            COUNT(*)::INT                              AS trader_count,
            COALESCE(SUM(identity_usdc), 0)::NUMERIC   AS aggregate_usdc
        FROM identity_agg
        """,
        contributing_wallets, condition_id, direction,
    )
    if not row:
        return 0, 0.0
    return int(row["trader_count"]), float(row["aggregate_usdc"])


async def detect_exits(
    conn: asyncpg.Connection,
    window_hours: int = EXIT_WINDOW_HOURS,
    trim_threshold: float = TRIM_THRESHOLD,
    exit_threshold: float = EXIT_THRESHOLD,
) -> list[ExitEvent]:
    """Find signal_log rows whose current cohort metrics dropped past threshold.

    R3c (Pass 3): no longer uses last_seen_at as a "still being detected"
    proxy. A signal is monitored as long as:
      - market is still open
      - signal hasn't already had an exit row written (UNIQUE on signal_log_id)
      - first_fired_at within EXIT_WINDOW_HOURS (24h cap to avoid evaluating
        ancient signals indefinitely; configurable via window_hours)

    R3b: aggregates are recomputed against signal_log.contributing_wallets
    (populated at fire time, migration 011), NOT against the current top-N
    pool. This means leaderboard churn (a wallet falling off top-N after
    its signal fired) does NOT cause a phantom exit.

    R3a: returns both TRIM (>=20% drop, <50%) and EXIT (>=50% drop) events.
    Caller decides what to do with each (typically: log both, auto-close
    paper trades only on EXIT).
    """
    # R3c: pull all signals whose markets are still open + within window cap.
    # Also pull contributing_wallets (R3b) for the cohort recompute.
    candidates = await conn.fetch(
        """
        SELECT s.id, s.condition_id, s.direction,
               s.peak_trader_count,
               s.peak_aggregate_usdc::numeric AS peak_aggregate_usdc,
               s.contributing_wallets,
               s.first_fired_at
        FROM signal_log s
        JOIN markets m ON m.condition_id = s.condition_id
        LEFT JOIN signal_exits e ON e.signal_log_id = s.id
        WHERE s.first_fired_at >= NOW() - make_interval(hours => $1)
          AND m.closed = FALSE
          AND e.id IS NULL
        """,
        window_hours,
    )

    events: list[ExitEvent] = []
    for r in candidates:
        peak_traders = int(r["peak_trader_count"] or 0)
        peak_agg = float(r["peak_aggregate_usdc"] or 0.0)
        if peak_traders < 5 or peak_agg <= 0:
            # Never met eligibility; not a real exit candidate
            continue

        contributing = list(r["contributing_wallets"] or [])
        # R3b: if we have no historical cohort (legacy pre-Pass-3 row), we
        # can't compute a cohort-aware exit. Skip rather than fall back to
        # current-pool which is exactly what R3b is fixing.
        if not contributing:
            log.debug(
                "exit_detector: signal_log_id=%s has no contributing_wallets "
                "(legacy pre-Pass-3 row?) -- skipping",
                r["id"],
            )
            continue

        cur_traders, cur_agg = await _recompute_one_signal_aggregates_for_cohort(
            conn, contributing, r["condition_id"], r["direction"],
        )
        classified = _classify_drop(
            cur_traders, peak_traders, cur_agg, peak_agg,
            trim_threshold=trim_threshold, exit_threshold=exit_threshold,
        )
        if classified is None:
            continue
        reason, event_type = classified

        events.append(ExitEvent(
            signal_log_id=int(r["id"]),
            condition_id=r["condition_id"],
            direction=r["direction"],
            exit_trader_count=cur_traders,
            peak_trader_count=peak_traders,
            exit_aggregate_usdc=cur_agg,
            peak_aggregate_usdc=peak_agg,
            drop_reason=reason,
            event_type=event_type,
        ))

    return events
