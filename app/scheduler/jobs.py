"""Scheduled jobs. Each function here is callable standalone (for manual runs)
and from the in-process scheduler.

Step 2 shipped `daily_leaderboard_snapshot`. Step 5 adds `refresh_top_trader_positions`.
Later steps add:
  - refresh_signals_and_alerts     (10-min cadence — Step 6)
  - run_backtest                   (daily — Step 8)
  - heartbeat_email                (daily — Step 7)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

import asyncpg

from app.db import crud
from app.db.connection import init_pool, job_lock
from app.services.polymarket import (
    LeaderboardCategory,
    LeaderboardOrderBy,
    LeaderboardTimePeriod,
    PolymarketClient,
)
from app.services.counterparty import check_and_persist_counterparty_count
from app.services.exit_detector import ExitEvent, detect_exits
from app.services.half_life import pick_offset_for_age
from app.services.market_sync import discover_and_persist_markets
from app.services.orderbook import compute_book_metrics
from app.services.polymarket_types import LeaderboardEntry, Position
from app.services.signal_detector import Signal, detect_signals, detect_signals_and_watchlist
from app.services.trader_ranker import (
    RankingMode,
    gather_union_top_n_wallets,
    rank_traders,
)

log = logging.getLogger(__name__)

# Daily snapshot scope — see decision in session-state.md (skip day/week as noisy).
SNAPSHOT_CATEGORIES: tuple[LeaderboardCategory, ...] = (
    "overall", "politics", "sports", "crypto", "culture", "tech", "finance",
)
SNAPSHOT_TIME_PERIODS: tuple[LeaderboardTimePeriod, ...] = ("all", "month")
SNAPSHOT_ORDER_BYS: tuple[LeaderboardOrderBy, ...] = ("PNL", "VOL")
SNAPSHOT_DEPTH = 100  # top 100 supports the UI's max top-N slider

# Refresh cycle is scheduled every 10 min; warn if a single cycle takes longer
# than this so we can spot when the system is falling behind. Set just below
# the cadence so warnings trigger before tick collisions.
REFRESH_CYCLE_WARN_SECONDS = 9 * 60


@dataclass
class SnapshotResult:
    snapshot_date: date
    total_combinations: int
    total_rows_seen: int
    total_unique_wallets: int
    failures: list[tuple[str, str]]  # [(combo_label, error)]
    duration_seconds: float


async def _snapshot_one(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    snapshot_date: date,
    category: LeaderboardCategory,
    time_period: LeaderboardTimePeriod,
    order_by: LeaderboardOrderBy,
) -> tuple[list[LeaderboardEntry], Exception | None]:
    """Fetch + persist one (category, time_period, order_by) leaderboard."""
    try:
        entries = await pm.get_leaderboard(
            order_by=order_by,
            time_period=time_period,
            category=category,
            depth=SNAPSHOT_DEPTH,
        )
    except Exception as e:  # noqa: BLE001 — we want to keep going on per-combo failure
        return [], e

    if not entries:
        return [], None

    # Persist inside try/except so a single combo's DB error (FK miss,
    # transient timeout, etc.) doesn't kill the whole 28-combo snapshot run.
    # The outer loop records `failures` and continues; without this the
    # exception bubbles out and we lose every combo we hadn't reached yet.
    try:
        async with conn.transaction():
            await crud.upsert_traders_bulk(conn, entries)
            await crud.insert_leaderboard_snapshot(
                conn,
                snapshot_date=snapshot_date,
                category=category,
                time_period=time_period,
                order_by=order_by,
                entries=entries,
            )
    except Exception as e:  # noqa: BLE001
        return [], e
    return entries, None


async def daily_leaderboard_snapshot(
    snapshot_date: date | None = None,
) -> SnapshotResult:
    """Fetch and persist all 28 leaderboard combos for one day.

    Idempotent — re-running on the same date is safe (ON CONFLICT DO NOTHING
    on the (snapshot_date, category, time_period, order_by, proxy_wallet) key).
    """
    started = datetime.now(timezone.utc)
    snapshot_date = snapshot_date or started.date()
    log.info("=== daily_leaderboard_snapshot for %s ===", snapshot_date)

    pool = await init_pool(min_size=1, max_size=12)
    failures: list[tuple[str, str]] = []
    total_rows = 0
    unique_wallets: set[str] = set()
    combo_count = 0

    # Advisory lock prevents the daily snapshot from running concurrently
    # with another invocation (manual retrigger, second worker, etc.).
    async with job_lock("daily_snapshot") as got:
        if not got:
            log.info("daily_snapshot lock held — skipping (another worker is running it)")
            return SnapshotResult(
                snapshot_date=snapshot_date, total_combinations=0,
                total_rows_seen=0, total_unique_wallets=0,
                failures=[("lock", "held by another worker")],
                duration_seconds=0.0,
            )

        # F24: acquire the pool connection PER COMBO instead of holding one
        # across the full 28-combo run. Each combo does ~1-2 HTTP calls
        # (rate-limited, can take several seconds total) and a short DB
        # write. Pre-fix held one connection idle during every HTTP round
        # trip, starving the rest of the system. Same pattern was already
        # fixed in `auto_close_resolved_paper_trades`.
        async with PolymarketClient() as pm:
            for category in SNAPSHOT_CATEGORIES:
                for time_period in SNAPSHOT_TIME_PERIODS:
                    for order_by in SNAPSHOT_ORDER_BYS:
                        combo_count += 1
                        label = f"{category}/{time_period}/{order_by}"
                        log.info("  [%02d/28] %s ...", combo_count, label)
                        async with pool.acquire() as conn:
                            entries, err = await _snapshot_one(
                                conn, pm, snapshot_date, category, time_period, order_by
                            )
                            if err is not None:
                                log.warning("    FAILED: %s: %s", label, err)
                                failures.append((label, repr(err)))
                                continue
                            total_rows += len(entries)
                            for e in entries:
                                unique_wallets.add(e.proxy_wallet)
                            log.info("    ok: %d entries  top=%s ($%.0f)",
                                     len(entries),
                                     entries[0].user_name if entries else "?",
                                     entries[0].pnl if entries else 0)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    result = SnapshotResult(
        snapshot_date=snapshot_date,
        total_combinations=combo_count,
        total_rows_seen=total_rows,
        total_unique_wallets=len(unique_wallets),
        failures=failures,
        duration_seconds=duration,
    )
    log.info(
        "=== done in %.1fs — %d rows, %d unique wallets, %d failures ===",
        duration, total_rows, len(unique_wallets), len(failures),
    )
    return result


# ===========================================================================
# Position refresh — 10-min cadence in production. Fetches current open
# positions for every wallet in the union of (mode × category) top-N pools,
# upserts them locally, and snapshots their portfolio value.
# ===========================================================================

POSITION_REFRESH_TOP_N = 100  # depth tracked per (mode, category)
POSITION_REFRESH_MODES: tuple[RankingMode, ...] = ("absolute", "hybrid", "specialist")


@dataclass
class PositionRefreshResult:
    wallets_targeted: int
    wallets_succeeded: int
    positions_persisted: int
    portfolio_values_persisted: int
    failures: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0


async def _gather_tracked_wallets(
    conn: asyncpg.Connection, top_n: int
) -> list[str]:
    """Union of every top-N wallet across all (mode, category) combos PLUS
    the manually curated insider_wallets list (B12).

    Insider wallets are appended unconditionally so they remain tracked even
    when they don't appear on any leaderboard top-N — that's the whole point
    of the manual list.

    Single bulk SQL via `gather_union_top_n_wallets` for the top-N pool;
    one additional SELECT for the insider proxies. Result is deduped.
    """
    pool_wallets = await gather_union_top_n_wallets(
        conn, top_n=top_n, categories=SNAPSHOT_CATEGORIES
    )
    insider_wallets = await crud.list_insider_wallet_proxies(conn)
    if not insider_wallets:
        return pool_wallets
    seen = set(pool_wallets)
    extras = [w for w in insider_wallets if w not in seen]
    if not extras:
        return pool_wallets
    return sorted(seen | set(extras))


async def _fetch_one_wallet(
    pm: PolymarketClient, wallet: str
) -> tuple[str, list[Position] | None, float | None, Exception | None]:
    """F3: also fetch the wallet's full portfolio value (positions + cash +
    unredeemed) so the portfolio_fraction denominator is honest. Pre-fix
    used `sum(open_position.current_value)` which excluded USDC cash —
    a trader with $10k positions + $90k cash looked 100% deployed.

    Portfolio-value fetch is best-effort: if it fails, we return None and
    the caller falls back to the old position-sum computation so the
    cycle never crashes on a /value blip.
    """
    try:
        positions = await pm.get_positions(wallet, limit=500)
    except Exception as e:  # noqa: BLE001
        return wallet, None, None, e
    portfolio_value: float | None = None
    try:
        pv = await pm.get_portfolio_value(wallet)
        if pv is not None and pv.value is not None:
            portfolio_value = float(pv.value)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "F3: get_portfolio_value failed for %s: %s — falling back to sum(positions)",
            wallet[:12], e,
        )
    return wallet, positions, portfolio_value, None


async def refresh_top_trader_positions(
    top_n: int = POSITION_REFRESH_TOP_N,
    concurrency: int = 12,
) -> PositionRefreshResult:
    """Fetch + persist current positions for every tracked wallet.

    Uses asyncio concurrency, but the rate limiter inside PolymarketClient
    paces actual outgoing calls (default ~10/s). With ~1000 wallets this
    takes roughly 100 seconds.
    """
    started = datetime.now(timezone.utc)
    log.info("=== refresh_top_trader_positions (top_n=%d) ===", top_n)

    pool = await init_pool(min_size=1, max_size=12)
    async with pool.acquire() as conn:
        wallets = await _gather_tracked_wallets(conn, top_n)
    log.info("tracked wallet pool: %d unique", len(wallets))

    succeeded = 0
    positions_persisted = 0
    portfolio_values_persisted = 0
    failures: list[tuple[str, str]] = []

    sem = asyncio.Semaphore(concurrency)

    async with PolymarketClient() as pm:
        async def fetch_one(w: str) -> tuple[str, list[Position] | None, float | None, Exception | None]:
            async with sem:
                return await _fetch_one_wallet(pm, w)

        # Phase 1: launch all fetches concurrently, collect results in memory.
        # We can't persist incrementally because we first need to discover any
        # unknown markets in bulk (one batch call cheaper than one-per-wallet).
        # F3: each fetch_one now also pulls /value so we have the true
        # portfolio denominator (cash + positions + unredeemed) for phase 3.
        log.info("phase 1: fetching positions for %d wallets...", len(wallets))
        tasks = [asyncio.create_task(fetch_one(w)) for w in wallets]
        # tuple is (wallet, positions, portfolio_value_from_api)
        results: list[tuple[str, list[Position], float | None]] = []
        for fut in asyncio.as_completed(tasks):
            wallet, positions, pv_api, err = await fut
            if err is not None:
                failures.append((wallet, repr(err)))
                continue
            assert positions is not None
            results.append((wallet, positions, pv_api))

        # Phase 2: discover and persist any markets we don't have yet.
        all_cids: set[str] = {
            p.condition_id for _, plist, _pv in results for p in plist if p.condition_id
        }
        log.info("phase 2: ensuring market metadata for %d distinct condition_ids...",
                 len(all_cids))
        async with pool.acquire() as conn:
            new_markets_written = await discover_and_persist_markets(conn, pm, all_cids)
        log.info("phase 2: %d new markets persisted via JIT discovery", new_markets_written)

        # Phase 3: persist positions and portfolio values per wallet.
        log.info("phase 3: persisting positions for %d wallets...", len(results))
        from app.services.polymarket_types import PortfolioValue
        positions_dropped_unknown_market = 0  # surfaced in logs to avoid silent loss
        for wallet, positions, pv_api in results:
            try:
                async with pool.acquire() as conn:
                    # Even after JIT discovery, a few cids may still be missing
                    # (e.g. market archived since the position was opened).
                    # Filter as a safety net so the FK never trips.
                    valid = await _filter_known_markets(conn, positions)
                    positions_dropped_unknown_market += len(positions) - len(valid)
                    if valid:
                        await crud.upsert_positions_for_trader(conn, wallet, valid)
                    # F3: prefer the value from data-api /value (true total
                    # equity including USDC cash + unredeemed) over the
                    # sum-of-open-positions fallback. Only fall back when the
                    # API call failed during phase 1.
                    if pv_api is not None:
                        portfolio_total = pv_api
                    else:
                        portfolio_total = sum((p.current_value or 0.0) for p in valid)
                    # R5 (Pass 3): always write a PV snapshot, even when 0.
                    # Pre-fix only wrote when portfolio_total > 0, so wallets
                    # that briefly went flat had no fresh PV row. The signal
                    # detector then read a stale row from weeks ago, biasing
                    # avg_portfolio_fraction. Now: always write -- a flat
                    # wallet legitimately has portfolio_value=0 and we record
                    # that. Only skip writes if the API call failed AND we
                    # have no positions (genuinely no information).
                    if pv_api is not None or portfolio_total > 0:
                        await crud.insert_portfolio_value(
                            conn,
                            PortfolioValue(
                                proxy_wallet=wallet,
                                value=max(portfolio_total, 0.0),
                            ),
                        )
                        portfolio_values_persisted += 1
                    succeeded += 1
                    positions_persisted += len(valid)
            except Exception as e:  # noqa: BLE001
                failures.append((wallet, f"persist failed: {e!r}"))
        if positions_dropped_unknown_market:
            log.warning(
                "phase 3: dropped %d position(s) whose markets were not in DB even after JIT discovery — likely archived",
                positions_dropped_unknown_market,
            )

        # Phase 4: drop-out cleanup. Delete positions for wallets no longer
        # in the tracked top-N (e.g. dropped from leaderboard since last
        # cycle). Their old positions would otherwise linger forever and
        # contribute zombie data to signal aggregation. Only runs if we
        # actually have a wallet list to compare against (defensive — an
        # empty list would be interpreted as "delete everything").
        if wallets:
            try:
                async with pool.acquire() as conn:
                    dropped = await crud.delete_positions_for_dropped_wallets(
                        conn, wallets
                    )
                if dropped:
                    log.info(
                        "phase 4: cleaned up %d position(s) from wallets dropped from top-N",
                        dropped,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("phase 4 dropout cleanup failed: %r", e)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    result = PositionRefreshResult(
        wallets_targeted=len(wallets),
        wallets_succeeded=succeeded,
        positions_persisted=positions_persisted,
        portfolio_values_persisted=portfolio_values_persisted,
        failures=failures,
        duration_seconds=duration,
    )
    log.info(
        "=== done in %.1fs — %d/%d wallets, %d positions, %d failures ===",
        duration, succeeded, len(wallets), positions_persisted, len(failures),
    )
    return result


async def _filter_known_markets(
    conn: asyncpg.Connection, positions: list[Position]
) -> list[Position]:
    """Drop positions whose condition_id we don't have a market row for yet.

    The positions table FKs to markets — without a market row the upsert
    fails. We log how many were dropped so the operator can re-run market_sync
    if needed.
    """
    if not positions:
        return []
    cids = list({p.condition_id for p in positions if p.condition_id})
    rows = await conn.fetch(
        "SELECT condition_id FROM markets WHERE condition_id = ANY($1::TEXT[])",
        cids,
    )
    known = {r["condition_id"] for r in rows}
    return [p for p in positions if p.condition_id in known]


# ===========================================================================
# Signal logging — runs after every position refresh. For each (mode×category)
# at the canonical top_n=50 we run detect_signals() and upsert each firing
# signal into `signal_log`. New rows = "new signals" the UI badge counts.
# Resolved/expired signals stay in the log with their lifetime stats — they
# feed the walk-forward backtest (Step 8).
# ===========================================================================

LOG_SIGNALS_TOP_N = 50  # canonical depth — UI default; must match badge query
LOG_SIGNALS_MODES: tuple[RankingMode, ...] = ("absolute", "hybrid", "specialist")


@dataclass
class LogSignalsResult:
    combos_run: int
    signals_seen: int        # total firing-signal observations across all combos
    new_signals: int         # of those, fresh inserts (first_fired_at = NOW)
    watchlist_seen: int = 0       # B3: total watchlist observations
    watchlist_dropped: int = 0    # B3: rows pruned because they fell below floors
    counterparty_warnings: int = 0  # B2: fresh signals where smart money was also seller
    failures: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0


async def _capture_book_for_signal(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    signal: Signal,
    mode: str,
    category: str,
    top_n: int,
) -> None:
    """Snapshot the CLOB book for a freshly-inserted signal and persist
    entry-pricing fields. Errors mark the row 'unavailable' so backtest
    knows to skip it; never raises out of this function.
    """
    sid = await crud.get_signal_log_id(
        conn, mode, category, top_n, signal.condition_id, signal.direction
    )
    if sid is None:
        return  # row vanished; nothing to do

    yes_token, no_token = await crud.get_market_clob_tokens(conn, signal.condition_id)
    token_id = yes_token if signal.direction == "YES" else no_token
    if not token_id:
        from app.services.orderbook import BookMetrics
        await crud.persist_book_snapshot_and_pricing(
            conn, sid, token_id="", side=signal.direction,
            metrics=BookMetrics(
                best_bid=None, best_ask=None, mid=None, spread_bps=None,
                entry_offer=None, liquidity_5c_usdc=None,
                liquidity_tier="unknown",
                bids_top20=[], asks_top20=[], raw_response_hash="",
                available=False,
            ),
        )
        return

    try:
        book = await pm.get_orderbook(token_id)
    except Exception as e:  # noqa: BLE001
        log.warning("  book fetch raised for %s: %s", token_id[:12], e)
        book = None

    metrics = compute_book_metrics(book, signal.direction)
    await crud.persist_book_snapshot_and_pricing(
        conn, sid, token_id, signal.direction, metrics
    )


async def log_signals(top_n: int = LOG_SIGNALS_TOP_N) -> LogSignalsResult:
    """Run detect_signals across all (mode × category) combos and persist.

    Designed to run on the same 10-min cadence as `refresh_top_trader_positions`,
    immediately after it (so positions are fresh). Idempotent — UNIQUE on
    (mode, category, top_n, condition_id, direction) keeps lifetime stats stable.

    For freshly-inserted signals we additionally snapshot the CLOB orderbook
    to capture an executable entry price (`signal_entry_offer`) and depth.
    Book capture happens outside the upsert transaction to avoid holding it
    open during network calls.
    """
    started = datetime.now(timezone.utc)
    log.info("=== log_signals (top_n=%d) ===", top_n)

    pool = await init_pool(min_size=1, max_size=12)
    failures: list[tuple[str, str]] = []
    signals_seen = 0
    new_signals = 0
    combos_run = 0
    books_captured = 0
    watchlist_seen = 0
    watchlist_dropped = 0
    counterparty_warnings = 0

    async with PolymarketClient() as pm:
        async with pool.acquire() as conn:
            # B2/F9: union of all (mode, category, top_n) wallets — the
            # "tracked pool" for counterparty check. Pre-fix used the calling
            # `top_n` (=LOG_SIGNALS_TOP_N=50), inconsistent with position-
            # refresh and exit-detector both using POSITION_REFRESH_TOP_N=100.
            # Net: a wallet ranked 51-100 was tracked + could fire exits but
            # never triggered a counterparty warning. Now uses the broadest
            # depth so the counterparty check sees every tracked wallet.
            # Computed once per cycle and reused across every fresh signal.
            try:
                tracked_pool_list = await gather_union_top_n_wallets(
                    conn, top_n=POSITION_REFRESH_TOP_N,
                    categories=SNAPSHOT_CATEGORIES,
                )
                tracked_pool: set[str] = {w.lower() for w in tracked_pool_list}
                log.info("counterparty: tracked pool size = %d wallets", len(tracked_pool))
            except Exception as e:  # noqa: BLE001
                log.warning("counterparty: failed to gather tracked pool: %s", e)
                tracked_pool = set()
            for mode in LOG_SIGNALS_MODES:
                for category in SNAPSHOT_CATEGORIES:
                    combos_run += 1
                    label = f"{mode}/{category}/{top_n}"
                    try:
                        det = await detect_signals_and_watchlist(
                            conn, mode=mode, category=category, top_n=top_n
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning("  %s FAILED: %s", label, e)
                        failures.append((label, repr(e)))
                        continue

                    sigs = det.official
                    watch = det.watchlist

                    if not sigs and not watch:
                        log.info("  %-28s -> 0 signals / 0 watchlist", label)
                        # Still cleanup any stale watchlist rows for this lens.
                        try:
                            dropped = await crud.cleanup_watchlist_dropouts(
                                conn, mode=mode, category=category, top_n=top_n,
                                keep_keys=set(),
                            )
                            watchlist_dropped += dropped
                        except Exception as e:  # noqa: BLE001
                            log.warning("  watchlist cleanup %s: %s", label, e)
                        continue

                    # Phase A: persist the official signals inside a tight transaction.
                    fresh_signals: list[Signal] = []
                    if sigs:
                        async with conn.transaction():
                            for s in sigs:
                                inserted = await crud.upsert_signal_log_entry(
                                    conn,
                                    mode=mode,
                                    category=category,
                                    top_n=top_n,
                                    condition_id=s.condition_id,
                                    direction=s.direction,
                                    trader_count=s.trader_count,
                                    avg_portfolio_fraction=s.avg_portfolio_fraction,
                                    aggregate_usdc=s.aggregate_usdc,
                                    direction_skew=s.direction_skew,
                                    first_top_trader_entry_price=s.avg_entry_price,
                                    current_price=s.current_price,
                                    cluster_id=s.event_id,
                                    market_type="binary",
                                    direction_dollar_skew=s.direction_dollar_skew,
                                    # contributing_wallets populated in R3b step
                                )
                                if inserted:
                                    fresh_signals.append(s)

                    # Phase A': persist watchlist candidates + drop stale ones.
                    if watch or sigs:
                        try:
                            for w in watch:
                                await crud.upsert_watchlist_signal(
                                    conn,
                                    mode=mode, category=category, top_n=top_n,
                                    condition_id=w.condition_id,
                                    direction=w.direction,
                                    trader_count=w.trader_count,
                                    aggregate_usdc=w.aggregate_usdc,
                                    net_skew=w.direction_skew,
                                    avg_portfolio_fraction=w.avg_portfolio_fraction,
                                    dollar_skew=w.direction_dollar_skew,
                                )
                            keep_keys = {(w.condition_id, w.direction) for w in watch}
                            dropped = await crud.cleanup_watchlist_dropouts(
                                conn, mode=mode, category=category, top_n=top_n,
                                keep_keys=keep_keys,
                            )
                            watchlist_dropped += dropped
                        except Exception as e:  # noqa: BLE001
                            log.warning("  watchlist persistence %s: %s", label, e)
                            failures.append((f"{label}/watchlist", repr(e)))

                    # Phase B: capture orderbook for fresh OFFICIAL signals (outside tx).
                    # Watchlist rows do NOT trigger book capture — they're not
                    # eligible for paper trading or backtest, so an executable
                    # entry price isn't needed and would just waste API calls.
                    # B2: also run the counterparty check using the same token_id
                    # we just used for the book lookup.
                    for s in fresh_signals:
                        try:
                            await _capture_book_for_signal(
                                conn, pm, s, mode, category, top_n
                            )
                            books_captured += 1
                        except Exception as e:  # noqa: BLE001
                            log.warning(
                                "  book capture failed for %s/%s: %s",
                                s.condition_id[:12], s.direction, e,
                            )
                            failures.append(
                                (f"{label}/book/{s.condition_id[:12]}", repr(e))
                            )

                        # R4+R7 (Pass 3): positions-based counterparty check.
                        # Replaces the fills-based F12 path. Non-blocking:
                        # failures leave counterparty_count = 0 (column default).
                        # The check now requires opposing-side wallets to hold
                        # >=$5k AND be >=75% concentrated against -- filters out
                        # partial profit-takers and hedgers that flooded the
                        # warning under the old logic.
                        if tracked_pool:
                            try:
                                sid = await crud.get_signal_log_id(
                                    conn, mode, category, top_n,
                                    s.condition_id, s.direction,
                                )
                                if sid is not None:
                                    cp_count = await check_and_persist_counterparty_count(
                                        conn,
                                        signal_log_id=sid,
                                        condition_id=s.condition_id,
                                        signal_direction=s.direction,
                                        tracked_pool=tracked_pool,
                                    )
                                    if cp_count > 0:
                                        counterparty_warnings += 1
                            except Exception as e:  # noqa: BLE001
                                log.warning(
                                    "  counterparty check failed for %s/%s: %s",
                                    s.condition_id[:12], s.direction, e,
                                )

                    signals_seen += len(sigs)
                    new_signals += len(fresh_signals)
                    watchlist_seen += len(watch)
                    log.info(
                        "  %-28s -> %d signals (%d new) | %d watchlist",
                        label, len(sigs), len(fresh_signals), len(watch),
                    )

    if books_captured:
        log.info("captured %d orderbook snapshot(s)", books_captured)

    # A20: heal any signal_log rows that still have cluster_id NULL because
    # the event hadn't been discovered yet at first-fire time. Cheap UPDATE,
    # touches at most a handful of rows per cycle.
    async with pool.acquire() as conn:
        healed = await crud.backfill_signal_log_cluster_ids(conn)
        if healed:
            log.info("backfilled cluster_id on %d previously-NULL signal_log row(s)", healed)

        # F10: cross-lens watchlist mutual exclusion. Pre-fix the in-lens
        # cleanup above prevented (cid, direction) from being in BOTH
        # watchlist + official within the SAME (mode, category, top_n) lens
        # but did nothing across lenses. Now: any watchlist row whose
        # (cid, direction) has been promoted to an official signal in
        # ANY lens gets removed in one bulk pass per cycle.
        promoted_cleared = await crud.cleanup_watchlist_promoted_to_signal(conn)
        if promoted_cleared:
            log.info(
                "F10: removed %d watchlist row(s) promoted to official in another lens",
                promoted_cleared,
            )
            watchlist_dropped += promoted_cleared

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    result = LogSignalsResult(
        combos_run=combos_run,
        signals_seen=signals_seen,
        new_signals=new_signals,
        watchlist_seen=watchlist_seen,
        watchlist_dropped=watchlist_dropped,
        counterparty_warnings=counterparty_warnings,
        failures=failures,
        duration_seconds=duration,
    )
    log.info(
        "=== done in %.1fs — %d combos, %d signals (%d new, %d counterparty), "
        "%d watchlist (%d dropped), %d failures ===",
        duration, combos_run, signals_seen, new_signals, counterparty_warnings,
        watchlist_seen, watchlist_dropped, len(failures),
    )
    return result


# ===========================================================================
# Wallet classification — runs weekly. For every wallet in the union of all
# top-N pools, fetch recent trades and tag as directional / market_maker /
# arbitrage / unknown. The trader_ranker SQL excludes MM/arb wallets from
# top-N pools, cleaning the consensus signal at source.
# ===========================================================================

CLASSIFY_TOP_N = 100  # union pool depth — same as POSITION_REFRESH_TOP_N


@dataclass
class ClassifyResult:
    wallets_classified: int
    by_class: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0


async def classify_tracked_wallets(
    top_n: int = CLASSIFY_TOP_N, concurrency: int = 8
) -> ClassifyResult:
    """Compute behavioral classifications for every tracked wallet.

    Cost: ~1 API call (last 500 trades) per wallet, paced by the rate limiter.
    With ~500 wallets at 10 req/s, runs in ~50 seconds + persistence overhead.
    """
    started = datetime.now(timezone.utc)
    log.info("=== classify_tracked_wallets (top_n=%d) ===", top_n)

    pool = await init_pool(min_size=1, max_size=12)
    async with pool.acquire() as conn:
        wallets = await _gather_tracked_wallets(conn, top_n)
    log.info("classifying %d wallets", len(wallets))

    from app.services.wallet_classifier import (
        CLASSIFIER_VERSION, classify, compute_features,
    )

    by_class: dict[str, int] = {}
    failures: list[tuple[str, str]] = []
    sem = asyncio.Semaphore(concurrency)

    async def classify_one(wallet: str) -> tuple[str, str | None, Exception | None]:
        async with sem:
            try:
                trades = await pm.get_trades(wallet, limit=500)
            except Exception as e:  # noqa: BLE001
                return wallet, None, e
        features = compute_features(trades)
        result = classify(features)
        async with pool.acquire() as conn:
            await crud.upsert_wallet_classification(
                conn,
                proxy_wallet=wallet,
                wallet_class=result.wallet_class,
                confidence=result.confidence,
                features=result.features,
                trades_observed=int(features.get("n_trades", 0) or 0),
                classifier_version=CLASSIFIER_VERSION,
            )
        return wallet, result.wallet_class, None

    async with PolymarketClient() as pm:
        tasks = [classify_one(w) for w in wallets]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    for wallet, klass, err in results:
        if err is not None:
            failures.append((wallet, repr(err)))
        elif klass is not None:
            by_class[klass] = by_class.get(klass, 0) + 1

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.1fs — %d classified, %d failures, distribution=%s ===",
        duration, sum(by_class.values()), len(failures), by_class,
    )
    return ClassifyResult(
        wallets_classified=sum(by_class.values()),
        by_class=by_class,
        failures=failures,
        duration_seconds=duration,
    )


# ===========================================================================
# Sybil cluster detection — finds wallet groups that systematically trade
# the same markets at the same time (the Théo / Fredi9999 pattern). Runs
# weekly. Clusters reduce the inflated "5 distinct top traders" count when
# multiple wallets are actually one entity.
# ===========================================================================


@dataclass
class SybilDetectionResult:
    wallets_analyzed: int
    clusters_found: int
    members_in_clusters: int
    duration_seconds: float = 0.0


async def detect_sybil_clusters_in_pool(
    top_n: int = CLASSIFY_TOP_N, concurrency: int = 8
) -> SybilDetectionResult:
    """Fetch trade history for tracked wallets and detect sybil clusters.

    Like classification, costs ~1 API call per wallet (last 500 trades).
    The two jobs could share fetched trades, but we keep them separate for
    simplicity — each is independently re-runnable. Total ~50 seconds at
    10 req/s with 530 wallets.
    """
    started = datetime.now(timezone.utc)
    log.info("=== detect_sybil_clusters_in_pool (top_n=%d) ===", top_n)

    pool = await init_pool(min_size=1, max_size=12)
    async with pool.acquire() as conn:
        wallets = await _gather_tracked_wallets(conn, top_n)
    log.info("fetching trades for %d wallets", len(wallets))

    sem = asyncio.Semaphore(concurrency)
    trades_by_wallet: dict[str, list] = {}

    async def fetch_one(wallet: str) -> tuple[str, list]:
        async with sem:
            try:
                ts = await pm.get_trades(wallet, limit=500)
                return wallet, ts
            except Exception as e:  # noqa: BLE001
                log.warning("trades fetch failed for %s: %s", wallet[:12], e)
                return wallet, []

    async with PolymarketClient() as pm:
        results = await asyncio.gather(*[fetch_one(w) for w in wallets])

    for wallet, ts in results:
        trades_by_wallet[wallet] = ts

    from app.services.sybil_detector import detect_clusters
    clusters = detect_clusters(trades_by_wallet)

    async with pool.acquire() as conn:
        deleted = await crud.clear_sybil_clusters_by_method(conn, "time_correlation")
        if deleted:
            log.info("cleared %d previous time_correlation cluster(s)", deleted)
        for c in clusters:
            cluster_id = await crud.persist_sybil_cluster(conn, c.members, c.evidence)
            trades_observed_by_wallet = {
                w: len(trades_by_wallet.get(w, [])) for w in c.members
            }
            await crud.mark_wallets_likely_sybil(
                conn,
                proxy_wallets=c.members,
                cluster_id=cluster_id,
                evidence=c.evidence,
                trades_observed_by_wallet=trades_observed_by_wallet,
            )

    members_total = sum(len(c.members) for c in clusters)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.1fs — %d clusters found, %d wallets clustered ===",
        duration, len(clusters), members_total,
    )
    return SybilDetectionResult(
        wallets_analyzed=len(wallets),
        clusters_found=len(clusters),
        members_in_clusters=members_total,
        duration_seconds=duration,
    )


# ===========================================================================
# Auto-close paper trades when their market resolves. Settles each open trade
# at $1 (winning side), $0 (losing side), or $0.50 (oracle 50_50). Same
# accounting model as the backtest engine, so paper P&L matches what the
# backtest would show for the equivalent signal.
# ===========================================================================


@dataclass
class AutoCloseResult:
    trades_closed: int
    realized_pnl_total: float
    duration_seconds: float = 0.0


def _payoff_for_resolution(direction: str, resolved_outcome: str) -> float | None:
    """Per-share payoff at resolution. None if outcome can't be paired with direction."""
    if resolved_outcome == "50_50":
        return 0.5
    if resolved_outcome in ("YES", "NO"):
        return 1.0 if resolved_outcome == direction else 0.0
    return None  # VOID, PENDING — caller skips


