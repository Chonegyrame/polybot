"""Backtest endpoints — wraps the engine with HTTP filters + slicing."""

from __future__ import annotations

import dataclasses
from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db import crud
from app.db.connection import init_pool
from app.services.backtest_engine import (
    BacktestFilters,
    VALID_BENCHMARKS,
    backtest_slice,
    backtest_with_rows,
    compute_benchmark,
    compute_corrections,
    compute_edge_decay,
    latency_unavailable,
)
from app.services.half_life import (
    HalfLifeRow,
    SNAPSHOT_OFFSETS_MIN,
    compute_half_life_summary,
)

router = APIRouter(prefix="/backtest", tags=["backtest"])

VALID_SLICE_DIMENSIONS = (
    "mode", "category", "direction", "market_category", "liquidity_tier",
    "skew_bucket", "trader_count_bucket", "aggregate_bucket",
    "entry_price_bucket", "gap_bucket", "lens_count_bucket",
)

VALID_LIQUIDITY_TIERS = ("thin", "medium", "deep", "unknown")
VALID_EXIT_STRATEGIES = ("hold", "smart_money_exit")
VALID_LATENCY_PROFILES = ("active", "responsive", "casual", "delayed", "custom")


def _build_filters(
    mode: str | None, category: str | None, direction: str | None,
    min_skew: float | None, max_skew: float | None,
    min_trader_count: int | None, min_aggregate_usdc: float | None,
    min_avg_portfolio_fraction: float | None,
    liquidity_tiers: tuple[str, ...] | None,
    market_category: str | None, max_gap: float | None,
    include_pre_fix: bool, include_multi_outcome: bool,
    trade_size_usdc: float,
    exit_strategy: str,
    dedup: bool,
    holdout_from: date | None,
    latency_profile: str | None,
    latency_min_min: float | None,
    latency_max_min: float | None,
) -> BacktestFilters:
    if exit_strategy not in VALID_EXIT_STRATEGIES:
        raise HTTPException(
            400,
            f"exit_strategy must be one of {VALID_EXIT_STRATEGIES}; got {exit_strategy!r}",
        )
    if latency_profile is not None and latency_profile not in VALID_LATENCY_PROFILES:
        raise HTTPException(
            400,
            f"latency_profile must be one of {VALID_LATENCY_PROFILES}; got {latency_profile!r}",
        )
    if latency_profile == "custom":
        if latency_min_min is None or latency_max_min is None:
            raise HTTPException(
                400,
                "latency_profile=custom requires latency_min_min AND latency_max_min",
            )
        if latency_max_min < latency_min_min:
            raise HTTPException(400, "latency_max_min must be >= latency_min_min")
    return BacktestFilters(
        mode=mode, category=category,
        direction=direction,                  # type: ignore[arg-type]
        min_skew=min_skew, max_skew=max_skew,
        min_trader_count=min_trader_count,
        min_aggregate_usdc=min_aggregate_usdc,
        min_avg_portfolio_fraction=min_avg_portfolio_fraction,
        liquidity_tiers=liquidity_tiers,
        market_category=market_category,
        max_gap_to_smart_money=max_gap,
        include_pre_fix=include_pre_fix,
        include_multi_outcome=include_multi_outcome,
        trade_size_usdc=trade_size_usdc,
        exit_strategy=exit_strategy,          # type: ignore[arg-type]
        dedup=dedup,
        holdout_from=holdout_from,
        latency_profile=latency_profile,      # type: ignore[arg-type]
        latency_min_min=latency_min_min,
        latency_max_min=latency_max_min,
    )


def _parse_liquidity_tiers(raw: list[str] | None) -> tuple[str, ...] | None:
    """Validate and normalize the `liquidity_tiers` query param."""
    if not raw:
        return None
    tiers = tuple(t.strip() for t in raw if t.strip())
    if not tiers:
        return None
    invalid = [t for t in tiers if t not in VALID_LIQUIDITY_TIERS]
    if invalid:
        raise HTTPException(
            400,
            f"liquidity_tiers must be subset of {VALID_LIQUIDITY_TIERS}; got invalid: {invalid}",
        )
    return tiers


def _filters_to_json_dict(f: BacktestFilters) -> dict:
    """Convert BacktestFilters to a JSON-serialisable dict for slice_lookups."""
    result: dict[str, Any] = {}
    for field in dataclasses.fields(f):
        v = getattr(f, field.name)
        if v is None or v is False:
            continue
        if isinstance(v, tuple):
            if not v:
                continue
            result[field.name] = list(v)
        elif hasattr(v, "isoformat"):
            result[field.name] = v.isoformat()
        else:
            result[field.name] = v
    return result


