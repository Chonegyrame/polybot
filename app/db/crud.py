"""Database read/write helpers — the only module that issues SQL.

Anything outside `app/db/` should call functions here, never write SQL inline.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Iterable

import asyncpg

from app.services.polymarket_types import LeaderboardEntry, Position, PortfolioValue

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# traders
# ---------------------------------------------------------------------------


async def upsert_trader(conn: asyncpg.Connection, entry: LeaderboardEntry) -> None:
    """Insert or update a trader row from a leaderboard entry."""
    await conn.execute(
        """
        INSERT INTO traders (
            proxy_wallet, user_name, x_username, verified_badge, profile_image,
            first_seen_at, last_seen_at
        )
        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
        ON CONFLICT (proxy_wallet) DO UPDATE SET
            user_name      = COALESCE(EXCLUDED.user_name, traders.user_name),
            x_username     = COALESCE(EXCLUDED.x_username, traders.x_username),
            verified_badge = EXCLUDED.verified_badge,
            profile_image  = COALESCE(EXCLUDED.profile_image, traders.profile_image),
            last_seen_at   = NOW()
        """,
        entry.proxy_wallet,
        entry.user_name,
        entry.x_username,
        entry.verified_badge,
        entry.profile_image,
    )


async def upsert_traders_bulk(
    conn: asyncpg.Connection, entries: Iterable[LeaderboardEntry]
) -> None:
    """Bulk upsert via executemany — same semantics as upsert_trader."""
    rows = [
        (
            e.proxy_wallet,
            e.user_name,
            e.x_username,
            e.verified_badge,
            e.profile_image,
        )
        for e in entries
    ]
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO traders (
            proxy_wallet, user_name, x_username, verified_badge, profile_image,
            first_seen_at, last_seen_at
        )
        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
        ON CONFLICT (proxy_wallet) DO UPDATE SET
            user_name      = COALESCE(EXCLUDED.user_name, traders.user_name),
            x_username     = COALESCE(EXCLUDED.x_username, traders.x_username),
            verified_badge = EXCLUDED.verified_badge,
            profile_image  = COALESCE(EXCLUDED.profile_image, traders.profile_image),
            last_seen_at   = NOW()
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# leaderboard_snapshots
# ---------------------------------------------------------------------------


async def insert_leaderboard_snapshot(
    conn: asyncpg.Connection,
    snapshot_date: date,
    category: str,
    time_period: str,
    order_by: str,
    entries: Iterable[LeaderboardEntry],
) -> int:
    """Insert one full leaderboard page-set as a single snapshot.

    Returns the number of rows actually inserted (excludes ON CONFLICT skips).
    """
    rows = [
        (
            snapshot_date,
            category,
            time_period,
            order_by,
            e.proxy_wallet,
            e.rank,
            e.pnl,
            e.vol,
        )
        for e in entries
    ]
    if not rows:
        return 0
    result = await conn.executemany(
        """
        INSERT INTO leaderboard_snapshots
            (snapshot_date, category, time_period, order_by, proxy_wallet,
             rank, pnl, vol)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (snapshot_date, category, time_period, order_by, proxy_wallet)
        DO NOTHING
        """,
        rows,
    )
    return len(rows)  # asyncpg's executemany doesn't return per-row counts


async def latest_snapshot_date(conn: asyncpg.Connection) -> date | None:
    row = await conn.fetchrow(
        "SELECT MAX(snapshot_date) AS d FROM leaderboard_snapshots"
    )
    return row["d"] if row and row["d"] else None


# ---------------------------------------------------------------------------
# portfolio_value_snapshots
# ---------------------------------------------------------------------------


async def insert_portfolio_value(
    conn: asyncpg.Connection, value: PortfolioValue
) -> None:
    """Append a portfolio value point. Composite PK on (wallet, fetched_at)."""
    await conn.execute(
        """
        INSERT INTO portfolio_value_snapshots (proxy_wallet, value, fetched_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (proxy_wallet, fetched_at) DO NOTHING
        """,
        value.proxy_wallet,
        value.value,
    )


# ---------------------------------------------------------------------------
# positions — first_seen_at preserved across upserts (for freshness/drift labels)
# ---------------------------------------------------------------------------


R13_GRACE_CYCLES = 3
"""R13 (Pass 3): consecutive dropout cycles before a wallet's positions
get swept. 3 cycles x 10 min/cycle = 30 min grace period. Catches normal
rank-jiggling around the top-N edge without losing data on bounce-back."""


async def update_wallet_dropout_counters(
    conn: asyncpg.Connection,
    current_wallets: list[str],
) -> tuple[int, int]:
    """R13 (Pass 3): per-cycle dropout-counter maintenance.

    For every tracked wallet:
      - if in current pool -> dropout_count = 0 (reset)
      - if NOT in current pool -> dropout_count += 1

    Returns (resets, increments) for logging.
    """
    if not current_wallets:
        return (0, 0)
    # Reset for re-entrants
    res_reset = await conn.execute(
        """
        UPDATE traders
        SET dropout_count = 0
        WHERE proxy_wallet = ANY($1::TEXT[])
          AND dropout_count > 0
        """,
        current_wallets,
    )
    # Increment for absentees
    res_inc = await conn.execute(
        """
        UPDATE traders
        SET dropout_count = dropout_count + 1
        WHERE proxy_wallet <> ALL($1::TEXT[])
        """,
        current_wallets,
    )
    def parse_count(r: str) -> int:
        parts = r.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0
    return (parse_count(res_reset), parse_count(res_inc))


async def delete_positions_for_dropped_wallets(
    conn: asyncpg.Connection,
    current_wallets: list[str],
    grace_cycles: int = R13_GRACE_CYCLES,
) -> int:
    """Delete positions for wallets no longer in the tracked top-N pool
    AND past the dropout-grace period (R13, Pass 3).

    Pre-fix this swept any wallet not in current_wallets immediately --
    a one-cycle dip out of top-N caused complete position wipe. Now: only
    wallets with dropout_count >= grace_cycles get swept (default 3 = 30
    min grace).

    Returns count of rows deleted.
    """
    if not current_wallets:
        # Empty list -- caller probably has a problem; safer to no-op than
        # delete every position in the table.
        return 0
    row = await conn.fetchrow(
        """
        WITH dropped_eligible AS (
            -- R13: only sweep wallets that have been dropped for at least
            -- `grace_cycles` consecutive cycles. Wallets without a traders
            -- row (shouldn't happen but defensive) get swept normally.
            SELECT t.proxy_wallet
            FROM traders t
            WHERE t.proxy_wallet <> ALL($1::TEXT[])
              AND t.dropout_count >= $2
        ),
        deleted AS (
            DELETE FROM positions
            WHERE proxy_wallet IN (SELECT proxy_wallet FROM dropped_eligible)
            RETURNING 1
        )
        SELECT COUNT(*) AS n FROM deleted
        """,
        current_wallets, grace_cycles,
    )
    return int(row["n"]) if row else 0


async def upsert_positions_for_trader(
    conn: asyncpg.Connection,
    proxy_wallet: str,
    positions: Iterable[Position],
) -> None:
    """Replace the position state for one trader in a single transaction.

    first_seen_at is preserved on existing rows (freshness label). Positions
    that were present before but are absent now get deleted (the trader has
    closed them).
    """
    plist = list(positions)
    async with conn.transaction():
        if plist:
            # F16: batch upsert via executemany — pre-fix issued one INSERT
            # per Position serially, which with ~530 wallets × ~20 positions
            # = ~10,000 sequential round-trips per cycle (~7 minutes,
            # bumping the 9-min cycle warning regularly). One executemany
            # collapses to a single round-trip per wallet.
            args = [
                (
                    p.proxy_wallet or proxy_wallet,
                    p.condition_id, p.asset, p.outcome,
                    p.size, p.avg_price, p.cur_price,
                    p.initial_value, p.current_value,
                    p.cash_pnl, p.realized_pnl, p.percent_pnl,
                )
                for p in plist
            ]
            await conn.executemany(
                """
                INSERT INTO positions (
                    proxy_wallet, condition_id, asset, outcome,
                    size, avg_price, cur_price,
                    initial_value, current_value,
                    cash_pnl, realized_pnl, percent_pnl,
                    first_seen_at, last_updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW(), NOW())
                ON CONFLICT (proxy_wallet, condition_id, asset) DO UPDATE SET
                    outcome         = EXCLUDED.outcome,
                    size            = EXCLUDED.size,
                    avg_price       = EXCLUDED.avg_price,
                    cur_price       = EXCLUDED.cur_price,
                    initial_value   = EXCLUDED.initial_value,
                    current_value   = EXCLUDED.current_value,
                    cash_pnl        = EXCLUDED.cash_pnl,
                    realized_pnl    = EXCLUDED.realized_pnl,
                    percent_pnl     = EXCLUDED.percent_pnl,
                    last_updated_at = NOW()
                """,
                args,
            )

        # Delete stale positions (anything not seen in this refresh)
        keep_keys = [(p.condition_id, p.asset) for p in plist]
        if keep_keys:
            await conn.execute(
                """
                DELETE FROM positions
                WHERE proxy_wallet = $1
                  AND (condition_id, asset) NOT IN (
                      SELECT * FROM unnest($2::TEXT[], $3::TEXT[])
                  )
                """,
                proxy_wallet,
                [c for c, _ in keep_keys],
                [a for _, a in keep_keys],
            )
        else:
            await conn.execute(
                "DELETE FROM positions WHERE proxy_wallet = $1", proxy_wallet
            )


# ---------------------------------------------------------------------------
# events / markets — minimal upsert helpers
# ---------------------------------------------------------------------------


async def upsert_event(
    conn: asyncpg.Connection,
    event_id: str,
    slug: str | None,
    title: str | None,
    category: str | None,
    tags: list[dict[str, Any]] | None,
    end_date: datetime | None,
    closed: bool,
) -> None:
    await conn.execute(
        """
        INSERT INTO events (id, slug, title, category, tags, end_date, closed, last_synced_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, NOW())
        ON CONFLICT (id) DO UPDATE SET
            slug           = EXCLUDED.slug,
            title          = EXCLUDED.title,
            category       = EXCLUDED.category,
            tags           = EXCLUDED.tags,
            end_date       = EXCLUDED.end_date,
            closed         = EXCLUDED.closed,
            last_synced_at = NOW()
        """,
        event_id,
        slug,
        title,
        category,
        json.dumps(tags) if tags is not None else None,
        end_date,
        closed,
    )


async def upsert_market(
    conn: asyncpg.Connection,
    condition_id: str,
    gamma_id: str | None,
    event_id: str | None,
    slug: str | None,
    question: str | None,
    clob_token_yes: str | None,
    clob_token_no: str | None,
    outcomes: list[str] | None,
    end_date: datetime | None,
    closed: bool,
    resolved_outcome: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO markets (
            condition_id, gamma_id, event_id, slug, question,
            clob_token_yes, clob_token_no, outcomes, end_date, closed,
            resolved_outcome, last_synced_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, NOW())
        ON CONFLICT (condition_id) DO UPDATE SET
            gamma_id         = EXCLUDED.gamma_id,
            event_id         = EXCLUDED.event_id,
            slug             = EXCLUDED.slug,
            question         = EXCLUDED.question,
            clob_token_yes   = EXCLUDED.clob_token_yes,
            clob_token_no    = EXCLUDED.clob_token_no,
            outcomes         = EXCLUDED.outcomes,
            end_date         = EXCLUDED.end_date,
            closed           = EXCLUDED.closed,
            resolved_outcome = COALESCE(EXCLUDED.resolved_outcome, markets.resolved_outcome),
            last_synced_at   = NOW()
        """,
        condition_id,
        gamma_id,
        event_id,
        slug,
        question,
        clob_token_yes,
        clob_token_no,
        json.dumps(outcomes) if outcomes is not None else None,
        end_date,
        closed,
        resolved_outcome,
    )


# ---------------------------------------------------------------------------
# health / utility
# ---------------------------------------------------------------------------


async def ping(conn: asyncpg.Connection) -> dict[str, Any]:
    """Lightweight connectivity check."""
    row = await conn.fetchrow(
        "SELECT current_database() AS db, current_user AS usr, NOW() AS ts"
    )
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# signal_log — durable per-lifetime signal record
# ---------------------------------------------------------------------------


async def upsert_signal_log_entry(
    conn: asyncpg.Connection,
    mode: str,
    category: str,
    top_n: int,
    condition_id: str,
    direction: str,
    trader_count: int,
    avg_portfolio_fraction: float | None,
    aggregate_usdc: float | None,
    direction_skew: float | None,
    first_top_trader_entry_price: float | None,
    current_price: float | None,
    cluster_id: str | None = None,
    market_type: str = "binary",
    direction_dollar_skew: float | None = None,   # R2 (Pass 3)
    contributing_wallets: list[str] | None = None,  # R3b (Pass 3)
) -> bool:
    """Insert or update one signal_log row.

    On insert: set first_fired_at = NOW(), last_seen_at = NOW(), seed BOTH
    `first_*` (frozen entry-time snapshot) AND `peak_*` (running max) from the
    same observation, snapshot the entry-price approximation, and persist
    `cluster_id` (gamma event_id, used to deduplicate correlated signals in
    backtest CIs) + `market_type`.

    On conflict: bump last_seen_at, monotonically max() the peak metrics, and
    refresh `current_price`. `first_fired_at`, all `first_*` fields, and
    `cluster_id` / `market_type` are never overwritten -- they describe the
    moment the signal first fired.

    R2: also persists `first_net_dollar_skew` (USDC-weighted skew at fire time).
    R3b: also persists `contributing_wallets` (the wallet addresses that fed
    into this signal at fire time, used by exit_detector for cohort-aware
    aggregation). Both fields preserved at first-fire (never overwritten on
    re-fire) since they describe the original cohort/composition.

    Returns True iff this was a fresh insert (signal first fired now).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO signal_log (
            mode, category, top_n, condition_id, direction,
            first_fired_at, last_seen_at,
            peak_trader_count, peak_avg_portfolio_fraction,
            peak_aggregate_usdc, peak_net_skew,
            first_trader_count, first_avg_portfolio_fraction,
            first_aggregate_usdc, first_net_skew,
            first_top_trader_entry_price, current_price,
            cluster_id, market_type,
            first_net_dollar_skew, contributing_wallets
        )
        VALUES ($1, $2, $3, $4, $5, NOW(), NOW(),
                $6, $7, $8, $9,
                $6, $7, $8, $9,
                $10, $11, $12, $13,
                $14, $15)
        ON CONFLICT (mode, category, top_n, condition_id, direction) DO UPDATE SET
            last_seen_at                  = NOW(),
            peak_trader_count             = GREATEST(signal_log.peak_trader_count, EXCLUDED.peak_trader_count),
            peak_avg_portfolio_fraction   = GREATEST(
                COALESCE(signal_log.peak_avg_portfolio_fraction, 0),
                COALESCE(EXCLUDED.peak_avg_portfolio_fraction, 0)
            ),
            peak_aggregate_usdc           = GREATEST(
                COALESCE(signal_log.peak_aggregate_usdc, 0),
                COALESCE(EXCLUDED.peak_aggregate_usdc, 0)
            ),
            peak_net_skew                 = GREATEST(
                COALESCE(signal_log.peak_net_skew, 0),
                COALESCE(EXCLUDED.peak_net_skew, 0)
            ),
            current_price                 = EXCLUDED.current_price,
            -- A20: heal NULL cluster_id if a later fire has it. The upsert
            -- never overwrites an existing cluster_id (those are immutable
            -- once captured), but a row inserted before its event was
            -- discovered will have cluster_id=NULL; the next signal-fire
            -- that knows the event_id should fill it in.
            cluster_id                    = COALESCE(signal_log.cluster_id, EXCLUDED.cluster_id)
        RETURNING (xmax = 0) AS inserted
        """,
        mode,
        category,
        top_n,
        condition_id,
        direction,
        trader_count,
        avg_portfolio_fraction,
        aggregate_usdc,
        direction_skew,
        first_top_trader_entry_price,
        current_price,
        cluster_id,
        market_type,
        direction_dollar_skew,
        contributing_wallets,
    )
    return bool(row["inserted"]) if row else False