async def _list_open_paper_trade_cids(conn: asyncpg.Connection) -> list[str]:
    """List unique condition_ids behind any currently open paper trade.

    Pulled out so we can release the DB connection before hitting gamma —
    avoids holding a pooled connection through HTTP round trips.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT pt.condition_id
        FROM paper_trades pt
        JOIN markets m USING (condition_id)
        WHERE pt.status = 'open'
        """
    )
    return [r["condition_id"] for r in rows]


async def _apply_paper_trade_market_refresh(
    conn: asyncpg.Connection, fetched_markets: list
) -> int:
    """Apply gamma-fetched market state to our local DB. DB-only; pure writes."""
    from app.services.market_sync import _infer_resolved_outcome, _parse_iso
    n = 0
    for m in fetched_markets:
        if not m.condition_id:
            continue
        resolved = _infer_resolved_outcome(m)
        await conn.execute(
            """
            UPDATE markets SET
                closed           = $2,
                resolved_outcome = COALESCE($3, resolved_outcome),
                end_date         = COALESCE($4, end_date),
                last_synced_at   = NOW()
            WHERE condition_id = $1
            """,
            m.condition_id, m.closed, resolved, _parse_iso(m.end_date),
        )
        n += 1
    return n


# ===========================================================================
# Smart-money exit detection (B1) — runs every 10-min cycle, AFTER positions
# are refreshed but BEFORE auto-close-resolved (so paper trades that should
# settle on smart-money exit do so before the resolution path runs). Exits
# are detected on the same canonical (mode, category, top_n) selections used
# to fire signals.
# ===========================================================================


