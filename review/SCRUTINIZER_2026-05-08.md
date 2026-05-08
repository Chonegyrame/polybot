# Scrutinizer Result — 2026-05-08

**Agents:** 3 | **Consensus threshold:** ≥2 | **Findings surfaced:** 1

## Verdict
⚠️ 1 FINDING — fix before UI build.

## Findings

### Finding 1 — Latency `_apply_latency` double-translates NO-direction snapshots since R8 (migration 012)
- **File:** `app/services/backtest_engine.py:1137`
- **Domain:** Live-vs-backtest divergence
- **Flagged by:** 3 of 3 agents
- **Confidence:** 83% (avg of 85/85/80)
- **Money / signal mechanism:** Pass 3 R8 / migration 012 changed `signal_price_snapshots` to capture the direction-side token's bid/ask in **direction-space** (NO-token book for NO signals). Each row gets a `sps.direction` column ('YES' or 'NO'). The half-life path was updated — `fetch_half_life_rows` returns `snapshot_direction` and `compute_half_life_summary` branches on it. The latency path was **not**: `app/db/crud.py:fetch_signal_price_snapshots` (~line 1842-1858) does not SELECT the `direction` column, and `_apply_latency` (line 1137) hard-codes the pre-R8 assumption `new_offer = snap_yes if r.direction == 'YES' else (1.0 - snap_yes)` with the now-stale comment "snapshot is YES-space; translate to direction space" at lines 1112-1113. For any post-R8 NO signal, the code computes `1 − NO_ask` and feeds that into `compute_pnl_per_dollar` as the direction-space `signal_entry_offer`, breaking the P&L identity by a multiplicative factor. The half_life path is the proof that direction-space storage is real and the latency path is the outlier.
- **Reproduction:** Hit `GET /backtest/summary?latency_profile=responsive` (or `delayed`/`active`/`casual`/`custom`) over a window that includes any NO signal fired after migration 012 shipped (every NO signal since 2026-05-07). Concrete numeric example — a winning NO signal where smart money was right and the market drifted from `NO_ASK = 0.42` at fire to `NO_ASK = 0.42` at +5 min: true latency-adjusted per-dollar payout is `1 / 0.42 ≈ 2.38`. The bug computes `effective_entry = 1 − 0.42 = 0.58` and reports `1 / 0.58 ≈ 1.72`, understating per-dollar payout by ~28pp on this trade. For a winning NO at `NO_ASK = 0.30`, truth is `1/0.30 = 3.33` vs buggy `1/0.70 = 1.43` — a >190pp absolute swing on the affected subset's `mean_pnl_per_dollar`. The existing smoke test `scripts/smoke_phase_b2.py::test_apply_latency` encodes the legacy YES-space assumption (`expected = {1.0 - 0.42, 1.0 - 0.32}`) and currently passes against the bug, so it will not catch this.
- **Fix sketch:**
  1. In `app/db/crud.py:fetch_signal_price_snapshots`, add `direction` to the SELECT and return it: `out[(sid, off)] = {"bid": bid_f, "ask": ask_f, "mid": mid_f, "direction": r["direction"]}`.
  2. In `app/services/backtest_engine.py:_apply_latency` (replace ~line 1137):
     ```python
     snap_dir = snap.get("direction")  # 'YES' | 'NO' | None (legacy YES-space)
     if r.direction == "YES":
         new_offer = snap_yes
     elif snap_dir == "NO":
         new_offer = snap_yes  # already direction-space (NO-space), no translate
     else:
         new_offer = 1.0 - snap_yes  # legacy YES-space NO row: translate
     ```
  3. Update `scripts/smoke_phase_b2.py::test_apply_latency` to cover both the legacy `direction=None` and the post-R8 `direction='NO'` cases on a NO signal — the post-R8 case must NOT translate.
- **Strict-confidence gate re-check:**
  - **(a) file:line:** PASS — `app/services/backtest_engine.py:1137` (translation), with the stale comment at 1112-1113 and the helper omission at `app/db/crud.py:1842-1858`.
  - **(b) reachable:** PASS — entry point is `/backtest/summary?latency_profile=...` (and `/backtest/slice` with the same param), which routes through `backtest_with_rows` → `_apply_latency` whenever `latency_profile is not None`.
  - **(c) material:** PASS — mutates the direction-space `signal_entry_offer` consumed by `compute_pnl_per_dollar` to produce `mean_pnl_per_dollar`, the headline backtest number the user reads to decide whether to follow signals (CLAUDE.md: "helps the user manually decide on entries").
  - **(d) asymmetric:** PASS — biases the NO-direction subset of latency-adjusted backtests in a single direction per signal (multiplicative misread, not noise that averages out). YES-direction is unaffected.
  - **(e) steel-manned:** PASS — verified `compute_half_life_summary` correctly dispatches on `snapshot_direction` (proves direction-space storage is real); verified `record_signal_price_snapshots` + `list_signals_pending_price_snapshots` capture NO-token book and persist `direction='NO'` since R8; verified `fetch_signal_price_snapshots` does NOT include `sps.direction`, so `_apply_latency` has no signal it could use; FIXES.md / PASS5_AUDIT.md describe R8 as half_life-only, confirming latency was not in scope of the prior fix; existing smoke test encodes the bug rather than catching it.

## Per-agent meta
- Agent 1: 11 candidates → 2 surfaced → 1 made consensus
- Agent 2: 6 candidates → 1 surfaced → 1 made consensus
- Agent 3: 6 candidates → 1 surfaced → 1 made consensus
