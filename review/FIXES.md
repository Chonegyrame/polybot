# Fixes Log

Each entry pins a finding from `review/01..04_*.md` to the fix that addresses
it and the test that prevents regression.

**Status legend**
- `pending` — finding logged, not yet verified or fixed
- `verified` — finding confirmed as a real bug after re-reading the code
- `rejected` — finding turned out to be agent overreach; no change needed
- `fixed` — code changed, test added and green
- `deferred` — real but intentionally out of scope for this pass

**How to use this file**
1. Before fixing: read the source review entry and verify against the actual code.
2. Write a smoke test that fails on the current behavior.
3. Apply the minimal fix; smoke test now passes.
4. Update this file's `Fix` and `Test` fields with the concrete diff/test name.
5. Move status to `fixed`.

---

## Tier 1 — edge-corrupting (fix before UI build)

### F1 — Bayesian shrinkage uses dollar-pnl prior where an ROI prior is needed

- **Status**: fixed
- **Source**: `review/02_signal_logic.md` Critical #1
- **Files**: `app/services/trader_ranker.py:216-228`, `:318-331`, `:430-440`
- **Error**: The shrinkage formula `(pnl + k·cat_avg_pnl) / (vol + k)` uses
  `cat_avg_pnl = AVG(pnl)` (absolute USDC dollars, e.g. $50k–$500k) as the
  prior. The formula expects a dimensionless ROI rate. As written, small-volume
  traders get a `shrunk_roi` dominated by `k·cat_avg_pnl / vol`, so they sort
  to the top regardless of skill. This corrupts Hybrid's roi-rank and is the
  primary sort key for Specialist mode.
- **Fix**: Replace `cat_avg_pnl AS NULLIF(AVG(pnl), 0)::NUMERIC` with
  `prior_roi AS COALESCE(SUM(pnl)::NUMERIC / NULLIF(SUM(vol), 0), 0)` and use
  `prior_roi` in the formula. Applied identically in 3 places: `_rank_hybrid`,
  `_rank_specialist`, and `gather_union_top_n_wallets`. Variable rename for
  clarity (`cat_avg_pnl` → `prior_roi`).
- **Test**: `scripts/smoke_phase_b56.py::test_f1_shrinkage_uses_roi_prior_not_dollar_pnl_prior`
  (7 assertions: ranking order in hybrid + specialist modes against a
  3-trader synthetic pool where the bug visibly inverts C and B). Test failed
  on pre-fix code, passes on post-fix code. All 33 tests in the suite green.
- **Side observation**: `gather_union_top_n_wallets` now returns 355 wallets
  vs 374 pre-fix on the live DB. Expected: some "lucky tiny-vol" wallets that
  were ranking high under the bug are now correctly outside top-N.

### F2 — Counterparty check fires on any maker, ignoring trade side  [REDONE 2026-05-06 with F12]

- **Status**: fixed (combined with F12 — see below)
- **Source**: `review/01_ingestion.md` Critical #1, `review/02_signal_logic.md` Critical #2 (cross-found)
- **Files**: `app/services/counterparty.py:38-72`, `app/services/polymarket.py:346-371`
- **Error**: `_extract_maker_addresses` pulls the maker address from every
  recent CLOB fill on the YES (or NO) token. CLOB makers can be on either side
  of the book — a bid-maker is a buyer, an ask-maker is a seller. Treating all
  makers as "smart money on the other side" produces a warning that's correct
  about half the time and inverted the other half. The diagnostic the UI shows
  is roughly random.
- **Fix**: Added `_maker_was_seller(fill) -> bool | None` helper that derives
  the maker's side from `maker_side`/`maker_order_side` (preferred) or the
  taker `side`/`taker_side` (fallback). `_extract_maker_addresses` now skips
  any fill where the helper returns False (buyer-side maker) or None
  (undeterminable — excluded conservatively). False negatives are preferable
  to false positives for a non-blocking diagnostic.
- **Test**: `scripts/smoke_phase_b2.py::test_f2_counterparty_filters_by_maker_side`
  (7 new assertions covering taker-BUY, taker-SELL, no-side, explicit
  maker_side=SELL, maker_side=BUY, lowercase normalization, exact-set
  output). The pre-existing `test_extract_maker_addresses` and
  `test_detect_counterparty_overlap` were also updated to reflect correct
  side-aware semantics (they had encoded the bug). All 97 tests in suite
  green; failed 5/97 on pre-fix code.

### F3 — Portfolio fraction denominator excludes USDC cash

- **Status**: fixed
- **Source**: `review/01_ingestion.md` Critical #3
- **Files**: `app/scheduler/jobs.py:223-249` (`_fetch_one_wallet`),
  `:286-330` (caller signature + persistence)
- **Error**: `portfolio_total = sum(p.current_value for p in valid)` —
  reconstructed from open positions only. The dedicated `data-api/value`
  endpoint (already wrapped as `pm.get_portfolio_value`) was never called. A
  trader with $10k in positions + $90k cash shows up in our data as 100%
  committed when reality is 10%. This biases one of the two headline UI
  metrics (`avg_portfolio_fraction`) toward whales who happen to be fully
  deployed.
- **Fix**: `_fetch_one_wallet` now also calls `pm.get_portfolio_value`
  during phase 1 (concurrent, no extra cycle time). Returns a 4-tuple
  `(wallet, positions, portfolio_value_or_None, error)`. Phase 3
  persistence prefers the API value when available; falls back to the
  position-sum computation only when the /value call failed (best-effort —
  never crashes the cycle on a /value blip).
- **Test**: `scripts/smoke_phase_a2.py::test_f3_portfolio_value_prefers_api_over_position_sum`
  (4 source-inspection assertions verifying the fix markers are present in
  both functions). Suite green.