@dataclass
class ExitDetectionResult:
    candidates_evaluated: int
    exits_fired: int
    paper_trades_closed: int
    paper_trades_realized_pnl_usdc: float
    duration_seconds: float = 0.0


async def _capture_current_bid(
    pm: PolymarketClient,
    conn: asyncpg.Connection,
    condition_id: str,
    direction: str,
) -> float | None:
    """Snapshot the current bid for the side we'd be selling out of.

    On exit, a YES-direction paper trade SELLS YES → counterparty pays the
    YES bid. A NO-direction paper trade SELLS NO → counterparty pays the
    NO bid. So we always look at the bid for the trade's `direction` token.

    Returns None on book-fetch failure; caller settles at current local
    `cur_price` as a degraded fallback.
    """
    yes_token, no_token = await crud.get_market_clob_tokens(conn, condition_id)
    token_id = yes_token if direction == "YES" else no_token
    if not token_id:
        return None
    try:
        book = await pm.get_orderbook(token_id)
    except Exception as e:  # noqa: BLE001
        log.warning("exit-bid book fetch failed for %s: %s", token_id[:12], e)
        return None
    if not isinstance(book, dict):
        return None
    bids = book.get("bids") or []
    if not bids or not isinstance(bids[0], dict):
        return None
    try:
        return float(bids[0].get("price"))
    except (TypeError, ValueError):
        return None


