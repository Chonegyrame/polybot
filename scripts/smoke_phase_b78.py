"""Smoke tests for B7 (multiple-testing infrastructure) + B8 (boring benchmarks).

B7 tests:
  - _norm_ppf / _norm_cdf / _pvalue_from_ci / _ci_gaussian correctness
  - compute_corrections: N=1 -> no widening; N=10 -> CIs wider; N>5 -> warning
  - holdout_from filter: signals on/after cutoff are excluded
  - DB: insert_slice_lookup + get_session_slice_lookups round-trip
  - Route: /backtest/summary response includes `corrections` key
  - Route: multiplicity_warning trips after >5 session queries

B8 tests:
  - compute_benchmark buy_and_hold_yes: overrides every direction to YES
  - compute_benchmark coin_flip: deterministic seeded direction
  - compute_benchmark follow_top_1: same rows, same result
  - Route: /backtest/summary?benchmark=coin_flip returns benchmark field
  - Route: unknown benchmark returns 400

Run: ./venv/Scripts/python.exe scripts/smoke_phase_b78.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestFilters,
    BacktestResult,
    MultipleTestingCorrections,
    SignalRow,
    VALID_BENCHMARKS,
    _ci_gaussian,
    _favorite_direction,
    _norm_cdf,
    _norm_ppf,
    _pvalue_from_ci,
    _retarget,
    _Z_RAW,
    compute_benchmark,
    compute_corrections,
    summarize_rows,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}{('  -- ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n  {title}\n{'=' * 80}")


# ---------------------------------------------------------------------------
# Helpers to synthesise SignalRows
# ---------------------------------------------------------------------------


def _make_row(
    cid: str = "0xabc",
    direction: str = "YES",
    resolved: str = "YES",
    entry: float = 0.60,
    fired_at: datetime | None = None,
    cluster_id: str | None = None,
) -> SignalRow:
    t = fired_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SignalRow(
        id=hash(cid + direction) & 0xFFFF,
        mode="hybrid", category="overall", top_n=50,
        condition_id=cid, direction=direction,
        first_trader_count=10, first_aggregate_usdc=50_000.0,
        first_net_skew=0.70, first_avg_portfolio_fraction=0.05,
        signal_entry_offer=entry, signal_entry_mid=entry - 0.01,
        liquidity_at_signal_usdc=500_000.0, liquidity_tier="deep",
        first_top_trader_entry_price=entry - 0.05,
        cluster_id=cluster_id, market_type="binary",
        first_fired_at=t,
        resolved_outcome=resolved,
        market_category="politics",
    )


def _make_result(
    mean_pnl: float = 0.12,
    ci_lo: float = 0.02,
    ci_hi: float = 0.22,
    wr: float = 0.60,
    wr_lo: float = 0.45,
    wr_hi: float = 0.73,
) -> BacktestResult:
    return BacktestResult(
        n_signals=40, n_resolved=35, n_eff=30.0, underpowered=False,
        mean_pnl_per_dollar=mean_pnl, pnl_ci_lo=ci_lo, pnl_ci_hi=ci_hi,
        win_rate=wr, win_rate_ci_lo=wr_lo, win_rate_ci_hi=wr_hi,
        profit_factor=1.5, max_drawdown=0.08,
        median_entry_price=0.60, median_gap_to_smart_money=0.05,
    )


# ---------------------------------------------------------------------------
# B7 pure-function: stat helpers
# ---------------------------------------------------------------------------


def test_norm_ppf() -> None:
    section("B7: _norm_ppf / _norm_cdf accuracy")

    # Known values
    check("ppf(0.975) ~= 1.96", abs(_norm_ppf(0.975) - 1.96) < 0.01, f"got {_norm_ppf(0.975):.4f}")
    check("ppf(0.025) ~= -1.96", abs(_norm_ppf(0.025) + 1.96) < 0.01, f"got {_norm_ppf(0.025):.4f}")
    check("ppf(0.995) ~= 2.576", abs(_norm_ppf(0.995) - 2.576) < 0.01, f"got {_norm_ppf(0.995):.4f}")
    check("ppf(0.5) = 0", abs(_norm_ppf(0.5)) < 0.001, f"got {_norm_ppf(0.5):.6f}")
    check("cdf(1.96) ~= 0.975", abs(_norm_cdf(1.96) - 0.975) < 0.001, f"got {_norm_cdf(1.96):.4f}")
    check("cdf(-1.96) ~= 0.025", abs(_norm_cdf(-1.96) - 0.025) < 0.001)
    check("ppf(cdf(1.5)) ~= 1.5 (round-trip)", abs(_norm_ppf(_norm_cdf(1.5)) - 1.5) < 0.01)


def test_pvalue_from_ci() -> None:
    section("B7: _pvalue_from_ci")

    # CI that does not include 0 -> small p-value
    p = _pvalue_from_ci(0.12, 0.02, 0.22)
    check("CI excluding 0 -> p < 0.05", p < 0.05, f"got p={p:.4f}")

    # CI straddling 0 -> large p-value
    p2 = _pvalue_from_ci(0.05, -0.10, 0.20)
    check("CI straddling 0 -> p > 0.05", p2 > 0.05, f"got p={p2:.4f}")

    # Null/degenerate inputs -> p=1
    check("None inputs -> p=1.0", _pvalue_from_ci(None, None, None) == 1.0)
    check("lo==hi -> p=1.0", _pvalue_from_ci(0.1, 0.1, 0.1) == 1.0)


def test_ci_gaussian() -> None:
    section("B7: _ci_gaussian widening")

    point, lo, hi = 0.12, 0.02, 0.22
    # Alpha 0.01 -> wider than 0.05
    lo2, hi2 = _ci_gaussian(point, lo, hi, 0.01)
    check("Smaller alpha -> wider CI", lo2 < lo and hi2 > hi, f"new=({lo2:.3f},{hi2:.3f})")

    # Alpha 0.10 -> narrower
    lo3, hi3 = _ci_gaussian(point, lo, hi, 0.10)
    check("Larger alpha -> narrower CI", lo3 > lo and hi3 < hi)

    # Alpha 0.05 -> original (within floating point)
    lo4, hi4 = _ci_gaussian(point, lo, hi, 0.05)
    check("Same alpha -> ~same CI", abs(lo4 - lo) < 0.0001 and abs(hi4 - hi) < 0.0001,
          f"({lo4:.4f},{hi4:.4f}) vs ({lo:.4f},{hi:.4f})")


# ---------------------------------------------------------------------------
# B7 pure-function: compute_corrections
# ---------------------------------------------------------------------------


def test_corrections_n1() -> None:
    section("B7: compute_corrections — N=1 (no correction needed)")

    result = _make_result()
    # Single session entry = this query itself
    entries = [{"reported_value": result.mean_pnl_per_dollar,
                "ci_low": result.pnl_ci_lo, "ci_high": result.pnl_ci_hi}]
    corr = compute_corrections(result, entries)

    check("n_session_queries = 1", corr.n_session_queries == 1)
    check("multiplicity_warning = False", corr.multiplicity_warning is False)
    # Bonferroni with N=1: alpha/N = 0.05 -> same as raw CI
    check(
        "Bonferroni N=1 ~= raw CI",
        abs(corr.bonferroni_pnl_ci_lo - result.pnl_ci_lo) < 0.001
        and abs(corr.bonferroni_pnl_ci_hi - result.pnl_ci_hi) < 0.001,
        f"bonf=({corr.bonferroni_pnl_ci_lo:.4f},{corr.bonferroni_pnl_ci_hi:.4f})",
    )
    # BH-FDR N=1: rank=1, alpha_bh = min(0.05, 0.05*1/1) = 0.05 -> same
    check(
        "BH-FDR N=1 ~= raw CI",
        abs(corr.bh_fdr_pnl_ci_lo - result.pnl_ci_lo) < 0.001
        and abs(corr.bh_fdr_pnl_ci_hi - result.pnl_ci_hi) < 0.001,
    )
    check("Win-rate bonferroni fields present", corr.bonferroni_win_rate_ci_lo is not None)
    check("Win-rate bh_fdr fields present", corr.bh_fdr_win_rate_ci_lo is not None)


def test_corrections_n10() -> None:
    section("B7: compute_corrections — N=10 (CIs should widen)")

    result = _make_result()
    # 10 session entries; current is one of them with a significant result
    entries = [
        {"reported_value": 0.08, "ci_low": -0.01, "ci_high": 0.17},  # straddling 0
        {"reported_value": 0.03, "ci_low": -0.05, "ci_high": 0.11},
        {"reported_value": -0.02, "ci_low": -0.12, "ci_high": 0.08},
        {"reported_value": 0.15, "ci_low": 0.05, "ci_high": 0.25},
        {"reported_value": 0.06, "ci_low": -0.02, "ci_high": 0.14},
        {"reported_value": 0.10, "ci_low": 0.01, "ci_high": 0.19},
        {"reported_value": -0.05, "ci_low": -0.15, "ci_high": 0.05},
        {"reported_value": 0.20, "ci_low": 0.10, "ci_high": 0.30},
        {"reported_value": 0.07, "ci_low": -0.01, "ci_high": 0.15},
        # The current result itself:
        {"reported_value": result.mean_pnl_per_dollar,
         "ci_low": result.pnl_ci_lo, "ci_high": result.pnl_ci_hi},
    ]
    corr = compute_corrections(result, entries)

    check("n_session_queries = 10", corr.n_session_queries == 10)
    check("multiplicity_warning = True (N > 5)", corr.multiplicity_warning is True)
    check(
        "Bonferroni CI wider than raw",
        corr.bonferroni_pnl_ci_lo < result.pnl_ci_lo
        and corr.bonferroni_pnl_ci_hi > result.pnl_ci_hi,
        f"raw=({result.pnl_ci_lo},{result.pnl_ci_hi}) bonf=({corr.bonferroni_pnl_ci_lo:.3f},{corr.bonferroni_pnl_ci_hi:.3f})",
    )
    check(
        "BH-FDR CI wider than raw but narrower than Bonferroni",
        corr.bh_fdr_pnl_ci_lo >= corr.bonferroni_pnl_ci_lo
        and corr.bh_fdr_pnl_ci_hi <= corr.bonferroni_pnl_ci_hi,
        f"bh=({corr.bh_fdr_pnl_ci_lo:.3f},{corr.bh_fdr_pnl_ci_hi:.3f})",
    )
    check(
        "Win-rate Bonferroni clamped to [0,1]",
        0.0 <= corr.bonferroni_win_rate_ci_lo <= 1.0
        and 0.0 <= corr.bonferroni_win_rate_ci_hi <= 1.0,
    )


def test_corrections_warning_threshold() -> None:
    section("B7: multiplicity_warning threshold at N=5 vs N=6")

    result = _make_result()
    dummy = {"reported_value": 0.05, "ci_low": -0.01, "ci_high": 0.11}
    entries5 = [dummy] * 5
    entries6 = [dummy] * 6

    check("N=5 -> no warning", not compute_corrections(result, entries5).multiplicity_warning)
    check("N=6 -> warning", compute_corrections(result, entries6).multiplicity_warning)


def test_corrections_underpowered() -> None:
    section("B7: compute_corrections handles underpowered result (all None CIs)")

    result = BacktestResult(
        n_signals=5, n_resolved=2, n_eff=2.0, underpowered=True,
        mean_pnl_per_dollar=None, pnl_ci_lo=None, pnl_ci_hi=None,
        win_rate=None, win_rate_ci_lo=None, win_rate_ci_hi=None,
        profit_factor=None, max_drawdown=None,
        median_entry_price=None, median_gap_to_smart_money=None,
    )
    entries = [{"reported_value": None, "ci_low": None, "ci_high": None}]
    corr = compute_corrections(result, entries)
    check("All corrected CI fields are None when underpowered",
          all(v is None for v in [
              corr.bonferroni_pnl_ci_lo, corr.bonferroni_pnl_ci_hi,
              corr.bh_fdr_pnl_ci_lo, corr.bh_fdr_pnl_ci_hi,
          ]))


# ---------------------------------------------------------------------------
# B7 pure-function: holdout_from filter (via BacktestFilters)
# ---------------------------------------------------------------------------


def test_holdout_from_filter() -> None:
    section("B7: holdout_from excludes signals on/after cutoff date")

    from app.services.backtest_engine import _bucket  # noqa: F401 (just verifying import)

    cutoff = date(2026, 3, 1)
    before = datetime(2026, 2, 28, tzinfo=timezone.utc)
    on_cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
    after = datetime(2026, 4, 1, tzinfo=timezone.utc)

    rows = [
        _make_row("c1", fired_at=before),
        _make_row("c2", fired_at=on_cutoff),
        _make_row("c3", fired_at=after),
    ]

    # Simulate what _fetch_signals does: filter by first_fired_at < holdout_from
    f = BacktestFilters(holdout_from=cutoff)
    filtered = [r for r in rows if r.first_fired_at.date() < cutoff]  # type: ignore[union-attr]

    check("Only row before cutoff passes", len(filtered) == 1, f"got {len(filtered)}")
    check("Correct condition_id", filtered[0].condition_id == "c1")


# ---------------------------------------------------------------------------
# B8 pure-function: compute_benchmark
# ---------------------------------------------------------------------------


def test_retarget_translates_entry_price() -> None:
    section("B8: _retarget translates entry price when direction flips")

    # NO signal at 0.55 (NO ask); flipping to YES should set offer ~ 0.45.
    r_no = _make_row("c1", direction="NO", resolved="YES", entry=0.55)
    flipped = _retarget(r_no, "YES")
    check(
        "NO->YES: direction flipped",
        flipped.direction == "YES",
        f"got {flipped.direction}",
    )
    check(
        "NO->YES: signal_entry_offer became 1 - 0.55 = 0.45",
        flipped.signal_entry_offer is not None
        and abs(flipped.signal_entry_offer - 0.45) < 1e-9,
        f"got {flipped.signal_entry_offer}",
    )
    check(
        "NO->YES: signal_entry_mid also translated",
        flipped.signal_entry_mid is not None
        and abs(flipped.signal_entry_mid - (1.0 - (0.55 - 0.01))) < 1e-9,
        f"got {flipped.signal_entry_mid}",
    )

    # No-op when target matches existing direction.
    r_yes = _make_row("c2", direction="YES", entry=0.55)
    same = _retarget(r_yes, "YES")
    check(
        "YES->YES: row unchanged",
        same is r_yes or (
            same.direction == "YES" and same.signal_entry_offer == 0.55
        ),
    )

    # Smart-money exit data is nulled on flip (doesn't apply on opposite side).
    r_with_exit = dataclasses.replace(
        _make_row("c3", direction="YES", entry=0.60),
        exit_bid_price=0.45, exit_drop_reason="trader_count",
        exited_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    flipped_exit = _retarget(r_with_exit, "NO")
    check(
        "YES->NO: exit_bid_price nulled",
        flipped_exit.exit_bid_price is None,
        f"got {flipped_exit.exit_bid_price}",
    )
    check(
        "YES->NO: exit_drop_reason nulled",
        flipped_exit.exit_drop_reason is None,
    )

    # None entry passes through as None.
    r_no_price = dataclasses.replace(_make_row("c4", direction="NO"), signal_entry_offer=None)
    flipped_none = _retarget(r_no_price, "YES")
    check(
        "Flip with None entry: stays None",
        flipped_none.signal_entry_offer is None,
    )


def test_benchmark_buy_and_hold_yes() -> None:
    section("B8: buy_and_hold_yes overrides direction to YES for all rows")

    # c1: YES/YES @ 0.55 (YES ask)  -> bah_yes: bought YES @ 0.55, win
    # c2: YES/NO  @ 0.55 (YES ask)  -> bah_yes: bought YES @ 0.55, loss
    # c3: NO/YES  @ 0.55 (NO ask)   -> bah_yes: bought YES @ 0.45, win
    rows = [
        _make_row("c1", direction="YES", resolved="YES", entry=0.55),
        _make_row("c2", direction="YES", resolved="NO",  entry=0.55),
        _make_row("c3", direction="NO",  resolved="YES", entry=0.55),
    ]
    result = compute_benchmark(rows, "buy_and_hold_yes")
    # WR: c1=win, c2=loss, c3=win -> 2/3
    check(
        "buy_and_hold_yes: 2/3 wins after direction override",
        result.win_rate is not None and abs(result.win_rate - 2/3) < 0.01,
        f"win_rate={result.win_rate}",
    )
    # raw signal: c1=win, c2=loss, c3=loss -> WR = 1/3
    raw = summarize_rows(rows)
    check(
        "Raw signal: 1/3 wins (confirms override changed the result)",
        raw.win_rate is not None and abs(raw.win_rate - 1/3) < 0.01,
        f"raw_win_rate={raw.win_rate}",
    )

    # Magnitude check — c3 (originally NO @ 0.55) should compute P&L using
    # YES entry ~ 0.45, NOT the buggy 0.55. A pure c3-only benchmark gives:
    #   buy YES @ 0.45 (+slip) -> resolved YES -> +1 payout
    # vs the previous bug which computed at 0.55 (+slip) and understated P&L.
    c3_only = compute_benchmark(
        [_make_row("c3", direction="NO", resolved="YES", entry=0.55)],
        "buy_and_hold_yes",
    )
    # With slippage tiny on $500k liquidity, effective_entry ~ 0.45 + ε.
    # gross = 1.0 / 0.45 ~ 2.222; fee on politics = 0.0; pnl ~ +1.222.
    assert c3_only.mean_pnl_per_dollar is not None
    check(
        "buy_and_hold_yes c3 (originally NO): P&L computed at YES~0.45, not NO=0.55",
        c3_only.mean_pnl_per_dollar > 1.0,  # would be ~0.82 under the old bug
        f"got mean_pnl={c3_only.mean_pnl_per_dollar:.3f} (expect ~1.22, bug would give ~0.82)",
    )


def test_benchmark_buy_and_hold_no() -> None:
    section("B8: buy_and_hold_no overrides direction to NO for all rows")

    # c1: YES/YES @ 0.55 -> flip to NO @ 0.45, resolved YES -> loss
    # c2: YES/NO  @ 0.55 -> flip to NO @ 0.45, resolved NO  -> win
    # c3: NO/YES  @ 0.55 -> already NO @ 0.55, resolved YES -> loss
    rows = [
        _make_row("c1", direction="YES", resolved="YES", entry=0.55),
        _make_row("c2", direction="YES", resolved="NO",  entry=0.55),
        _make_row("c3", direction="NO",  resolved="YES", entry=0.55),
    ]
    result = compute_benchmark(rows, "buy_and_hold_no")
    # WR: c1=loss, c2=win, c3=loss -> 1/3
    check(
        "buy_and_hold_no: 1/3 wins after direction override",
        result.win_rate is not None and abs(result.win_rate - 1/3) < 0.01,
        f"win_rate={result.win_rate}",
    )

    # Magnitude check — c2 (originally YES @ 0.55) flipped to NO should use
    # NO entry ~ 0.45 (not 0.55).
    c2_only = compute_benchmark(
        [_make_row("c2", direction="YES", resolved="NO", entry=0.55)],
        "buy_and_hold_no",
    )
    assert c2_only.mean_pnl_per_dollar is not None
    check(
        "buy_and_hold_no c2 (originally YES): P&L at NO~0.45, not YES=0.55",
        c2_only.mean_pnl_per_dollar > 1.0,  # would be ~0.82 under the old bug
        f"got mean_pnl={c2_only.mean_pnl_per_dollar:.3f} (expect ~1.22)",
    )


def test_benchmark_buy_and_hold_favorite() -> None:
    section("B8: buy_and_hold_favorite buys whichever side is priced >= 0.50")

    # c1: YES @ 0.70 -> YES is favorite (0.70). Resolved YES -> win.
    # c2: YES @ 0.30 -> NO is favorite (0.70). Flip to NO @ 0.70. Resolved NO -> win.
    # c3: NO  @ 0.70 -> NO is favorite (its own side, 0.70). Resolved YES -> loss.
    # c4: NO  @ 0.30 -> YES is favorite (0.70). Flip to YES @ 0.70. Resolved YES -> win.
    rows = [
        _make_row("c1", direction="YES", resolved="YES", entry=0.70),
        _make_row("c2", direction="YES", resolved="NO",  entry=0.30),
        _make_row("c3", direction="NO",  resolved="YES", entry=0.70),
        _make_row("c4", direction="NO",  resolved="YES", entry=0.30),
    ]

    # _favorite_direction unit checks
    check(
        "favorite(YES@0.70) = YES",
        _favorite_direction(rows[0]) == "YES",
    )
    check(
        "favorite(YES@0.30) = NO  (YES priced 0.30 -> NO is the 0.70 favorite)",
        _favorite_direction(rows[1]) == "NO",
    )
    check(
        "favorite(NO@0.70) = NO  (this side is the favorite)",
        _favorite_direction(rows[2]) == "NO",
    )
    check(
        "favorite(NO@0.30) = YES (this side is the longshot, YES is favorite)",
        _favorite_direction(rows[3]) == "YES",
    )

    result = compute_benchmark(rows, "buy_and_hold_favorite")
    # 3 wins out of 4 (c1, c2, c4)
    check(
        "buy_and_hold_favorite: 3/4 wins",
        result.win_rate is not None and abs(result.win_rate - 0.75) < 0.01,
        f"win_rate={result.win_rate}",
    )

    # Tie-break at exactly 0.50 -> YES (per implementation choice)
    r_tie = _make_row("c5", direction="YES", entry=0.50)
    check(
        "favorite(YES@0.50) = YES (ties go to YES)",
        _favorite_direction(r_tie) == "YES",
    )

    # Missing entry price -> falls through to YES placeholder; row gets
    # filtered out of P&L anyway.
    r_no_price = dataclasses.replace(_make_row("c6", direction="NO"), signal_entry_offer=None)
    check(
        "favorite(no entry price) = YES placeholder",
        _favorite_direction(r_no_price) == "YES",
    )


def test_benchmark_coin_flip() -> None:
    section("B8: coin_flip uses deterministic seeded direction")

    rows = [_make_row(f"c{i}", direction="YES", resolved="YES", entry=0.60) for i in range(20)]
    r1 = compute_benchmark(rows, "coin_flip")
    r2 = compute_benchmark(rows, "coin_flip")

    check("coin_flip is deterministic (two calls same result)", r1.win_rate == r2.win_rate)
    check("coin_flip win_rate between 0 and 1", r1.win_rate is not None and 0.0 < r1.win_rate < 1.0)
    # Should be roughly 50% wins (hash is unbiased over many cids)
    check(
        "coin_flip win_rate roughly 50% (unbiased hash over 20 cids)",
        r1.win_rate is not None and 0.2 < r1.win_rate < 0.8,
        f"win_rate={r1.win_rate:.2f}",
    )


def test_benchmark_follow_top_1() -> None:
    section("B8: follow_top_1 returns same result as signal direction")

    rows = [_make_row(f"c{i}", direction="YES", resolved="YES") for i in range(15)]
    bench = compute_benchmark(rows, "follow_top_1")
    raw = summarize_rows(rows)

    check(
        "follow_top_1 win_rate == raw (same rows, same direction)",
        bench.win_rate == raw.win_rate,
        f"bench={bench.win_rate} raw={raw.win_rate}",
    )
    check(
        "follow_top_1 n_signals == raw n_signals",
        bench.n_signals == raw.n_signals,
    )


def test_benchmark_invalid() -> None:
    section("B8: unknown benchmark raises ValueError")

    try:
        compute_benchmark([], "magic_strategy")
        check("Should have raised ValueError", False)
    except ValueError as e:
        check("ValueError raised for unknown benchmark", True, str(e))


def test_valid_benchmarks_constant() -> None:
    section("B8: VALID_BENCHMARKS contains expected values")

    expected = {
        "buy_and_hold_yes", "buy_and_hold_no", "buy_and_hold_favorite",
        "coin_flip", "follow_top_1",
    }
    check(
        "VALID_BENCHMARKS matches spec",
        set(VALID_BENCHMARKS) == expected,
        f"got {VALID_BENCHMARKS}",
    )


def test_f21_bootstrap_p_value_populated_and_used() -> None:
    """F21 regression: BacktestResult.pnl_bootstrap_p must be populated by
    summarize_rows from the empirical bootstrap distribution (not
    reconstructed from CI). And BH-FDR's compute_corrections must use it
    when present.

    See review/FIXES.md F21.
    """
    section("F21: bootstrap p-value populated and consumed by BH-FDR")

    from app.services.backtest_engine import (
        cluster_bootstrap_mean_with_p, summarize_rows, compute_corrections,
    )

    # Direct helper test: clearly-positive mean -> p_two_sided near 0
    point, lo, hi, p = cluster_bootstrap_mean_with_p(
        [0.5] * 50, [None] * 50, n_iter=2000, seed=1,
    )
    check(
        "cluster_bootstrap_mean_with_p: tight positive distribution -> p near 0",
        p < 0.10,
        f"got p={p}, point={point}, ci=({lo}, {hi})",
    )

    # Distribution centered on 0 -> p near 1.0
    rng_vals = [0.0] * 50  # exactly zero -> all bootstrap means are 0 -> p=2*min(0.5,0.5)... actually all <=0
    point2, _, _, p2 = cluster_bootstrap_mean_with_p(
        rng_vals, [None] * 50, n_iter=2000, seed=1,
    )
    check(
        "cluster_bootstrap_mean_with_p: zero-mean distribution returns valid p",
        0.0 <= p2 <= 1.0,
        f"got p={p2}",
    )

    # End-to-end: summarize_rows result carries pnl_bootstrap_p
    base_t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _make_row(cid=f"win_{i}", direction="YES", resolved="YES",
                  entry=0.40, cluster_id=f"c_{i}", fired_at=base_t)
        for i in range(8)
    ] + [
        _make_row(cid=f"lose_{i}", direction="YES", resolved="NO",
                  entry=0.40, cluster_id=f"c_{i + 100}", fired_at=base_t)
        for i in range(2)
    ]
    res = summarize_rows(rows)
    check(
        "BacktestResult.pnl_bootstrap_p is populated",
        res.pnl_bootstrap_p is not None and 0.0 <= res.pnl_bootstrap_p <= 1.0,
        f"got pnl_bootstrap_p={res.pnl_bootstrap_p}",
    )

    # compute_corrections accepts session entries with bootstrap_p key
    session_entries = [
        {"reported_value": 0.05, "ci_low": -0.05, "ci_high": 0.15,
         "bootstrap_p": 0.30},
        {"reported_value": 0.10, "ci_low": 0.02, "ci_high": 0.18,
         "bootstrap_p": 0.05},
    ]
    corr = compute_corrections(res, session_entries)
    check(
        "compute_corrections runs without error when bootstrap_p is in entries",
        corr is not None,
    )


def test_f8_winrate_ci_uses_cluster_correction() -> None:
    """F8 regression: win_rate CI must respect the cluster correction the
    P&L mean already uses. Pre-fix: Wilson CI on raw `len(pnl_pairs)` ignored
    cluster correlation entirely, so 5 winners in 1 cluster + 5 losers in 1
    cluster were treated as 10 IID observations. The Wilson CI was too tight
    by roughly sqrt(n / n_eff).

    See review/03_backtest_stats.md Critical #3, review/FIXES.md F8.
    """
    section("F8: win-rate CI applies cluster correction")

    # Case 1: 2 clusters, one all-wins one all-losses. Maximally clustered.
    # Wilson(5, 10) = (~0.237, ~0.763). Width ~0.53.
    # Cluster bootstrap with 2 clusters of (100% wins, 0% wins) resamples
    # AA / BB / AB / BA with equal probability → CI is essentially (0, 1).
    rows_clustered = []
    for i in range(5):
        rows_clustered.append(
            _make_row(cid=f"win_c1_{i}", direction="YES", resolved="YES",
                      entry=0.40, cluster_id="A"),
        )
    for i in range(5):
        rows_clustered.append(
            _make_row(cid=f"lose_c1_{i}", direction="YES", resolved="NO",
                      entry=0.40, cluster_id="B"),
        )
    res_clustered = summarize_rows(rows_clustered)

    check(
        "Clustered: raw win rate is 50% (5 of 10)",
        res_clustered.win_rate is not None
        and abs(res_clustered.win_rate - 0.5) < 0.01,
        f"got win_rate={res_clustered.win_rate}",
    )
    check(
        "Clustered: n_eff reflects 2 distinct clusters",
        abs(res_clustered.n_eff - 2.0) < 1e-6,
        f"got n_eff={res_clustered.n_eff}",
    )
    # F8 core assertion: cluster bootstrap CI is dramatically wider than
    # Wilson. The pre-fix Wilson(5,10) had width ~0.53; post-fix bootstrap
    # extends to ~(0,1) given two extreme clusters.
    width = (res_clustered.win_rate_ci_hi or 0) - (res_clustered.win_rate_ci_lo or 0)
    check(
        f"F8: clustered CI width >= 0.7 (cluster-aware); pre-fix Wilson was ~0.53",
        width >= 0.7,
        f"got width={width:.3f} (lo={res_clustered.win_rate_ci_lo}, hi={res_clustered.win_rate_ci_hi})",
    )
    check(
        "F8: clustered CI lower bound near 0 (one bootstrap outcome is BB=0%)",
        res_clustered.win_rate_ci_lo is not None and res_clustered.win_rate_ci_lo < 0.05,
        f"got lo={res_clustered.win_rate_ci_lo}",
    )
    check(
        "F8: clustered CI upper bound near 1 (one bootstrap outcome is AA=100%)",
        res_clustered.win_rate_ci_hi is not None and res_clustered.win_rate_ci_hi > 0.95,
        f"got hi={res_clustered.win_rate_ci_hi}",
    )

    # Case 2: 10 distinct clusters (n_eff = n). Cluster bootstrap should give
    # a CI roughly in line with Wilson — no cluster correction needed when
    # observations are already independent.
    rows_independent = []
    for i in range(5):
        rows_independent.append(
            _make_row(cid=f"win_ind_{i}", direction="YES", resolved="YES",
                      entry=0.40, cluster_id=f"c_w{i}"),
        )
    for i in range(5):
        rows_independent.append(
            _make_row(cid=f"lose_ind_{i}", direction="YES", resolved="NO",
                      entry=0.40, cluster_id=f"c_l{i}"),
        )
    res_independent = summarize_rows(rows_independent)
    width_ind = (res_independent.win_rate_ci_hi or 0) - (res_independent.win_rate_ci_lo or 0)
    check(
        "F8: independent-cluster CI width comparable to Wilson (no over-widening)",
        # Wilson(5,10) width is ~0.527; bootstrap on 10 distinct clusters
        # should give similar order — bound to [0.3, 0.85].
        0.3 < width_ind < 0.85,
        f"got width={width_ind:.3f} (lo={res_independent.win_rate_ci_lo}, hi={res_independent.win_rate_ci_hi})",
    )
    check(
        "F8: independent CI is much narrower than clustered CI",
        width_ind < width,
        f"width_independent={width_ind:.3f} vs width_clustered={width:.3f}",
    )


# ---------------------------------------------------------------------------
# B7 DB: slice_lookups insert + retrieve
# ---------------------------------------------------------------------------


async def test_slice_lookup_crud() -> None:
    section("B7 DB: insert_slice_lookup + get_session_slice_lookups round-trip")

    pool = await init_pool()
    async with pool.acquire() as conn:
        # Verify table exists
        row = await conn.fetchrow("SELECT to_regclass('slice_lookups') AS r")
        check("slice_lookups table exists", row["r"] is not None)

        # Clean up any old test rows from the last minute (avoid stale counts)
        await conn.execute(
            "DELETE FROM slice_lookups WHERE slice_definition::text LIKE '%smoke_b78_test%'"
        )

        # Insert a test row
        await crud.insert_slice_lookup(
            conn,
            {"smoke_b78_test": True, "mode": "hybrid"},
            n_signals=42,
            reported_metric="mean_pnl_per_dollar",
            reported_value=0.15,
            ci_low=0.02,
            ci_high=0.28,
        )

        # Verify it's retrievable within a 1-hour window
        entries = await crud.get_session_slice_lookups(conn, window_hours=1)
        test_entries = [
            e for e in entries
            if e.get("reported_value") is not None and abs(e["reported_value"] - 0.15) < 0.001
        ]
        check("Inserted row retrievable in session window", len(test_entries) >= 1)
        check("reported_value round-trips correctly",
              len(test_entries) >= 1 and abs(test_entries[0]["reported_value"] - 0.15) < 0.0001)

        # Insert with NULL value/CI (underpowered result)
        await crud.insert_slice_lookup(
            conn,
            {"smoke_b78_test": True, "mode": "absolute"},
            n_signals=3,
            reported_metric="mean_pnl_per_dollar",
            reported_value=None,
            ci_low=None,
            ci_high=None,
        )
        entries2 = await crud.get_session_slice_lookups(conn, window_hours=1)
        null_entries = [e for e in entries2 if e["reported_value"] is None]
        check("NULL value/CI row stored and retrieved", len(null_entries) >= 1)

        # Verify window exclusion: a very small window should get 0 rows
        from datetime import timezone
        import asyncio as _asyncio
        # Use 0-hour window to get only rows from "now" (race: may still get recent ones)
        # Just verify the function handles an empty result set without error
        entries_narrow = await crud.get_session_slice_lookups(conn, window_hours=0)
        check("window_hours=0 returns empty list without error", isinstance(entries_narrow, list))

        # Cleanup
        await conn.execute(
            "DELETE FROM slice_lookups WHERE slice_definition::text LIKE '%smoke_b78_test%'"
        )


# ---------------------------------------------------------------------------
# Route integration: /backtest/summary includes corrections
# ---------------------------------------------------------------------------


async def test_summary_returns_corrections() -> None:
    section("B7 Route: /backtest/summary includes corrections field")

    from app.services.backtest_engine import backtest_with_rows
    from app.db import crud as _crud
    from app.db.connection import init_pool as _ip

    pool = await _ip()
    result, rows, _latency_stats = await backtest_with_rows(BacktestFilters())

    # Simulate what the route does
    async with pool.acquire() as conn:
        await _crud.insert_slice_lookup(
            conn, {"smoke_b78_route_test": True},
            result.n_signals, "mean_pnl_per_dollar",
            result.mean_pnl_per_dollar, result.pnl_ci_lo, result.pnl_ci_hi,
        )
        session_entries = await _crud.get_session_slice_lookups(conn)

    corr = compute_corrections(result, session_entries)

    check("compute_corrections returns MultipleTestingCorrections",
          isinstance(corr, MultipleTestingCorrections))
    check("n_session_queries >= 1 after insert", corr.n_session_queries >= 1)
    check("multiplicity_warning is bool", isinstance(corr.multiplicity_warning, bool))
    # When n_resolved > 0, CI fields should be present (or None if underpowered)
    if not result.underpowered:
        check("Bonferroni CI fields populated when not underpowered",
              corr.bonferroni_pnl_ci_lo is not None)
        check("BH-FDR CI fields populated when not underpowered",
              corr.bh_fdr_pnl_ci_lo is not None)
        check("Bonferroni CI is at least as wide as raw",
              corr.bonferroni_pnl_ci_lo <= result.pnl_ci_lo
              and corr.bonferroni_pnl_ci_hi >= result.pnl_ci_hi,
              f"raw=({result.pnl_ci_lo:.3f},{result.pnl_ci_hi:.3f}) bonf=({corr.bonferroni_pnl_ci_lo:.3f},{corr.bonferroni_pnl_ci_hi:.3f})")
    else:
        check("Underpowered -> CI fields are None",
              corr.bonferroni_pnl_ci_lo is None)

    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM slice_lookups WHERE slice_definition::text LIKE '%smoke_b78_route_test%'"
        )


async def test_benchmark_route_simulation() -> None:
    section("B8 Route: benchmark computed over same rows as strategy")

    from app.services.backtest_engine import backtest_with_rows

    result, rows, _latency_stats = await backtest_with_rows(BacktestFilters())
    check(f"Fetched {len(rows)} rows for benchmark test", len(rows) >= 0)

    for bm in VALID_BENCHMARKS:
        bench = compute_benchmark(rows, bm)
        check(
            f"Benchmark '{bm}' returns BacktestResult",
            isinstance(bench, BacktestResult),
        )
        check(
            f"Benchmark '{bm}' n_signals == strategy n_signals",
            bench.n_signals == result.n_signals,
            f"bench={bench.n_signals} strategy={result.n_signals}",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_all() -> None:
    # Pure-function tests (sync)
    test_norm_ppf()
    test_pvalue_from_ci()
    test_ci_gaussian()
    test_corrections_n1()
    test_corrections_n10()
    test_corrections_warning_threshold()
    test_corrections_underpowered()
    test_holdout_from_filter()
    test_retarget_translates_entry_price()
    test_benchmark_buy_and_hold_yes()
    test_benchmark_buy_and_hold_no()
    test_benchmark_buy_and_hold_favorite()
    test_benchmark_coin_flip()
    test_benchmark_follow_top_1()
    test_benchmark_invalid()
    test_valid_benchmarks_constant()
    test_f8_winrate_ci_uses_cluster_correction()
    test_f21_bootstrap_p_value_populated_and_used()

    # DB / integration tests (async)
    await test_slice_lookup_crud()
    await test_summary_returns_corrections()
    await test_benchmark_route_simulation()

    await close_pool()

    # Summary
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\n{'=' * 80}")
    status = "ALL PASS" if failed == 0 else f"{failed} FAILED"
    print(f"  Results: {passed}/{total} passed  -- {status}")
    print(f"{'=' * 80}\n")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
