"""Backtest engine — turns `signal_log` + market resolutions into honest
P&L metrics, sliceable across dimensions.

Design decisions (vs the original sketch):
  - Mean P&L per $1 risked is the PRIMARY metric. Win rate is a secondary
    diagnostic — the two metrics can disagree and following win rate alone
    misleads (a 95% WR signal entering at $0.85 actually loses money).
  - Filters operate on `first_*` snapshot fields (frozen at first_fired_at),
    NOT `peak_*` — peaks are forward-looking and would bias backtest results.
  - `cluster_id` (gamma event_id) groups correlated signals — Trump 2024
    spawned hundreds of related sub-markets that aren't independent. The
    bootstrap CI resamples by cluster to avoid overstating confidence.
  - Cost model: per-category taker fee + square-root slippage scaled by
    book depth captured at signal-fire time. Three trade-size points are
    supported so the user can see the size-friction curve.
  - n < 30 effective sample → "UNDERPOWERED" — engine refuses to report a
    number the user could anchor to.
  - Pre-fix rows (`signal_entry_source = 'unavailable'`) are excluded by
    default since they have no executable entry price; can be opted in.
  - Zero new heavy deps. Wilson CI is closed-form, bootstrap is hand-rolled.

Public API:
  - `backtest_summary(filters)` → BacktestResult
  - `backtest_slice(dimension, filters)` → dict[bucket_label, BacktestResult]
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Literal

import asyncpg

from app.db import crud
from app.db.connection import init_pool

log = logging.getLogger(__name__)

MIN_SAMPLE_SIZE = 30  # n_eff threshold; below this we report "underpowered"
DEFAULT_TRADE_SIZE_USDC = 100.0
VALID_BENCHMARKS = (
    "buy_and_hold_yes",
    "buy_and_hold_no",
    "buy_and_hold_favorite",
    "coin_flip",
    "follow_top_1",
)

# B10: latency profile windows in MINUTES (min_inclusive, max_inclusive).
# `custom` reads the (latency_min_min, latency_max_min) fields off the filters.
LATENCY_PROFILES: dict[str, tuple[float, float]] = {
    "active":     (1.0, 3.0),
    "responsive": (5.0, 10.0),
    "casual":     (12.0, 20.0),
    "delayed":    (30.0, 60.0),
}

# Snapshot offsets we actually capture (mirrors half_life.SNAPSHOT_OFFSETS_MIN).
# Hard-coded here to avoid a layering import; if these diverge there's a smoke
# test in the suite that pins them together.
# F7: added 5 + 15 min offsets so active/responsive/casual latency profiles
# are no longer pure no-ops.
LATENCY_SNAPSHOT_OFFSETS: tuple[int, ...] = (5, 15, 30, 60, 120)
LATENCY_OFFSET_TOLERANCE_MIN = 5.0  # ±tolerance for "matches a canonical offset"
# F7: when fallback rate exceeds this fraction, the response carries
# latency_unavailable=True so the UI can warn "this profile has insufficient
# snapshot coverage" rather than silently showing un-adjusted numbers.
LATENCY_FALLBACK_WARN_FRACTION = 0.5
SLIPPAGE_K = 0.02   # square-root impact coefficient; calibrate empirically

# Per-category taker fees as of March 2026 — APPROXIMATE. Verify against
# polymarket.com/learn/fees before locking. Encoded here so they're easy to
# override; v2 should move this to a versioned `fee_schedule` DB table.
TAKER_FEES: dict[str, float] = {
    "politics": 0.000,  # geopolitics fee-free per public schedule
    "sports":   0.018,
    "crypto":   0.018,
    "culture":  0.012,
    "tech":     0.012,
    "finance":  0.012,
    "_default": 0.012,
}


# ---------------------------------------------------------------------------
# Statistics helpers (hand-rolled — no heavy deps)
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    """Normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Normal quantile function — Abramowitz & Stegun 26.2.17. Max error <4.5e-4."""
    if p <= 0.0: return -8.0
    if p >= 1.0: return 8.0
    if p < 0.5:
        t = math.sqrt(-2.0 * math.log(p))
        sign = -1.0
    else:
        t = math.sqrt(-2.0 * math.log(1.0 - p))
        sign = 1.0
    a = (2.515517, 0.802853, 0.010328)
    b = (1.432788, 0.189269, 0.001308)
    return sign * (t - (a[0] + a[1]*t + a[2]*t*t) / (1.0 + b[0]*t + b[1]*t*t + b[2]*t*t*t))


_Z_RAW = _norm_ppf(0.975)  # ≈ 1.96, used throughout for SE inversion


def _pvalue_from_ci(
    point: float | None, lo: float | None, hi: float | None,
) -> float:
    """Approximate two-sided p-value (H0: point=0) from CI, Gaussian assumption."""
    if None in (point, lo, hi) or hi == lo:
        return 1.0
    se = (hi - lo) / (2.0 * _Z_RAW)
    if se <= 0:
        return 1.0
    z = abs(point / se)
    return 2.0 * _norm_cdf(-z)


def _ci_gaussian(
    point: float, lo: float, hi: float, alpha_new: float,
) -> tuple[float, float]:
    """Recompute CI at a new alpha level using Gaussian SE approximation.

    Used to widen CIs for multiple-testing corrections without re-running
    the expensive bootstrap. Conservative in the same direction as the
    original CI (symmetric widening around the point estimate).
    """
    se = (hi - lo) / (2.0 * _Z_RAW)
    z = _norm_ppf(1.0 - alpha_new / 2.0)
    return (point - z * se, point + z * se)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestFilters:
    """All filterable dimensions. None on a field = no filter on that axis."""
    mode: str | None = None
    category: str | None = None
    direction: Literal["YES", "NO"] | None = None
    min_skew: float | None = None
    max_skew: float | None = None
    min_trader_count: int | None = None
    min_aggregate_usdc: float | None = None
    min_avg_portfolio_fraction: float | None = None
    liquidity_tiers: tuple[str, ...] | None = None
    market_category: str | None = None
    max_gap_to_smart_money: float | None = None  # entry_offer / first_trader_price - 1
    include_pre_fix: bool = False
    # Multi-outcome markets (e.g. team-name resolutions) break the binary YES/NO
    # P&L math — payouts aren't $1/$0 per share. Excluded by default; opt in
    # only if the user explicitly knows their downstream pipeline can handle it.
    include_multi_outcome: bool = False
    trade_size_usdc: float = DEFAULT_TRADE_SIZE_USDC
    # B1: which strategy to backtest.
    #   "hold"             — settle every signal at market resolution outcome
    #                        (current default behavior; the optimistic baseline)
    #   "smart_money_exit" — settle at signal_exits.exit_bid_price if an exit
    #                        was logged before resolution; otherwise fall back
    #                        to resolution outcome (same as hold)
    exit_strategy: Literal["hold", "smart_money_exit"] = "hold"
    # B6: when True, read from vw_signals_unique_market (one row per
    # (cid, direction) chosen as the earliest-fired across lenses) instead
    # of signal_log. Use to avoid double-counting markets that fire under
    # multiple (mode, category) selections.
    dedup: bool = False
    # B7: out-of-sample reserve — only include signals fired BEFORE this date.
    # Data from holdout_from onward is untouched, preserving a true test set.
    holdout_from: date | None = None
    # B10: realistic execution-latency simulation. None = no adjustment
    # (current/optimistic baseline: enter at fire-time signal_entry_offer).
    # When set, each row's entry price is replaced by the YES-bid snapshot at
    # `first_fired_at + sampled_offset`, where sampled_offset ~ Uniform(window)
    # seeded by hash(condition_id). Falls back to signal_entry_offer when no
    # snapshot is available within tolerance of the sampled offset.
    latency_profile: Literal[
        "active", "responsive", "casual", "delayed", "custom"
    ] | None = None
    latency_min_min: float | None = None  # only used when latency_profile == 'custom'
    latency_max_min: float | None = None


@dataclass
class BacktestResult:
    n_signals: int
    n_resolved: int
    n_eff: float                      # cluster-effective sample size
    underpowered: bool                # n_eff < MIN_SAMPLE_SIZE

    # Headline: P&L per $1 risked.
    mean_pnl_per_dollar: float | None
    pnl_ci_lo: float | None
    pnl_ci_hi: float | None

    # Secondary: win rate.
    win_rate: float | None
    win_rate_ci_lo: float | None
    win_rate_ci_hi: float | None

    # Diagnostics.
    profit_factor: float | None
    max_drawdown: float | None
    median_entry_price: float | None
    median_gap_to_smart_money: float | None

    # Sample composition for transparency.
    by_direction: dict[str, int] = field(default_factory=dict)
    by_resolution: dict[str, int] = field(default_factory=dict)

    # F21: empirical two-sided bootstrap p-value vs H0: mean = 0. Used by
    # BH-FDR ranking (see compute_corrections) instead of a Gaussian-SE-
    # from-CI reconstruction. Optional with default None for back-compat
    # with construction sites that pre-date F21.
    pnl_bootstrap_p: float | None = None


@dataclass
class MultipleTestingCorrections:
    """B7: Bonferroni + BH-FDR corrected CIs for a single backtest result.

    CIs are widened from the raw 95% interval using a Gaussian SE approximation
    (SE inferred from the bootstrap CI). This is an approximation — it widens
    conservatively in the right direction without re-running the expensive
    bootstrap. Correction level is based on how many queries have been run in
    the current session window.

    BH-FDR is less conservative than Bonferroni: it ranks all session queries
    by approximate p-value and assigns the current query an effective alpha
    proportional to its rank among them.
    """
    n_session_queries: int
    multiplicity_warning: bool        # True when n_session_queries > 5
    bonferroni_pnl_ci_lo: float | None
    bonferroni_pnl_ci_hi: float | None
    bonferroni_win_rate_ci_lo: float | None
    bonferroni_win_rate_ci_hi: float | None
    bh_fdr_pnl_ci_lo: float | None
    bh_fdr_pnl_ci_hi: float | None
    bh_fdr_win_rate_ci_lo: float | None
    bh_fdr_win_rate_ci_hi: float | None


@dataclass(frozen=True)
class SignalRow:
    """One signal_log row joined with its market + event resolution data."""
    id: int
    mode: str
    category: str
    top_n: int
    condition_id: str
    direction: str
    first_trader_count: int | None
    first_aggregate_usdc: float | None
    first_net_skew: float | None
    first_avg_portfolio_fraction: float | None
    signal_entry_offer: float | None
    signal_entry_mid: float | None
    liquidity_at_signal_usdc: float | None
    liquidity_tier: str | None
    first_top_trader_entry_price: float | None
    cluster_id: str | None
    market_type: str
    first_fired_at: datetime
    resolved_outcome: str | None
    market_category: str | None
    # B1: smart-money-exit data (LEFT JOIN signal_exits). All None if the
    # signal never exited before resolution / is still live.
    exit_bid_price: float | None = None
    exit_drop_reason: str | None = None
    exited_at: datetime | None = None
    # B6: cross-mode lens info. lens_count = how many (mode, category) lenses
    # detected the same (cid, direction); 1 when not deduped.
    lens_count: int = 1
    lens_list: list[str] | None = None

    @property
    def gap_to_smart_money(self) -> float | None:
        """How far the current ask has moved from smart-money cost basis.

        Positive = price moved toward smart money's view (less edge left).
        Negative = price moved against them (you'd enter cheaper than they did).

        F19 (verified): both `signal_entry_offer` and
        `first_top_trader_entry_price` are stored in DIRECTION-space —
        signal_entry_offer is the ask of the chosen-direction's CLOB token
        (jobs.py:419 picks YES token for YES signals, NO for NO), and
        first_top_trader_entry_price is `SUM(avg_price * size) / SUM(size)`
        from positions filtered to the signal's direction's outcome. So
        comparing them directly is correct in either YES or NO space; no
        translation needed. (The F5 half-life bug existed because snapshots
        are YES-space while these two are direction-space — a different
        comparison.)
        """
        if (self.signal_entry_offer is None
                or self.first_top_trader_entry_price is None
                or self.first_top_trader_entry_price <= 0):
            return None
        return (self.signal_entry_offer - self.first_top_trader_entry_price) \
            / self.first_top_trader_entry_price


# ---------------------------------------------------------------------------
# Statistics (hand-rolled — no heavy deps)
# ---------------------------------------------------------------------------


def wilson_ci(wins: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson 95% confidence interval for a binomial proportion.

    Better than Wald (normal approximation) at small n — the only kind we'll
    have for the first few weeks of operation.
    """
    if n == 0:
        return (0.0, 1.0)
    z = 1.959964  # Φ^-1(1 - α/2) for α = 0.05
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - spread), min(1.0, center + spread))