async def _settle_paper_trade_at_exit(
    conn: asyncpg.Connection,
    trade: dict[str, Any],
    exit_price: float,
) -> tuple[bool, float]:
    """Settle one open paper trade against an exit price (current bid).

    R10 (Pass 3): unified close formula via paper_trade_close.compute_realized_pnl.
    Pre-fix this used a hand-rolled (and inconsistent with manual-close) formula
    that derived fee_rate as fee_usdc/size -- which divided correctly by
    accident under the OLD flat-percentage fee model but breaks under the
    correct Polymarket curve where fee_rate varies with price.

    Returns (closed_ok, realized_pnl).
    """
    from app.services.paper_trade_close import compute_realized_pnl

    entry_price = float(trade["entry_price"])
    size = float(trade["entry_size_usdc"])
    if entry_price <= 0 or size <= 0 or exit_price <= 0 or entry_price >= 1.0:
        return False, 0.0

    # The trade's category is needed for the new fee curve. paper_trades doesn't
    # store it directly -- look it up from markets via the condition_id.
    cat_row = await conn.fetchrow(
        """
        SELECT e.category FROM markets m
        LEFT JOIN events e ON e.id = m.event_id
        WHERE m.condition_id = $1
        """,
        trade["condition_id"],
    )
    category = cat_row["category"] if cat_row else None

    close = compute_realized_pnl(
        entry_price=entry_price,
        entry_size_usdc=size,
        entry_slippage_usdc=float(trade.get("entry_slippage_usdc") or 0.0),
        entry_fee_usdc=float(trade.get("entry_fee_usdc") or 0.0),
        exit_price=exit_price,
        exit_kind="smart_money_exit",
        category=category,
    )

    ok = await crud.close_paper_trade_smart_money_exit(
        conn,
        trade_id=int(trade["id"]),
        exit_price=exit_price,
        realized_pnl_usdc=close.realized_pnl_usdc,
    )
    return ok, close.realized_pnl_usdc