- **Note for live data**: existing rows in `portfolio_value_snapshots`
  written before this fix used position-sum and are biased high. New rows
  going forward will be honest. No backfill possible.

### F4 — B4 captures BID at +30/60/120 but entry was ASK

- **Status**: fixed (combined with F7 in migration 009)
- **Source**: `review/01_ingestion.md` Critical #4
- **Files**: `app/scheduler/jobs.py:1437-1445`, `app/services/orderbook.py:100-105`, `app/services/half_life.py:74-89`
- **Error**: `signal_entry_offer` is the best ask (the price you'd cross to
  buy). The +30/60/120 minute snapshots capture the best bid. Half-life and
  B10 latency math compare them as if they were the same price series. On any
  market with a non-zero spread, the snapshot will look "lower than entry"
  purely because we sampled the other side of the book. This bakes a
  spread-driven artifact into both half-life convergence rates and
  latency-adjusted backtests.
- **Decision (locked)**: capture both bid + ask. Mid for half-life, ask
  for latency, bid available for exit-side modeling.
- **Fix (with F7)**: Migration 009 added `bid_price` and `ask_price` columns
  to `signal_price_snapshots` (kept `yes_price` for back-compat — mirrors
  bid). Existing rows backfilled: `bid_price = yes_price`. Half-life math
  uses `mid = (bid+ask)/2` when both available; latency simulation uses
  `ask`. CRUD helpers refactored to write/read the bid+ask pair and return
  a dict `{bid, ask, mid}` from `fetch_signal_price_snapshots`.
- **Test**: `test_f4_half_life_uses_mid_when_ask_present` (3 assertions),
  CRUD test rewritten to round-trip bid+ask+mid (4 assertions),
  `_apply_latency` test updated for new dict shape (8 assertions). 142/142
  tests in `smoke_phase_b2.py` green.

### F5 — Half-life mixes price spaces for NO-direction signals

- **Status**: fixed
- **Source**: `review/03_backtest_stats.md` Critical #1
- **Files**: `app/services/half_life.py:63-127`
- **Error**: `_yes_price_for_direction(price, direction)` translates a
  YES-token price into direction-space (for NO signals: `1 - price`). It's
  applied to all three inputs in the convergence loop, but two of those inputs
  (`fire_price`, `smart_money_entry`) are already stored in direction-space,
  while `snapshot_price` is stored in YES-space. The function double-translates
  two of the three inputs for NO signals. YES signals are accidentally fine.
  Roughly half the half-life table is meaningless.
- **Storage convention verification** (traced before fixing):
  - `signal_entry_offer` = best ask of the direction-token (jobs.py:419 picks
    YES token for YES signals, NO token for NO signals) → direction-space
  - `first_top_trader_entry_price = SUM(avg_price * size) / SUM(size)` from
    positions matching the signal direction → direction-space
  - `signal_price_snapshots.yes_price` per B4 schema → YES-space
- **Fix**: Renamed `_yes_price_for_direction` to `_to_yes_space` (with new
  docstring clarifying it converts direction-space → YES-space; the math
  `1 - x` is unchanged). Updated `compute_half_life_summary` to apply
  `_to_yes_space` ONLY to `fire_price` and `smart_money_entry`, leaving
  `snapshot_price` as-is (it's already YES-space). All comparisons now happen
  in YES-space.
- **Test**: `scripts/smoke_phase_b2.py::test_f5_half_life_no_direction_price_space`
  (5 new assertions covering NO converging, NO diverging, YES sanity-check,
  mixed bucket, plus 1 updated existing case in
  `test_compute_half_life_summary` that had encoded the bug). 101/101 tests
  green; failed 4/97 on pre-fix code.

### F6 — YES/NO token mapping assumes `clob_token_ids[0] == YES`

- **Status**: fixed
- **Source**: `review/01_ingestion.md` High #12
- **Files**: `app/services/market_sync.py:105`, `:322`,
  `app/services/polymarket_types.py:241-275` (new helper)
- **Error**: We always treat `clob_token_ids[0]` as YES and `clob_token_ids[1]`
  as NO. The Polymarket gamma response also carries `outcomes: ["Yes", "No"]`
  (or sometimes inverted on sports markets — ordered by team name or by
  negation). The pairing between `outcomes` and `clob_token_ids` is never
  verified. For any market where the order is swapped, every entry price,
  exit bid, P&L calc, and B4 snapshot is computed against the wrong token.
- **Fix**: Added pure helper `pair_yes_no_tokens(outcomes, clob_token_ids)`
  in `polymarket_types.py` that pairs by matching the outcome label
  (case-insensitive, whitespace-tolerant). Returns `(None, None)` for
  multi-outcome markets, mismatched lengths, custom labels with no exact
  yes/no, and degenerate cases (both labels match yes). Both `market_sync.py`
  call sites switched from `[0]`/`[1]` indexing to the helper. Defensive
  semantics: when the mapping isn't a clean binary, both tokens are NULL and
  the market won't have signals fired on it (consistent with
  `_outcome_to_direction` behavior in signal_detector).