@router.get("/summary")
async def get_summary(
    mode: str | None = Query(None),
    category: str | None = Query(None),
    direction: str | None = Query(None),
    min_skew: float | None = Query(None),
    max_skew: float | None = Query(None),
    min_trader_count: int | None = Query(None),
    min_aggregate_usdc: float | None = Query(None),
    min_avg_portfolio_fraction: float | None = Query(
        None,
        description="Min mean (current_value / portfolio_total) across involved traders, 0..1",
    ),
    liquidity_tiers: list[str] | None = Query(
        None,
        description=f"Restrict to these tiers; subset of {VALID_LIQUIDITY_TIERS}",
    ),
    market_category: str | None = Query(None),
    max_gap: float | None = Query(None, description="entry-offer / smart-money-cost - 1"),
    include_pre_fix: bool = Query(False),
    include_multi_outcome: bool = Query(False),
    trade_size_usdc: float = Query(100.0, gt=0),
    exit_strategy: str = Query(
        "hold",
        description=f"One of {VALID_EXIT_STRATEGIES}; smart_money_exit settles at signal_exits.exit_bid_price when present",
    ),
    dedup: bool = Query(
        False,
        description="Read from vw_signals_unique_market — one row per (cid, direction); avoids overcounting cross-lens hits",
    ),
    holdout_from: date | None = Query(
        None,
        description="Exclude signals fired on/after this date — reserves out-of-sample data (YYYY-MM-DD)",
    ),
    latency_profile: str | None = Query(
        None,
        description=f"B10 — execution-latency adjustment. One of {VALID_LATENCY_PROFILES}.",
    ),
    latency_min_min: float | None = Query(
        None, description="latency_profile=custom — uniform window lower bound (minutes).",
    ),
    latency_max_min: float | None = Query(
        None, description="latency_profile=custom — uniform window upper bound (minutes).",
    ),
    benchmark: str | None = Query(
        None,
        description=f"Compare strategy against a dumb baseline: one of {VALID_BENCHMARKS}",
    ),
) -> dict[str, Any]:
    """Headline backtest metrics for whatever filter spec the UI sends.

    Response always includes B7 multiple-testing corrections:
      corrections.n_session_queries — how many backtest queries in this session
      corrections.multiplicity_warning — True when n_session_queries > 5
      corrections.bonferroni_pnl_ci_{lo,hi} — Bonferroni-corrected P&L CI
      corrections.bh_fdr_pnl_ci_{lo,hi} — BH-FDR-corrected P&L CI
      (and equivalent win-rate fields)

    Optional ?benchmark= returns a second result for a dumb strategy run over
    the same signal universe. Strategy must beat benchmark by ≥2× CI to claim alpha.
    """
    if benchmark is not None and benchmark not in VALID_BENCHMARKS:
        raise HTTPException(400, f"benchmark must be one of {VALID_BENCHMARKS}; got {benchmark!r}")

    tiers = _parse_liquidity_tiers(liquidity_tiers)
    filters = _build_filters(
        mode, category, direction, min_skew, max_skew,
        min_trader_count, min_aggregate_usdc,
        min_avg_portfolio_fraction, tiers,
        market_category, max_gap,
        include_pre_fix, include_multi_outcome, trade_size_usdc,
        exit_strategy, dedup, holdout_from,
        latency_profile, latency_min_min, latency_max_min,
    )

    result, rows, latency_stats = await backtest_with_rows(filters)

    # B7: persist this query to the audit log, then fetch session window for corrections.
    pool = await init_pool()
    slice_def = _filters_to_json_dict(filters)
    async with pool.acquire() as conn:
        await crud.insert_slice_lookup(
            conn, slice_def,
            result.n_signals, "mean_pnl_per_dollar",
            result.mean_pnl_per_dollar, result.pnl_ci_lo, result.pnl_ci_hi,
            bootstrap_p=result.pnl_bootstrap_p,
        )
        session_entries = await crud.get_session_slice_lookups(conn)

    corrections = compute_corrections(result, session_entries)

    resp: dict[str, Any] = asdict(result)
    resp["corrections"] = asdict(corrections)
    resp["holdout_from"] = holdout_from.isoformat() if holdout_from else None
    resp["latency_profile"] = latency_profile
    if latency_profile is not None:
        # F7: include 'latency_unavailable' so the UI can warn when most
        # rows fell back to the optimistic baseline (signals predate F7
        # snapshot offsets, or pool is too thin at the chosen profile's
        # window).
        latency_stats = {
            **latency_stats,
            "latency_unavailable": latency_unavailable(
                latency_stats.get("adjusted", 0),
                latency_stats.get("fallback", 0),
            ),
        }
        resp["latency_stats"] = latency_stats

    # B8: optional benchmark comparison
    if benchmark is not None:
        bench_result = compute_benchmark(
            rows, benchmark,
            trade_size_usdc=filters.trade_size_usdc,
            exit_strategy=filters.exit_strategy,
        )
        resp["benchmark"] = {"name": benchmark, **asdict(bench_result)}

    return resp