async def detect_and_persist_exits(
    top_n: int = LOG_SIGNALS_TOP_N,
) -> ExitDetectionResult:
    """Run the exit detector across the tracked wallet pool, persist exits,
    and auto-close any open paper trades on exited signals.

    Cost: one short bulk SQL pass per candidate to recompute current
    aggregates, plus one orderbook fetch per fresh exit (only fresh ones —
    re-runs of an already-exited signal are no-ops via the UNIQUE constraint).
    For ~20 active signals that's negligible; the orderbook fetches dominate.
    """
    started = datetime.now(timezone.utc)
    log.info("=== detect_and_persist_exits ===")

    pool = await init_pool()
    candidates_evaluated = 0
    exits_fired = 0
    trades_closed = 0
    realized_total = 0.0

    # Step 1: short DB acquire — fetch tracked wallets + run detection
    async with pool.acquire() as conn:
        wallets = await gather_union_top_n_wallets(
            conn, top_n=POSITION_REFRESH_TOP_N, categories=SNAPSHOT_CATEGORIES,
        )
        events = await detect_exits(conn, wallets)
        candidates_evaluated = len(events)

    if not events:
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        log.info("=== done in %.2fs — 0 exits ===", duration)
        return ExitDetectionResult(
            candidates_evaluated=0, exits_fired=0,
            paper_trades_closed=0, paper_trades_realized_pnl_usdc=0.0,
            duration_seconds=duration,
        )

    # Step 2: for each event, capture current bid + persist + close paper trades
    async with PolymarketClient() as pm:
        for ev in events:
            async with pool.acquire() as conn:
                bid = await _capture_current_bid(
                    pm, conn, ev.condition_id, ev.direction
                )
                exit_id = await crud.insert_signal_exit(
                    conn,
                    signal_log_id=ev.signal_log_id,
                    exit_trader_count=ev.exit_trader_count,
                    peak_trader_count=ev.peak_trader_count,
                    exit_aggregate_usdc=ev.exit_aggregate_usdc,
                    peak_aggregate_usdc=ev.peak_aggregate_usdc,
                    drop_reason=ev.drop_reason,
                    exit_bid_price=bid,
                )
                if exit_id is None:
                    # Already exited (race); skip
                    continue
                exits_fired += 1
                log.info(
                    "exit fired: signal_log_id=%d %s/%s (drop=%s; %d->%d traders, $%.0f->$%.0f)",
                    ev.signal_log_id, ev.condition_id[:12], ev.direction,
                    ev.drop_reason,
                    ev.peak_trader_count, ev.exit_trader_count,
                    ev.peak_aggregate_usdc, ev.exit_aggregate_usdc,
                )

                # Step 3: auto-close any open paper trades on this signal
                if bid is None:
                    log.warning(
                        "  no bid captured for %s — paper trades on this exit skipped",
                        ev.condition_id[:12],
                    )
                    continue
                open_trades = await crud.list_open_paper_trades_for_signal(
                    conn, ev.signal_log_id,
                )
                for t in open_trades:
                    ok, realized = await _settle_paper_trade_at_exit(conn, t, bid)
                    if ok:
                        trades_closed += 1
                        realized_total += realized
                        log.info(
                            "  closed paper trade #%d at bid=%.4f -> $%+.2f",
                            t["id"], bid, realized,
                        )

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.2fs — %d exits fired, %d paper trades closed ($%+.2f) ===",
        duration, exits_fired, trades_closed, realized_total,
    )
    return ExitDetectionResult(
        candidates_evaluated=candidates_evaluated,
        exits_fired=exits_fired,
        paper_trades_closed=trades_closed,
        paper_trades_realized_pnl_usdc=realized_total,
        duration_seconds=duration,
    )