async def backfill_signal_log_cluster_ids(conn: asyncpg.Connection) -> int:
    """One-shot sweep: fill in signal_log.cluster_id from markets.event_id
    for any rows still NULL.

    The upsert path now self-heals NULL cluster_ids on the next signal fire
    via COALESCE, but rows that don't fire again would otherwise stay NULL
    forever (and get excluded from cluster-bootstrap CIs in the backtest).
    Running this on a cadence — or once at startup — fills in any gaps that
    accumulated from earlier discovery races.

    Returns count of rows updated.
    """
    row = await conn.fetchrow(
        """
        WITH updated AS (
            UPDATE signal_log s
            SET cluster_id = m.event_id
            FROM markets m
            WHERE s.condition_id = m.condition_id
              AND s.cluster_id IS NULL
              AND m.event_id IS NOT NULL
            RETURNING 1
        )
        SELECT COUNT(*) AS n FROM updated
        """
    )
    return int(row["n"]) if row else 0


async def count_new_signals_since(
    conn: asyncpg.Connection,
    mode: str,
    category: str,
    top_n: int,
    since: datetime,
) -> int:
    """How many signals first fired after `since` for this UI selection.

    Drives the "X new signals" header badge. UI passes its
    localStorage-stored `lastReadSignalsAt`.
    """
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n
        FROM signal_log
        WHERE mode = $1
          AND category = $2
          AND top_n = $3
          AND first_fired_at > $4
        """,
        mode,
        category,
        top_n,
        since,
    )
    return int(row["n"]) if row else 0


async def latest_position_refresh_at(conn: asyncpg.Connection) -> datetime | None:
    """Most recent `fetched_at` across portfolio_value_snapshots.

    Drives the dashboard's status-row "Updated N min ago" timestamp.
    """
    row = await conn.fetchrow(
        "SELECT MAX(fetched_at) AS m FROM portfolio_value_snapshots"
    )
    return row["m"] if row and row["m"] else None


# ---------------------------------------------------------------------------
# Paper trades — discretionary "fake money" entries on signals
# ---------------------------------------------------------------------------


async def insert_paper_trade(
    conn: asyncpg.Connection,
    signal_log_id: int | None,
    condition_id: str,
    direction: str,
    entry_price: float,
    entry_mid: float | None,
    entry_size_usdc: float,
    entry_fee_usdc: float,
    entry_slippage_usdc: float,
    notes: str | None,
) -> int:
    """Open a new paper trade. Returns the new row id."""
    row = await conn.fetchrow(
        """
        INSERT INTO paper_trades (
            signal_log_id, condition_id, direction,
            entry_price, entry_mid, entry_size_usdc,
            entry_fee_usdc, entry_slippage_usdc, notes
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        signal_log_id, condition_id, direction,
        entry_price, entry_mid, entry_size_usdc,
        entry_fee_usdc, entry_slippage_usdc, notes,
    )
    return int(row["id"])