@router.get("/slice")
async def get_slice(
    dimension: str = Query(..., description=f"One of {VALID_SLICE_DIMENSIONS}"),
    mode: str | None = Query(None),
    category: str | None = Query(None),
    direction: str | None = Query(None),
    min_skew: float | None = Query(None),
    max_skew: float | None = Query(None),
    min_trader_count: int | None = Query(None),
    min_aggregate_usdc: float | None = Query(None),
    min_avg_portfolio_fraction: float | None = Query(None),
    liquidity_tiers: list[str] | None = Query(None),
    market_category: str | None = Query(None),
    max_gap: float | None = Query(None),
    include_pre_fix: bool = Query(False),
    include_multi_outcome: bool = Query(False),
    trade_size_usdc: float = Query(100.0, gt=0),
    exit_strategy: str = Query("hold"),
    dedup: bool = Query(False),
    holdout_from: date | None = Query(None, description="Exclude signals on/after this date"),
    latency_profile: str | None = Query(
        None,
        description=f"B10 — execution-latency adjustment. One of {VALID_LATENCY_PROFILES}.",
    ),
    latency_min_min: float | None = Query(None),
    latency_max_min: float | None = Query(None),
) -> dict[str, Any]:
    """Same backtest, broken down per bucket of `dimension`.

    Each bucket is a distinct hypothesis. B7: inserts one slice_lookup row per
    bucket (for accurate session-level N count) and returns the post-insert
    session query count + multiplicity_warning at the top level.
    """
    if dimension not in VALID_SLICE_DIMENSIONS:
        raise HTTPException(400, f"dimension must be one of {VALID_SLICE_DIMENSIONS}")
    tiers = _parse_liquidity_tiers(liquidity_tiers)
    filters = _build_filters(
        mode, category, direction, min_skew, max_skew,
        min_trader_count, min_aggregate_usdc,
        min_avg_portfolio_fraction, tiers,
        market_category, max_gap,
        include_pre_fix, include_multi_outcome, trade_size_usdc,
        exit_strategy, dedup, holdout_from,
        latency_profile, latency_min_min, latency_max_min,
    )
    results = await backtest_slice(dimension, filters)

    # B7: insert one row per bucket into slice_lookups (each bucket = one hypothesis)
    pool = await init_pool()
    base_def = _filters_to_json_dict(filters)
    async with pool.acquire() as conn:
        for bucket_label, br in results.items():
            bucket_def = {**base_def, "dimension": dimension, "bucket": bucket_label}
            await crud.insert_slice_lookup(
                conn, bucket_def,
                br.n_signals, "mean_pnl_per_dollar",
                br.mean_pnl_per_dollar, br.pnl_ci_lo, br.pnl_ci_hi,
                bootstrap_p=br.pnl_bootstrap_p,
            )
        session_entries = await crud.get_session_slice_lookups(conn)

    n_session = len(session_entries)
    return {
        "dimension": dimension,
        "holdout_from": holdout_from.isoformat() if holdout_from else None,
        "latency_profile": latency_profile,
        "n_session_queries": n_session,
        "multiplicity_warning": n_session > 5,
        "buckets": {k: asdict(v) for k, v in results.items()},
    }