# ===========================================================================
# Trader category stats batch (B5) — runs nightly. For each tracked wallet,
# fetches recent /trades, attributes to a category via markets+events, and
# upserts trader_category_stats with (pnl, volume, resolved_trades,
# last_trade_at). Trader_ranker uses the result for recency filters,
# sample-size floors, and Bayesian shrinkage.
# ===========================================================================


@dataclass
class CategoryStatsResult:
    wallets_processed: int
    rows_upserted: int
    failures: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0


async def compute_trader_category_stats(
    top_n: int = CLASSIFY_TOP_N, concurrency: int = 8
) -> CategoryStatsResult:
    """Refresh trader_category_stats for every tracked wallet.

    Cost: 1 API call (last 500 trades) per wallet × ~530 wallets = ~50s at
    10 req/s. Cheaper than classify_tracked_wallets because we don't need
    to compute behavioral features — just bucket trades by category. Runs
    daily (separate from the weekly classifier).
    """
    started = datetime.now(timezone.utc)
    log.info("=== compute_trader_category_stats (top_n=%d) ===", top_n)

    pool = await init_pool()
    async with pool.acquire() as conn:
        wallets = await _gather_tracked_wallets(conn, top_n)
        leaderboard_pnl_vol = await crud.latest_pnl_volume_per_category(conn, wallets)
    log.info("processing %d wallets across %d categories", len(wallets),
             len(SNAPSHOT_CATEGORIES))

    failures: list[tuple[str, str]] = []
    rows_to_upsert: list[
        tuple[str, str, float, float, int, datetime | None]
    ] = []

    sem = asyncio.Semaphore(concurrency)
    trades_by_wallet: dict[str, list] = {}
    all_cids: set[str] = set()

    async def fetch_one(wallet: str) -> None:
        async with sem:
            try:
                trades = await pm.get_trades(wallet, limit=500)
            except Exception as e:  # noqa: BLE001
                failures.append((wallet, repr(e)))
                trades_by_wallet[wallet] = []
                return
        trades_by_wallet[wallet] = trades
        for t in trades:
            if t.condition_id:
                all_cids.add(t.condition_id)

    async with PolymarketClient() as pm:
        await asyncio.gather(*[fetch_one(w) for w in wallets])

    log.info("fetched trades; %d distinct cids seen", len(all_cids))

    # Build (cid -> category, cid -> resolved) lookups in one query
    from app.services.trader_stats import (  # local import to avoid cycle at module import
        ALL_CATEGORIES,
        aggregate_trades_per_category,
        fetch_cid_lookups,
    )

    async with pool.acquire() as conn:
        cid_to_category, cid_to_resolved = await fetch_cid_lookups(
            conn, sorted(all_cids)
        )

    # Aggregate per wallet, build upsert rows
    for wallet, trades in trades_by_wallet.items():
        per_cat = aggregate_trades_per_category(
            trades, cid_to_category, cid_to_resolved,
        )
        for cat in ALL_CATEGORIES:
            stats = per_cat.get(cat)
            pnl, vol = leaderboard_pnl_vol.get((wallet, cat), (0.0, 0.0))
            resolved = stats.resolved_trades if stats else 0
            last_at = stats.last_trade_at if stats else None
            # Skip writing rows where we have NEITHER trade activity NOR a
            # leaderboard entry — they'd be zero-valued noise. Anything
            # non-zero in any column gets persisted.
            if pnl == 0.0 and vol == 0.0 and resolved == 0 and last_at is None:
                continue
            rows_to_upsert.append((wallet, cat, pnl, vol, resolved, last_at))

    if rows_to_upsert:
        async with pool.acquire() as conn:
            await crud.upsert_trader_category_stats_bulk(conn, rows_to_upsert)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.1fs — %d wallets, %d rows upserted, %d failures ===",
        duration, len(wallets), len(rows_to_upsert), len(failures),
    )
    return CategoryStatsResult(
        wallets_processed=len(wallets),
        rows_upserted=len(rows_to_upsert),
        failures=failures,
        duration_seconds=duration,
    )