async def list_paper_trades(
    conn: asyncpg.Connection, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    if status is not None:
        rows = await conn.fetch(
            "SELECT * FROM paper_trades WHERE status = $1 ORDER BY entry_at DESC LIMIT $2",
            status, limit,
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM paper_trades ORDER BY entry_at DESC LIMIT $1", limit,
        )
    return [dict(r) for r in rows]


async def get_paper_trade(
    conn: asyncpg.Connection, trade_id: int
) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM paper_trades WHERE id = $1", trade_id)
    return dict(row) if row else None


async def close_paper_trade_manual(
    conn: asyncpg.Connection,
    trade_id: int,
    exit_price: float,
    realized_pnl_usdc: float,
) -> bool:
    """Mark a paper trade closed at user-initiated exit. Returns True if updated."""
    result = await conn.execute(
        """
        UPDATE paper_trades SET
            exit_price       = $2,
            exit_at          = NOW(),
            exit_reason      = 'manual_close',
            realized_pnl_usdc = $3,
            status           = 'closed_manual'
        WHERE id = $1 AND status = 'open'
        """,
        trade_id, exit_price, realized_pnl_usdc,
    )
    return result.endswith(" 1")


async def list_open_paper_trades_on_resolved_markets(
    conn: asyncpg.Connection,
) -> list[dict[str, Any]]:
    """Find paper trades that should be auto-closed.

    Returns rows joining paper_trades + markets where:
      - paper_trade.status = 'open'
      - markets.resolved_outcome IS NOT NULL (and not PENDING)
    """
    rows = await conn.fetch(
        """
        SELECT pt.id, pt.condition_id, pt.direction, pt.entry_price,
               pt.entry_size_usdc, pt.entry_fee_usdc, pt.entry_slippage_usdc,
               m.resolved_outcome
        FROM paper_trades pt
        JOIN markets m USING (condition_id)
        WHERE pt.status = 'open'
          AND m.resolved_outcome IS NOT NULL
          AND m.resolved_outcome IN ('YES','NO','50_50')
        """
    )
    return [dict(r) for r in rows]


async def close_paper_trade_resolved(
    conn: asyncpg.Connection,
    trade_id: int,
    exit_price: float,
    realized_pnl_usdc: float,
) -> bool:
    """Mark a paper trade auto-closed because its market resolved."""
    result = await conn.execute(
        """
        UPDATE paper_trades SET
            exit_price        = $2,
            exit_at           = NOW(),
            exit_reason       = 'resolved',
            realized_pnl_usdc = $3,
            status            = 'closed_resolved'
        WHERE id = $1 AND status = 'open'
        """,
        trade_id, exit_price, realized_pnl_usdc,
    )
    return result.endswith(" 1")


# ---------------------------------------------------------------------------
# B7: slice_lookups — multiple-testing audit log
# ---------------------------------------------------------------------------


async def insert_slice_lookup(
    conn: asyncpg.Connection,
    slice_definition: dict,
    n_signals: int,
    reported_metric: str,
    reported_value: float | None,
    ci_low: float | None,
    ci_high: float | None,
    bootstrap_p: float | None = None,
) -> None:
    """Append one backtest query to the audit log for multiple-testing tracking.

    Pass 5 #8: `bootstrap_p` is the empirical 2-sided bootstrap p-value
    (vs H0: mean = 0) computed by `cluster_bootstrap_mean_with_p`. Pre-fix
    we never persisted it -- so every prior session entry returned NULL
    in `compute_corrections`, which then fell back to the Gaussian-from-CI
    approximation F21 said was wrong. The kwarg defaults to None so old
    rows persisted before migration 018 are forwards-compatible (the
    column was added by migration 018, nullable).
    """
    await conn.execute(
        """
        INSERT INTO slice_lookups
            (slice_definition, n_signals, reported_metric, reported_value,
             ci_low, ci_high, bootstrap_p)
        VALUES ($1::jsonb, $2, $3, $4, $5, $6, $7)
        """,
        json.dumps(slice_definition),
        n_signals,
        reported_metric,
        reported_value,
        ci_low,
        ci_high,
        bootstrap_p,
    )


async def get_session_slice_lookups(
    conn: asyncpg.Connection,
    window_hours: int = 4,
) -> list[dict[str, float | None]]:
    """Return DISTINCT slice_lookup entries within the session window.

    A "session" is defined as the last `window_hours` hours. Used to count N
    for Bonferroni / BH-FDR corrections.

    R9 (Pass 3): deduplicates by slice_definition before counting. Pre-fix
    every backtest API call inserted a row, so repeatedly hitting Refresh
    on the same query inflated N -- the user paid a multiplicity penalty
    for clicking. /slice was even worse (one row per bucket per call).
    Now identical filter specs collapse to a single hypothesis regardless
    of how many times they were queried.

    Among duplicates we keep the most-recent one (DISTINCT ON ... ORDER BY
    slice_definition, ran_at DESC) so the latest CI/value is used.

    Pass 5 #8: now also returns `bootstrap_p` per entry (NULL on rows
    persisted before migration 018; `compute_corrections` falls back to
    the Gaussian-from-CI approximation only for those NULL rows).
    """
    from datetime import timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows = await conn.fetch(
        """
        WITH deduped AS (
            SELECT DISTINCT ON (slice_definition)
                ran_at, reported_value, ci_low, ci_high, bootstrap_p
            FROM slice_lookups
            WHERE ran_at >= $1
            ORDER BY slice_definition, ran_at DESC
        )
        SELECT reported_value, ci_low, ci_high, bootstrap_p
        FROM deduped
        ORDER BY ran_at
        """,
        cutoff,
    )
    return [
        {
            "reported_value": float(r["reported_value"]) if r["reported_value"] is not None else None,
            "ci_low":         float(r["ci_low"])         if r["ci_low"]         is not None else None,
            "ci_high":        float(r["ci_high"])        if r["ci_high"]        is not None else None,
            "bootstrap_p":    float(r["bootstrap_p"])    if r["bootstrap_p"]    is not None else None,
        }
        for r in rows
    ]


async def close_paper_trade_smart_money_exit(
    conn: asyncpg.Connection,
    trade_id: int,
    exit_price: float,
    realized_pnl_usdc: float,
) -> bool:
    """Mark a paper trade auto-closed because smart money exited the signal."""
    result = await conn.execute(
        """
        UPDATE paper_trades SET
            exit_price        = $2,
            exit_at           = NOW(),
            exit_reason       = 'smart_money_exit',
            realized_pnl_usdc = $3,
            status            = 'closed_exit'
        WHERE id = $1 AND status = 'open'
        """,
        trade_id, exit_price, realized_pnl_usdc,
    )
    return result.endswith(" 1")


# ---------------------------------------------------------------------------
# Smart-money exit events (B1)
# ---------------------------------------------------------------------------


async def insert_signal_exit(
    conn: asyncpg.Connection,
    signal_log_id: int,
    exit_trader_count: int,
    peak_trader_count: int,
    exit_aggregate_usdc: float,
    peak_aggregate_usdc: float,
    drop_reason: str,
    exit_bid_price: float | None,
    event_type: str = "exit",
) -> int | None:
    """Persist one detected trim/exit event. Returns the new row id, or None
    if a row already exists for this signal_log_id (UNIQUE-key dedup).

    R3a (Pass 3): event_type ('trim' | 'exit') tier added. Default 'exit'
    preserves the pre-Pass-3 behavior. Migration 013 added the column.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO signal_exits (
            signal_log_id, exited_at,
            exit_trader_count, peak_trader_count,
            exit_aggregate_usdc, peak_aggregate_usdc,
            drop_reason, exit_bid_price, event_type
        )
        VALUES ($1, NOW(), $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (signal_log_id) DO NOTHING
        RETURNING id
        """,
        signal_log_id,
        exit_trader_count, peak_trader_count,
        exit_aggregate_usdc, peak_aggregate_usdc,
        drop_reason, exit_bid_price, event_type,
    )
    return int(row["id"]) if row else None