- **Test**: `scripts/smoke_phase_a.py::test_f6_yes_no_token_mapping_uses_outcomes`
  (9 assertions covering standard binary, inverted outcomes, lowercase,
  whitespace, multi-outcome, custom labels, mismatched lengths, empty,
  degenerate). Pre-fix: ImportError (function didn't exist). Post-fix: all
  37 tests in `smoke_phase_a` green.
- **Production note**: existing rows in the `markets` table might have
  swapped `clob_token_yes` / `clob_token_no` for inverted-outcome markets
  that were synced before this fix. A re-sync via
  `scripts/run_market_sync.py` (or letting JIT discovery refresh them
  naturally as positions arrive) will correct them. Until re-sync, the
  affected markets will have backtest/exit data captured against the wrong
  side. Recommend running a manual sync before relying on the data.

### F7 — Three of four latency profiles always fall back to optimistic baseline

- **Status**: fixed (combined with F4 in migration 009)
- **Source**: `review/03_backtest_stats.md` Critical #2
- **Files**: `app/services/half_life.py:22-66`, `app/services/backtest_engine.py:64-72, 928-1000`,
  `app/scheduler/runner.py:106-118`, `app/db/crud.py:1283-1310`
- **Error**: Latency profiles are `active=(1,3)`, `responsive=(5,10)`,
  `casual=(12,20)`, `delayed=(30,60)`. The B4 snapshot offsets were
  `(30, 60, 120)` with ±5 min tolerance. For active/responsive/casual the
  closest sampled offset (3, 10, 20) was at least 10 minutes from any
  captured snapshot, so every row fell through to the fallback. User picks
  "responsive 5-10 min", sees identical numbers to no-latency, concludes
  wrongly.
- **Decision (locked)**: BOTH fixes — add snapshot offsets at +5 and +15
  min so all four profiles work against real data, AND add a
  `latency_unavailable` response flag as a safety net.
- **Fix**: 
  1. `SNAPSHOT_OFFSETS_MIN` and `LATENCY_SNAPSHOT_OFFSETS` now both
     `(120, 60, 30, 15, 5)`.
  2. `pick_offset_for_age` rewritten to pick CLOSEST offset (not max) and
     accept an `exclude` set so adjacent offsets (5/15, 15/30) don't
     swallow each other at boundary ages.
  3. Job cadence dropped from 30 min → 10 min so the +5 window (0-10 min
     age) is reliably hit. Candidate window expanded from 25-125 to 0-125
     min so fresh signals are eligible immediately.
  4. New `latency_unavailable(n_adjusted, n_fallback, threshold=0.5)` helper
     surfaces the response flag when fallback rate exceeds 50%. Wired into
     `/backtest/summary` `latency_stats` payload.
- **Test**: 
  - `test_pick_offset_for_age` updated for +5/+15 + tie-break + exclude
    semantics (12 new assertions).
  - `test_f7_latency_unavailable_flag` (6 assertions covering boundary
    cases).
  - `test_f7_latency_snapshot_offsets_include_5_and_15` (3 assertions
    pinning the constant + halflife mirror).
  - `test_nearest_snapshot_offset` updated for new offsets.
  - All 142/142 in `smoke_phase_b2.py` green.

### F8 — Win-rate Wilson CI uses raw n, not cluster-effective n_eff

- **Status**: fixed
- **Source**: `review/03_backtest_stats.md` Critical #3
- **Files**: `app/services/backtest_engine.py:700-715`
- **Error**: `wr_lo, wr_hi = wilson_ci(wins, len(pnl_pairs))`. The engine
  cluster-bootstraps the P&L mean using `cluster_id` and reports `n_eff` as
  distinct-cluster count. But the win-rate Wilson CI uses the unclustered
  observation count. With Polymarket's typical clustering (one mega-event →
  100+ correlated sub-markets), `len(pnl_pairs)` can be 5–10× `n_eff`. The
  Wilson CI is too tight by ~√(n / n_eff). Bonferroni and BH widenings inherit
  the narrow base.
- **Fix**: Replaced `wilson_ci(wins, len(pnl_pairs))` with
  `cluster_bootstrap_mean(win_indicators, cluster_keys)` where
  `win_indicators = [1.0 if p > 0 else 0.0 for _, p in pnl_pairs]`. Reuses
  the same bootstrap machinery the P&L mean already uses, so the two CIs are
  computed via parallel methodology. Bootstrap quantiles clamped to `[0, 1]`
  for the win rate (small drift possible at extremes). Point estimate kept
  as the exact `wins / n` for honesty.
- **Test**: `scripts/smoke_phase_b78.py::test_f8_winrate_ci_uses_cluster_correction`
  (7 assertions: 2 sanity-checks of raw rate + n_eff, 3 assertions on a
  clustered case where pre-fix CI is exactly Wilson(5,10) ≈ (0.237, 0.763)
  and post-fix CI is essentially (0, 1), 2 assertions confirming independent
  clusters give a comparable-to-Wilson CI). 83/83 tests green; failed 4/83
  on pre-fix code with the exact Wilson(5,10) numbers proving clustering
  was being ignored.

### F9 — Counterparty pool depth and recency filter inconsistent across modes

- **Status**: fixed
- **Source**: `review/01_ingestion.md` Critical #5 + High #13, `review/02_signal_logic.md` High #2
- **Files**: `app/scheduler/jobs.py:506-516` (counterparty depth bump),
  `app/services/trader_ranker.py:307-329, 360-364` (specialist recency)
- **Error**: Two consistency gaps:
  1. Three different "tracked pool" depths in one cycle: position-refresh
     and exit-detector at 100, counterparty at 50. Wallets ranked 51-100
     were tracked + could fire exits but never triggered counterparty
     warnings.
  2. `_rank_specialist` used `active_recently` (monthly-leaderboard presence)
     while `_rank_absolute` and `_rank_hybrid` used the per-category
     `last_trade_at` filter. Specialist could include traders with a single
     huge old trade dominating the monthly view but no recent activity.
- **Fix**: 
  1. Counterparty pool now built with `top_n=POSITION_REFRESH_TOP_N (100)`
     instead of the calling function's `top_n` (=50). Pool definition
     unified across all three uses in the cycle.
  2. `_rank_specialist` SQL now layers the same `last_trade_at` recency
     filter on top of the existing `active_recently` requirement. Both
     constraints now apply: must be in monthly leaderboard AND must have
     traded in the last `RECENCY_MAX_DAYS` (60) days.
