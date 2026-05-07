"""Pass 5 Tier B #8 -- bootstrap_p persistence to slice_lookups.

F21 (Pass 2) added empirical bootstrap p-values to BacktestResult so
BH-FDR ranking would not depend on the Gaussian-from-CI approximation
that's broken for skewed P&L distributions. F21 deferred persisting
the value into slice_lookups -- so every prior session entry returned
NULL in compute_corrections, which then fell back to the broken
approximation for every comparator.

This commit closes the gap:
  - Migration 018 (Tier A) added the slice_lookups.bootstrap_p column.
  - crud.insert_slice_lookup gains a bootstrap_p kwarg (defaults to
    None for back-compat).
  - crud.get_session_slice_lookups returns bootstrap_p in each dict.
  - The two call sites in app/api/routes/backtest.py
    (get_summary, get_slice) pass result.pnl_bootstrap_p /
    bucket.pnl_bootstrap_p through.

Tests cover: code-shape regressions, round-trip on the helper, NULL
back-compat, and compute_corrections actually consuming the persisted
bootstrap_p (not the fallback) when it's present.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_bootstrap_p.py
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import init_pool, close_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.backtest_engine import (  # noqa: E402
    BacktestResult,
    compute_corrections,
    _pvalue_from_ci,
)


PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS]  {label}" + (f"  -- {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL]  {label}" + (f"  -- {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# Code-shape regression checks
# ---------------------------------------------------------------------------


def test_code_shape() -> None:
    section("Code-shape -- bootstrap_p plumbed end-to-end")

    sig = inspect.signature(crud.insert_slice_lookup)
    check(
        "crud.insert_slice_lookup has bootstrap_p parameter",
        "bootstrap_p" in sig.parameters,
    )
    check(
        "crud.insert_slice_lookup bootstrap_p defaults to None (back-compat)",
        sig.parameters["bootstrap_p"].default is None,
    )

    insert_src = inspect.getsource(crud.insert_slice_lookup)
    check(
        "crud.insert_slice_lookup INSERT writes bootstrap_p column",
        "bootstrap_p" in insert_src and "$7" in insert_src,
    )

    get_src = inspect.getsource(crud.get_session_slice_lookups)
    check(
        "crud.get_session_slice_lookups SELECTs bootstrap_p",
        "bootstrap_p" in get_src,
    )
    check(
        "crud.get_session_slice_lookups returns bootstrap_p in dict",
        "\"bootstrap_p\"" in get_src,
    )

    # Route call sites must pass bootstrap_p (otherwise we'd silently
    # persist NULL and BH-FDR would still use the fallback).
    routes_src = (ROOT / "app" / "api" / "routes" / "backtest.py").read_text(
        encoding="utf-8",
    )
    n_calls = routes_src.count("insert_slice_lookup(")
    n_with_kwarg = routes_src.count("bootstrap_p=")
    check(
        "all insert_slice_lookup call sites in routes pass bootstrap_p",
        n_with_kwarg >= n_calls and n_calls >= 2,
        f"calls={n_calls} with_kwarg={n_with_kwarg}",
    )

    # F21 / Pass 5 #8 — compute_corrections still prefers bootstrap_p.
    cc_src = inspect.getsource(compute_corrections)
    check(
        "compute_corrections prefers bootstrap_p over _pvalue_from_ci",
        "result.pnl_bootstrap_p is not None" in cc_src
        and "e.get(\"bootstrap_p\")" in cc_src,
    )


# ---------------------------------------------------------------------------
# Round-trip: insert + get_session_slice_lookups
# ---------------------------------------------------------------------------


async def test_round_trip() -> None:
    section("#8 round-trip: insert with bootstrap_p, read back via session helper")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            tag = "_pass5_8_test_roundtrip"
            await conn.execute(
                "DELETE FROM slice_lookups WHERE slice_definition->>'_tag' = $1",
                tag,
            )

            slice_def_a = {"_tag": tag, "case": "with_p"}
            slice_def_b = {"_tag": tag, "case": "without_p"}

            # Insert with bootstrap_p
            await crud.insert_slice_lookup(
                conn, slice_def_a,
                n_signals=42, reported_metric="mean_pnl_per_dollar",
                reported_value=0.10, ci_low=0.05, ci_high=0.15,
                bootstrap_p=0.04,
            )
            # Insert without (default None) -- back-compat path
            await crud.insert_slice_lookup(
                conn, slice_def_b,
                n_signals=21, reported_metric="mean_pnl_per_dollar",
                reported_value=0.20, ci_low=0.10, ci_high=0.30,
            )

            # Direct DB verification: bootstrap_p persisted correctly
            row_a = await conn.fetchrow(
                """
                SELECT bootstrap_p
                FROM slice_lookups
                WHERE slice_definition->>'_tag' = $1
                  AND slice_definition->>'case' = 'with_p'
                """,
                tag,
            )
            row_b = await conn.fetchrow(
                """
                SELECT bootstrap_p
                FROM slice_lookups
                WHERE slice_definition->>'_tag' = $1
                  AND slice_definition->>'case' = 'without_p'
                """,
                tag,
            )
            check(
                "#8: row_a has bootstrap_p = 0.04 in the DB",
                row_a is not None and row_a["bootstrap_p"] is not None
                and abs(float(row_a["bootstrap_p"]) - 0.04) < 1e-9,
                f"got {row_a['bootstrap_p'] if row_a else None}",
            )
            check(
                "#8: row_b has bootstrap_p IS NULL (back-compat path)",
                row_b is not None and row_b["bootstrap_p"] is None,
            )

            # Session helper round-trip
            entries = await crud.get_session_slice_lookups(conn)
            tagged_entries = []
            # We can't filter by tag inside the helper (it returns abbreviated
            # rows), so re-query to find the entries with our reported_value
            # signatures (0.10 / 0.20 are unique enough for our test scope).
            for e in entries:
                if e.get("reported_value") in (0.10, 0.20):
                    tagged_entries.append(e)

            with_p = next(
                (e for e in tagged_entries if e.get("reported_value") == 0.10), None,
            )
            without_p = next(
                (e for e in tagged_entries if e.get("reported_value") == 0.20), None,
            )

            check(
                "#8: session helper returned the with_p entry",
                with_p is not None,
            )
            check(
                "#8: with_p.bootstrap_p == 0.04 round-trips through helper",
                with_p is not None and with_p.get("bootstrap_p") is not None
                and abs(float(with_p["bootstrap_p"]) - 0.04) < 1e-9,
                f"got {with_p.get('bootstrap_p') if with_p else None}",
            )
            check(
                "#8: session helper returned the without_p entry",
                without_p is not None,
            )
            check(
                "#8: without_p.bootstrap_p is None (legacy NULL path)",
                without_p is not None and without_p.get("bootstrap_p") is None,
            )

            # Cleanup
            await conn.execute(
                "DELETE FROM slice_lookups WHERE slice_definition->>'_tag' = $1",
                tag,
            )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# compute_corrections actually consumes bootstrap_p (not Gaussian fallback)
# ---------------------------------------------------------------------------


def _make_result(pnl_bootstrap_p: float | None) -> BacktestResult:
    """Minimal BacktestResult fixture for compute_corrections."""
    return BacktestResult(
        n_signals=10,
        n_resolved=10,
        n_eff=10.0,
        underpowered=False,
        mean_pnl_per_dollar=0.10, pnl_ci_lo=0.05, pnl_ci_hi=0.15,
        win_rate=0.55, win_rate_ci_lo=0.40, win_rate_ci_hi=0.70,
        profit_factor=1.5, max_drawdown=-0.05,
        median_entry_price=0.40, median_gap_to_smart_money=0.05,
        pnl_bootstrap_p=pnl_bootstrap_p,
    )


def test_compute_corrections_uses_persisted_bootstrap_p() -> None:
    section("#8 compute_corrections: persisted bootstrap_p is used in BH-FDR")

    # MultipleTestingCorrections doesn't expose raw alphas — it surfaces the
    # widened CIs. So we prove the persisted bootstrap_p is being consumed
    # by constructing scenarios where the SAME current entry produces
    # DIFFERENT widened CIs depending on whether the comparators' p-values
    # are read from the persisted bootstrap_p column or computed via the
    # Gaussian-from-CI fallback. If the column path is wired correctly, the
    # CIs differ; if compute_corrections silently ignored bootstrap_p, both
    # scenarios would produce identical CIs.
    #
    # Construction:
    #   Current (rank target): pnl_bootstrap_p = 0.50 (moderate).
    #   Comparators: CIs that STRADDLE zero -- so the Gaussian-from-CI
    #     fallback gives high p (~0.86). With persisted bootstrap_p = 0.001
    #     they have very small p instead.
    #   Scenario A (persisted): comparators' p = 0.001 -> current ranks #4
    #     of 4 -> alpha_BH = 0.05*4/4 = 0.05 -> minimal CI widening.
    #   Scenario B (NULL/fallback): comparators' p ~0.86 -> current ranks
    #     #1 of 4 -> alpha_BH = 0.05/4 = 0.0125 -> significant widening.

    result = _make_result(pnl_bootstrap_p=0.50)

    session_with_p = [
        {"reported_value": 0.10, "ci_low": 0.05,  "ci_high": 0.15, "bootstrap_p": 0.50},  # current
        {"reported_value": 0.01, "ci_low": -0.10, "ci_high": 0.12, "bootstrap_p": 0.001},
        {"reported_value": 0.02, "ci_low": -0.08, "ci_high": 0.12, "bootstrap_p": 0.001},
        {"reported_value": 0.03, "ci_low": -0.05, "ci_high": 0.11, "bootstrap_p": 0.001},
    ]
    corr_a = compute_corrections(result, session_with_p)

    session_null = [{**e, "bootstrap_p": None} for e in session_with_p]
    # Keep the current entry's bootstrap_p None too so compute_corrections
    # falls all the way back to the Gaussian-from-CI path on every entry.
    result_null = _make_result(pnl_bootstrap_p=None)
    corr_b = compute_corrections(result_null, session_null)

    width_a = (
        (corr_a.bh_fdr_pnl_ci_hi or 0.0) - (corr_a.bh_fdr_pnl_ci_lo or 0.0)
    )
    width_b = (
        (corr_b.bh_fdr_pnl_ci_hi or 0.0) - (corr_b.bh_fdr_pnl_ci_lo or 0.0)
    )

    check(
        "#8: BH-FDR widened CI is non-empty in both scenarios",
        width_a > 0 and width_b > 0,
        f"width_a={width_a:.4f} width_b={width_b:.4f}",
    )
    # Scenario B (fallback) widens MORE than scenario A (persisted).
    # The ratio is bounded by sqrt(z_0.0125 / z_0.05) ~= 1.28 at the upper
    # end. We assert >= 1.20x to leave numerical headroom.
    check(
        "#8: persisted bootstrap_p path produces narrower CI than fallback "
        "(proves compute_corrections is reading the column)",
        width_b / width_a > 1.20,
        f"width_a={width_a:.4f} width_b={width_b:.4f} ratio={width_b/width_a:.3f}",
    )

    # Both should also flip the multiplicity_warning at the same N (>5).
    check(
        "#8: n_session_queries reported as 4 in both runs",
        corr_a.n_session_queries == 4 and corr_b.n_session_queries == 4,
    )

    # And the Bonferroni branch widens identically in both -- it doesn't
    # depend on bootstrap_p at all. Sanity-check that the BH-FDR change is
    # NOT just a mechanical artifact of N changes.
    bonf_width_a = (
        (corr_a.bonferroni_pnl_ci_hi or 0.0) - (corr_a.bonferroni_pnl_ci_lo or 0.0)
    )
    bonf_width_b = (
        (corr_b.bonferroni_pnl_ci_hi or 0.0) - (corr_b.bonferroni_pnl_ci_lo or 0.0)
    )
    check(
        "#8: Bonferroni width unchanged between scenarios (no bootstrap_p dependency)",
        abs(bonf_width_a - bonf_width_b) < 1e-9,
        f"bonf_a={bonf_width_a:.4f} bonf_b={bonf_width_b:.4f}",
    )

    # Pure-function regression: _pvalue_from_ci on a CI straddling 0 is
    # large; on a tight CI strictly above 0 it's small. (Sanity that our
    # scenario B mechanics make sense.)
    p_straddle = _pvalue_from_ci(0.01, -0.10, 0.12)
    p_tight = _pvalue_from_ci(0.05, 0.04, 0.06)
    check(
        "#8: _pvalue_from_ci on CI straddling 0 returns large p (>0.5)",
        p_straddle is not None and p_straddle > 0.5,
        f"got {p_straddle}",
    )
    check(
        "#8: _pvalue_from_ci on tight non-zero CI returns small p (<0.01)",
        p_tight is not None and p_tight < 0.01,
        f"got {p_tight}",
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_all() -> None:
    test_code_shape()
    await test_round_trip()
    test_compute_corrections_uses_persisted_bootstrap_p()


asyncio.run(run_all())


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 #8 bootstrap_p persistence tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