async def list_open_paper_trades_for_signal(
    conn: asyncpg.Connection,
    signal_log_id: int,
) -> list[dict[str, Any]]:
    """Open paper trades whose `signal_log_id` matches the exited signal."""
    rows = await conn.fetch(
        """
        SELECT id, condition_id, direction,
               entry_price, entry_size_usdc,
               entry_fee_usdc, entry_slippage_usdc
        FROM paper_trades
        WHERE signal_log_id = $1 AND status = 'open'
        """,
        signal_log_id,
    )
    return [dict(r) for r in rows]


async def list_recent_signal_exits(
    conn: asyncpg.Connection,
    hours: int = 24,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Recent exit events joined to their signal_log row, for the alerts feed."""
    rows = await conn.fetch(
        """
        SELECT
            e.id                         AS exit_id,
            e.signal_log_id,
            e.exited_at,
            e.exit_trader_count, e.peak_trader_count,
            e.exit_aggregate_usdc::numeric AS exit_aggregate_usdc,
            e.peak_aggregate_usdc::numeric AS peak_aggregate_usdc,
            e.drop_reason,
            e.exit_bid_price::numeric    AS exit_bid_price,
            s.mode, s.category, s.top_n,
            s.condition_id, s.direction,
            s.first_fired_at,
            m.question                   AS market_question,
            m.slug                       AS market_slug
        FROM signal_exits e
        JOIN signal_log s ON s.id = e.signal_log_id
        LEFT JOIN markets m ON m.condition_id = s.condition_id
        WHERE e.exited_at >= NOW() - make_interval(hours => $1)
        ORDER BY e.exited_at DESC
        LIMIT $2
        """,
        hours, limit,
    )
    return [dict(r) for r in rows]


async def get_exit_for_signal(
    conn: asyncpg.Connection,
    mode: str, category: str, top_n: int,
    condition_id: str, direction: str,
) -> dict[str, Any] | None:
    """Look up the exit event (if any) for the given signal selection."""
    row = await conn.fetchrow(
        """
        SELECT e.id AS exit_id, e.exited_at, e.drop_reason,
               e.exit_trader_count, e.peak_trader_count,
               e.exit_aggregate_usdc::numeric AS exit_aggregate_usdc,
               e.peak_aggregate_usdc::numeric AS peak_aggregate_usdc,
               e.exit_bid_price::numeric AS exit_bid_price
        FROM signal_exits e
        JOIN signal_log s ON s.id = e.signal_log_id
        WHERE s.mode = $1 AND s.category = $2 AND s.top_n = $3
          AND s.condition_id = $4 AND s.direction = $5
        """,
        mode, category, top_n, condition_id, direction,
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Wallet classification
# ---------------------------------------------------------------------------


async def upsert_wallet_classification(
    conn: asyncpg.Connection,
    proxy_wallet: str,
    wallet_class: str,
    confidence: float,
    features: dict[str, Any],
    trades_observed: int,
    classifier_version: str,
) -> None:
    """Persist or refresh a wallet's classification."""
    await conn.execute(
        """
        INSERT INTO wallet_classifications (
            proxy_wallet, wallet_class, confidence, features,
            trades_observed, classified_at, classifier_version
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, NOW(), $6)
        ON CONFLICT (proxy_wallet) DO UPDATE SET
            wallet_class       = EXCLUDED.wallet_class,
            confidence         = EXCLUDED.confidence,
            features           = EXCLUDED.features,
            trades_observed    = EXCLUDED.trades_observed,
            classified_at      = NOW(),
            classifier_version = EXCLUDED.classifier_version
        """,
        proxy_wallet, wallet_class, confidence,
        json.dumps(features), trades_observed, classifier_version,
    )


async def persist_sybil_cluster(
    conn: asyncpg.Connection,
    members: list[str],
    evidence: dict[str, Any],
) -> str:
    """Insert a new sybil cluster and link the member wallets.

    Returns the newly-created cluster_id (UUID as string). Idempotent only
    in the sense of "won't crash on re-run" — re-running will create a new
    cluster row with the same members. Caller is responsible for clearing
    old clusters before a fresh detection pass.
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO wallet_clusters (detection_method, evidence)
            VALUES ('time_correlation', $1::jsonb)
            RETURNING cluster_id
            """,
            json.dumps(evidence),
        )
        cluster_id = row["cluster_id"]
        await conn.executemany(
            """
            INSERT INTO cluster_membership (cluster_id, proxy_wallet)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            [(cluster_id, m) for m in members],
        )
    return str(cluster_id)


async def mark_wallets_likely_sybil(
    conn: asyncpg.Connection,
    proxy_wallets: list[str],
    cluster_id: str,
    evidence: dict[str, Any],
    trades_observed_by_wallet: dict[str, int],
) -> None:
    """Mark cluster members as 'likely_sybil' in wallet_classifications.

    Writes via the classifier's upsert path so trader_ranker's
    _EXCLUDE_CONTAMINATED_SQL filter (which reads wallet_classifications)
    automatically removes them from top-N pools. Without this, sybil
    cluster_id rows existed but exclusion was a no-op.

    Sybil detection runs AFTER classify_tracked_wallets in the weekly
    schedule, so this overwrites any directional/MM/arb classification
    from the same cycle for cluster members. Wallets no longer in any
    cluster get reclassified by the next classifier run.
    """
    classifier_version = "sybil_detector_v1"
    confidence = 0.95
    features = {
        "sybil_cluster_id": cluster_id,
        "cluster_evidence": evidence,
    }
    for wallet in proxy_wallets:
        await upsert_wallet_classification(
            conn,
            proxy_wallet=wallet,
            wallet_class="likely_sybil",
            confidence=confidence,
            features=features,
            trades_observed=trades_observed_by_wallet.get(wallet, 0),
            classifier_version=classifier_version,
        )


async def clear_sybil_clusters_by_method(
    conn: asyncpg.Connection, detection_method: str
) -> int:
    """Delete all clusters detected by a specific method (cascades to membership).

    Used before a fresh detection pass to avoid duplicate clusters across runs.
    Returns the number of clusters deleted.
    """
    row = await conn.fetchrow(
        """
        WITH deleted AS (
            DELETE FROM wallet_clusters
            WHERE detection_method = $1
            RETURNING cluster_id
        )
        SELECT COUNT(*) AS n FROM deleted
        """,
        detection_method,
    )
    return int(row["n"]) if row else 0


async def upsert_trader_category_stats_bulk(
    conn: asyncpg.Connection,
    rows: list[tuple[str, str, float, float, int, datetime | None]],
) -> None:
    """Bulk upsert per-(wallet, category) rows.

    `rows` is a list of (proxy_wallet, category, pnl_usdc, volume_usdc,
    resolved_trades, last_trade_at) tuples. Upsert refreshes every column
    so the table always reflects the most recent batch run.

    Schema note: the underlying table (created in migration 002) uses
    `category_pnl_usdc` / `category_volume_usdc` / `category_roi` column
    names — kept here for back-compat with that migration. We compute roi
    inline so the NOT NULL constraint is satisfied; trader_ranker uses
    Bayesian-shrunk roi instead of this raw value.
    """
    if not rows:
        return
    # Pre-compute roi per row (= pnl/vol with safe division)
    rows_with_roi = [
        (w, c, pnl, vol, (pnl / vol) if vol > 0 else 0.0, resolved, last_at)
        for (w, c, pnl, vol, resolved, last_at) in rows
    ]
    await conn.executemany(
        """
        INSERT INTO trader_category_stats (
            proxy_wallet, category,
            category_pnl_usdc, category_volume_usdc, category_roi,
            resolved_trades, last_trade_at, computed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        ON CONFLICT (proxy_wallet, category) DO UPDATE SET
            category_pnl_usdc    = EXCLUDED.category_pnl_usdc,
            category_volume_usdc = EXCLUDED.category_volume_usdc,
            category_roi         = EXCLUDED.category_roi,
            resolved_trades      = EXCLUDED.resolved_trades,
            last_trade_at        = EXCLUDED.last_trade_at,
            computed_at          = NOW()
        """,
        rows_with_roi,
    )


async def latest_pnl_volume_per_category(
    conn: asyncpg.Connection,
    wallets: list[str],
) -> dict[tuple[str, str], tuple[float, float]]:
    """Return {(wallet, category): (pnl, vol)} from the latest leaderboard
    snapshot per category.

    Uses time_period='all' / order_by='PNL' (the canonical lifetime view).
    Categories with no snapshot row for a wallet simply don't appear in the
    result — the caller treats them as zeros for that wallet/category pair.
    """
    if not wallets:
        return {}
    rows = await conn.fetch(
        """
        WITH latest_per_category AS (
            SELECT category, MAX(snapshot_date) AS d
            FROM leaderboard_snapshots
            WHERE time_period = 'all' AND order_by = 'PNL'
            GROUP BY category
        )
        SELECT ls.proxy_wallet, ls.category,
               ls.pnl::numeric AS pnl,
               ls.vol::numeric AS vol
        FROM leaderboard_snapshots ls
        JOIN latest_per_category lpc
          ON lpc.category = ls.category AND lpc.d = ls.snapshot_date
        WHERE ls.time_period = 'all' AND ls.order_by = 'PNL'
          AND ls.proxy_wallet = ANY($1::TEXT[])
        """,
        wallets,
    )
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for r in rows:
        out[(r["proxy_wallet"], r["category"])] = (
            float(r["pnl"]), float(r["vol"]),
        )
    return out


async def get_contaminated_wallets(conn: asyncpg.Connection) -> set[str]:
    """Return the set of wallets classified as MM/arb/sybil — to be excluded
    from top-N pools.

    Wallets without a classification (NULL) are considered safe by default;
    classifying everyone is too expensive, so the floor is "innocent until
    proven guilty" — the classifier batch flags the contaminated ones.
    """
    rows = await conn.fetch(
        """
        SELECT proxy_wallet FROM wallet_classifications
        WHERE wallet_class IN ('market_maker', 'arbitrage', 'likely_sybil')
        """
    )
    return {r["proxy_wallet"] for r in rows}


# ---------------------------------------------------------------------------
# CLOB orderbook capture for signal entry-pricing
# ---------------------------------------------------------------------------


async def get_market_clob_tokens(
    conn: asyncpg.Connection, condition_id: str
) -> tuple[str | None, str | None]:
    """Return (clob_token_yes, clob_token_no) for a market, or (None, None)."""
    row = await conn.fetchrow(
        "SELECT clob_token_yes, clob_token_no FROM markets WHERE condition_id = $1",
        condition_id,
    )
    if not row:
        return (None, None)
    return (row["clob_token_yes"], row["clob_token_no"])


async def get_trader_profile(
    conn: asyncpg.Connection, wallet: str,
) -> dict[str, Any] | None:
    """F23: Trader profile fields. Returns dict or None."""
    row = await conn.fetchrow(
        """
        SELECT proxy_wallet, user_name, x_username, verified_badge, profile_image,
               first_seen_at, last_seen_at
        FROM traders
        WHERE proxy_wallet = $1
        """,
        wallet,
    )
    return dict(row) if row else None


async def get_trader_per_category_stats(
    conn: asyncpg.Connection, wallet: str,
) -> list[dict[str, Any]]:
    """F23: Per-category stats from latest leaderboard snapshot for one wallet."""
    rows = await conn.fetch(
        """
        SELECT category, pnl::numeric AS pnl, vol::numeric AS vol,
               CASE WHEN vol > 0 THEN pnl/vol ELSE 0 END AS roi,
               rank
        FROM leaderboard_snapshots
        WHERE proxy_wallet = $1
          AND time_period = 'all'
          AND order_by = 'PNL'
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM leaderboard_snapshots)
        ORDER BY pnl DESC
        """,
        wallet,
    )
    return [dict(r) for r in rows]


async def get_trader_open_positions(
    conn: asyncpg.Connection, wallet: str, limit: int = 200,
) -> list[dict[str, Any]]:
    """F23: Currently-open positions for one wallet, joined with market metadata."""
    rows = await conn.fetch(
        """
        SELECT p.condition_id, p.outcome, p.size,
               p.avg_price::numeric AS avg_price,
               p.cur_price::numeric AS cur_price,
               p.current_value::numeric AS current_value,
               p.cash_pnl::numeric AS cash_pnl,
               p.percent_pnl::numeric AS percent_pnl,
               p.first_seen_at,
               m.question, m.slug, m.closed,
               e.category AS market_category
        FROM positions p
        JOIN markets m USING (condition_id)
        LEFT JOIN events e ON e.id = m.event_id
        WHERE p.proxy_wallet = $1 AND p.size > 0
        ORDER BY p.current_value DESC NULLS LAST
        LIMIT $2
        """,
        wallet, limit,
    )
    return [dict(r) for r in rows]


async def get_trader_classification(
    conn: asyncpg.Connection, wallet: str,
) -> dict[str, Any] | None:
    """F23: Latest wallet_classifications row for a trader, or None."""
    row = await conn.fetchrow(
        """
        SELECT wallet_class, confidence, classified_at
        FROM wallet_classifications WHERE proxy_wallet = $1
        """,
        wallet,
    )
    return dict(row) if row else None


async def get_trader_sybil_cluster(
    conn: asyncpg.Connection, wallet: str,
) -> dict[str, Any] | None:
    """F23: Sybil cluster membership info for a trader, or None."""
    row = await conn.fetchrow(
        """
        SELECT cm.cluster_id::text, wc.detection_method, wc.evidence
        FROM cluster_membership cm
        JOIN wallet_clusters wc USING (cluster_id)
        WHERE cm.proxy_wallet = $1
        LIMIT 1
        """,
        wallet,
    )
    return dict(row) if row else None


async def fetch_half_life_rows(
    conn: asyncpg.Connection, category: str | None = None,
) -> list[dict[str, Any]]:
    """F23: Pull signal_log + signal_price_snapshots joined for half-life
    computation. Returns raw dicts; the route shapes them into HalfLifeRow.

    R8 (Pass 3): also returns sps.direction (snapshot_direction) so half-life
    math can compare in the correct space (NO-direction snapshots are in
    NO-space; legacy + YES are YES-space)."""
    sql = """
        SELECT
            s.id, s.direction,
            s.signal_entry_offer::numeric    AS fire_price,
            s.first_top_trader_entry_price::numeric AS smart_money_entry,
            e.category                       AS category,
            sps.snapshot_offset_min,
            sps.yes_price::numeric           AS yes_price,
            sps.bid_price::numeric           AS bid_price,
            sps.ask_price::numeric           AS ask_price,
            sps.direction                    AS snapshot_direction
        FROM signal_log s
        JOIN signal_price_snapshots sps ON sps.signal_log_id = s.id
        JOIN markets m ON m.condition_id = s.condition_id
        LEFT JOIN events e ON e.id = m.event_id
        WHERE s.signal_entry_offer IS NOT NULL
          AND s.first_top_trader_entry_price IS NOT NULL
    """
    args: list[Any] = []
    if category is not None:
        args.append(category)
        sql += f" AND e.category = ${len(args)}"
    rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def latest_classification_at(
    conn: asyncpg.Connection,
) -> datetime | None:
    """F23: MAX(classified_at) from wallet_classifications, or None."""
    return await conn.fetchval(
        "SELECT MAX(classified_at) FROM wallet_classifications"
    )


async def count_distinct_wallets_with_positions(
    conn: asyncpg.Connection,
) -> int:
    """F23: Number of distinct wallets that have at least one open position."""
    n = await conn.fetchval(
        "SELECT COUNT(DISTINCT proxy_wallet) FROM positions"
    )
    return int(n or 0)


async def count_signals_since(
    conn: asyncpg.Connection, since: datetime,
) -> int:
    """F23: Number of signal_log rows fired at or after `since`."""
    n = await conn.fetchval(
        "SELECT COUNT(*) FROM signal_log WHERE first_fired_at > $1",
        since,
    )
    return int(n or 0)


async def get_signal_enrichment(
    conn: asyncpg.Connection, *,
    mode: str, category: str, top_n: int, condition_ids: list[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """F23: Bulk enrichment for /signals/active — liquidity_tier, entry-pricing,
    counterparty warning, and any matching signal_exits row. Keyed by
    (condition_id, direction)."""
    if not condition_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT s.condition_id, s.direction,
               s.liquidity_tier,
               s.liquidity_at_signal_usdc::numeric AS liquidity_at_signal_usdc,
               s.signal_entry_offer::numeric       AS signal_entry_offer,
               s.signal_entry_source,
               s.counterparty_warning,
               s.counterparty_count,
               e.id                  AS exit_id,
               e.exited_at,
               e.drop_reason         AS exit_drop_reason,
               e.exit_bid_price::numeric AS exit_bid_price,
               e.exit_trader_count, e.peak_trader_count,
               e.exit_aggregate_usdc::numeric AS exit_aggregate_usdc,
               e.peak_aggregate_usdc::numeric AS peak_aggregate_usdc
        FROM signal_log s
        LEFT JOIN signal_exits e ON e.signal_log_id = s.id
        WHERE s.mode = $1 AND s.category = $2 AND s.top_n = $3
          AND s.condition_id = ANY($4::TEXT[])
        """,
        mode, category, top_n, condition_ids,
    )
    return {(r["condition_id"], r["direction"]): dict(r) for r in rows}