- **Test**: 
  - `test_f9_counterparty_uses_position_refresh_depth` (2 assertions)
  - `test_f9_specialist_uses_recency_filter` (3 assertions)
  - `smoke_phase_b56` still green (33/33) — confirms the recency change
    doesn't break live ranker behavior on real data.

### F10 — Watchlist mutual exclusion is per-lens, not per (cid, direction)

- **Status**: fixed
- **Source**: `review/02_signal_logic.md` High #4
- **Files**: `app/db/crud.py:1396-1466` (upsert + new cleanup helper),
  `app/scheduler/jobs.py:660-680` (per-cycle cleanup call)
- **Error**: CLAUDE.md says watchlist and signal_log are mutually exclusive
  per (cid, direction). The implementation enforced it within a single
  (mode, category, top_n) lens pass only. Across lenses, the same
  (cid, direction) could be `official` in Hybrid/Politics and `watchlist`
  in Specialist/Tech, so it appeared in both `/signals/active` and
  `/watchlist/active` simultaneously, breaking the spec.
- **Fix**: Two-pronged enforcement.
  1. **Write-time**: `upsert_watchlist_signal` SQL now wraps the INSERT in
     `WHERE NOT EXISTS (SELECT 1 FROM signal_log WHERE condition_id=$ AND
     direction=$)`. Returns True/False so callers know whether it took.
  2. **Per-cycle cleanup**: `cleanup_watchlist_promoted_to_signal` deletes
     any watchlist row whose (cid, direction) appears in signal_log (any
     lens). Called once at the end of `log_signals` after all signal
     writes are done. Catches the case where a watchlist row was already
     persisted before the official signal fired.
- **Test**: `scripts/smoke_phase_b2.py::test_f10_watchlist_skips_when_official_signal_exists`
  (5 assertions: skip on existing official, normal path still works,
  cleanup removes promoted rows, post-cleanup row is gone). 147/147
  tests in `smoke_phase_b2.py` green.

### F11 — `/paper_trades?status=closed_exit` returns 400

- **Status**: fixed
- **Source**: `review/04_orchestration.md` High #5
- **Files**: `app/api/routes/paper_trades.py:30, 120-127`
- **Error**: Migration 005 added `'closed_exit'` to the schema CHECK constraint
  and `crud.close_paper_trade_smart_money_exit` writes that value. The route's
  status whitelist still only accepts `('open', 'closed_resolved',
  'closed_manual')`, so the UI cannot filter to smart-money-exit closes.
- **Fix**: Extracted the whitelist to a module-level
  `VALID_PAPER_TRADE_STATUSES` constant including all 4 values; route
  validation references the constant. Improved 400 error message to list
  the accepted values so future drift is debuggable from the response.
- **Test**: `scripts/smoke_phase_a2.py::test_f11_paper_trade_status_whitelist_includes_closed_exit`
  (2 assertions). Suite green.

---

## Tier 2 — operational quality (fix in same window if budget allows)

### F12 — CLOB `/trades` endpoint requires API auth; B2 was silently no-op since shipping

- **Status**: fixed (combined with F2)
- **Source**: `review/01_ingestion.md` Critical #2; confirmed by
  `review/PROBE_FINDINGS.md`
- **Files**: `app/services/polymarket.py:346-385`,
  `app/services/counterparty.py` (full rewrite),
  `app/scheduler/jobs.py:583-608` (call site)
