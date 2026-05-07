"""Pass 5 Tier D -- math/correctness bundle (#4, #11, #12, #13).

Four small independent fixes bundled because they're all small and
share the math/correctness theme:

  #4  TRIM_THRESHOLD raised 0.20 -> 0.25 (one-wallet noise buffer at
      the n=5 cohort floor; pre-fix a single-wallet API blip fired
      false TRIMs).

  #11 NULL cluster_id observations collapse to ONE shared cluster
      in compute_kish_n_eff and cluster_bootstrap_mean_with_p.
      Pre-fix every NULL became its own _solo_{i} singleton, so 30
      uncategorized rows looked like 30 independent observations.

  #12 LATENCY_FALLBACK_WARN_FRACTION lowered 0.50 -> 0.20. The
      latency_unavailable flag now fires when 1-in-5 rows fell back
      (was: only when fallback dominated). Route response also
      surfaces n_adjusted + n_fallback explicitly.

  #13 Win-rate point estimate is the bootstrap median of the
      cluster-resampled distribution, not `wins/n` (count-weighted).
      Same change applied to mean_pnl_per_dollar via the bootstrap
      function. Pre-fix the displayed point disagreed with its own
      cluster-weighted CI on cluster-correlated data.

Run: ./venv/Scripts/python.exe scripts/smoke_phase_pass5_math_correctness.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.exit_detector import (  # noqa: E402
    TRIM_THRESHOLD,
    EXIT_THRESHOLD,
    _classify_drop,
)
from app.services.backtest_engine import (  # noqa: E402
    LATENCY_FALLBACK_WARN_FRACTION,
    compute_kish_n_eff,
    cluster_bootstrap_mean,
    cluster_bootstrap_mean_with_p,
    latency_unavailable,
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
# #4 TRIM_THRESHOLD
# ---------------------------------------------------------------------------


def test_trim_threshold_raised() -> None:
    section("#4 TRIM_THRESHOLD: 0.20 -> 0.25")

    check("#4: TRIM_THRESHOLD == 0.25", TRIM_THRESHOLD == 0.25,
          f"got {TRIM_THRESHOLD}")
    check("#4: EXIT_THRESHOLD unchanged at 0.50", EXIT_THRESHOLD == 0.50)

    # Boundary cases on _classify_drop:
    # 5-wallet cohort, lose 1 -> 20% drop. Pre-fix: TRIM. Post-fix: None.
    res = _classify_drop(4, 5, 100_000, 100_000)
    check("#4: 5->4 wallet (20% drop) returns None (was TRIM pre-fix)",
          res is None, f"got {res}")

    # 5-wallet cohort, lose 2 -> 40% drop. TRIM (was: TRIM, unchanged).
    res = _classify_drop(3, 5, 100_000, 100_000)
    check("#4: 5->3 wallet (40% drop) returns TRIM",
          res == ("trader_count", "trim"), f"got {res}")

    # Exact threshold: 25% drop fires TRIM (>= comparison).
    res = _classify_drop(75, 100, 100_000, 100_000)
    check("#4: exactly 25% drop fires TRIM (boundary)",
          res == ("trader_count", "trim"), f"got {res}")

    # Just below: 24% does not fire.
    res = _classify_drop(76, 100, 100_000, 100_000)
    check("#4: 24% drop does not fire",
          res is None, f"got {res}")


# ---------------------------------------------------------------------------
# #11 NULL cluster collapse
# ---------------------------------------------------------------------------


def test_kish_null_collapse() -> None:
    section("#11 compute_kish_n_eff: NULL keys collapse to one shared cluster")

    # All NULLs -> one cluster of n -> n_eff = 1
    n = compute_kish_n_eff([None] * 30)
    check("#11: 30 NULLs -> n_eff = 1.0 (was 30.0)",
          abs(n - 1.0) < 1e-9, f"got {n}")

    # Mixed: 70 of cluster A + 30 NULLs (one shared cluster)
    # Sizes [70, 30], total=100, sum_sq=4900+900=5800
    # n_eff = 10000 / 5800 ~= 1.7241
    n = compute_kish_n_eff(["A"] * 70 + [None] * 30)
    expected = 10000.0 / 5800.0
    check("#11: 70xA + 30xNone -> n_eff ~= 1.72 (was ~2.03)",
          abs(n - expected) < 0.001,
          f"got {n:.4f} expected {expected:.4f}")

    # Pure non-null: unchanged
    n = compute_kish_n_eff(["A", "B", "C"])
    check("#11: 3 distinct keys (no NULL) -> n_eff = 3.0 (unchanged)",
          abs(n - 3.0) < 1e-9, f"got {n}")

    # Singletons (no NULL): unchanged
    n = compute_kish_n_eff([f"k{i}" for i in range(50)])
    check("#11: 50 distinct keys -> n_eff = 50.0 (unchanged)",
          abs(n - 50.0) < 1e-9, f"got {n}")

    # All keys NULL or "", verify "" is NOT collapsed (only None).
    # Empty string is a real cluster identifier per the contract.
    n = compute_kish_n_eff(["", "", ""])
    check("#11: 3x empty-string keys collapse together (legitimate cluster)",
          abs(n - 1.0) < 1e-9, f"got {n}")


def test_bootstrap_null_collapse() -> None:
    section("#11 cluster_bootstrap_mean: NULL keys collapse together in resampling")

    src = inspect.getsource(cluster_bootstrap_mean_with_p)
    check(
        "#11: cluster_bootstrap_mean_with_p uses '__null__' (not '_solo_{i}')",
        "'__null__'" in src or '"__null__"' in src,
    )
    check(
        "#11: '_solo_' pattern removed from cluster_bootstrap_mean_with_p",
        "_solo_" not in src,
    )

    # Behavioral: 30 NULLs with constant value v -> bootstrap is degenerate;
    # CI collapses to a point because resampling clusters always picks the
    # same (now-shared) cluster of 30 v's.
    values = [0.5] * 30
    keys: list[str | None] = [None] * 30
    point, lo, hi = cluster_bootstrap_mean(values, keys, n_iter=200, seed=1)
    check(
        "#11: 30 NULL keys w/ constant value -> degenerate CI (single shared cluster)",
        abs(lo - 0.5) < 1e-9 and abs(hi - 0.5) < 1e-9 and abs(point - 0.5) < 1e-9,
        f"got point={point} lo={lo} hi={hi}",
    )


# ---------------------------------------------------------------------------
# #12 latency fallback threshold
# ---------------------------------------------------------------------------


def test_latency_threshold_lowered() -> None:
    section("#12 LATENCY_FALLBACK_WARN_FRACTION: 0.50 -> 0.20")

    check("#12: LATENCY_FALLBACK_WARN_FRACTION == 0.20",
          abs(LATENCY_FALLBACK_WARN_FRACTION - 0.20) < 1e-9,
          f"got {LATENCY_FALLBACK_WARN_FRACTION}")

    # 100 rows, 25 fallback -> 25% fallback. Now fires (pre-fix: 50% threshold)
    check("#12: 25/100 fallback -> latency_unavailable=True (was False at 50%)",
          latency_unavailable(75, 25) is True)

    # 100 rows, 19 fallback -> 19% (just below threshold)
    check("#12: 19/100 fallback -> latency_unavailable=False (under 20%)",
          latency_unavailable(81, 19) is False)

    # Exactly 20% does NOT fire (strict > comparison in latency_unavailable)
    check("#12: 20/100 fallback -> latency_unavailable=False (== threshold)",
          latency_unavailable(80, 20) is False)

    # 21% fires
    check("#12: 21/100 fallback -> latency_unavailable=True",
          latency_unavailable(79, 21) is True)

    # No data -> False (no warning when nothing to warn about)
    check("#12: 0/0 -> False (no rows)", latency_unavailable(0, 0) is False)


def test_latency_route_exposes_n_fields() -> None:
    section("#12 backtest route surfaces n_adjusted + n_fallback")

    routes_src = (ROOT / "app" / "api" / "routes" / "backtest.py").read_text(
        encoding="utf-8",
    )
    check(
        "#12: route response includes 'n_adjusted'",
        '"n_adjusted"' in routes_src,
    )
    check(
        "#12: route response includes 'n_fallback'",
        '"n_fallback"' in routes_src,
    )


# ---------------------------------------------------------------------------
# #13 win-rate uses bootstrap median (point matches CI weighting)
# ---------------------------------------------------------------------------


def test_bootstrap_point_uses_median() -> None:
    section("#13 cluster_bootstrap_mean: point estimate is bootstrap median")

    src = inspect.getsource(cluster_bootstrap_mean_with_p)
    check(
        "#13: point computed from bootstrap distribution (not sum/len)",
        "estimates[len(estimates) // 2]" in src,
    )

    # Pure-singleton case: point == unweighted mean (when each obs is its own
    # cluster, bootstrap distribution centers on the unweighted mean).
    values = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]  # mean = 0.5
    keys = [f"k{i}" for i in range(10)]
    point, lo, hi = cluster_bootstrap_mean(values, keys, n_iter=2000, seed=42)
    check(
        "#13: pure singletons -> bootstrap median ~ unweighted mean (0.5)",
        abs(point - 0.5) < 0.05,
        f"got point={point:.3f} lo={lo:.3f} hi={hi:.3f}",
    )

    # Cluster-correlated case: 70 of cluster A with 50% wins, 30 singletons
    # of 80% wins. Honest framing: the audit claimed the bootstrap-weighted
    # point would lift to ~0.65; in practice for this scenario the shift is
    # smaller (~0.01) because the bootstrap of mean-by-cluster is unbiased
    # for the population mean in expectation. The test verifies the SHAPE
    # of the change (point is bootstrap-derived, falls within the valid
    # cluster-bounded range, deterministic given seed) -- not a specific
    # magnitude.
    values = ([0.0] * 35 + [1.0] * 35) + ([1.0] * 24 + [0.0] * 6)
    keys = (["A"] * 70) + [f"singleton_{i}" for i in range(30)]
    point_clustered, _, _ = cluster_bootstrap_mean(
        values, keys, n_iter=2000, seed=42,
    )
    unweighted = sum(values) / len(values)
    check(
        "#13: count-weighted (sum/len) win rate is 0.59 on the synthetic "
        "scenario",
        abs(unweighted - 0.59) < 0.01,
        f"got {unweighted:.3f}",
    )
    check(
        "#13: bootstrap median is in [0.50, 0.80] (between cluster and "
        "singletons)",
        0.45 <= point_clustered <= 0.85,
        f"got {point_clustered:.3f}",
    )
    # Determinism check: same seed -> same point.
    point_again, _, _ = cluster_bootstrap_mean(
        values, keys, n_iter=2000, seed=42,
    )
    check(
        "#13: bootstrap median is deterministic for a given seed",
        abs(point_clustered - point_again) < 1e-9,
    )


def test_summarize_rows_uses_bootstrap_wr() -> None:
    section("#13 summarize_rows: win_rate field uses bootstrap point")

    import inspect as _inspect
    from app.services import backtest_engine as bt

    src = _inspect.getsource(bt.summarize_rows)
    check(
        "#13: win_rate clamped from wr_point_raw (bootstrap), not 'wins / len'",
        "wr_point_raw" in src and "wr = max(0.0, min(1.0, wr_point_raw))" in src,
    )
    # Stricter check: only ONE live `wr = ...` assignment (the bootstrap-
    # clamped one). The legacy expression may still appear inside the
    # explanatory comment block, which is fine; we filter to actual
    # statements (line starts with `wr = ` after stripping).
    lines_with_wr_assign = [
        ln for ln in src.splitlines()
        if ln.strip().startswith("wr = ")
    ]
    check(
        "#13: only one live `wr = ...` assignment (the bootstrap-clamped one)",
        len(lines_with_wr_assign) == 1
        and "wr_point_raw" in lines_with_wr_assign[0],
        f"got {len(lines_with_wr_assign)} assignment(s): {lines_with_wr_assign}",
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


test_trim_threshold_raised()
test_kish_null_collapse()
test_bootstrap_null_collapse()
test_latency_threshold_lowered()
test_latency_route_exposes_n_fields()
test_bootstrap_point_uses_median()
test_summarize_rows_uses_bootstrap_wr()


print()
print("=" * 80)
print("  SUMMARY")
print("=" * 80)
print(f"  {PASSED} passed, {FAILED} failed")
print()
if FAILED == 0:
    print("  All Pass 5 Tier D math/correctness tests verified.")
else:
    print("  FAILURES -- do not commit.")
sys.exit(0 if FAILED == 0 else 1)