async def get_market_with_event(
    conn: asyncpg.Connection, condition_id: str,
) -> dict[str, Any] | None:
    """F23: Single market joined with event metadata."""
    row = await conn.fetchrow(
        """
        SELECT m.condition_id, m.gamma_id, m.event_id, m.slug, m.question,
               m.clob_token_yes, m.clob_token_no, m.outcomes, m.end_date,
               m.closed, m.resolved_outcome, m.last_synced_at,
               e.title AS event_title, e.category AS event_category,
               e.tags AS event_tags
        FROM markets m
        LEFT JOIN events e ON e.id = m.event_id
        WHERE m.condition_id = $1
        """,
        condition_id,
    )
    return dict(row) if row else None


async def get_market_positions_summary(
    conn: asyncpg.Connection, condition_id: str,
) -> list[dict[str, Any]]:
    """F23: Aggregate tracked-trader positions on a market grouped by outcome,
    deduped by sybil cluster identity."""
    rows = await conn.fetch(
        """
        WITH identities AS (
            SELECT p.proxy_wallet, p.outcome, p.size, p.current_value,
                   p.avg_price, p.cur_price, p.first_seen_at,
                   COALESCE(cm.cluster_id::text, p.proxy_wallet) AS identity
            FROM positions p
            LEFT JOIN cluster_membership cm USING (proxy_wallet)
            WHERE p.condition_id = $1 AND p.size > 0
        )
        SELECT outcome,
               COUNT(DISTINCT identity)        AS trader_count,
               COUNT(*)                         AS wallet_count,
               SUM(current_value)::numeric      AS aggregate_usdc,
               CASE WHEN SUM(size) > 0
                    THEN (SUM(avg_price * size) / SUM(size))::numeric
                    ELSE NULL END               AS avg_entry_price,
               AVG(cur_price)::numeric          AS current_price,
               MIN(first_seen_at)               AS first_observed_at
        FROM identities
        GROUP BY outcome
        ORDER BY aggregate_usdc DESC NULLS LAST
        """,
        condition_id,
    )
    return [dict(r) for r in rows]