def cluster_bootstrap_mean(
    values: list[float],
    cluster_keys: list[str | None],
    n_iter: int = 5000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Cluster bootstrap 95% CI for the mean.

    Resamples WHOLE CLUSTERS with replacement (Cameron-Gelbach-Miller 2008).
    None-keyed observations are treated as their own singleton cluster.
    Returns (point_estimate, ci_lo, ci_hi).
    """
    if not values:
        return (0.0, 0.0, 0.0)
    point, lo, hi, _ = cluster_bootstrap_mean_with_p(values, cluster_keys, n_iter, seed)
    return (point, lo, hi)


def cluster_bootstrap_mean_with_p(
    values: list[float],
    cluster_keys: list[str | None],
    n_iter: int = 5000,
    seed: int = 42,
) -> tuple[float, float, float, float]:
    """F21: Same as cluster_bootstrap_mean but also returns the two-sided
    bootstrap p-value vs H0: mean = 0.

    Pre-fix BH-FDR ranking reconstructed an approximate p-value from the CI
    via a Gaussian SE assumption (`(hi-lo) / (2 × 1.96)`), which breaks down
    when the bootstrap distribution is skewed (P&L distributions on
    Polymarket are heavy-tailed). Computing p directly from the empirical
    bootstrap distribution: p_one_sided = fraction of resampled means below
    zero (or above zero for negative point estimates), p_two_sided =
    2 × min(p_one_sided, 1 - p_one_sided).

    Returns (point, ci_lo, ci_hi, p_two_sided). p_two_sided is in [0, 1].
    """
    if not values:
        return (0.0, 0.0, 0.0, 1.0)

    by_cluster: dict[str, list[float]] = {}
    for i, (v, k) in enumerate(zip(values, cluster_keys)):
        key = k if k is not None else f"_solo_{i}"
        by_cluster.setdefault(key, []).append(v)

    keys = list(by_cluster.keys())
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(n_iter):
        sample: list[float] = []
        for _ in keys:
            sample.extend(by_cluster[rng.choice(keys)])
        if sample:
            estimates.append(sum(sample) / len(sample))

    estimates.sort()
    point = sum(values) / len(values)
    lo = estimates[int(0.025 * n_iter)]
    hi = estimates[int(0.975 * n_iter)]

    # F21: empirical two-sided p vs H0: mean = 0. Fraction of resamples
    # at or below 0; p_two_sided = 2 × min(p_below, 1 - p_below) clamped
    # to [0, 1].
    n_eff_iter = len(estimates)
    if n_eff_iter == 0:
        return (point, lo, hi, 1.0)
    n_below = sum(1 for e in estimates if e <= 0)
    p_below = n_below / n_eff_iter
    p_two_sided = min(1.0, 2.0 * min(p_below, 1.0 - p_below))
    return (point, lo, hi, p_two_sided)


# ---------------------------------------------------------------------------
# Per-signal P&L
# ---------------------------------------------------------------------------


def compute_pnl_per_dollar_exit(
    entry_price: float,
    exit_bid_price: float,
    category: str,
    trade_size_usdc: float,
    liquidity_at_signal: float | None,
    median_liquidity_fallback: float | None = None,
) -> float | None:
    """P&L per $1 when exiting at a known bid (smart-money-exit strategy).

    Same structure as `compute_pnl_per_dollar` but the payoff is the bid we
    sell into, not the binary $1/$0 resolution payoff. Entry slippage is
    already absorbed via `effective_entry`; we don't model exit slippage
    separately because the bid we captured IS the price you'd actually clear
    at (worst-case fill).

    Returns None for invalid inputs (entry >= 1.0, non-positive bid, etc.) so
    callers can filter the row out cleanly.
    """
    if entry_price is None or entry_price <= 0:
        return None
    if entry_price >= 1.0:
        log.warning(
            "compute_pnl_per_dollar_exit: entry_price=%.4f >= 1.0 — skipping",
            entry_price,
        )
        return None
    if exit_bid_price is None or exit_bid_price <= 0:
        return None

    effective_liquidity: float | None
    if liquidity_at_signal and liquidity_at_signal > 0:
        effective_liquidity = liquidity_at_signal
    elif median_liquidity_fallback and median_liquidity_fallback > 0:
        effective_liquidity = median_liquidity_fallback
    else:
        effective_liquidity = None

    if effective_liquidity is not None:
        slip = min(0.10, SLIPPAGE_K * math.sqrt(trade_size_usdc / effective_liquidity))
    else:
        slip = min(0.05, trade_size_usdc / 50_000.0)

    effective_entry = min(0.999, entry_price + slip)
    fee_rate = TAKER_FEES.get(category, TAKER_FEES["_default"])
    gross_per_dollar = exit_bid_price / effective_entry
    return gross_per_dollar * (1.0 - fee_rate) - 1.0


def compute_pnl_per_dollar(
    entry_price: float,
    direction: str,
    resolved_outcome: str,
    category: str,
    trade_size_usdc: float,
    liquidity_at_signal: float | None,
    median_liquidity_fallback: float | None = None,
) -> float | None:
    """P&L per $1 invested at signal-fire entry, fee + slippage adjusted.

    Math: buy `1/effective_entry` shares with $1, get `payout_per_share`
    each at resolution. Subtract fee. Returns None for VOID or unknown.

    Slippage: square-root impact in trade size relative to depth at fire,
    capped at 10c. Empirically the right shape per the IMDEA arbitrage
    paper; magnitude (`SLIPPAGE_K=0.02`) is a placeholder until calibrated.
    When liquidity is missing, falls back to `median_liquidity_fallback`
    (the median of liquidity_at_signal across resolvable rows in the same
    backtest pass) before resorting to the hard-coded $50k tier guess.
    """
    if entry_price is None or entry_price <= 0:
        return None
    if entry_price >= 1.0:
        # Sanity check — exchange would never let an order print at >=$1.
        # Almost always indicates a stale/garbage data point. Log so we can
        # spot systematic capture issues in raw snapshots.
        log.warning(
            "compute_pnl_per_dollar: entry_price=%.4f >= 1.0 (direction=%s, outcome=%s) — skipping",
            entry_price, direction, resolved_outcome,
        )
        return None
    if resolved_outcome == "VOID":
        return None

    if resolved_outcome == direction:
        payout_per_share = 1.0
    elif resolved_outcome in ("YES", "NO"):
        payout_per_share = 0.0
    elif resolved_outcome == "50_50":
        payout_per_share = 0.5
    else:
        return None  # PENDING or unknown — caller should have filtered

    effective_liquidity: float | None
    if liquidity_at_signal and liquidity_at_signal > 0:
        effective_liquidity = liquidity_at_signal
    elif median_liquidity_fallback and median_liquidity_fallback > 0:
        effective_liquidity = median_liquidity_fallback
    else:
        effective_liquidity = None

    if effective_liquidity is not None:
        slip = min(0.10, SLIPPAGE_K * math.sqrt(trade_size_usdc / effective_liquidity))
    else:
        slip = min(0.05, trade_size_usdc / 50_000.0)

    effective_entry = min(0.999, entry_price + slip)
    fee_rate = TAKER_FEES.get(category, TAKER_FEES["_default"])
    gross_per_dollar = payout_per_share / effective_entry
    # Polymarket charges taker fees on PAYOUT value, not on stake. Previously
    # we deducted `fee_rate` flat from every trade — this over-penalized
    # losers (you can't lose more than your stake) and under-penalized big
    # winners (where the fee on the larger payout is bigger). The correct
    # form: fee scales with what you actually receive.
    return gross_per_dollar * (1.0 - fee_rate) - 1.0


# ---------------------------------------------------------------------------
# DB → SignalRow
# ---------------------------------------------------------------------------


# Column projection — same shape whether `s` is signal_log or
# vw_signals_unique_market. The view has lens_count + lens_list extras;
# signal_log doesn't, so we COALESCE them to (1, NULL) below.
_SELECT_COLS = """
    s.id, s.mode, s.category, s.top_n, s.condition_id, s.direction,
    s.first_trader_count, s.first_aggregate_usdc, s.first_net_skew,
    s.first_avg_portfolio_fraction,
    s.signal_entry_offer, s.signal_entry_mid,
    s.liquidity_at_signal_usdc, s.liquidity_tier,
    s.first_top_trader_entry_price,
    s.cluster_id, s.market_type, s.first_fired_at,
    m.resolved_outcome,
    e.category AS market_category,
    se.exit_bid_price, se.drop_reason AS exit_drop_reason, se.exited_at
"""

_SELECT_COLS_DEDUP = _SELECT_COLS + ",\n    s.lens_count, s.lens_list"


def _row_to_signal(r: asyncpg.Record) -> SignalRow:
    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None
    keys = r.keys()
    lens_count = int(r["lens_count"]) if "lens_count" in keys and r["lens_count"] is not None else 1
    lens_list = list(r["lens_list"]) if "lens_list" in keys and r["lens_list"] is not None else None
    return SignalRow(
        id=r["id"], mode=r["mode"], category=r["category"], top_n=r["top_n"],
        condition_id=r["condition_id"], direction=r["direction"],
        first_trader_count=r["first_trader_count"],
        first_aggregate_usdc=_f(r["first_aggregate_usdc"]),
        first_net_skew=_f(r["first_net_skew"]),
        first_avg_portfolio_fraction=_f(r["first_avg_portfolio_fraction"]),
        signal_entry_offer=_f(r["signal_entry_offer"]),
        signal_entry_mid=_f(r["signal_entry_mid"]),
        liquidity_at_signal_usdc=_f(r["liquidity_at_signal_usdc"]),
        liquidity_tier=r["liquidity_tier"],
        first_top_trader_entry_price=_f(r["first_top_trader_entry_price"]),
        cluster_id=r["cluster_id"], market_type=r["market_type"],
        first_fired_at=r["first_fired_at"],
        resolved_outcome=r["resolved_outcome"],
        market_category=r["market_category"],
        exit_bid_price=_f(r["exit_bid_price"]),
        exit_drop_reason=r["exit_drop_reason"],
        exited_at=r["exited_at"],
        lens_count=lens_count,
        lens_list=lens_list,
    )


async def _fetch_signals(
    conn: asyncpg.Connection, filters: BacktestFilters
) -> list[SignalRow]:
    """Pull signal_log + market resolution joined, applying SQL filters where
    possible. Computed-property filters (gap) applied in Python after fetch.
    """
    # B6: when dedup is on, source from vw_signals_unique_market (one row per
    # cid+direction). Mode/category filters then refer to the canonical
    # earliest-fired row's lens. Most filters still make sense; mode/category
    # are slightly weaker because they only match the canonical lens.
    table_alias = "vw_signals_unique_market" if filters.dedup else "signal_log"
    cols = _SELECT_COLS_DEDUP if filters.dedup else _SELECT_COLS

    parts = [f"""
        SELECT {cols}
        FROM {table_alias} s
        JOIN markets m ON m.condition_id = s.condition_id
        LEFT JOIN events e ON e.id = m.event_id
        LEFT JOIN signal_exits se ON se.signal_log_id = s.id
        WHERE 1=1
    """]
    args: list[Any] = []

    if not filters.include_pre_fix:
        parts.append("AND COALESCE(s.signal_entry_source, '') != 'unavailable'")
    if not filters.include_multi_outcome:
        parts.append("AND COALESCE(s.market_type, 'binary') = 'binary'")
    if filters.mode is not None:
        args.append(filters.mode); parts.append(f"AND s.mode = ${len(args)}")
    if filters.category is not None:
        args.append(filters.category); parts.append(f"AND s.category = ${len(args)}")
    if filters.direction is not None:
        args.append(filters.direction); parts.append(f"AND s.direction = ${len(args)}")
    if filters.min_skew is not None:
        args.append(filters.min_skew); parts.append(f"AND s.first_net_skew >= ${len(args)}")
    if filters.max_skew is not None:
        args.append(filters.max_skew); parts.append(f"AND s.first_net_skew <= ${len(args)}")
    if filters.min_trader_count is not None:
        args.append(filters.min_trader_count); parts.append(f"AND s.first_trader_count >= ${len(args)}")
    if filters.min_aggregate_usdc is not None:
        args.append(filters.min_aggregate_usdc); parts.append(f"AND s.first_aggregate_usdc >= ${len(args)}")
    if filters.min_avg_portfolio_fraction is not None:
        args.append(filters.min_avg_portfolio_fraction); parts.append(f"AND s.first_avg_portfolio_fraction >= ${len(args)}")
    if filters.liquidity_tiers:
        args.append(list(filters.liquidity_tiers)); parts.append(f"AND s.liquidity_tier = ANY(${len(args)}::TEXT[])")
    if filters.market_category is not None:
        args.append(filters.market_category); parts.append(f"AND e.category = ${len(args)}")
    if filters.holdout_from is not None:
        # F22: hold the cutoff as an explicit UTC midnight timestamp. Postgres
        # would otherwise implicit-cast a `date` to a session-TZ-dependent
        # timestamp, shifting the cutoff by hours if session TZ ever drifts
        # off UTC. Edge-of-day signals could leak into / out of the training set.
        cutoff = datetime(
            filters.holdout_from.year,
            filters.holdout_from.month,
            filters.holdout_from.day,
            tzinfo=timezone.utc,
        )
        args.append(cutoff); parts.append(f"AND s.first_fired_at < ${len(args)}")

    rows = await conn.fetch("\n".join(parts), *args)
    out: list[SignalRow] = []
    for r in rows:
        sr = _row_to_signal(r)
        if filters.max_gap_to_smart_money is not None:
            g = sr.gap_to_smart_money
            if g is not None and g > filters.max_gap_to_smart_money:
                continue
        out.append(sr)
    return out


# ---------------------------------------------------------------------------
# Pure metric computation
# ---------------------------------------------------------------------------


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarize_rows(
    rows: list[SignalRow],
    trade_size_usdc: float = DEFAULT_TRADE_SIZE_USDC,
    exit_strategy: Literal["hold", "smart_money_exit"] = "hold",
) -> BacktestResult:
    """Compute all metrics over a set of signals. Pure function.

    `exit_strategy`:
      - "hold"             — settle every signal at market resolution outcome
      - "smart_money_exit" — settle at signal_exits.exit_bid_price if exited
                             before resolution; fall back to resolution
                             outcome otherwise (so a never-exited signal still
                             contributes to the strategy via its resolution)
    """
    n_signals = len(rows)
    by_direction: dict[str, int] = {}
    by_resolution: dict[str, int] = {}
    for r in rows:
        by_direction[r.direction] = by_direction.get(r.direction, 0) + 1
        by_resolution[r.resolved_outcome or "PENDING"] = (
            by_resolution.get(r.resolved_outcome or "PENDING", 0) + 1
        )

    # A row is "settleable" under smart_money_exit if it has a recorded exit
    # OR is resolved (otherwise we don't know how to close it). For hold, only
    # resolved rows count. n_resolved keeps the resolution-only count for
    # back-compat in the result; settleable is what feeds pnl_pairs.
    resolved = [r for r in rows if r.resolved_outcome in ("YES", "NO", "50_50")]
    n_resolved = len(resolved)

    if exit_strategy == "smart_money_exit":
        settleable = [
            r for r in rows
            if (r.exit_bid_price is not None and r.exit_bid_price > 0)
            or r.resolved_outcome in ("YES", "NO", "50_50")
        ]
    else:
        settleable = resolved

    # Median liquidity across settleable rows that DO have it — used as the
    # slippage fallback for rows missing liquidity_at_signal_usdc.
    observed_liquidities = [
        r.liquidity_at_signal_usdc for r in settleable
        if r.liquidity_at_signal_usdc is not None and r.liquidity_at_signal_usdc > 0
    ]
    median_liquidity_fallback = _median(observed_liquidities)

    # Per-signal P&L pairs (only signals where we can compute one).
    pnl_pairs: list[tuple[SignalRow, float]] = []
    for r in settleable:
        if r.signal_entry_offer is None:
            continue
        pnl: float | None
        if exit_strategy == "smart_money_exit" and r.exit_bid_price is not None:
            pnl = compute_pnl_per_dollar_exit(
                r.signal_entry_offer, r.exit_bid_price,
                r.category, trade_size_usdc, r.liquidity_at_signal_usdc,
                median_liquidity_fallback=median_liquidity_fallback,
            )
        elif r.resolved_outcome in ("YES", "NO", "50_50"):
            pnl = compute_pnl_per_dollar(
                r.signal_entry_offer, r.direction, r.resolved_outcome,  # type: ignore[arg-type]
                r.category, trade_size_usdc, r.liquidity_at_signal_usdc,
                median_liquidity_fallback=median_liquidity_fallback,
            )
        else:
            continue
        if pnl is None:
            continue
        pnl_pairs.append((r, pnl))

    if not pnl_pairs:
        return BacktestResult(
            n_signals=n_signals, n_resolved=n_resolved, n_eff=0.0,
            underpowered=True,
            mean_pnl_per_dollar=None, pnl_ci_lo=None, pnl_ci_hi=None,
            win_rate=None, win_rate_ci_lo=None, win_rate_ci_hi=None,
            profit_factor=None, max_drawdown=None,
            median_entry_price=None, median_gap_to_smart_money=None,
            by_direction=by_direction, by_resolution=by_resolution,
        )

    values = [p for _, p in pnl_pairs]
    cluster_keys = [r.cluster_id for r, _ in pnl_pairs]
    # "Win" = trade made money in dollar terms. Cleanest definition:
    # pnl_per_dollar > 0. Properly handles 50_50 resolutions (which can
    # be a win when entered cheap and a loss when entered expensive) and
    # stays consistent with the fee/slippage cost model. Exact-zero P&L
    # counts as a loss (didn't profit).
    wins = sum(1 for _, p in pnl_pairs if p > 0)

    # Effective sample size = number of distinct clusters represented.
    distinct_clusters = len({k or f"_solo_{i}" for i, k in enumerate(cluster_keys)})
    n_eff = float(distinct_clusters)
    underpowered = n_eff < MIN_SAMPLE_SIZE

    # F21: also stash the empirical bootstrap p-value so BH-FDR ranking can
    # use it directly instead of reconstructing an approximate p from the
    # CI via a Gaussian SE assumption (which breaks down on skewed P&L
    # distributions). p_two_sided ∈ [0, 1].
    pnl_point, pnl_lo, pnl_hi, pnl_bootstrap_p = cluster_bootstrap_mean_with_p(
        values, cluster_keys,
    )
    # F8: cluster-bootstrap the win rate the same way as the P&L mean. Pre-fix
    # used Wilson(wins, len(pnl_pairs)), which assumed IID — too tight by
    # ~sqrt(n / n_eff) under Polymarket-style cluster correlation (one mega-
    # event spawning many correlated sub-markets). Bootstrapping the binary
    # win indicators with the same cluster_keys gives a CI that reflects
    # between-cluster variability honestly.
    win_indicators = [1.0 if p > 0 else 0.0 for _, p in pnl_pairs]
    _wr_point, wr_lo_raw, wr_hi_raw = cluster_bootstrap_mean(
        win_indicators, cluster_keys,
    )
    wr = wins / len(pnl_pairs)  # exact rate; bootstrap point is approx the same
    # Bootstrap quantiles can drift fractionally outside [0, 1] with skewed
    # cluster sizes; clamp.
    wr_lo = max(0.0, wr_lo_raw)
    wr_hi = min(1.0, wr_hi_raw)

    gross_wins = sum(p for p in values if p > 0)
    gross_losses = -sum(p for p in values if p < 0)
    if gross_losses > 0:
        pf: float | None = gross_wins / gross_losses
    elif gross_wins > 0:
        # No losses at all yet — profit factor is mathematically undefined
        # (would be `inf`). Return None so JSON renders as null and the UI
        # can show "n/a"; callers that need a number can fall back to
        # win_rate + mean_pnl_per_dollar.
        pf = None
    else:
        pf = 0.0

    # MaxDD on simulated equity curve, 1% sizing per signal, chronological order.
    chronological = sorted(pnl_pairs, key=lambda x: x[0].first_fired_at)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    sizing = 0.01
    for _, p in chronological:
        equity *= (1 + sizing * p)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    median_entry = _median([r.signal_entry_offer for r, _ in pnl_pairs
                             if r.signal_entry_offer is not None])
    gaps = [r.gap_to_smart_money for r, _ in pnl_pairs if r.gap_to_smart_money is not None]
    median_gap = _median(gaps) if gaps else None

    return BacktestResult(
        n_signals=n_signals, n_resolved=n_resolved, n_eff=n_eff,
        underpowered=underpowered,
        mean_pnl_per_dollar=pnl_point, pnl_ci_lo=pnl_lo, pnl_ci_hi=pnl_hi,
        pnl_bootstrap_p=pnl_bootstrap_p,
        win_rate=wr, win_rate_ci_lo=wr_lo, win_rate_ci_hi=wr_hi,
        profit_factor=pf, max_drawdown=max_dd,
        median_entry_price=median_entry, median_gap_to_smart_money=median_gap,
        by_direction=by_direction, by_resolution=by_resolution,
    )


# ---------------------------------------------------------------------------
# Slicing
# ---------------------------------------------------------------------------


def _bucket(row: SignalRow, dimension: str) -> str:
    """Map a row to its bucket label for a given slicing dimension."""
    if dimension == "mode":
        return row.mode
    if dimension == "category":
        return row.category
    if dimension == "direction":
        return row.direction
    if dimension == "market_category":
        return row.market_category or "uncategorized"
    if dimension == "liquidity_tier":
        return row.liquidity_tier or "unknown"
    if dimension == "skew_bucket":
        s = row.first_net_skew
        if s is None:
            return "unknown"
        s = abs(s)
        if s < 0.6: return "<60%"
        if s < 0.7: return "60-69%"
        if s < 0.8: return "70-79%"
        if s < 0.9: return "80-89%"
        return "90-100%"
    if dimension == "trader_count_bucket":
        n = row.first_trader_count
        if n is None: return "unknown"
        if n < 5: return "<5"
        if n < 10: return "5-9"
        if n < 15: return "10-14"
        if n < 20: return "15-19"
        return "20+"
    if dimension == "aggregate_bucket":
        a = row.first_aggregate_usdc
        if a is None: return "unknown"
        if a < 100_000: return "<$100k"
        if a < 500_000: return "$100k-$500k"
        if a < 1_000_000: return "$500k-$1M"
        return "$1M+"
    if dimension == "entry_price_bucket":
        p = row.signal_entry_offer
        if p is None: return "unknown"
        if p < 0.2: return "0-20¢"
        if p < 0.4: return "20-40¢"
        if p < 0.6: return "40-60¢"
        if p < 0.8: return "60-80¢"
        return "80-100¢"
    if dimension == "gap_bucket":
        g = row.gap_to_smart_money
        if g is None: return "unknown"
        if g < -0.10: return "<-10% (cheaper than smart money)"
        if g < 0.10:  return "near smart money entry"
        if g < 0.50:  return "10-50% gap (price moved up)"
        return ">50% gap (mostly priced in)"
    if dimension == "lens_count_bucket":
        # B9: how many (mode, category) lenses detected this market. Only
        # meaningful when querying with dedup=true (otherwise every row is 1).
        lc = row.lens_count
        if lc <= 1: return "1"
        if lc <= 3: return "2-3"
        if lc <= 5: return "4-5"
        return "6+"
    raise ValueError(f"Unknown slice dimension: {dimension!r}")


def slice_rows(
    rows: list[SignalRow], dimension: str,
    trade_size_usdc: float = DEFAULT_TRADE_SIZE_USDC,
    exit_strategy: Literal["hold", "smart_money_exit"] = "hold",
) -> dict[str, BacktestResult]:
    """Group rows by `_bucket(dim)` and compute metrics per bucket."""
    buckets: dict[str, list[SignalRow]] = {}
    for r in rows:
        buckets.setdefault(_bucket(r, dimension), []).append(r)
    return {
        k: summarize_rows(v, trade_size_usdc, exit_strategy=exit_strategy)
        for k, v in buckets.items()
    }


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def backtest_summary(filters: BacktestFilters | None = None) -> BacktestResult:
    """Headline metrics. Honors `latency_profile` if set."""
    result, _, _ = await backtest_with_rows(filters)
    return result


async def backtest_slice(
    dimension: str, filters: BacktestFilters | None = None
) -> dict[str, BacktestResult]:
    """Same as backtest_summary but bucketed by `dimension`. Honors latency."""
    f = filters or BacktestFilters()
    pool = await init_pool(min_size=1, max_size=2)
    async with pool.acquire() as conn:
        rows = await _fetch_signals(conn, f)
        snapshots: dict[tuple[int, int], dict[str, float | None]] = {}
        if _resolve_latency_window(f) is not None and rows:
            snapshots = await crud.fetch_signal_price_snapshots(
                conn, [r.id for r in rows],
            )
    rows_after, _, _ = _apply_latency(rows, f, snapshots)
    return slice_rows(
        rows_after, dimension,
        trade_size_usdc=f.trade_size_usdc,
        exit_strategy=f.exit_strategy,
    )


def _resolve_latency_window(f: BacktestFilters) -> tuple[float, float] | None:
    """Return (min_min, max_min) for the configured profile, or None if disabled."""
    if f.latency_profile is None:
        return None
    if f.latency_profile == "custom":
        if f.latency_min_min is None or f.latency_max_min is None:
            return None
        if f.latency_max_min < f.latency_min_min:
            return None
        return (float(f.latency_min_min), float(f.latency_max_min))
    return LATENCY_PROFILES.get(f.latency_profile)


def _sampled_latency_minutes(condition_id: str, window: tuple[float, float]) -> float:
    """Deterministic Uniform(min, max) sample seeded by condition_id.

    Reproducible across runs (no randomness leaks) so backtests are
    deterministic given the same filters + signal_log + snapshot data.
    """
    seed_int = int(hashlib.sha256(condition_id.encode()).hexdigest(), 16)
    # Map the 256-bit hash to [0, 1) deterministically.
    u = (seed_int % (10 ** 18)) / float(10 ** 18)
    lo, hi = window
    return lo + u * (hi - lo)


def _nearest_snapshot_offset(latency_min: float) -> int | None:
    """Closest canonical offset within tolerance, or None if none qualifies."""
    closest = min(
        LATENCY_SNAPSHOT_OFFSETS,
        key=lambda o: abs(o - latency_min),
    )
    if abs(closest - latency_min) <= LATENCY_OFFSET_TOLERANCE_MIN:
        return closest
    return None


def _apply_latency(
    rows: list[SignalRow],
    f: BacktestFilters,
    snapshots: dict[tuple[int, int], dict[str, float | None]],
) -> tuple[list[SignalRow], int, int]:
    """Replace each row's signal_entry_offer with the latency-adjusted price
    when a matching snapshot exists.

    F4: snapshots now carry both bid + ask. Latency simulates "I bought
    `latency_min` minutes after the signal fired" — which means crossing
    the ask at that time. So we use the ASK from the snapshot, not the
    bid. (Pre-fix used yes_price = bid, which was systematically optimistic
    by half the spread. The bias was small but real and now corrected.)
    Falls back to bid-only on legacy rows that don't have ask captured.

    Returns (new_rows, n_adjusted, n_fallback). Rows without a usable
    snapshot keep their original signal_entry_offer.

    NO-direction rows: snapshot is YES-space; translate to direction space
    via 1 − price.
    """
    window = _resolve_latency_window(f)
    if window is None:
        return rows, 0, 0

    new_rows: list[SignalRow] = []
    adjusted = 0
    fallback = 0
    for r in rows:
        sampled = _sampled_latency_minutes(r.condition_id, window)
        offset = _nearest_snapshot_offset(sampled)
        snap = snapshots.get((r.id, offset)) if offset is not None else None
        # F4: prefer ask (true buy-cross price); fall back to bid for
        # legacy rows that pre-date F4 (bid only).
        snap_yes = None
        if snap is not None:
            snap_yes = snap.get("ask")
            if snap_yes is None:
                snap_yes = snap.get("bid")
        if snap_yes is None:
            fallback += 1
            new_rows.append(r)
            continue
        new_offer = snap_yes if r.direction == "YES" else (1.0 - snap_yes)
        new_rows.append(dataclasses.replace(r, signal_entry_offer=new_offer))
        adjusted += 1
    return new_rows, adjusted, fallback


def latency_unavailable(
    n_adjusted: int, n_fallback: int,
    threshold: float = LATENCY_FALLBACK_WARN_FRACTION,
) -> bool:
    """F7: Surface a 'latency_unavailable' flag when fallback dominates.

    The short profiles (active 1-3, responsive 5-10, casual 12-20) require
    +5/+15 snapshot offsets which only became available in F7. Until those
    snapshots accumulate (or for backtests on signals fired before F7),
    most rows fall back to the optimistic baseline. This flag tells the UI
    to warn 'this profile cannot be honored — current snapshot coverage is
    insufficient' instead of pretending the simulation worked.
    """
    total = n_adjusted + n_fallback
    if total == 0:
        return False
    return (n_fallback / total) > threshold


async def backtest_with_rows(
    filters: BacktestFilters | None = None,
) -> tuple[BacktestResult, list[SignalRow], dict[str, int]]:
    """Return BacktestResult + raw SignalRows + latency_stats dict.

    Needed by the route layer when it also wants to compute benchmarks
    over the same row set without a second DB round-trip. latency_stats
    carries `adjusted` / `fallback` counts so the route can surface them.
    """
    f = filters or BacktestFilters()
    pool = await init_pool(min_size=1, max_size=2)
    async with pool.acquire() as conn:
        rows = await _fetch_signals(conn, f)
        # B10: pull all snapshots up-front in one query, then apply per-row.
        snapshots: dict[tuple[int, int], dict[str, float | None]] = {}
        if _resolve_latency_window(f) is not None and rows:
            snapshots = await crud.fetch_signal_price_snapshots(
                conn, [r.id for r in rows],
            )
    rows_after, adjusted, fallback = _apply_latency(rows, f, snapshots)
    result = summarize_rows(
        rows_after, trade_size_usdc=f.trade_size_usdc, exit_strategy=f.exit_strategy,
    )
    return result, rows_after, {"adjusted": adjusted, "fallback": fallback}


# ---------------------------------------------------------------------------
# B7: Multiple-testing corrections
# ---------------------------------------------------------------------------


def compute_corrections(
    result: BacktestResult,
    session_entries: list[dict],
) -> MultipleTestingCorrections:
    """Bonferroni + BH-FDR corrected CIs from the raw BacktestResult.

    `session_entries` — all slice_lookup rows from the current session window
    (INCLUDING the row just inserted for this query), each a dict with keys
    reported_value, ci_low, ci_high. n_session_queries = len(session_entries).

    Bonferroni: alpha / N — most conservative, controls family-wise error rate.
    BH-FDR: ranks all session queries by approximate p-value; assigns the
    current query an effective alpha ∝ its rank. Less conservative than
    Bonferroni, controls false-discovery rate at 5%.

    Both use a Gaussian SE approximation to widen CIs without re-running
    the bootstrap (SE inferred as (ci_hi − ci_lo) / (2 × 1.96)).
    """
    N = max(1, len(session_entries))
    alpha_bonf = 0.05 / N

    # --- BH-FDR: rank-based effective alpha --------------------------------
    # F21: prefer the empirical bootstrap p-value when available (stored on
    # BacktestResult as `pnl_bootstrap_p`). Falls back to the Gaussian-from-CI
    # approximation only for session entries that pre-date F21 or for the
    # current result if it lacks the bootstrap p (defensive — shouldn't
    # happen since we now always populate it). The bootstrap p is more
    # accurate on skewed P&L distributions.
    if result.pnl_bootstrap_p is not None:
        current_pnl_p = result.pnl_bootstrap_p
    else:
        current_pnl_p = _pvalue_from_ci(
            result.mean_pnl_per_dollar, result.pnl_ci_lo, result.pnl_ci_hi,
        )
    all_pvals = [
        e["bootstrap_p"]
        if e.get("bootstrap_p") is not None
        else _pvalue_from_ci(
            e.get("reported_value"), e.get("ci_low"), e.get("ci_high"),
        )
        for e in session_entries
    ]
    sorted_p = sorted(all_pvals)
    # F20: rank of current query among all session queries (1-indexed). For
    # tied p-values we use the highest available rank (ties -> highest),
    # matching statsmodels.stats.multitest.fdrcorrection. Pre-fix the comment
    # claimed "ties -> lowest" which would have been more conservative; we
    # align the comment to the code's actual behavior. See review/FIXES.md F20.
    current_rank = max(1, sum(1 for p in sorted_p if p <= current_pnl_p))
    alpha_bh = min(0.05, 0.05 * current_rank / N)

    # --- P&L CI corrections ------------------------------------------------
    bonf_pnl_lo: float | None = None
    bonf_pnl_hi: float | None = None
    bh_pnl_lo: float | None = None
    bh_pnl_hi: float | None = None
    if (result.mean_pnl_per_dollar is not None
            and result.pnl_ci_lo is not None
            and result.pnl_ci_hi is not None):
        bonf_pnl_lo, bonf_pnl_hi = _ci_gaussian(
            result.mean_pnl_per_dollar, result.pnl_ci_lo, result.pnl_ci_hi, alpha_bonf,
        )
        bh_pnl_lo, bh_pnl_hi = _ci_gaussian(
            result.mean_pnl_per_dollar, result.pnl_ci_lo, result.pnl_ci_hi, alpha_bh,
        )

    # --- Win-rate CI corrections -------------------------------------------
    bonf_wr_lo: float | None = None
    bonf_wr_hi: float | None = None
    bh_wr_lo: float | None = None
    bh_wr_hi: float | None = None
    if (result.win_rate is not None
            and result.win_rate_ci_lo is not None
            and result.win_rate_ci_hi is not None):
        lo, hi = _ci_gaussian(
            result.win_rate, result.win_rate_ci_lo, result.win_rate_ci_hi, alpha_bonf,
        )
        bonf_wr_lo, bonf_wr_hi = max(0.0, lo), min(1.0, hi)
        lo, hi = _ci_gaussian(
            result.win_rate, result.win_rate_ci_lo, result.win_rate_ci_hi, alpha_bh,
        )
        bh_wr_lo, bh_wr_hi = max(0.0, lo), min(1.0, hi)

    return MultipleTestingCorrections(
        n_session_queries=N,
        multiplicity_warning=N > 5,
        bonferroni_pnl_ci_lo=bonf_pnl_lo,
        bonferroni_pnl_ci_hi=bonf_pnl_hi,
        bonferroni_win_rate_ci_lo=bonf_wr_lo,
        bonferroni_win_rate_ci_hi=bonf_wr_hi,
        bh_fdr_pnl_ci_lo=bh_pnl_lo,
        bh_fdr_pnl_ci_hi=bh_pnl_hi,
        bh_fdr_win_rate_ci_lo=bh_wr_lo,
        bh_fdr_win_rate_ci_hi=bh_wr_hi,
    )


# ---------------------------------------------------------------------------
# B8: Boring benchmarks
# ---------------------------------------------------------------------------


def _coin_direction(condition_id: str) -> str:
    """Deterministic YES/NO from condition_id hash — consistent across runs."""
    h = int(hashlib.sha256(condition_id.encode()).hexdigest(), 16)
    return "YES" if h % 2 == 0 else "NO"


def _retarget(r: SignalRow, target: str) -> SignalRow:
    """Return r with direction=target. If target differs from r.direction,
    translate direction-dependent fields to the opposite token.

    YES + NO ≈ $1 in any binary market, so YES_ask ≈ 1 − NO_ask (modulo a
    few-bp spread we ignore here). Without this translation, a NO-direction
    row flipped to YES would still hold the NO ask in `signal_entry_offer`,
    making the benchmark P&L systematically wrong on flipped rows.

    Smart-money exit (signal_exits row) was recorded for the ORIGINAL side's
    position; it does not transfer to a hypothetical position on the opposite
    side, so we null those fields out and let the row settle at resolution
    instead.
    """
    if r.direction == target:
        return r
    new_offer = (1.0 - r.signal_entry_offer) if r.signal_entry_offer is not None else None
    new_mid = (1.0 - r.signal_entry_mid) if r.signal_entry_mid is not None else None
    return dataclasses.replace(
        r,
        direction=target,
        signal_entry_offer=new_offer,
        signal_entry_mid=new_mid,
        exit_bid_price=None,
        exit_drop_reason=None,
        exited_at=None,
    )


def _favorite_direction(r: SignalRow) -> str:
    """Whichever side is currently priced ≥ $0.50 (the market-implied favorite).

    YES price is reconstructed from r.direction + r.signal_entry_offer:
      - YES signal: signal_entry_offer is the YES ask
      - NO signal:  signal_entry_offer is the NO ask, so YES price ≈ 1 − offer
    """
    if r.signal_entry_offer is None:
        # No price info — this row will be filtered out in P&L computation
        # anyway. Pick any direction; result doesn't contribute.
        return "YES"
    yes_price = (
        r.signal_entry_offer if r.direction == "YES"
        else 1.0 - r.signal_entry_offer
    )
    return "YES" if yes_price >= 0.5 else "NO"


# ---------------------------------------------------------------------------
# B11: Edge decay (cohort grouping)
# ---------------------------------------------------------------------------


@dataclass
class EdgeDecayCohort:
    week: str                 # ISO date of the cohort's Monday (UTC)
    n_eff: float
    mean_pnl_per_dollar: float | None
    pnl_ci_lo: float | None
    pnl_ci_hi: float | None
    win_rate: float | None
    win_rate_ci_lo: float | None
    win_rate_ci_hi: float | None
    underpowered: bool


@dataclass
class EdgeDecayResult:
    cohorts: list[EdgeDecayCohort]
    decay_warning: bool
    insufficient_history: bool
    weeks_of_data: int
    min_weeks_needed: int


def _iso_week_monday(dt: datetime) -> str:
    """Return the ISO date of the Monday of the week containing dt (UTC)."""
    days_since_monday = dt.weekday()  # Monday = 0
    monday_date = dt.date() if days_since_monday == 0 else (
        dt.date().fromordinal(dt.date().toordinal() - days_since_monday)
    )
    return monday_date.isoformat()


def compute_edge_decay(
    rows: list[SignalRow],
    *,
    min_n_per_cohort: int = 5,
    trade_size_usdc: float = DEFAULT_TRADE_SIZE_USDC,
    exit_strategy: Literal["hold", "smart_money_exit"] = "hold",
    min_weeks_for_warning: int = 4,
) -> EdgeDecayResult:
    """Group signal rows by week-of-fire and run the same engine per cohort.

    `decay_warning` fires when the mean of the LAST 3 cohorts' mean_pnl_per_dollar
    is below the mean of the PRECEDING cohorts. Requires >= `min_weeks_for_warning`
    cohorts (default 4) to be honest; otherwise `insufficient_history=True` and
    decay_warning stays False.
    """
    by_week: dict[str, list[SignalRow]] = {}
    for r in rows:
        wk = _iso_week_monday(r.first_fired_at)
        by_week.setdefault(wk, []).append(r)

    cohorts: list[EdgeDecayCohort] = []
    for wk in sorted(by_week.keys()):
        group_rows = by_week[wk]
        result = summarize_rows(group_rows, trade_size_usdc, exit_strategy=exit_strategy)
        if result.n_eff < min_n_per_cohort:
            continue
        cohorts.append(EdgeDecayCohort(
            week=wk,
            n_eff=result.n_eff,
            mean_pnl_per_dollar=result.mean_pnl_per_dollar,
            pnl_ci_lo=result.pnl_ci_lo,
            pnl_ci_hi=result.pnl_ci_hi,
            win_rate=result.win_rate,
            win_rate_ci_lo=result.win_rate_ci_lo,
            win_rate_ci_hi=result.win_rate_ci_hi,
            underpowered=result.underpowered,
        ))

    weeks_of_data = len(cohorts)
    insufficient_history = weeks_of_data < min_weeks_for_warning
    decay_warning = False
    if not insufficient_history and weeks_of_data >= 4:
        recent = cohorts[-3:]
        preceding = cohorts[:-3]
        recent_means = [c.mean_pnl_per_dollar for c in recent if c.mean_pnl_per_dollar is not None]
        prec_means = [c.mean_pnl_per_dollar for c in preceding if c.mean_pnl_per_dollar is not None]
        if recent_means and prec_means:
            recent_avg = sum(recent_means) / len(recent_means)
            prec_avg = sum(prec_means) / len(prec_means)
            decay_warning = recent_avg < prec_avg

    return EdgeDecayResult(
        cohorts=cohorts,
        decay_warning=decay_warning,
        insufficient_history=insufficient_history,
        weeks_of_data=weeks_of_data,
        min_weeks_needed=min_weeks_for_warning,
    )


def compute_benchmark(
    rows: list[SignalRow],
    benchmark: str,
    trade_size_usdc: float = DEFAULT_TRADE_SIZE_USDC,
    exit_strategy: Literal["hold", "smart_money_exit"] = "hold",
) -> BacktestResult:
    """Compute a dumb benchmark strategy over the same signal universe.

    Strategy must beat the benchmark by ≥2× CI overlap to claim meaningful alpha.

    buy_and_hold_yes — always buy YES regardless of signal direction.
      Tests whether smart money's direction-picking adds value vs. just
      buying YES on every market they paid attention to.

    buy_and_hold_no — always buy NO regardless of signal direction.
      Symmetric counterpart to buy_and_hold_yes; together they remove the
      arbitrary YES/NO labelling asymmetry.

    buy_and_hold_favorite — buy whichever side is currently priced ≥ $0.50.
      The most meaningful direction baseline: tests whether smart money's
      direction calls beat "just go with whatever the market crowd thinks."

    coin_flip — deterministic-random direction per condition_id (seeded).
      Expected P&L ≈ −fees−slippage. Strategy must beat this to have any edge.

    follow_top_1 — use the raw consensus signal direction with no further
      filter overrides. When no extra filters are applied this equals the
      strategy; with filters applied it shows the unfiltered signal baseline.
    """
    if benchmark == "buy_and_hold_yes":
        bench_rows: list[SignalRow] = [_retarget(r, "YES") for r in rows]
    elif benchmark == "buy_and_hold_no":
        bench_rows = [_retarget(r, "NO") for r in rows]
    elif benchmark == "buy_and_hold_favorite":
        bench_rows = [_retarget(r, _favorite_direction(r)) for r in rows]
    elif benchmark == "coin_flip":
        bench_rows = [_retarget(r, _coin_direction(r.condition_id)) for r in rows]
    elif benchmark == "follow_top_1":
        bench_rows = rows
    else:
        raise ValueError(f"Unknown benchmark: {benchmark!r}")
    return summarize_rows(bench_rows, trade_size_usdc, exit_strategy=exit_strategy)