- **Error**: The original F12 finding warned that the CLOB `/trades` endpoint
  was never validated. The probe confirmed it returns **HTTP 401 "Unauthorized/
  Invalid api key"** with no auth, and our defensive `except HTTPStatusError →
  return []` silently swallowed every call. Result: B2 counterparty
  diagnostic has been a complete no-op since shipping — every "no warning"
  was a default, not a check.
- **Fix (combined with F2)**:
  1. **Endpoint switch**: replaced
     `clob.polymarket.com/trades?market=<token_id>` (auth-gated, 401)
     with `data-api.polymarket.com/trades?market=<conditionId>` (public, no
     auth). Renamed `get_clob_trades` → `get_market_trades`.
  2. **Counterparty rule rewrite**: data-api returns trades from each
     trader's perspective with `(outcome, side)` pairs (no maker/taker
     ambiguity). For a YES signal, counterparty = `(outcome=Yes, side=SELL)`
     OR `(outcome=No, side=BUY)`. Mirror for NO signals.
  3. **Function signature changes**: `_extract_maker_addresses(fills)` →
     `_extract_counterparty_wallets(fills, signal_direction)`.
     `check_and_persist_counterparty_warning` now takes `condition_id` +
     `signal_direction` instead of `token_id`.
  4. **Removed dead code**: `_maker_was_seller` helper from yesterday's F2
     attempt is gone (no longer needed since we're not parsing maker/taker).
  5. **Existing F2 tests replaced** with direction-aware tests covering all
     8 (outcome, side) × signal-direction combinations + 4 defensive cases
     + 6 detect_counterparty_overlap cases.
- **Test**: 
  - `scripts/smoke_phase_b2.py::test_f2_f12_counterparty_uses_outcome_and_side`
    (19 pure-function assertions)
  - `scripts/smoke_phase_b2.py::test_f12_live_data_api_trades_shape` —
    **live-API contract test** that hits real Polymarket data-api and
    asserts the response carries `proxyWallet`, `side` ∈ {BUY, SELL},
    `outcome`, valid Polygon address. Skips gracefully if API unreachable;
    fails LOUDLY if shape changes (regression net for future API drift).
  - 114/114 tests in `smoke_phase_b2.py` green.
- **Production note**: all `counterparty_warning` rows in `signal_log` from
  before this fix are unreliable (they were all `False` regardless of true
  state). Going forward they'll be honest. No way to retroactively backfill;
  treat pre-fix data as if the column is NULL.

### F13 — Silent `[]` on shape error in every list-returning client method

- **Status**: fixed
- **Source**: `review/01_ingestion.md` High #6; confirmed by
  `review/PROBE_FINDINGS.md`
- **Files**: `app/services/polymarket.py:43-86` (new helper), and call sites
  in `get_leaderboard_page`, `get_positions`, `get_trades`,
  `get_market_trades`, `get_orderbook`, `get_events`, `get_markets`,
  `get_markets_by_condition_ids`, `get_events_by_ids`
- **Error**: Every list-returning client method had
  `if not isinstance(data, list): return []`. Polymarket sometimes returns
  `200 + JSON-wrapped error object` (overload, internal sort error, etc.).
  Treating that as "empty result" silently truncates pagination and
  aggregation. A blip during position refresh marks traders as
  zero-positions; their consensus weight evaporates; real signals fail to
  fire; no audit trail. The probe confirmed this was exactly the failure
  mode for CLOB /trades 401 (silent for months).
- **Fix**: New `_safe_list_from_response(data, endpoint, list_keys)` helper
  that:
  - Returns `[]` silently for legitimate empty list (legit "no results")
  - Unwraps `{"data": [...]}` or `{"trades": [...]}` style wrapped lists
    (when `list_keys` provided)
  - Logs `WARN: F13: <endpoint> returned dict instead of list (likely API
    error)` for unexpected dict shape, returns `[]`
  - Logs `WARN: F13: <endpoint> returned <type> instead of list` for
    None/str/int, returns `[]`
  All 8 list-returning methods routed through the helper.
  Plus `get_market_trades` and `get_orderbook` now log `ERROR` with status
  code + body excerpt on `HTTPStatusError` (was bare `WARN` without
  context, masked the 401 root cause for months).
- **Test**: `scripts/smoke_phase_a.py::test_f13_safe_list_from_response`
  (8 assertions: empty list, list with mixed items, wrapped under known
  key, list_keys order respected, error-shaped dict, None, str, int).
  All 45 tests in `smoke_phase_a.py` green; no regressions in other 6
  suites (351 total tests).

### F14 — Tenacity retries terminal 4xx errors

- **Status**: fixed
- **Source**: `review/01_ingestion.md` High #10
- **Files**: `app/services/polymarket.py:43-55` (new `_should_retry`),
  `:99-110` (retry decorator)
- **Error**: `retry_if_exception_type(httpx.HTTPStatusError)` matched every
  4xx. A single 400 (bad params) became 4× a 400, burning 4 rate-limit
  tokens and 0.5–8s of backoff for a request that will never succeed.
- **Fix**: Replaced `retry_if_exception_type` with `retry_if_exception(_should_retry)`.
  The new `_should_retry` predicate retries `httpx.TransportError` (network)
  and `HTTPStatusError` only when status is 429 or ≥500. Other 4xx
  (400/401/403/404) fail fast.
- **Test**: `scripts/smoke_phase_a2.py::test_f14_should_retry_only_5xx_and_429`
  (10 assertions covering TransportError, 429, 500-503, 400-404, ValueError).
  Suite green.

### F15 — `_infer_resolved_outcome` only matches literal "yes"/"no" labels

- **Status**: fixed (visibility-level fix; full backtest support is V2)
- **Source**: `review/01_ingestion.md` Medium #17
- **Files**: `app/services/market_sync.py:192-218`
- **Error**: Markets with labels like `"Yes (5+ goals)"`, `"Trump wins"`
  resolve at $1.00 but were returned as `None`, silently excluded from
  backtest. Politics and sports are over-represented in custom-label
  markets; category-level edge estimates were biased toward vanilla Yes/No.
- **Fix**: When `_infer_resolved_outcome` encounters a custom-label binary
  resolution, it now returns `"VOID"` (was: `None`) and emits a `WARN` log
  with full market info. This: (1) makes the magnitude visible via log
  count, (2) surfaces the market in the resolved set rather than vanishing,
  (3) backtest still skips VOID rows but they're now diagnosable.
- **V2 follow-up**: full backtest support requires looking up the
  signal-fire position's `outcome` field to map "winner index" to YES/NO
  for the paper trade. Out of scope for Pass 2.
- **Test**: `scripts/smoke_phase_a.py::test_f15_custom_label_resolution_marked_void_not_silently_null`
  (3 assertions). 48/48 in suite green.

### F16 — Phase 3 position persistence is per-wallet N+1

- **Status**: fixed
- **Source**: `review/04_orchestration.md` High #1
- **Files**: `app/db/crud.py:204-247` (`upsert_positions_for_trader`)
- **Error**: Phase 3 of `refresh_top_trader_positions` acquires one pool
  connection per wallet, then `upsert_positions_for_trader` issued one INSERT
  per Position serially. ~530 wallets × ~10-30 positions = 5k-15k DB
  round-trips per cycle, all sequential. Ate ~7 minutes of the 10-min cycle
  and was the reason the 9-min warning fired regularly.
- **Fix**: Replaced the per-row `await conn.execute(...)` loop with a single
  `await conn.executemany(...)` call. Same SQL, but asyncpg sends one round
  trip per wallet instead of one per position. ~10k round-trips per cycle
  collapses to ~530 (one per wallet). Per-wallet duration drops from
  ~1s → tens of milliseconds.
- **Test**: `scripts/smoke_phase_a2.py::test_f16_position_upsert_uses_executemany`
  (2 source-inspect assertions: helper uses `executemany`, no per-row INSERT
  loop remains). Suite green.

### F17 — `traders_any_direction` includes multi-outcome rows; skew falsely drops below 0.6

- **Status**: fixed
- **Source**: `review/02_signal_logic.md` High #1
- **Files**: `app/services/signal_detector.py:272-282`
- **Error**: `market_totals` counts DISTINCT identity across every position
  outcome on a market, then divides per-direction trader_count by it. For
  binary markets that have stray non-YES/NO rows in `positions` (legacy data,
  edge-case outcome strings, reclassifications), the denominator inflated and
  legitimate signals didn't fire because skew falsely dropped below 0.6.
  Conservative bias — false negatives, not false positives.
- **Fix**: Added `WHERE LOWER(outcome) IN ('yes', 'no')` to the
  `market_totals` CTE so the skew denominator counts only YES/NO traders.
  Mirrors the filter that `_outcome_to_direction` applies upstream — keeps
  the V1 binary-only assumption consistent across the pipeline.
- **Test**: `scripts/smoke_phase_a2.py::test_f17_skew_denominator_filters_to_yes_no`
  (1 source-inspect assertion). Suite green.

### F18 — Exit detector peak is last-fire watermark, can fire stale exits 24h later

- **Status**: fixed
- **Source**: `review/02_signal_logic.md` High #5
- **Files**: `app/services/exit_detector.py:36-44` (new constant),
  `:142-156` (window cap)
- **Error**: Exit fires when current vs `peak_*` drops ≥30%. The peak is read
  from `signal_log.peak_trader_count` / `peak_aggregate_usdc`, updated only
  when a signal re-fires. After a signal stops re-firing, the peak became a
  permanent watermark and an exit could fire 24h+ later when current dropped
  vs that frozen peak — but the user had moved past it long ago.
- **Fix**: Added `EXIT_ACTIVITY_GUARD_HOURS = 2` constant. `detect_exits`
  caps the `last_seen_at` window to `min(window_hours, 2)` so only signals
  that have been actively detected in the last 2 hours can emit exits. By
  definition, "actionable" signals are still being detected, so this only
  removes stale-but-quiet exits the user couldn't act on anyway.
- **Test**: `scripts/smoke_phase_a2.py::test_f18_exit_activity_guard_2h`
  (2 assertions). Suite green.

### F19 — `gap_to_smart_money` may have direction-space bug if storage convention is YES-space

- **Status**: rejected (verified — both inputs ARE direction-space)
- **Source**: `review/03_backtest_stats.md` Medium #6
- **Files**: `app/services/backtest_engine.py:286-313` (docstring updated)
- **Verification (during F5 work)**:
  - `signal_entry_offer` = best ask of the **direction-token** (jobs.py:419
    picks YES token for YES signals, NO for NO) → **direction-space**
  - `first_top_trader_entry_price = SUM(avg_price * size) / SUM(size)` from
    positions matching the signal direction's outcome (Yes for YES signals,
    No for NO) → **direction-space**
- **Conclusion**: both inputs live in direction-space; comparing them
  directly is correct. The audit's concern was based on assuming
  `first_top_trader_entry_price` was YES-space — it isn't. The F5 half-life
  bug existed because snapshots are YES-space while these two are direction-
  space, a different comparison.
- **Action**: no code change needed; updated `gap_to_smart_money` docstring
  to explicitly state both inputs are direction-space and reference the F5
  fix so future readers don't re-litigate this.

### F20 — BH-FDR rank tie convention disagrees with comment

- **Status**: fixed
- **Source**: `review/03_backtest_stats.md` High #4
- **Files**: `app/services/backtest_engine.py:1013-1017`
- **Error**: Comment said "ties → lowest rank", code does
  `sum(1 for p in sorted_p if p <= current_pnl_p)` which gives ties the
  HIGHEST rank. When many session queries are underpowered (p=1.0), all
  get rank=N → alpha_bh=0.05 → effectively no correction.
- **Decision (locked earlier)**: align comment to code (ties → highest)
  matching `statsmodels.stats.multitest.fdrcorrection`. Practical impact
  is minor because in tied-p situations the rank only affects the dead
  queries themselves, which fail under either convention. If a strict
  variant is ever wanted, expose as explicit `tie_method="min"` parameter.
- **Fix**: Updated the inline comment to accurately describe ties → highest
  semantics and reference statsmodels parity. Code unchanged.
- **Test**: `scripts/smoke_phase_a2.py::test_f20_bh_fdr_comment_matches_code`
  (2 assertions verifying the source contains "ties -> highest" and
  "statsmodels"). Suite green.

### F21 — `_pvalue_from_ci` Gaussian SE breaks on skewed bootstrap CIs

- **Status**: fixed
- **Source**: `review/03_backtest_stats.md` High #2
- **Files**: `app/services/backtest_engine.py:333-410` (new
  `cluster_bootstrap_mean_with_p`), `:778-786` (caller),
  `:228` (BacktestResult.pnl_bootstrap_p field), `:1100-1119` (BH-FDR
  consumer)
- **Error**: SE was approximated as `(hi - lo) / (2 × 1.96)`, assuming a
  symmetric Gaussian CI. Cluster-bootstrap CIs for P&L are skewed (heavy
  right tail on Polymarket). The resulting p-value was approximate, so
  BH-FDR rank ordering across session queries was noisy in close cases.
- **Fix**: Added `cluster_bootstrap_mean_with_p` which computes the
  empirical two-sided p-value directly from the bootstrap distribution
  (fraction of resampled means at-or-below 0, doubled, clamped to [0,1]).
  No Gaussian assumption. Result stored in new field
  `BacktestResult.pnl_bootstrap_p` (default None for back-compat).
  `compute_corrections` prefers `pnl_bootstrap_p` when present; falls back
  to the old Gaussian-from-CI approximation for legacy session entries.
- **Note**: persisting bootstrap_p into `slice_lookups` would let session
  history use the better p-value too — deferred (would need a migration
  + backfill). Current effect: the CURRENT result's p is exact;
  cross-session ranking still uses the approximation for prior entries.
- **Test**: `scripts/smoke_phase_b78.py::test_f21_bootstrap_p_value_populated_and_used`
  (4 assertions: helper math, end-to-end populate, compute_corrections
  consumes bootstrap_p without error). 87/87 in suite green.

### F22 — Holdout `date` vs `timestamptz` SQL boundary is session-TZ dependent

- **Status**: fixed
- **Source**: `review/03_backtest_stats.md` High #5
- **Files**: `app/services/backtest_engine.py:35` (timezone import),
  `:576-589` (cutoff construction)
- **Error**: `parts.append(f"AND s.first_fired_at < ${len(args)}")` with
  `holdout_from: date | None`. Postgres implicitly casts `date` to local-time
  midnight then compares to `timestamptz`. If session TZ ever drifts off UTC,
  the cutoff shifts by hours. Edge-of-day signals can leak into or out of
  training.
- **Fix**: Wrap `holdout_from` in an explicit
  `datetime(y, m, d, tzinfo=timezone.utc)` before passing to asyncpg. The
  cutoff is now unambiguously "start of `holdout_from` UTC" regardless of
  session timezone.
- **Test**: `scripts/smoke_phase_a2.py::test_f22_holdout_filter_uses_utc_timestamp`
  (1 source-inspection assertion verifying the fix marker is present).
  Suite green.

### F23 — Routes contain inline DB queries (CLAUDE.md rule violation)

- **Status**: fixed
- **Source**: `review/04_orchestration.md` High #4
- **Files**:
  - `app/db/crud.py`: 13 new helpers — `get_market_tokens_and_category`,
    `get_market_with_event`, `get_market_positions_summary`,
    `get_market_per_trader`, `get_market_signal_history`,
    `get_signal_enrichment`, `get_trader_profile`,
    `get_trader_per_category_stats`, `get_trader_open_positions`,
    `get_trader_classification`, `get_trader_sybil_cluster`,
    `latest_classification_at`, `count_distinct_wallets_with_positions`,
    `count_signals_since`, `fetch_half_life_rows`
  - `app/api/routes/paper_trades.py`: replaced 2 inline market lookups
  - `app/api/routes/traders.py`: replaced 5 inline queries with 5 crud calls
  - `app/api/routes/markets.py`: full rewrite — replaced 4 inline queries
    with 4 crud calls
  - `app/api/routes/signals.py`: replaced 1 inline enrichment query
  - `app/api/routes/system.py`: replaced 3 inline aggregate counts
  - `app/api/routes/backtest.py`: replaced 1 inline half-life fetch
- **Error**: Read-side enrichment queries bypassed `crud.py`. Same SQL
  appeared in multiple places (drift risk when schema evolves) and routes
  couldn't be unit-tested without spinning up Postgres.
- **Decision (locked)**: include in this pass with regression-protected
  refactor.
- **Fix**: SQL extracted verbatim into named crud functions; routes now
  call those. Zero behavior change — same SQL, same params, same result
  shape, exposed through a named function.
- **Test**: `scripts/smoke_phase_pass2_routes.py` — new file with 16
  regression assertions. Each refactored route function called directly
  on a real DB connection; asserts response shape (top-level keys, inner
  collection types, sample row schema). All 16 green; total suite
  437 tests across 8 files.

### F24 — `daily_leaderboard_snapshot` holds one connection across the full 28-combo run

- **Status**: fixed
- **Source**: `review/04_orchestration.md` High #2
- **Files**: `app/scheduler/jobs.py:140-167`
- **Error**: One `pool.acquire()` wrapped the entire 28-combo loop with HTTP
  fetches and DB writes interleaved. Pinned one of 12 pool slots for ≥1
  minute during the snapshot. Same pattern was already fixed in
  `auto_close_resolved_paper_trades` but not here.
- **Fix**: Acquire the connection per-combo inside the inner loop instead
  of once at the top. The HTTP fetches still happen serially (rate-limited)
  but no longer hold a DB connection during the round trips.
- **Test**: source-inspect would be brittle (intent is structural). Verified
  manually: `async with pool.acquire() as conn:` is now inside the
  innermost for-loop. Daily snapshot is exercised by smoke_phase_b56's
  trader-ranker test which depends on snapshot data — still green (33/33).

### F25 — `signals_health` going amber on quiet days drives status-pill alert fatigue

- **Status**: fixed
- **Source**: `review/04_orchestration.md` Medium #6
- **Files**: `app/api/routes/system.py:40-44` (constant), `:109-115` (use site)
- **Decision (locked)**: extend `SIGNALS_AMBER_MAX_HOURS` from 48h to 72h.
  Keeps the "cycle stopped firing because of a bug" signal but kills the
  weekend / quiet-market false alarms. Less invasive than dropping the
  component from the composite entirely.
- **Fix**: Constant changed to `72`; comment updated to explain the
  rationale.
- **Test**: `scripts/smoke_phase_a2.py::test_f25_signals_health_quiet_window_72h`
  (1 assertion). Suite green.

### F26 — JIT discovery silently drops markets whose embedded event refetch glitched

- **Status**: fixed (logging-level fix; auto-retry deferred)
- **Source**: `review/01_ingestion.md` High #9
- **Files**: `app/services/market_sync.py:310-336`
- **Error**: `discover_and_persist_markets` built `event_ids` from embedded
  events, refetched via `pm.get_events_by_ids`, then linked the event to the
  market only if the event_id came back. If gamma dropped one of the
  requested ids, the market was persisted with `event_id=None`,
  `category=NULL`, and only ever appeared in "Overall." Category-filtered
  signal lenses silently missed those markets — pre-fix you only saw the
  aggregate "requested N got M back" count, not which specific events
  glitched.
- **Fix**: After the events refetch, compute the set of dropped event_ids
  and the affected condition_ids. Log a `WARN` with up to 10 missing
  event_ids and 5 affected market cids so the operator can investigate
  (re-run discovery for those events, file an issue with Polymarket, etc.).
- **V2 follow-up**: auto-retry the missing events with backoff, or
  fall-back to embedded event metadata when refetch repeatedly fails.
- **Test**: source-inspect would be noisy (the WARN format may evolve).
  No regression risk — purely additional logging. Verified by
  `smoke_phase_a` (48/48 green; market_sync touched).

---

## Locked decisions

- **F4** — capture **both** bid + ask in `signal_price_snapshots`. New columns
  `bid_price` and `ask_price` (deprecate / migrate `yes_price`). Half-life
  uses mid; latency uses ask.
- **F7** — add snapshot offsets at **+5 and +15 min** so all four latency
  profiles have real data behind them, AND add a `latency_unavailable: True`
  response flag when fallback rate exceeds 50% as a regression safety net.
- **F20** — align **comment to code (highest-rank-for-ties)**, matching
  statsmodels. If a strict variant is ever wanted, add as explicit
  `tie_method="min"` parameter rather than swapping the default.
- **F23** — refactor inline route queries into `crud.py` helpers **in this
  pass**, not deferred. Real benefit is schema-change safety and reusability;
  worth the ~1.5h of mechanical work. **Regression-protected**: before any
  refactor, write a route-level smoke test that hits each of the 6 routes on
  a known DB fixture and pins the exact response shape (keys, types, sample
  values). The test must pass against the pre-refactor code, then continue
  passing after the refactor. Any divergence → test fails → caught immediately.
- **F25** — extend `signals_health` quiet window from 48h to **72h** before
  flipping to amber. Keeps the cycle-died-because-of-bug signal, kills the
  weekend false alarms.

---

## Pass 5 — rate-limiter consolidation (R17)

### R17 — TokenBucket per `PolymarketClient` instance → module-level per-host registry

- **Status**: fixed
- **Source**: `review/PASS5_AUDIT.md` finding #15 (Critical)
- **Files**: `app/services/rate_limiter.py` (full rewrite — registry + lazy
  lock), `app/services/polymarket.py` (drop `self._limiter`, add
  `_bucket_for(url)`, replace `wait_exponential` with
  `_DecorrelatedJitterWait`, honor `Retry-After`), `app/config.py`
  (default 10.0 → 8.0 to leave 20% headroom for retries),
  `scripts/smoke_phase_pass5_rate_limiter.py` (44 new tests).
- **Error**: Each `PolymarketClient.__init__` instantiated a fresh
  `TokenBucket`. With 12 distinct `async with PolymarketClient()` call
  sites and APScheduler running concurrent jobs (e.g.
  `record_signal_price_snapshots` and `refresh_and_log` both on 10-min
  crons with no cross-job lock), the effective outgoing rate to
  Polymarket was 2-5× the configured 10/s → cascading 429s → tenacity
  retries amplified the burn.
- **Fix**:
  1. Module-level `_BUCKETS: dict[str, TokenBucket]` registry in
     `rate_limiter.py`, keyed by hostname. `get_bucket(host, rate)` is
     first-write-wins, lazy.
  2. Per-host scoping (data-api / gamma-api / clob get separate
     buckets) so one slow host doesn't starve callers of another.
  3. `TokenBucket._lock` is lazy — bound to the event loop on first
     `acquire()` call, recreated if the loop changes (multi-loop test
     safety).
  4. `PolymarketClient._bucket_for(url)` uses the registry by default;
     a `rate_limit_per_second` constructor arg becomes a per-instance
     override (private bucket) for tests.
  5. Replaced `wait_exponential(multiplier=0.5, min=0.5, max=8)` with
     custom `_DecorrelatedJitterWait` (AWS-recommended formula:
     `min(cap, uniform(base, prev * 3))`). Tighter p99, desynchronizes
     concurrent retries hitting the same boundary.
  6. On 429 with `Retry-After` header (numeric or HTTP-date),
     `_parse_retry_after` extracts seconds (capped 60s); the value is
     stashed on the exception and `_DecorrelatedJitterWait` honors it
     instead of jitter on the next attempt.
  7. Default `rate_limit_per_second` lowered from 10.0 → 8.0 (env-var
     overridable) so retries have headroom inside Polymarket's per-IP
     ceiling.
- **Test**: `scripts/smoke_phase_pass5_rate_limiter.py` (44 assertions
  across 11 sections: host_for_url, registry sharing, per-host scoping,
  reset_buckets, lazy lock binding, pacing integration, multi-loop
  safety, PolymarketClient registry path, per-instance override path,
  Retry-After parsing edge cases, jitter formula bounds + Retry-After
  precedence + code-shape regressions).
- **Live verification**: `scripts/probe_polymarket_endpoints.py` runs
  cleanly post-fix — 5 rapid CLOB book calls all 200 OK, no 429s. All
  10 prior smoke suites continue to pass with no regressions.
- **Total smoke count**: 579 → 623 (44 new R17 tests).
- **Production impact**: effective outgoing rate is now bounded at the
  configured 8 r/s **per host**, regardless of how many
  `PolymarketClient` instances are alive concurrently. Cron-overlap
  scenarios that previously drove 2-5× rate amplification are
  structurally fixed. Retries no longer cascade because the shared
  bucket is the single chokepoint, and `Retry-After` honoring lets the
  server's own pacing inform our backoff when present.