async def get_market_per_trader(
    conn: asyncpg.Connection, condition_id: str,
) -> list[dict[str, Any]]:
    """F23: Per-trader position detail on a market — name, classification,
    cluster, cost basis, current value, and portfolio fraction."""
    rows = await conn.fetch(
        """
        WITH latest_pv AS (
            SELECT DISTINCT ON (proxy_wallet)
                proxy_wallet, value AS portfolio_value
            FROM portfolio_value_snapshots
            ORDER BY proxy_wallet, fetched_at DESC
        )
        SELECT p.proxy_wallet,
               t.user_name,
               t.verified_badge,
               wc.wallet_class,
               cm.cluster_id::text                AS cluster_id,
               p.outcome,
               p.size::numeric                    AS size,
               p.avg_price::numeric               AS avg_entry_price,
               p.cur_price::numeric               AS current_price,
               p.current_value::numeric           AS current_value_usdc,
               p.initial_value::numeric           AS initial_value_usdc,
               p.cash_pnl::numeric                AS cash_pnl_usdc,
               p.percent_pnl::numeric             AS percent_pnl,
               p.first_seen_at,
               p.last_updated_at,
               pv.portfolio_value::numeric        AS portfolio_total_usdc,
               CASE WHEN pv.portfolio_value > 0
                    THEN (p.current_value / pv.portfolio_value)::numeric
                    ELSE NULL END                  AS portfolio_fraction
        FROM positions p
        LEFT JOIN traders t                 USING (proxy_wallet)
        LEFT JOIN wallet_classifications wc USING (proxy_wallet)
        LEFT JOIN cluster_membership cm     USING (proxy_wallet)
        LEFT JOIN latest_pv pv              USING (proxy_wallet)
        WHERE p.condition_id = $1 AND p.size > 0
        ORDER BY p.current_value DESC NULLS LAST
        """,
        condition_id,
    )
    return [dict(r) for r in rows]


async def get_market_signal_history(
    conn: asyncpg.Connection, condition_id: str,
) -> list[dict[str, Any]]:
    """F23: All signal_log entries for a market, newest first."""
    rows = await conn.fetch(
        """
        SELECT mode, category, top_n, direction, first_fired_at, last_seen_at,
               peak_trader_count, peak_aggregate_usdc::numeric AS peak_aggregate_usdc,
               peak_net_skew::numeric AS peak_net_skew,
               first_trader_count,
               first_aggregate_usdc::numeric AS first_aggregate_usdc,
               first_net_skew::numeric AS first_net_skew,
               signal_entry_offer::numeric AS signal_entry_offer,
               liquidity_tier, resolution_outcome
        FROM signal_log
        WHERE condition_id = $1
        ORDER BY first_fired_at DESC
        """,
        condition_id,
    )
    return [dict(r) for r in rows]


async def get_market_tokens_and_category(
    conn: asyncpg.Connection, condition_id: str,
) -> dict[str, Any] | None:
    """F23: Return clob_token_yes, clob_token_no, and event category for a
    market in one query. Used by paper-trade open + close paths to look up
    the right token + fee bucket. Returns None if the market doesn't exist.
    """
    row = await conn.fetchrow(
        """
        SELECT m.clob_token_yes, m.clob_token_no, e.category
        FROM markets m
        LEFT JOIN events e ON e.id = m.event_id
        WHERE m.condition_id = $1
        """,
        condition_id,
    )
    return dict(row) if row else None


async def get_signal_log_id(
    conn: asyncpg.Connection,
    mode: str,
    category: str,
    top_n: int,
    condition_id: str,
    direction: str,
) -> int | None:
    """Look up the row id for a signal_log entry by its unique tuple."""
    return await conn.fetchval(
        """
        SELECT id FROM signal_log
        WHERE mode = $1 AND category = $2 AND top_n = $3
          AND condition_id = $4 AND direction = $5
        """,
        mode, category, top_n, condition_id, direction,
    )