async def auto_close_resolved_paper_trades() -> AutoCloseResult:
    """Find every open paper trade on a now-resolved market and settle it.

    Idempotent — only operates on `status='open'` rows. First refreshes the
    resolution state of markets behind open paper trades (markets that have
    resolved since we last touched them via JIT discovery), then settles any
    that now have a final outcome. Designed to run on the same 10-min cadence
    as the position refresh — any Polymarket resolution propagates to the
    user's paper portfolio within one cycle.

    Connection-scope discipline: gamma HTTP calls happen OUTSIDE pool.acquire().
    Holding a pooled DB connection across network round trips can starve the
    pool under load (matters at ≥dozens of open paper trades on Railway).
    """
    started = datetime.now(timezone.utc)
    log.info("=== auto_close_resolved_paper_trades ===")

    pool = await init_pool(min_size=1, max_size=2)
    closed = 0
    realized_total = 0.0

    # Step 1: short DB acquire — list cids that need a refresh
    async with pool.acquire() as conn:
        cids = await _list_open_paper_trade_cids(conn)

    fetched_markets: list = []
    async with PolymarketClient() as pm:
        # Step 2: gamma fetches happen WITHOUT a held DB connection.
        # A28: paper trades on markets that disappear from gamma's default
        # active feed (because they resolved) used to stay open forever —
        # the default fetch returned `[]` for resolved cids, so we'd never
        # see resolved_outcome and never settle. Two-pass fetch closes that
        # gap: try active first, then `closed=true` for any cid still missing.
        if cids:
            try:
                fetched_active = await pm.get_markets_by_condition_ids(cids)
            except Exception as e:  # noqa: BLE001
                log.warning("gamma active fetch failed in auto_close: %r", e)
                fetched_active = []
            seen_cids = {m.condition_id for m in fetched_active if m.condition_id}
            still_missing = [c for c in cids if c not in seen_cids]
            fetched_closed: list = []
            if still_missing:
                try:
                    fetched_closed = await pm.get_markets_by_condition_ids(
                        still_missing, closed=True
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "gamma closed=true fetch failed in auto_close: %r", e
                    )
                    fetched_closed = []
                if fetched_closed:
                    log.info(
                        "auto_close: recovered %d resolved market(s) via closed=true sweep",
                        len(fetched_closed),
                    )
            fetched_markets = fetched_active + fetched_closed

        # Step 3: re-acquire for writes (refresh markets, find resolved candidates,
        # settle each). All pure-DB; no network in this scope.
        async with pool.acquire() as conn:
            refreshed = await _apply_paper_trade_market_refresh(conn, fetched_markets)
            if refreshed:
                log.info("refreshed resolution status for %d market(s)", refreshed)
            candidates = await crud.list_open_paper_trades_on_resolved_markets(conn)
            log.info("found %d open trade(s) on resolved markets", len(candidates))

            from app.services.paper_trade_close import compute_realized_pnl

            for t in candidates:
                entry_price = float(t["entry_price"])
                size = float(t["entry_size_usdc"])
                payoff = _payoff_for_resolution(t["direction"], t["resolved_outcome"])
                if payoff is None or entry_price <= 0 or size <= 0:
                    continue
                if entry_price >= 1.0:
                    continue  # bad entry price; skip

                # R10 (Pass 3): unified close formula via paper_trade_close.
                # Pre-fix had a special-case "fee on payout" formula that
                # divided fee_usdc by size to recover a fee_rate -- correct
                # only under the OLD flat-percentage model. Under the correct
                # Polymarket curve, that division gives the wrong rate. The
                # new helper does the math right + matches manual close.
                cat_row = await conn.fetchrow(
                    """
                    SELECT e.category FROM markets m
                    LEFT JOIN events e ON e.id = m.event_id
                    WHERE m.condition_id = $1
                    """,
                    t["condition_id"],
                )
                category = cat_row["category"] if cat_row else None

                close = compute_realized_pnl(
                    entry_price=entry_price,
                    entry_size_usdc=size,
                    entry_slippage_usdc=float(t["entry_slippage_usdc"] or 0.0),
                    entry_fee_usdc=float(t["entry_fee_usdc"] or 0.0),
                    exit_price=payoff,
                    exit_kind="resolution",
                    category=category,
                )

                ok = await crud.close_paper_trade_resolved(
                    conn, trade_id=int(t["id"]), exit_price=payoff,
                    realized_pnl_usdc=close.realized_pnl_usdc,
                )
                if ok:
                    closed += 1
                    realized_total += close.realized_pnl_usdc
                    log.info(
                        "  closed trade #%d: %s on %s (resolved=%s) -> $%+.2f",
                        t["id"], t["direction"], t["condition_id"][:12],
                        t["resolved_outcome"], close.realized_pnl_usdc,
                    )

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.2fs — %d closed, realized total $%+.2f ===",
        duration, closed, realized_total,
    )
    return AutoCloseResult(
        trades_closed=closed,
        realized_pnl_total=realized_total,
        duration_seconds=duration,
    )


