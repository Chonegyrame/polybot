"""GET /system/status — drives the dashboard's green/amber/red health pill.

Composite check across five subsystems. Overall health = the worst single
component, so an amber pill points the user straight at what's behind on its
SLO. Each component returns enough raw fields that the UI can show a tooltip
explaining "snapshot is 2 days behind".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends

from app.api.deps import get_conn
from app.db import crud
from app.services.health_counters import snapshot as health_counter_snapshot

router = APIRouter(prefix="/system", tags=["system"])

Health = Literal["green", "amber", "red"]

# Position refresh — meant to run every 10 min.
REFRESH_GREEN_MAX_MINUTES = 15
REFRESH_AMBER_MAX_MINUTES = 60

# Snapshot — meant to run daily.
SNAPSHOT_GREEN_MAX_DAYS = 1
SNAPSHOT_AMBER_MAX_DAYS = 3

# Wallet classifier — meant to run weekly. Allow one missed week before alerting.
CLASSIFIER_GREEN_MAX_DAYS = 8
CLASSIFIER_AMBER_MAX_DAYS = 16

# Tracked wallet pool — should never be empty in steady state.
WALLETS_RED_MIN = 1

# Signals fired in the rolling 24h window. Quiet days are normal so amber, not red.
SIGNALS_AMBER_MAX_HOURS = 72  # F25: extended from 48h to 72h to reduce
# weekend / quiet-market false alarms. Polymarket has genuinely quiet
# stretches; an honest "cycle stopped firing because of a bug" signal
# usually shows zero signals for >2 days, so 72h is the right threshold
# to flag real problems without alert fatigue.


_HEALTH_RANK: dict[Health, int] = {"green": 0, "amber": 1, "red": 2}


def _worst(*colors: Health) -> Health:
    return max(colors, key=lambda c: _HEALTH_RANK[c])


def _by_minutes(m: float | None) -> Health:
    if m is None:
        return "red"
    if m <= REFRESH_GREEN_MAX_MINUTES:
        return "green"
    if m <= REFRESH_AMBER_MAX_MINUTES:
        return "amber"
    return "red"


def _by_days(d: int | None, green: int, amber: int) -> Health:
    if d is None:
        return "red"
    if d <= green:
        return "green"
    if d <= amber:
        return "amber"
    return "red"


@router.get("/status")
async def get_status(conn: asyncpg.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Composite system health for the dashboard pill."""
    now = datetime.now(timezone.utc)

    # --- Position refresh ---
    last_refresh = await crud.latest_position_refresh_at(conn)
    minutes_since_refresh = (
        round((now - last_refresh).total_seconds() / 60.0, 1)
        if last_refresh else None
    )
    refresh_health = _by_minutes(minutes_since_refresh)

    # --- Daily snapshot ---
    last_snapshot = await crud.latest_snapshot_date(conn)
    days_since_snapshot = (now.date() - last_snapshot).days if last_snapshot else None
    snapshot_health = _by_days(
        days_since_snapshot, SNAPSHOT_GREEN_MAX_DAYS, SNAPSHOT_AMBER_MAX_DAYS
    )

    # Pass 5 #16: surface the latest snapshot run's completeness state.
    # Operator-visible indicator so a partial run (e.g. 27/28 combos) is
    # caught immediately instead of silently mixing today's incomplete
    # data with yesterday's complete data downstream.
    latest_run = await crud.latest_snapshot_run(conn)
    last_complete_date = await crud.latest_complete_snapshot_date(conn)

    # Pass 5 #6: trader_category_stats freshness. When seeded but stale
    # (>7 days old) the rankers' recency filters bypass automatically and
    # the STATS_STALE health counter ticks; this surface lets the UI show
    # the operator the underlying state.
    stats_freshness = await crud.get_stats_freshness(conn)

    # --- Wallet classifier (weekly) ---
    # F23: extracted to crud.latest_classification_at
    last_classified_at = await crud.latest_classification_at(conn)
    days_since_classifier = (
        (now - last_classified_at).days if last_classified_at else None
    )
    classifier_health = _by_days(
        days_since_classifier, CLASSIFIER_GREEN_MAX_DAYS, CLASSIFIER_AMBER_MAX_DAYS
    )

    # --- Tracked wallet pool size (latest distinct in positions) ---
    tracked_wallets = await crud.count_distinct_wallets_with_positions(conn)
    wallets_health: Health = "red" if tracked_wallets < WALLETS_RED_MIN else "green"

    # --- Signal activity in last SIGNALS_AMBER_MAX_HOURS (F25: 72h) ---
    signals_recent = await crud.count_signals_since(
        conn, now - timedelta(hours=SIGNALS_AMBER_MAX_HOURS),
    )
    signals_health: Health = "green" if signals_recent > 0 else "amber"

    overall = _worst(
        refresh_health, snapshot_health, classifier_health,
        wallets_health, signals_health,
    )

    # D5 (Pass 3): live operational counters surfaced for the UI's
    # health pane. In-memory; reset on process restart.
    counters = health_counter_snapshot()

    return {
        "overall_health": overall,
        "components": {
            "position_refresh": {
                "health": refresh_health,
                "last_at": last_refresh.isoformat() if last_refresh else None,
                "minutes_since": minutes_since_refresh,
            },
            "daily_snapshot": {
                "health": snapshot_health,
                "last_date": last_snapshot.isoformat() if last_snapshot else None,
                "days_since": days_since_snapshot,
                # Pass 5 #16: completeness ledger (snapshot_runs) state.
                # `complete=True` iff the latest run had failed_combos = 0;
                # `last_complete_date` is the most-recent fully-successful
                # run's date (may differ from `last_date` if today's run
                # had partial failures).
                "latest_run": (
                    {
                        "snapshot_date": latest_run["snapshot_date"].isoformat(),
                        "complete": int(latest_run["failed_combos"]) == 0,
                        "total_combos": int(latest_run["total_combos"]),
                        "succeeded_combos": int(latest_run["succeeded_combos"]),
                        "failed_combos": int(latest_run["failed_combos"]),
                        "duration_seconds": float(latest_run["duration_seconds"]),
                        "completed_at": latest_run["completed_at"].isoformat(),
                    }
                    if latest_run else None
                ),
                "last_complete_date": (
                    last_complete_date.isoformat() if last_complete_date else None
                ),
            },
            # Pass 5 #6: trader-stats freshness for the rankers' recency
            # filter. seeded=False means we're in bootstrap mode; fresh=False
            # with seeded=True means the nightly job is stuck and the
            # ranker is bypassing recency until it recovers.
            "stats_freshness": {
                "seeded": bool(stats_freshness["seeded"]),
                "fresh": bool(stats_freshness["fresh"]),
                "last_refresh": (
                    stats_freshness["last_refresh"].isoformat()
                    if stats_freshness["last_refresh"] else None
                ),
            },
            "wallet_classifier": {
                "health": classifier_health,
                "last_at": last_classified_at.isoformat() if last_classified_at else None,
                "days_since": days_since_classifier,
            },
            "tracked_wallets": {
                "health": wallets_health,
                "count": tracked_wallets,
            },
            "recent_signals": {
                "health": signals_health,
                # D5/cosmetic-fix: field renamed to match the actual window
                # (F25 widened to 72h but the legacy field name still said
                # 48h -- breaking operator trust during incident triage).
                "fired_last_72h": signals_recent,
                # Back-compat alias kept until UI migrates
                "fired_last_48h": signals_recent,
            },
        },
        # D5 (Pass 3): operational health counters
        "counters": {
            "rate_limit_hits_last_hour": counters["rate_limit_hit"],
            "cycle_duration_warnings_last_24h": counters["cycle_duration_warning"],
            "api_failures_last_hour": counters["api_failure"],
            # Pass 5 #6: stats-stale events recorded by the ranker
            # entrypoints when they detect trader_category_stats is
            # seeded but >7 days old. Non-zero = nightly job problem.
            "stats_stale_last_hour": counters["stats_stale"],
            # Zombie/dust position drops at the API boundary (24h windows).
            # If `redeemable` suddenly drops to ~0 with the others unchanged,
            # Polymarket has likely renamed the field -- investigate.
            "zombie_drops_last_24h": {
                "redeemable": counters["zombie_drop_redeemable"],
                "market_closed": counters["zombie_drop_market_closed"],
                "dust_size": counters["zombie_drop_dust_size"],
                "resolved_price_past": counters["zombie_drop_resolved_price_past"],
                # Pass 5 #17: residual stale-metadata sweep.
                "incomplete_metadata": counters["zombie_drop_incomplete_metadata"],
                "total": (
                    counters["zombie_drop_redeemable"]
                    + counters["zombie_drop_market_closed"]
                    + counters["zombie_drop_dust_size"]
                    + counters["zombie_drop_resolved_price_past"]
                    + counters["zombie_drop_incomplete_metadata"]
                ),
            },
        },
        # Back-compat fields -- earlier UI builds read these flat. Keep until
        # the UI has migrated to `components.*`.
        "last_position_refresh_at": last_refresh.isoformat() if last_refresh else None,
        "minutes_since_refresh": minutes_since_refresh,
        "health": overall,
        "last_snapshot_date": last_snapshot.isoformat() if last_snapshot else None,
    }