async def persist_book_snapshot_and_pricing(
    conn: asyncpg.Connection,
    signal_log_id: int,
    token_id: str,
    side: str,
    metrics: Any,  # orderbook.BookMetrics — typed loosely to avoid circular import
) -> None:
    """Persist the raw book + write entry-pricing fields onto the signal_log row.

    Called once per fresh signal insertion. If `metrics.available` is False
    (no book / fetch error), only updates `signal_log.signal_entry_source`
    so backtest knows to exclude this row.
    """
    if not metrics.available:
        # Only mark 'unavailable' if we don't already have a successful capture
        # — never downgrade a real clob_l2 row back to 'unavailable'.
        await conn.execute(
            """
            UPDATE signal_log SET
                signal_entry_source = 'unavailable',
                signal_entry_captured_at = NOW()
            WHERE id = $1
              AND signal_entry_source IS NULL
            """,
            signal_log_id,
        )
        return

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO signal_book_snapshots (
                signal_log_id, token_id, side_captured, captured_at,
                best_bid, best_ask, bids, asks,
                total_bid_size_5c, total_ask_size_5c, raw_response_hash
            )
            VALUES ($1, $2, $3, NOW(), $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10)
            """,
            signal_log_id,
            token_id,
            side,
            metrics.best_bid,
            metrics.best_ask,
            json.dumps(metrics.bids_top20),
            json.dumps(metrics.asks_top20),
            # split bid/ask side liquidity not separately exposed yet; store
            # combined in both columns for now (sum across both sides is the
            # canonical liquidity_at_signal we report on signal_log)
            metrics.liquidity_5c_usdc,
            metrics.liquidity_5c_usdc,
            metrics.raw_response_hash,
        )
        # Overwrite guard: only write entry-pricing fields if they haven't
        # been captured yet (NULL) or a previous attempt failed
        # ('unavailable'). Once we've recorded a real `clob_l2` entry price,
        # treat it as immutable — the whole point of `first_*`/`signal_entry_*`
        # is that they describe the moment the signal first fired.
        await conn.execute(
            """
            UPDATE signal_log SET
                signal_entry_offer       = $2,
                signal_entry_mid         = $3,
                signal_entry_spread_bps  = $4,
                signal_entry_captured_at = NOW(),
                signal_entry_source      = 'clob_l2',
                liquidity_at_signal_usdc = $5,
                liquidity_tier           = $6
            WHERE id = $1
              AND (signal_entry_source IS NULL OR signal_entry_source = 'unavailable')
            """,
            signal_log_id,
            metrics.entry_offer,
            metrics.mid,
            metrics.spread_bps,
            metrics.liquidity_5c_usdc,
            metrics.liquidity_tier,
        )


# ---------------------------------------------------------------------------
# B12 — insider_wallets (manually curated watchlist)
# ---------------------------------------------------------------------------


async def list_insider_wallets(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT proxy_wallet, label, notes, added_at, last_seen_at
        FROM insider_wallets
        ORDER BY added_at DESC
        """
    )
    return [dict(r) for r in rows]