async def refresh_positions_then_log_signals() -> tuple[
    PositionRefreshResult, LogSignalsResult, ExitDetectionResult, AutoCloseResult
]:
    """Composed 10-min job: fresh positions, signals, exits, then settle paper trades.

    Order matters:
      1. refresh_top_trader_positions       — pulls latest positions per wallet
      2. log_signals                        — reads those positions, fires signals
      3. detect_and_persist_exits           — recomputes current vs peak for live
                                              signals, fires exits, auto-closes
                                              paper trades at current bid
      4. auto_close_resolved_paper_trades   — re-checks markets behind open paper
                                              trades and settles any that resolved
    Running them as one logical unit keeps the cadence aligned and avoids racing
    triggers. Exits run BEFORE resolution-settlement so a paper trade that should
    settle on smart-money exit takes precedence over end-of-life resolution
    (in practice the two paths shouldn't both apply to the same trade in one
    cycle, but order is well-defined either way).

    Concurrency guard: serialized via Postgres advisory lock (`refresh_cycle`).
    APScheduler's `max_instances=1` only protects within a single process; the
    advisory lock also blocks a manually-triggered `scripts/run_position_refresh.py`
    from racing against the in-process scheduler. If we can't acquire the lock,
    skip this tick — the next one will pick up.

    Cycle duration: logs a warning if total elapsed >= REFRESH_CYCLE_WARN_SECONDS
    so we can tell when the system is falling behind the 10-min cadence.
    """
    cycle_started = datetime.now(timezone.utc)
    async with job_lock("refresh_cycle") as got:
        if not got:
            log.info("refresh_cycle lock held by another worker — skipping this tick")
            empty_refresh = PositionRefreshResult(
                wallets_targeted=0, wallets_succeeded=0,
                positions_persisted=0, portfolio_values_persisted=0,
            )
            empty_log = LogSignalsResult(combos_run=0, signals_seen=0, new_signals=0)
            empty_exits = ExitDetectionResult(
                candidates_evaluated=0, exits_fired=0,
                paper_trades_closed=0, paper_trades_realized_pnl_usdc=0.0,
            )
            empty_close = AutoCloseResult(trades_closed=0, realized_pnl_total=0.0)
            return empty_refresh, empty_log, empty_exits, empty_close

        refresh_result = await refresh_top_trader_positions()
        log_result = await log_signals()
        exit_result = await detect_and_persist_exits()
        autoclose_result = await auto_close_resolved_paper_trades()

    elapsed = (datetime.now(timezone.utc) - cycle_started).total_seconds()
    if elapsed >= REFRESH_CYCLE_WARN_SECONDS:
        log.warning(
            "10-min cycle took %.1fs (>= %ds threshold) — pipeline falling behind cadence",
            elapsed, REFRESH_CYCLE_WARN_SECONDS,
        )
    else:
        log.info("=== refresh cycle done in %.1fs ===", elapsed)
    return refresh_result, log_result, exit_result, autoclose_result


@dataclass
class PriceSnapshotResult:
    candidates_evaluated: int
    snapshots_inserted: int
    skipped_already_snapped: int
    skipped_no_offset_match: int
    failures: list[tuple[int, str]] = field(default_factory=list)
    duration_seconds: float = 0.0


async def record_signal_price_snapshots() -> PriceSnapshotResult:
    """B4/F4/F7 — capture bid + ask at +5/15/30/60/120 min after fire.

    F7: added +5 and +15 offsets so short latency profiles have real data.
    F4: capture both bid_price and ask_price (was just bid via yes_price).
    Mid = (bid+ask)/2 is used by half-life math; ask is used by B10
    latency simulation; bid is used for exit-side modeling.

    Runs every 10 min via the scheduler (was 30 min). Pulls signals fired
    in the 0-125 min window. For each signal: looks up which offsets are
    already captured, picks the BEST eligible new offset for current age,
    fetches the orderbook (one HTTP per signal per tick), writes one row.
    """
    started = datetime.now(timezone.utc)
    log.info("=== record_signal_price_snapshots ===")

    candidates_evaluated = 0
    snapshots_inserted = 0
    skipped_already_snapped = 0
    skipped_no_offset_match = 0
    failures: list[tuple[int, str]] = []

    pool = await init_pool(min_size=1, max_size=2)
    async with PolymarketClient() as pm:
        async with pool.acquire() as conn:
            candidates = await crud.list_signals_pending_price_snapshots(conn)
            log.info("  %d candidate signal(s) in 0-125min window", len(candidates))

            for c in candidates:
                candidates_evaluated += 1
                sid = int(c["signal_log_id"])
                fired_at = c["first_fired_at"]
                # R8 (Pass 3): pick the direction-side token (NO token for
                # NO-direction signals). Pre-fix always used the YES token,
                # which biased half-life math for NO signals (YES bid/ask !=
                # 1 - NO ask/bid in practice -- different MMs set them).
                direction = c.get("direction") or "YES"
                token_id = c["token_id"]
                age_min = (
                    datetime.now(timezone.utc) - fired_at
                ).total_seconds() / 60.0

                # F7: pass already-snapshotted offsets so the helper picks
                # the next-best new one instead of repeatedly returning a
                # duplicate (matters now that 5 + 15 + 30 overlap at boundaries).
                existing = await crud.existing_price_snapshot_offsets(conn, sid)
                offset = pick_offset_for_age(age_min, exclude=existing)
                if offset is None:
                    if existing:
                        skipped_already_snapped += 1
                    else:
                        skipped_no_offset_match += 1
                    continue

                try:
                    book = await pm.get_orderbook(token_id)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "  book fetch raised for sid=%s token=%s: %s",
                        sid, token_id[:12], e,
                    )
                    failures.append((sid, repr(e)))
                    continue

                # F4: capture BOTH best bid and best ask
                bid_price: float | None = None
                ask_price: float | None = None
                if isinstance(book, dict):
                    bids = book.get("bids") or []
                    asks = book.get("asks") or []
                    if bids and isinstance(bids[0], dict):
                        try:
                            bid_price = float(bids[0].get("price"))
                        except (TypeError, ValueError):
                            bid_price = None
                    if asks and isinstance(asks[0], dict):
                        try:
                            ask_price = float(asks[0].get("price"))
                        except (TypeError, ValueError):
                            ask_price = None

                try:
                    inserted = await crud.insert_signal_price_snapshot(
                        conn,
                        signal_log_id=sid,
                        snapshot_offset_min=offset,
                        bid_price=bid_price,
                        ask_price=ask_price,
                        token_id=token_id,
                        direction=direction,  # R8 (Pass 3)
                    )
                    if inserted:
                        snapshots_inserted += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("  insert failed for sid=%s offset=%s: %s", sid, offset, e)
                    failures.append((sid, repr(e)))

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== price snapshots done in %.1fs — %d evaluated, %d inserted, "
        "%d already-snapped, %d not-in-window, %d failures ===",
        duration, candidates_evaluated, snapshots_inserted,
        skipped_already_snapped, skipped_no_offset_match, len(failures),
    )
    return PriceSnapshotResult(
        candidates_evaluated=candidates_evaluated,
        snapshots_inserted=snapshots_inserted,
        skipped_already_snapped=skipped_already_snapped,
        skipped_no_offset_match=skipped_no_offset_match,
        failures=failures,
        duration_seconds=duration,
    )


async def catch_up_snapshot_if_stale(max_age_hours: int = 24) -> SnapshotResult | None:
    """Run a snapshot only if the most recent one is older than `max_age_hours`.

    Designed to be called at app startup so we don't lose snapshot days when
    the laptop has been off. Polymarket's leaderboard API only ever returns
    *current* state — we can't actually backfill missed days. When the gap is
    >1 day we emit a loud warning so downstream backtests / point-in-time
    analysis know there are blind days they have to handle (e.g. by holding
    the previous-day snapshot constant or skipping that window).
    """
    pool = await init_pool(min_size=1, max_size=12)
    async with pool.acquire() as conn:
        latest = await crud.latest_snapshot_date(conn)
    today = datetime.now(timezone.utc).date()
    if latest == today:
        log.info("snapshot for %s already exists — skipping catch-up", today)
        return None
    if latest is None:
        log.info("no prior snapshots — running first one")
    else:
        gap = (today - latest).days
        # gap == 1 is normal (yesterday + today); >1 means missed days we
        # can never recover. Log loudly so the operator notices.
        if gap > 1:
            missed = gap - 1
            log.warning(
                "snapshot gap detected: %d day(s) missing between %s and %s — "
                "those leaderboard windows are unrecoverable (Polymarket only "
                "returns current state)",
                missed, latest, today,
            )
        else:
            log.info("last snapshot was %s (%d day(s) ago) — running catch-up", latest, gap)
    return await daily_leaderboard_snapshot()
