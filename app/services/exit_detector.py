"""Smart-money exit detector (B1).

For every signal in `signal_log` whose `last_seen_at` is within the last
`EXIT_WINDOW_HOURS`, recompute the CURRENT trader_count and aggregate_usdc
(using the same logic as `signal_detector` but only for that one market),
compare against `peak_trader_count` and `peak_aggregate_usdc`, and fire an
exit event when either drops by ≥ EXIT_DROP_THRESHOLD.

Exits are durable rows in `signal_exits` keyed by `signal_log_id` (UNIQUE) —
once a signal exits, subsequent drops are no-ops until the row gets purged
or the signal_log entry is reset.

Why this matters: paper-trade backtests that hold every signal until market
resolution overstate alpha because they ignore that smart money frequently
exits BEFORE resolution. The "follow + exit on smart-money exit" variant —
backtestable via `?exit_strategy=smart_money_exit` — is the honest
counter-strategy. Without B1, you can't compute it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import asyncpg

from app.services.trader_ranker import gather_union_top_n_wallets

log = logging.getLogger(__name__)

# Recompute exits only for signals that have been live in the last 24h.
# Beyond that the signal is stale (we re-emit it on every cycle if conditions
# still hold, so a 24h-old last_seen_at means it's effectively dead anyway).
EXIT_WINDOW_HOURS = 24

# F18: an exit is only actionable if the signal was actively detected recently.
# Pre-fix `last_seen_at` window was just EXIT_WINDOW_HOURS=24 — a signal that
# stopped re-firing 20 hours ago could still emit an exit, even though the
# user had long forgotten about it. Tighter window = exits are emitted only
# while the signal is still on the user's screen.
EXIT_ACTIVITY_GUARD_HOURS = 2

# Fire an exit when either metric drops by at least this fraction from peak.
EXIT_DROP_THRESHOLD = 0.30

DropReason = Literal["trader_count", "aggregate", "both"]


@dataclass(frozen=True)
class ExitEvent:
    """One detected exit. Mirrors the columns of the signal_exits row that
    will be written for it."""
    signal_log_id: int
    condition_id: str
    direction: str  # "YES" or "NO"
    exit_trader_count: int
    peak_trader_count: int
    exit_aggregate_usdc: float
    peak_aggregate_usdc: float
    drop_reason: DropReason


def _classify_drop(
    cur_traders: int, peak_traders: int,
    cur_agg: float, peak_agg: float,
    threshold: float = EXIT_DROP_THRESHOLD,
) -> DropReason | None:
    """Return which metric(s) dropped past the threshold, or None.

    Pure function — no DB access. Easy to unit-test with synthetic numbers.
    """
    traders_dropped = peak_traders > 0 and (peak_traders - cur_traders) / peak_traders >= threshold
    agg_dropped = peak_agg > 0 and (peak_agg - cur_agg) / peak_agg >= threshold
    if traders_dropped and agg_dropped:
        return "both"
    if traders_dropped:
        return "trader_count"
    if agg_dropped:
        return "aggregate"
    return None


async def _recompute_one_signal_aggregates(
    conn: asyncpg.Connection,
    wallets: list[str],
    condition_id: str,
    direction: str,
) -> tuple[int, float]:
    """For a given (cid, direction), count current distinct identities + sum
    of current_value across the tracked wallet pool.

    Mirrors `signal_detector._aggregate_positions` but scoped to a single
    market. Returns (trader_count, aggregate_usdc).
    """
    if not wallets:
        return 0, 0.0
    # Map our_canonical direction back to position.outcome — Polymarket uses
    # "Yes"/"No" but we store canonical "YES"/"NO" on signal_log.
    outcome_filter = direction  # case-insensitive comparison below
    row = await conn.fetchrow(
        """
        WITH wallet_pool AS (
            SELECT proxy_wallet
            FROM unnest($1::TEXT[]) AS proxy_wallet
        ),
        wallet_identity AS (
            SELECT
                w.proxy_wallet,
                COALESCE(cm.cluster_id::text, w.proxy_wallet) AS identity
            FROM wallet_pool w
            LEFT JOIN cluster_membership cm USING (proxy_wallet)
        )
        SELECT
            COUNT(DISTINCT wi.identity)::INT AS trader_count,
            COALESCE(SUM(p.current_value), 0)::NUMERIC AS aggregate_usdc
        FROM positions p
        JOIN wallet_identity wi USING (proxy_wallet)
        JOIN markets m ON m.condition_id = p.condition_id
        WHERE p.condition_id = $2
          AND p.size > 0
          AND p.last_updated_at >= NOW() - INTERVAL '20 minutes'
          AND m.closed = FALSE
          AND UPPER(p.outcome) = UPPER($3)
        """,
        wallets, condition_id, outcome_filter,
    )
    if not row:
        return 0, 0.0
    return int(row["trader_count"]), float(row["aggregate_usdc"])


async def detect_exits(
    conn: asyncpg.Connection,
    tracked_wallets: list[str],
    window_hours: int = EXIT_WINDOW_HOURS,
    threshold: float = EXIT_DROP_THRESHOLD,
) -> list[ExitEvent]:
    """Find all signal_log rows whose current metrics have dropped past
    threshold relative to their peak_* values.

    Skips rows that already have a row in signal_exits (UNIQUE-key dedup) and
    skips rows whose markets are already resolved/closed (no point exiting
    something the user can't trade out of anyway).
    """
    # F18: tighten last_seen_at window to EXIT_ACTIVITY_GUARD_HOURS so we
    # only emit exits for signals that are still actively being detected.
    # The original `window_hours` (24h) caller default produced stale exit
    # notifications for signals the user had moved past hours ago.
    activity_guard = min(window_hours, EXIT_ACTIVITY_GUARD_HOURS)
    candidates = await conn.fetch(
        """
        SELECT s.id, s.condition_id, s.direction,
               s.peak_trader_count,
               s.peak_aggregate_usdc::numeric AS peak_aggregate_usdc
        FROM signal_log s
        JOIN markets m ON m.condition_id = s.condition_id
        LEFT JOIN signal_exits e ON e.signal_log_id = s.id
        WHERE s.last_seen_at >= NOW() - make_interval(hours => $1)
          AND m.closed = FALSE
          AND e.id IS NULL
        """,
        activity_guard,
    )

    events: list[ExitEvent] = []
    for r in candidates:
        peak_traders = int(r["peak_trader_count"] or 0)
        peak_agg = float(r["peak_aggregate_usdc"] or 0.0)
        if peak_traders < 5 or peak_agg <= 0:
            continue  # never met eligibility; not a real exit candidate

        cur_traders, cur_agg = await _recompute_one_signal_aggregates(
            conn, tracked_wallets, r["condition_id"], r["direction"],
        )
        reason = _classify_drop(cur_traders, peak_traders, cur_agg, peak_agg, threshold)
        if reason is None:
            continue

        events.append(ExitEvent(
            signal_log_id=int(r["id"]),
            condition_id=r["condition_id"],
            direction=r["direction"],
            exit_trader_count=cur_traders,
            peak_trader_count=peak_traders,
            exit_aggregate_usdc=cur_agg,
            peak_aggregate_usdc=peak_agg,
            drop_reason=reason,
        ))

    return events