async def get_insider_wallet(
    conn: asyncpg.Connection, proxy_wallet: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT proxy_wallet, label, notes, added_at, last_seen_at
        FROM insider_wallets
        WHERE proxy_wallet = $1
        """,
        proxy_wallet,
    )
    return dict(row) if row else None


async def upsert_insider_wallet(
    conn: asyncpg.Connection,
    proxy_wallet: str,
    label: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """Insert or update one insider wallet. Returns the persisted row."""
    row = await conn.fetchrow(
        """
        INSERT INTO insider_wallets (proxy_wallet, label, notes)
        VALUES ($1, $2, $3)
        ON CONFLICT (proxy_wallet) DO UPDATE SET
            label = COALESCE(EXCLUDED.label, insider_wallets.label),
            notes = COALESCE(EXCLUDED.notes, insider_wallets.notes)
        RETURNING proxy_wallet, label, notes, added_at, last_seen_at
        """,
        proxy_wallet, label, notes,
    )
    assert row is not None
    return dict(row)


async def delete_insider_wallet(
    conn: asyncpg.Connection, proxy_wallet: str,
) -> bool:
    """Returns True if a row was deleted, False if it didn't exist."""
    result = await conn.execute(
        "DELETE FROM insider_wallets WHERE proxy_wallet = $1", proxy_wallet,
    )
    return result.endswith(" 1")


async def list_insider_wallet_proxies(conn: asyncpg.Connection) -> list[str]:
    """Just the list of proxy wallets — used to extend the position-refresh pool
    so insiders are tracked even if they don't appear on any leaderboard top-N."""
    rows = await conn.fetch("SELECT proxy_wallet FROM insider_wallets")
    return [r["proxy_wallet"] for r in rows]


async def list_signals_pending_price_snapshots(
    conn: asyncpg.Connection,
    *,
    min_age_minutes: int = 0,
    max_age_minutes: int = 125,
) -> list[dict[str, Any]]:
    """B4/F7/R8 -- fetch signal_log rows due for a +5/15/30/60/120 min snapshot.

    Window expanded from 25-125 to 0-125 min so the +5 offset (added in F7)
    is reachable for fresh signals. Job cadence dropped from 30 min to 10
    min in `runner.py` to ensure the +5 window (0-10 min) is hit reliably.

    R8 (Pass 3): now returns BOTH yes_token and no_token, plus the signal's
    direction. The snapshot job picks the direction-side token so half-life
    math compares against the actual price the trader would see, not against
    YES-side spread artifacts on NO signals.

    Filters: signal fired in window, market still open, AND has whichever
    token the signal direction needs. (A YES signal with no clob_token_yes
    is still skipped; same for NO.)
    """
    rows = await conn.fetch(
        """
        SELECT
            s.id              AS signal_log_id,
            s.first_fired_at,
            s.condition_id,
            s.direction,
            m.clob_token_yes,
            m.clob_token_no,
            m.closed
        FROM signal_log s
        JOIN markets m ON m.condition_id = s.condition_id
        WHERE s.first_fired_at <= NOW() - make_interval(mins => $1)
          AND s.first_fired_at >= NOW() - make_interval(mins => $2)
          AND m.closed = FALSE
          AND (
              (s.direction = 'YES' AND m.clob_token_yes IS NOT NULL AND m.clob_token_yes <> '')
              OR
              (s.direction = 'NO'  AND m.clob_token_no  IS NOT NULL AND m.clob_token_no  <> '')
          )
        """,
        min_age_minutes,
        max_age_minutes,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Pick the direction-side token for the caller's convenience.
        if d["direction"] == "NO":
            d["token_id"] = d["clob_token_no"]
        else:
            d["token_id"] = d["clob_token_yes"]
        out.append(d)
    return out


async def existing_price_snapshot_offsets(
    conn: asyncpg.Connection, signal_log_id: int,
) -> set[int]:
    """Return the set of offset_min values already snapshotted for this signal."""
    rows = await conn.fetch(
        """
        SELECT snapshot_offset_min
        FROM signal_price_snapshots
        WHERE signal_log_id = $1
        """,
        signal_log_id,
    )
    return {int(r["snapshot_offset_min"]) for r in rows}


async def insert_signal_price_snapshot(
    conn: asyncpg.Connection,
    *,
    signal_log_id: int,
    snapshot_offset_min: int,
    bid_price: float | None,
    ask_price: float | None,
    token_id: str,
    direction: str | None = None,  # R8 (Pass 3) -- 'YES' or 'NO'
) -> bool:
    """F4 + R8: record one price snapshot with bid + ask + direction.

    R8: prices are stored in DIRECTION-space (i.e., the bid/ask of the
    direction-side token, not always the YES token). The direction column
    distinguishes new direction-aware rows from legacy YES-only rows
    (NULL direction).

    yes_price is kept populated for back-compat (mirrors bid_price). For
    NO-direction snapshots, yes_price now contains the NO-token bid -- but
    the column name is misleading for those rows, which is why direction
    must always be inspected when interpreting these prices going forward.

    Returns True if inserted, False on duplicate (UNIQUE on
    (signal_log_id, snapshot_offset_min)).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO signal_price_snapshots (
            signal_log_id, snapshot_offset_min,
            bid_price, ask_price, yes_price, token_id, direction
        )
        VALUES ($1, $2, $3, $4, $3, $5, $6)
        ON CONFLICT (signal_log_id, snapshot_offset_min) DO NOTHING
        RETURNING id
        """,
        signal_log_id, snapshot_offset_min,
        bid_price, ask_price, token_id, direction,
    )
    return row is not None


async def fetch_signal_price_snapshots(
    conn: asyncpg.Connection,
    signal_log_ids: list[int],
) -> dict[tuple[int, int], dict[str, float | None]]:
    """F4: Return {(signal_log_id, offset_min): {bid, ask, mid}} for ids.

    `mid` is computed from (bid+ask)/2 when both available; falls back to
    bid only on legacy rows that pre-date F4. Used by B10 latency
    simulation (uses ask) and half-life analytics (uses mid).
    """
    if not signal_log_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT signal_log_id, snapshot_offset_min,
               bid_price, ask_price, yes_price
        FROM signal_price_snapshots
        WHERE signal_log_id = ANY($1::BIGINT[])
        """,
        signal_log_ids,
    )
    out: dict[tuple[int, int], dict[str, float | None]] = {}
    for r in rows:
        bid = r["bid_price"] if r["bid_price"] is not None else r["yes_price"]
        ask = r["ask_price"]
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        mid_f = (bid_f + ask_f) / 2.0 if (bid_f is not None and ask_f is not None) else bid_f
        out[(int(r["signal_log_id"]), int(r["snapshot_offset_min"]))] = {
            "bid": bid_f, "ask": ask_f, "mid": mid_f,
        }
    return out


async def set_counterparty_warning(
    conn: asyncpg.Connection, signal_log_id: int,
) -> bool:
    """B2 -- flip counterparty_warning to TRUE on a signal_log row.

    DEPRECATED in Pass 3 (R4+R7) -- use `set_counterparty_count` instead.
    Kept around because some old test code may still reference it. New
    code paths write counterparty_count integer (0 or N) which the legacy
    boolean can be derived from as (count > 0).

    Idempotent: re-running on an already-flagged row is a no-op. Returns
    True if any row was actually updated to TRUE (i.e., previously FALSE).
    """
    result = await conn.execute(
        """
        UPDATE signal_log
        SET counterparty_warning = TRUE
        WHERE id = $1 AND counterparty_warning = FALSE
        """,
        signal_log_id,
    )
    return result.endswith(" 1")


async def set_counterparty_count(
    conn: asyncpg.Connection, signal_log_id: int, count: int,
) -> bool:
    """R4+R7 (Pass 3) -- write the counterparty wallet count to signal_log.

    Replaces the binary `counterparty_warning` boolean from B2/F12 with an
    integer count produced by the new positions-based check. Also bumps the
    legacy boolean for back-compat (count > 0 -> warning TRUE).

    Returns True if any row was updated.
    """
    result = await conn.execute(
        """
        UPDATE signal_log
        SET counterparty_count = $2,
            counterparty_warning = ($2 > 0)
        WHERE id = $1
        """,
        signal_log_id, count,
    )
    return result.endswith(" 1")


async def upsert_watchlist_signal(
    conn: asyncpg.Connection,
    *,
    mode: str,
    category: str,
    top_n: int,
    condition_id: str,
    direction: str,
    trader_count: int,
    aggregate_usdc: float,
    net_skew: float,
    avg_portfolio_fraction: float | None,
    dollar_skew: float | None = None,  # R2 (Pass 3)
) -> bool:
    """B3 -- record/refresh a watchlist candidate. last_seen_at always bumps.

    F10: skips the insert when an OFFICIAL signal already exists for this
    (condition_id, direction) in any lens. Pre-fix mutual exclusion was
    enforced only within one (mode, category, top_n) lens, so the same
    market could appear in /signals/active under one lens and
    /watchlist/active under another simultaneously, breaking the spec.

    R2: also persists `dollar_skew` (USDC-weighted direction skew). Updated
    on each refresh since watchlist tracks current state, not first-fire.

    Returns True if the row was inserted/updated, False if skipped due to
    an existing official signal.
    """
    # R14 (Pass 3): scope NOT EXISTS to recent signals only (last 24h),
    # matching the scope cleanup_watchlist_promoted_to_signal uses. Pre-fix
    # had asymmetric scopes (unscoped here, recency in cleanup), so a
    # recurring market with an ancient resolved signal_log row would block
    # the watchlist insert AND not get cleaned up later.
    result = await conn.execute(
        """
        INSERT INTO watchlist_signals (
            mode, category, top_n, condition_id, direction,
            trader_count, aggregate_usdc, net_skew, avg_portfolio_fraction,
            dollar_skew
        )
        SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
        WHERE NOT EXISTS (
            SELECT 1 FROM signal_log
            WHERE condition_id = $4 AND direction = $5
              AND last_seen_at >= NOW() - INTERVAL '24 hours'
        )
        ON CONFLICT (mode, category, top_n, condition_id, direction) DO UPDATE SET
            trader_count           = EXCLUDED.trader_count,
            aggregate_usdc         = EXCLUDED.aggregate_usdc,
            net_skew               = EXCLUDED.net_skew,
            avg_portfolio_fraction = EXCLUDED.avg_portfolio_fraction,
            dollar_skew            = EXCLUDED.dollar_skew,
            last_seen_at           = NOW()
        """,
        mode, category, top_n, condition_id, direction,
        trader_count, aggregate_usdc, net_skew, avg_portfolio_fraction,
        dollar_skew,
    )
    # asyncpg returns "INSERT 0 1" on success, "INSERT 0 0" on the
    # WHERE-NOT-EXISTS skip path.
    parts = result.split()
    return len(parts) >= 3 and parts[2] == "1"


async def cleanup_watchlist_promoted_to_signal(
    conn: asyncpg.Connection,
) -> int:
    """F10 + R14 (Pass 3): Remove any watchlist row whose (condition_id, direction)
    has been promoted to an OFFICIAL signal that's still active.

    Pre-fix the EXISTS subquery checked against ALL signal_log rows ever,
    including months-old resolved markets. For recurring markets (e.g.
    "will X happen this week" reposted weekly) this silently nuked the
    fresh watchlist row whenever an old resolved version existed.

    R14: scope to last 24h (signals are considered "active" if last_seen_at
    refreshed in that window). Anything older is treated as historical and
    doesn't suppress new watchlist entries.

    Returns count deleted. Run once per cycle after signal-detection writes
    are done so cross-lens mutual exclusion is enforced.
    """
    result = await conn.execute(
        """
        DELETE FROM watchlist_signals w
        WHERE EXISTS (
            SELECT 1 FROM signal_log s
            WHERE s.condition_id = w.condition_id
              AND s.direction = w.direction
              AND s.last_seen_at >= NOW() - INTERVAL '24 hours'
        )
        """,
    )
    parts = result.split()
    return int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0


async def cleanup_watchlist_dropouts(
    conn: asyncpg.Connection,
    *,
    mode: str,
    category: str,
    top_n: int,
    keep_keys: set[tuple[str, str]],
) -> int:
    """Delete watchlist rows for this (mode, category, top_n) whose
    (condition_id, direction) is not in `keep_keys`. Returns count deleted.

    Mirrors the position-dropout cleanup pattern: anything in the table for
    this lens that isn't in the freshly-detected set has dropped below the
    watchlist floors and should be removed.
    """
    if not keep_keys:
        result = await conn.execute(
            """
            DELETE FROM watchlist_signals
            WHERE mode = $1 AND category = $2 AND top_n = $3
            """,
            mode, category, top_n,
        )
    else:
        cids = [k[0] for k in keep_keys]
        dirs = [k[1] for k in keep_keys]
        result = await conn.execute(
            """
            DELETE FROM watchlist_signals
            WHERE mode = $1 AND category = $2 AND top_n = $3
              AND (condition_id, direction) NOT IN (
                  SELECT cid, dir
                  FROM unnest($4::TEXT[], $5::TEXT[]) AS t(cid, dir)
              )
            """,
            mode, category, top_n, cids, dirs,
        )
    # asyncpg returns "DELETE N"
    parts = result.split()
    return int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0


async def list_watchlist_signals(
    conn: asyncpg.Connection,
    *,
    mode: str,
    category: str,
    top_n: int,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            w.id, w.mode, w.category, w.top_n,
            w.condition_id, w.direction,
            w.trader_count,
            w.aggregate_usdc::numeric AS aggregate_usdc,
            w.net_skew::numeric       AS net_skew,
            w.avg_portfolio_fraction::numeric AS avg_portfolio_fraction,
            w.first_seen_at, w.last_seen_at,
            m.question AS market_question, m.slug AS market_slug,
            e.category AS market_category
        FROM watchlist_signals w
        JOIN markets m ON m.condition_id = w.condition_id
        LEFT JOIN events e ON e.id = m.event_id
        WHERE w.mode = $1 AND w.category = $2 AND w.top_n = $3
        ORDER BY w.aggregate_usdc DESC
        """,
        mode, category, top_n,
    )
    return [dict(r) for r in rows]


async def insider_holdings_for_markets(
    conn: asyncpg.Connection, condition_ids: list[str],
) -> set[tuple[str, str]]:
    """For each (cid, direction) where any insider currently holds a position,
    return the pair. Used by /signals/active to set `has_insider=True`.

    Direction is normalised to 'YES' / 'NO' to match Signal.direction. Markets
    where positions.outcome is something else (multi-outcome) are excluded —
    those aren't binary signals anyway.
    """
    if not condition_ids:
        return set()
    rows = await conn.fetch(
        """
        SELECT DISTINCT
            p.condition_id,
            CASE
                WHEN UPPER(p.outcome) = 'YES' THEN 'YES'
                WHEN UPPER(p.outcome) = 'NO'  THEN 'NO'
                ELSE NULL
            END AS direction
        FROM positions p
        JOIN insider_wallets iw ON iw.proxy_wallet = p.proxy_wallet
        WHERE p.size > 0
          AND p.condition_id = ANY($1::TEXT[])
        """,
        condition_ids,
    )
    return {(r["condition_id"], r["direction"]) for r in rows if r["direction"]}