@router.get("/half_life")
async def get_half_life(
    category: str | None = Query(
        None,
        description="Restrict to one market category. Default: all categories.",
    ),
) -> dict[str, Any]:
    """B4 — convergence rate per (category, offset_min) bucket.

    For each signal we previously snapshotted (see scheduler job
    `record_signal_price_snapshots`), compares the YES price at fire to the
    YES price at +30 / +60 / +120 min, and asks: did the market move toward
    the smart-money cost basis? Convergence rate is the fraction of signals
    that did. Per-category, per-offset.

    Each bucket carries `underpowered: true` until n >= 30. Honest by default.
    """
    # F23: extracted to crud.fetch_half_life_rows. F4: rows now carry
    # bid_price + ask_price; HalfLifeRow uses mid when both available.
    pool = await init_pool()
    async with pool.acquire() as conn:
        rows = await crud.fetch_half_life_rows(conn, category=category)

    def _opt_f(v: Any) -> float | None:
        return float(v) if v is not None else None

    hl_rows = [
        HalfLifeRow(
            category=r["category"],
            fire_price=float(r["fire_price"]),
            direction=r["direction"],
            smart_money_entry=_opt_f(r["smart_money_entry"]),
            snapshot_price=_opt_f(r["yes_price"]),
            offset_min=int(r["snapshot_offset_min"]),
            bid_price=_opt_f(r["bid_price"]),
            ask_price=_opt_f(r["ask_price"]),
            snapshot_direction=r.get("snapshot_direction"),  # R8 (Pass 3)
        )
        for r in rows
    ]
    buckets = compute_half_life_summary(hl_rows)

    return {
        "category_filter": category,
        "offsets_min": list(SNAPSHOT_OFFSETS_MIN),
        "buckets": [
            {
                "category": b.category,
                "offset_min": b.offset_min,
                "n": b.n,
                "convergence_rate": b.convergence_rate,
                "underpowered": b.underpowered,
            }
            for b in buckets
        ],
    }


@router.get("/edge_decay")
async def get_edge_decay(
    mode: str | None = Query(None),
    category: str | None = Query(None),
    direction: str | None = Query(None),
    min_skew: float | None = Query(None),
    max_skew: float | None = Query(None),
    min_trader_count: int | None = Query(None),
    min_aggregate_usdc: float | None = Query(None),
    min_avg_portfolio_fraction: float | None = Query(None),
    liquidity_tiers: list[str] | None = Query(None),
    market_category: str | None = Query(None),
    max_gap: float | None = Query(None),
    include_pre_fix: bool = Query(False),
    include_multi_outcome: bool = Query(False),
    trade_size_usdc: float = Query(100.0, gt=0),
    exit_strategy: str = Query("hold"),
    dedup: bool = Query(False),
    holdout_from: date | None = Query(None),
    min_n_per_cohort: int = Query(5, ge=1, le=50),
) -> dict[str, Any]:
    """B11 — rolling weekly P&L cohorts grouped by signal first_fired week.

    Same filters as /backtest/summary. `decay_warning` fires when the most
    recent 3 cohorts' mean P&L sits below the preceding cohorts' mean P&L,
    once we have at least 4 weeks of data. Below that → insufficient_history.
    """
    tiers = _parse_liquidity_tiers(liquidity_tiers)
    filters = _build_filters(
        mode, category, direction, min_skew, max_skew,
        min_trader_count, min_aggregate_usdc,
        min_avg_portfolio_fraction, tiers,
        market_category, max_gap,
        include_pre_fix, include_multi_outcome, trade_size_usdc,
        exit_strategy, dedup, holdout_from,
        latency_profile=None, latency_min_min=None, latency_max_min=None,
    )

    # Reuse backtest_with_rows so we get the same row fetch pipeline + filters.
    _, rows, _ = await backtest_with_rows(filters)
    decay = compute_edge_decay(
        rows,
        min_n_per_cohort=min_n_per_cohort,
        trade_size_usdc=filters.trade_size_usdc,
        exit_strategy=filters.exit_strategy,
    )

    return {
        "min_n_per_cohort": min_n_per_cohort,
        "decay_warning": decay.decay_warning,
        "insufficient_history": decay.insufficient_history,
        "weeks_of_data": decay.weeks_of_data,
        "min_weeks_needed": decay.min_weeks_needed,
        "cohorts": [
            {
                "week": c.week,
                "n_eff": c.n_eff,
                "mean_pnl_per_dollar": c.mean_pnl_per_dollar,
                "pnl_ci_lo": c.pnl_ci_lo,
                "pnl_ci_hi": c.pnl_ci_hi,
                "win_rate": c.win_rate,
                "win_rate_ci_lo": c.win_rate_ci_lo,
                "win_rate_ci_hi": c.win_rate_ci_hi,
                "underpowered": c.underpowered,
            }
            for c in decay.cohorts
        ],
    }
