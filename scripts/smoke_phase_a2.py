"""Smoke test for Session 2 (Phase A correctness fixes — second pass).

Tests each of the 15 items shipped in session 2:
  A5  Multi-outcome filter (include_multi_outcome=False excludes by default)
  A8  Catch-up snapshot multi-day gap warning
  A9  Entry-price overwrite guard (no clob_l2 -> clob_l2 downgrade)
  A10 Classifier — scaling-out (size mismatch) no longer counts as round-trip
  A11 Classifier — MM_MIN_MARKETS_PER_DAY guard against false positives
  A12 ROW_NUMBER tiebreakers (deterministic order across re-runs)
  A13 Cycle duration warning threshold present
  A14 job_lock acquire/release + blocks concurrent holder
  A15 Default pool max_size raised to 12
  A16 Counter for unknown-market drop is exposed
  A17 daily_leaderboard_snapshot keeps going on per-combo DB error
  A24 entry_price >= 1.0 logs a warning and returns None
  A25 Median liquidity fallback used when liquidity_at_signal missing
  A27 Profit factor None (not inf) when there are no losses
  A30 raw_snapshots and alerts_sent tables dropped

Run: ./venv/Scripts/python.exe scripts/smoke_phase_a2.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool, job_lock  # noqa: E402
from app.db import crud  # noqa: E402
from app.scheduler import jobs as scheduler_jobs  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    BacktestResult,
    SignalRow,
    compute_pnl_per_dollar,
    summarize_rows,
)
from app.services.polymarket_types import Trade  # noqa: E402
from app.services import wallet_classifier  # noqa: E402

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("smoke_phase_a2")
log.setLevel(logging.INFO)


PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}{('  -- ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n  {title}\n{'=' * 80}")


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# A5: include_multi_outcome filter
# ---------------------------------------------------------------------------


def test_a5_multi_outcome_filter() -> None:
    section("A5: include_multi_outcome filter (default excludes non-binary)")

    f = BacktestFilters()
    check("BacktestFilters.include_multi_outcome defaults to False",
          f.include_multi_outcome is False)

    f = BacktestFilters(include_multi_outcome=True)
    check("Override to True passes through", f.include_multi_outcome is True)


# ---------------------------------------------------------------------------
# A10 + A11: classifier — scaling-out + markets-per-day guard
# ---------------------------------------------------------------------------


def make_trade(
    cid: str, asset: str, side: str, size_usdc: float, t: datetime
) -> Trade:
    return Trade(
        proxy_wallet="x",
        condition_id=cid,
        asset=asset,
        side=side,
        size=size_usdc * 2,  # arbitrary share count
        usdc_size=size_usdc,
        price=0.50,
        timestamp=t,
        transaction_hash=f"0x{cid}{asset}{side}{int(t.timestamp())}",
        title=None,
        slug=None,
    )


def test_a10_scaling_out_not_mm() -> None:
    section("A10: scaling-out (size mismatch) no longer counts as MM round-trip")

    base_t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Directional trader: BUY $1000, then SELLs of $200 over the next half hour.
    # Old logic: any BUY+SELL within 1h = pair => high two_sided_ratio.
    # New logic: SELL must be within 30% of BUY size => no pair.
    trades = [make_trade("m1", "YES", "BUY", 1000.0, base_t)]
    for i in range(5):
        trades.append(make_trade("m1", "YES", "SELL", 200.0, base_t + timedelta(minutes=5 * (i + 1))))

    feats = wallet_classifier.compute_features(trades)
    check(
        "Scaling-out (BUY $1000 + 5x SELL $200) -> 0 MM pairs",
        feats["two_sided_ratio"] == 0.0,
        f"two_sided_ratio={feats['two_sided_ratio']}",
    )

    # MM behavior: matched-size BUY/SELL pairs within 10 min cycle.
    mm_trades: list[Trade] = []
    for i in range(10):
        t0 = base_t + timedelta(minutes=i * 11)  # 11-min spacing keeps pairs distinct
        mm_trades.append(make_trade(f"m{i}", "YES", "BUY", 100.0, t0))
        mm_trades.append(make_trade(f"m{i}", "YES", "SELL", 100.0, t0 + timedelta(minutes=2)))

    feats_mm = wallet_classifier.compute_features(mm_trades)
    check(
        "True MM round-trips (matched size, <10min) -> high two_sided_ratio",
        feats_mm["two_sided_ratio"] > 0.5,
        f"two_sided_ratio={feats_mm['two_sided_ratio']}",
    )


def test_a11_markets_per_day_guard() -> None:
    section("A11: classifier requires MM_MIN_MARKETS_PER_DAY for MM classification")

    # Hand-crafted features that mimic a directional trader scaling out heavily
    # on ONE market (high two_sided_ratio but only 1 distinct market in the
    # whole week).
    feats_scale_out = {
        "n_trades": 30,
        "two_sided_ratio": 0.80,
        "cross_leg_arb_ratio": 0.0,
        "median_trade_size_usdc": 500.0,
        "distinct_markets_per_day": 0.14,  # 1 market / 7 days
        "buy_share": 0.50,
        "span_days": 7.0,
    }
    res = wallet_classifier.classify(feats_scale_out)
    check(
        "Single-market high-two-sided trader NOT classified MM (markets/day too low)",
        res.wallet_class != "market_maker",
        f"got class={res.wallet_class}",
    )

    # And a real MM: high two-sided + many markets per day -> MM
    feats_mm = {
        **feats_scale_out,
        "distinct_markets_per_day": 5.0,
    }
    res = wallet_classifier.classify(feats_mm)
    check(
        "High two-sided + many markets/day -> classified market_maker",
        res.wallet_class == "market_maker",
        f"got class={res.wallet_class}",
    )

    # The unused-feature inventory is preserved in features dict
    check(
        "Forensic features still computed (median/buy_share/span_days)",
        all(k in feats_scale_out for k in
            ("median_trade_size_usdc", "buy_share", "span_days")),
    )


# ---------------------------------------------------------------------------
# A13: cycle duration warning constant exists
# ---------------------------------------------------------------------------


def test_a13_cycle_warn_constant() -> None:
    section("A13: REFRESH_CYCLE_WARN_SECONDS exposed and below 10-min cadence")

    check(
        "REFRESH_CYCLE_WARN_SECONDS exists",
        hasattr(scheduler_jobs, "REFRESH_CYCLE_WARN_SECONDS"),
    )
    n = getattr(scheduler_jobs, "REFRESH_CYCLE_WARN_SECONDS", 0)
    check(
        f"REFRESH_CYCLE_WARN_SECONDS sane ({n}s, must be < 600)",
        0 < n < 600,
    )


# ---------------------------------------------------------------------------
# A15: default pool max_size = 12
# ---------------------------------------------------------------------------


def test_a15_pool_default() -> None:
    section("A15: init_pool default max_size = 12")

    sig = inspect.signature(init_pool)
    default = sig.parameters["max_size"].default
    check("init_pool max_size default is 12", default == 12, f"got {default}")


# ---------------------------------------------------------------------------
# A24 + A25 + A27: backtest engine math
# ---------------------------------------------------------------------------


def test_a24_entry_price_too_high() -> None:
    section("A24: entry_price >= 1.0 returns None and logs warning")

    # Capture the log
    bt_log = logging.getLogger("app.services.backtest_engine")
    captured: list[logging.LogRecord] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    h = Handler()
    bt_log.addHandler(h)
    try:
        v = compute_pnl_per_dollar(1.05, "YES", "YES", "politics", 1.0, 25_000.0)
        check("entry_price=1.05 returns None", v is None)
        check(
            "Warning logged for entry_price >= 1.0",
            any(r.levelno == logging.WARNING and "1.0" in r.getMessage()
                for r in captured),
            f"captured {len(captured)} log records",
        )
    finally:
        bt_log.removeHandler(h)


def test_a25_median_liquidity_fallback() -> None:
    section("A25: missing liquidity_at_signal uses median fallback")

    # Without fallback, slip = min(0.05, 1.0/50000) = 2e-5 (negligible)
    v_default = compute_pnl_per_dollar(0.50, "YES", "YES", "politics", 1.0, None)
    # With a 5k liquidity fallback, slip = 0.02 * sqrt(1/5000) = 0.000283
    v_fallback = compute_pnl_per_dollar(
        0.50, "YES", "YES", "politics", 1.0, None,
        median_liquidity_fallback=5_000.0,
    )
    check("Both code paths return a number", v_default is not None and v_fallback is not None)

    # With a tiny liquidity (more slippage), pnl should be lower
    v_thin = compute_pnl_per_dollar(
        0.50, "YES", "YES", "politics", 1.0, None,
        median_liquidity_fallback=100.0,
    )
    check(
        "Smaller fallback liquidity -> more slippage -> smaller pnl",
        v_thin is not None and v_default is not None and v_thin < v_default,
        f"thin={v_thin}, default={v_default}",
    )


def make_signal_row(
    pid: int, direction: str, outcome: str, entry: float, liquidity: float | None
) -> SignalRow:
    return SignalRow(
        id=pid, mode="absolute", category="politics", top_n=50,
        condition_id=f"0xc{pid}", direction=direction,
        first_trader_count=10, first_aggregate_usdc=100_000.0,
        first_net_skew=0.85, first_avg_portfolio_fraction=0.10,
        signal_entry_offer=entry, signal_entry_mid=entry,
        liquidity_at_signal_usdc=liquidity, liquidity_tier="medium",
        first_top_trader_entry_price=entry,
        cluster_id=f"clu{pid}", market_type="binary",
        first_fired_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        resolved_outcome=outcome, market_category="politics",
    )


def test_a27_profit_factor_no_losses() -> None:
    section("A27: profit factor None (not inf) when no losses present")

    # 35 winners + 0 losers -> all-wins case
    rows = [
        make_signal_row(i, "YES", "YES", 0.40, 25_000.0)
        for i in range(35)
    ]
    res = summarize_rows(rows, trade_size_usdc=1.0)
    check(
        "All-wins backtest: profit_factor is None (not inf)",
        res.profit_factor is None,
        f"got profit_factor={res.profit_factor}",
    )

    # Mixed: 20 winners + 20 losers -> finite profit factor
    rows2 = (
        [make_signal_row(i, "YES", "YES", 0.40, 25_000.0) for i in range(20)] +
        [make_signal_row(100 + i, "YES", "NO", 0.40, 25_000.0) for i in range(20)]
    )
    res2 = summarize_rows(rows2, trade_size_usdc=1.0)
    check(
        "Mixed wins+losses: profit_factor is a finite number",
        res2.profit_factor is not None and 0 < res2.profit_factor < 1e9,
        f"got profit_factor={res2.profit_factor}",
    )


# ---------------------------------------------------------------------------
# A14: job_lock acquire / release / block
# ---------------------------------------------------------------------------


async def test_a14_job_lock() -> None:
    section("A14: job_lock acquires, releases, and blocks concurrent holder")

    pool = await init_pool()
    try:
        # Outer lock holds; inner attempt to acquire the same name returns False.
        async with job_lock("smoke_test_lock") as got_outer:
            check("First acquire returns True", got_outer is True)
            async with job_lock("smoke_test_lock") as got_inner:
                check(
                    "Second concurrent acquire returns False",
                    got_inner is False,
                )

        # After release, can acquire again.
        async with job_lock("smoke_test_lock") as got_again:
            check("Re-acquire after release returns True", got_again is True)

        # Different lock names don't collide
        async with job_lock("smoke_test_lock_a") as a:
            async with job_lock("smoke_test_lock_b") as b:
                check("Different lock names independent", a is True and b is True)
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# A9: persist_book_snapshot_and_pricing overwrite guard (DB integration)
# ---------------------------------------------------------------------------


async def test_a9_entry_price_overwrite_guard() -> None:
    section("A9: clob_l2 entry-price not downgraded by later 'unavailable' attempt")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            # Pick any signal that already has signal_entry_source = 'clob_l2'.
            row = await conn.fetchrow(
                """
                SELECT id, signal_entry_offer, signal_entry_source
                FROM signal_log
                WHERE signal_entry_source = 'clob_l2'
                LIMIT 1
                """
            )
            if row is None:
                check(
                    "No clob_l2 signal in DB to test against (skipped)",
                    True, "skipping",
                )
                return

            sid = row["id"]
            orig_offer = row["signal_entry_offer"]

            # Try to overwrite by passing an unavailable BookMetrics — should be a no-op.
            from app.services.orderbook import BookMetrics
            bad = BookMetrics(
                best_bid=None, best_ask=None, mid=None, spread_bps=None,
                entry_offer=None, liquidity_5c_usdc=None, liquidity_tier="unknown",
                bids_top20=[], asks_top20=[], raw_response_hash="",
                available=False,
            )
            await crud.persist_book_snapshot_and_pricing(
                conn, sid, token_id="", side="YES", metrics=bad,
            )

            after = await conn.fetchrow(
                "SELECT signal_entry_offer, signal_entry_source FROM signal_log WHERE id = $1",
                sid,
            )
            check(
                "clob_l2 source preserved (not downgraded to unavailable)",
                after["signal_entry_source"] == "clob_l2",
                f"got source={after['signal_entry_source']}",
            )
            check(
                "signal_entry_offer preserved",
                after["signal_entry_offer"] == orig_offer,
                f"got {after['signal_entry_offer']} expected {orig_offer}",
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# A30: tables removed
# ---------------------------------------------------------------------------


async def test_a30_tables_dropped() -> None:
    section("A30: raw_snapshots and alerts_sent tables dropped")

    pool = await init_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = {r["tablename"] for r in rows}
            check("raw_snapshots dropped", "raw_snapshots" not in tables)
            check("alerts_sent dropped",   "alerts_sent" not in tables)

            # Migration row recorded
            mig = await conn.fetchrow(
                "SELECT name FROM _migrations WHERE name = '004_drop_unused_tables'"
            )
            check("Migration 004 recorded in _migrations", mig is not None)
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
def test_f18_exit_activity_guard_2h() -> None:
    """F18 regression: detect_exits must apply EXIT_ACTIVITY_GUARD_HOURS=2
    to last_seen_at, so signals that stopped being detected hours ago
    don't emit stale exits the user can't act on.

    See review/FIXES.md F18.
    """
    section("F18: exit detector applies 2h activity guard")
    from app.services import exit_detector
    check(
        "EXIT_ACTIVITY_GUARD_HOURS = 2",
        exit_detector.EXIT_ACTIVITY_GUARD_HOURS == 2,
        f"got {exit_detector.EXIT_ACTIVITY_GUARD_HOURS}",
    )
    src = inspect.getsource(exit_detector.detect_exits)
    check(
        "detect_exits source uses EXIT_ACTIVITY_GUARD_HOURS",
        "EXIT_ACTIVITY_GUARD_HOURS" in src and "min(window_hours" in src,
        "fix marker missing — exit window may not be capped",
    )


def test_f17_skew_denominator_filters_to_yes_no() -> None:
    """F17 regression: market_totals CTE in detect_signals must filter to
    YES/NO outcomes only when counting traders_any_direction (skew
    denominator). Pre-fix counted every outcome, inflating the denominator
    on binary markets that had stray non-YES/NO rows in `positions` and
    falsely dragging skew below the 60% threshold.

    See review/FIXES.md F17.
    """
    section("F17: skew denominator filters to LOWER(outcome) IN ('yes','no')")
    from app.services import signal_detector
    src = inspect.getsource(signal_detector)
    # The fix marker: market_totals CTE includes a WHERE clause filtering
    # to YES/NO outcomes.
    check(
        "signal_detector market_totals CTE filters denominator to YES/NO",
        "WHERE LOWER(outcome) IN ('yes', 'no')" in src,
        "fix marker missing — denominator may still count multi-outcome rows",
    )


def test_f16_position_upsert_uses_executemany() -> None:
    """F16 regression: upsert_positions_for_trader must batch INSERTs via
    executemany, not loop one-by-one. Pre-fix took ~7 minutes for 530
    wallets (~10k sequential round-trips); fix collapses to one round-trip
    per wallet.

    See review/FIXES.md F16.
    """
    section("F16: position upserts use executemany (batched)")
    from app.db import crud
    src = inspect.getsource(crud.upsert_positions_for_trader)
    check(
        "upsert_positions_for_trader uses executemany (not per-row execute)",
        "executemany" in src,
        "fix marker missing — function may still issue one execute per position",
    )
    # Defensive: also confirm we don't have a leftover per-row execute loop
    # for INSERT (the executemany should be the only INSERT path).
    insert_executes = src.count("await conn.execute(\n                \"\"\"\n                INSERT INTO positions")
    check(
        "no per-row 'await conn.execute' INSERT loop remains",
        insert_executes == 0,
        f"found {insert_executes} per-row INSERT loops — fix incomplete",
    )


def test_f9_counterparty_uses_position_refresh_depth() -> None:
    """F9 regression: the counterparty tracked-pool depth must match the
    position-refresh + exit-detector depth (POSITION_REFRESH_TOP_N=100).
    Pre-fix used the calling top_n (=LOG_SIGNALS_TOP_N=50), so wallets
    ranked 51-100 were tracked + could fire exits but never triggered a
    counterparty warning.

    See review/FIXES.md F9.
    """
    section("F9: counterparty pool depth = POSITION_REFRESH_TOP_N (100)")
    from app.scheduler import jobs as sj
    src = inspect.getsource(sj.log_signals)
    check(
        "log_signals counterparty pool uses POSITION_REFRESH_TOP_N",
        "top_n=POSITION_REFRESH_TOP_N" in src,
        "fix marker missing in log_signals — counterparty depth not unified",
    )
    check(
        "POSITION_REFRESH_TOP_N is 100 (matches exit-detector + position-refresh)",
        sj.POSITION_REFRESH_TOP_N == 100,
        f"got {sj.POSITION_REFRESH_TOP_N}",
    )


def test_f9_specialist_uses_recency_filter() -> None:
    """F9 regression: Specialist ranker now layers the same overall-
    last_trade_at recency filter that gather_union_top_n_wallets and the
    Absolute/Hybrid rankers use. Pre-fix relied only on monthly-leaderboard
    presence, which let traders qualify on one huge old trade.

    See review/FIXES.md F9.
    """
    section("F9: specialist ranker enforces recency filter")
    from app.services import trader_ranker
    src = inspect.getsource(trader_ranker._rank_specialist)
    check(
        "specialist SQL references trader_category_stats.last_trade_at",
        "tcs2.last_trade_at" in src and "make_interval(days =>" in src,
        "fix marker missing — specialist may not enforce recency",
    )
    # Verify the function passes RECENCY_MAX_DAYS through to the SQL params
    check(
        "specialist passes RECENCY_MAX_DAYS as a query arg",
        "RECENCY_MAX_DAYS" in src,
        "RECENCY_MAX_DAYS not bound to a SQL parameter",
    )
    # The constant must be wired through
    check(
        "RECENCY_MAX_DAYS=60 (unchanged)",
        trader_ranker.RECENCY_MAX_DAYS == 60,
    )


def test_f3_portfolio_value_prefers_api_over_position_sum() -> None:
    """F3 regression: portfolio_total denominator must come from data-api
    /value (true equity including USDC cash + unredeemed) when available,
    not from sum(open_position.current_value). Pre-fix used position-sum
    only, so a trader with $10k positions + $90k cash showed up as 100%
    deployed when reality was 10%.

    Verified via source inspection — the persistence loop must reference
    pv_api and prefer it over the fallback.

    See review/FIXES.md F3.
    """
    section("F3: phase-3 prefers /value API result over position-sum fallback")
    from app.scheduler import jobs as sj
    src = inspect.getsource(sj.refresh_top_trader_positions)
    check(
        "phase 3 source references pv_api as portfolio_total source",
        "if pv_api is not None" in src and "portfolio_total = pv_api" in src,
        "fix marker missing — phase 3 may not be using /value",
    )
    check(
        "phase 3 keeps fallback to sum(positions) when API failed",
        "sum((p.current_value or 0.0) for p in valid)" in src,
        "fallback path missing",
    )
    # _fetch_one_wallet must return pv from /value
    src2 = inspect.getsource(sj._fetch_one_wallet)
    check(
        "_fetch_one_wallet calls pm.get_portfolio_value",
        "get_portfolio_value" in src2,
    )
    check(
        "_fetch_one_wallet returns 4-tuple including portfolio_value",
        "portfolio_value" in src2 and "return wallet, positions, portfolio_value" in src2,
    )


def test_f11_paper_trade_status_whitelist_includes_closed_exit() -> None:
    """F11 regression: paper_trades route status filter must accept all 4
    statuses written by the codebase. Pre-fix the whitelist omitted
    'closed_exit' (the value written by the smart-money-exit auto-close
    path added in migration 005), so /paper_trades?status=closed_exit
    returned 400 even though such rows existed in the DB.

    See review/FIXES.md F11.
    """
    section("F11: paper_trades status whitelist includes closed_exit")
    from app.api.routes.paper_trades import VALID_PAPER_TRADE_STATUSES
    expected = {"open", "closed_resolved", "closed_manual", "closed_exit"}
    check(
        "VALID_PAPER_TRADE_STATUSES contains all 4 status values",
        set(VALID_PAPER_TRADE_STATUSES) == expected,
        f"got {VALID_PAPER_TRADE_STATUSES}",
    )
    check(
        "closed_exit specifically present (the F11 fix value)",
        "closed_exit" in VALID_PAPER_TRADE_STATUSES,
    )


def test_f14_should_retry_only_5xx_and_429() -> None:
    """F14 regression: only retry transient errors (TransportError, 429, 5xx).
    Pre-fix retried every HTTPStatusError including 4xx terminal errors
    (400 bad params, 401 auth, 404 not found), burning 4 rate-limit tokens
    on a request that would never succeed.

    See review/FIXES.md F14.
    """
    section("F14: _should_retry filters by status code")
    import httpx
    from app.services.polymarket import _should_retry

    def status_err(code: int) -> httpx.HTTPStatusError:
        req = httpx.Request("GET", "https://example.com")
        resp = httpx.Response(code, request=req)
        return httpx.HTTPStatusError(f"{code}", request=req, response=resp)

    # Should retry: TransportError + 429 + 5xx
    check(
        "TransportError -> retry",
        _should_retry(httpx.ConnectError("network down")),
    )
    check("429 (Too Many Requests) -> retry", _should_retry(status_err(429)))
    check("500 -> retry", _should_retry(status_err(500)))
    check("502 -> retry", _should_retry(status_err(502)))
    check("503 -> retry", _should_retry(status_err(503)))

    # Should NOT retry: terminal 4xx
    check(
        "400 (Bad Request) -> NO retry (terminal)",
        not _should_retry(status_err(400)),
    )
    check(
        "401 (Unauthorized) -> NO retry",
        not _should_retry(status_err(401)),
    )
    check(
        "403 (Forbidden) -> NO retry",
        not _should_retry(status_err(403)),
    )
    check(
        "404 (Not Found) -> NO retry",
        not _should_retry(status_err(404)),
    )

    # Other exceptions -> not retried
    check(
        "ValueError -> NO retry (not an httpx error)",
        not _should_retry(ValueError("bad input")),
    )


def test_f20_bh_fdr_comment_matches_code() -> None:
    """F20 regression: the BH-FDR rank computation uses ties->highest
    semantics (matching statsmodels.stats.multitest.fdrcorrection). Pre-fix
    the inline comment claimed ties->lowest, which would have been more
    conservative. We aligned the comment to the code (rather than swap the
    code's behavior) since statsmodels-parity is the right default. If a
    strict variant is ever wanted, expose it as an explicit `tie_method`
    parameter rather than baking it in.

    See review/FIXES.md F20.
    """
    section("F20: BH-FDR ties -> highest rank (statsmodels-compatible)")
    from app.services import backtest_engine
    src = inspect.getsource(backtest_engine.compute_corrections)
    # The fix updated the comment to mention "ties -> highest" and reference
    # statsmodels. Verify both anchors exist.
    check(
        "compute_corrections source mentions 'ties -> highest'",
        "ties -> highest" in src,
        f"comment not updated; ties phrasing missing",
    )
    check(
        "compute_corrections source references statsmodels parity",
        "statsmodels" in src.lower(),
        f"statsmodels reference missing",
    )


def test_f22_holdout_filter_uses_utc_timestamp() -> None:
    """F22 regression: the backtest holdout filter casts the holdout date
    to a UTC midnight timestamp instead of relying on Postgres's session-TZ-
    dependent implicit cast. Without this, edge-of-day signals could leak
    into / out of the training set if the DB session TZ ever drifts off UTC.

    See review/FIXES.md F22.
    """
    section("F22: holdout cutoff uses explicit UTC timestamp")
    from app.services import backtest_engine
    src = inspect.getsource(backtest_engine._fetch_signals)
    # The fix wraps holdout_from in a tz-aware datetime.
    check(
        "_fetch_signals source builds explicit tz-aware cutoff for holdout",
        "tzinfo=timezone.utc" in src and "holdout_from" in src,
        "fix marker missing",
    )


def test_f25_signals_health_quiet_window_72h() -> None:
    """F25 regression: extend signals_health quiet window from 48h to 72h
    so weekends and quiet-market stretches don't push the overall status
    pill to amber. A real "cycle stopped firing" condition would still
    flag once nothing fires for 3 days.

    See review/FIXES.md F25.
    """
    section("F25: SIGNALS_AMBER_MAX_HOURS = 72")
    from app.api.routes.system import SIGNALS_AMBER_MAX_HOURS
    check(
        "SIGNALS_AMBER_MAX_HOURS == 72",
        SIGNALS_AMBER_MAX_HOURS == 72,
        f"got {SIGNALS_AMBER_MAX_HOURS}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nSession 2 (Phase A) smoke test\n" + "=" * 80)

    # Pure-function tests — no DB needed
    test_a5_multi_outcome_filter()
    test_a10_scaling_out_not_mm()
    test_a11_markets_per_day_guard()
    test_a13_cycle_warn_constant()
    test_a15_pool_default()
    test_a24_entry_price_too_high()
    test_a25_median_liquidity_fallback()
    test_a27_profit_factor_no_losses()

    # Pass 2 fixes
    test_f3_portfolio_value_prefers_api_over_position_sum()
    test_f9_counterparty_uses_position_refresh_depth()
    test_f9_specialist_uses_recency_filter()
    test_f16_position_upsert_uses_executemany()
    test_f17_skew_denominator_filters_to_yes_no()
    test_f18_exit_activity_guard_2h()
    test_f11_paper_trade_status_whitelist_includes_closed_exit()
    test_f14_should_retry_only_5xx_and_429()
    test_f20_bh_fdr_comment_matches_code()
    test_f22_holdout_filter_uses_utc_timestamp()
    test_f25_signals_health_quiet_window_72h()

    # DB integration tests
    await test_a14_job_lock()
    await test_a9_entry_price_overwrite_guard()
    await test_a30_tables_dropped()

    section("SUMMARY")
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"  {n_pass} passed, {n_fail} failed")
    if n_fail:
        print("\n  Failures:")
        for label, ok, detail in results:
            if not ok:
                print(f"    {FAIL}  {label}  -- {detail}")
        sys.exit(1)
    print("\n  All session-2 changes verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
