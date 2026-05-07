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

### Tier A — migrations 018, 019, 020 (schema-only, no behavior change)

- **Status**: fixed (commit 1 of the Pass 5 plan)
- **Source**: `review/PASS5_PLAN.md` Tier A. Schema groundwork for items
  #8, #9, #16. No code changes here — code consumers for #8 and #16 land
  in later commits; #9 is fully fixed by migration 019 alone.
- **Files**:
  - `migrations/018_slice_lookups_bootstrap_p.sql` — adds nullable
    `bootstrap_p NUMERIC` column. Legacy rows stay NULL;
    `compute_corrections` keeps falling back to `_pvalue_from_ci` for
    those, and new rows (after item #8 lands) will persist the real
    bootstrap p-value.
  - `migrations/019_dedup_view_skip_unavailable.sql` — drops and
    recreates `vw_signals_unique_market`, moving the
    `signal_entry_source != 'unavailable'` filter **inside** the
    `first_fired` CTE so it runs **before** the `DISTINCT ON`. The
    canonical row per `(condition_id, direction)` is now the earliest
    *executable* fire, not the earliest fire period. Column list
    preserved verbatim from migration 007 — no downstream code change
    needed. Lens aggregation continues to count all fires (including
    unavailable ones).
  - `migrations/020_snapshot_runs.sql` — new completeness ledger keyed
    by `snapshot_date`. Records `started_at`/`completed_at`,
    `total_combos`/`succeeded_combos`/`failed_combos`, and a
    `failures JSONB` list. Index on `completed_at DESC` for the future
    `/system/errors` page consumer. Code that writes to this table
    lands with item #16.
  - `scripts/smoke_phase_pass5_migrations.py` — 34 new tests covering
    file content (key SQL fragments, comments, structural ordering of
    `WHERE` vs `ORDER BY` inside the CTE) plus live-DB checks
    (column types, view comment, behavior round-trip on the dedup view
    using a unique `mode='__pass5_test_019'` tag with full cleanup, PK
    + index existence on `snapshot_runs`, JSONB round-trip).
- **Decision call-out**: the original plan sketched the migration 019
  view with extra Pass 3+ columns (`contributing_wallets`,
  `first_net_dollar_skew`, etc.) that don't appear in the current view
  contract. We preserved the existing column set verbatim — no scope
  creep, no risk of breaking the backtest engine's existing reads. If a
  future audit wants those columns surfaced via the dedup view, that's
  a separate migration.
- **Live verification**: `scripts/apply_migrations.py` applied all
  three cleanly to live Supabase (commit `ad44c26` → migrations 018,
  019, 020 registered in `_migrations`). Behavioral round-trip on the
  rebuilt view confirms `unavailable` rows are filtered before the
  `DISTINCT ON` (test row absent from the view; canonical row is the
  later `clob_l2` row, not the earlier `unavailable` one).
- **Total smoke count**: 623 → 657 across 12 suites (34 new
  Tier A tests in `smoke_phase_pass5_migrations.py`).

### Tier B — items #1+#2+#5 — cluster-collapse family

- **Status**: fixed (commit 2 of the Pass 5 plan)
- **Source**: `review/PASS5_AUDIT.md` items #1 (Critical), #2 (Critical),
  #5 (High). One conceptual fix (identity-collapse the SUM/HAVING) in
  three SQL hotspots in three services.
- **Files**:
  - `app/services/signal_detector.py` — `_aggregate_positions` now
    inserts an `identity_positions` CTE that pre-aggregates
    `(identity, condition_id, outcome)` before `direction_agg` and
    `market_totals` consume. Adds a sibling `direction_wallets` CTE that
    keeps `contributing_wallets` derived from raw wallet rows (so the
    R3b cohort tracker still gets a flat list of underlying wallets).
  - `app/services/counterparty.py` — `find_counterparty_wallets`
    SQL adds a `wallet_identity` CTE and groups by `wi.identity`. The
    `is_counterparty` floor + concentration check is now applied at the
    entity level. Returned dicts gain a `wallets` field (full underlying
    list); the legacy `wallet` field becomes a deterministic
    representative (alphabetically-first wallet of the entity) so back-
    compat callers that print "the counterparty" keep working.
  - `app/services/exit_detector.py` —
    `_recompute_one_signal_aggregates_for_cohort` adds an `identity_agg`
    inner CTE with `HAVING SUM(p.current_value) > 0` so the COUNT and
    SUM are derived from the same per-identity aggregate. The outer
    SELECT becomes `COUNT(*)` + `SUM(identity_usdc)` over identity_agg.
  - `scripts/smoke_phase_pass5_cluster_collapse.py` — 49 new tests:
    code-shape regression checks, pure `is_counterparty` regression,
    identity-collapse aggregation against live DB (cluster + retail,
    pure wash-trading cluster), counterparty cluster behaviors at the
    floor (cluster $20k each, $4k each, $1k each, lone wallets), and
    exit_detector identity-summed cohort recompute (cluster fully
    holds, partial dropout, full dropout, two independent traders).
- **Behavioral change vs raw-wallet predecessor**:
  - `signal_detector.aggregate_usdc` and `total_dollars_in_market` are
    **numerically unchanged** for all scenarios (sum across wallets =
    sum across identity-summed wallets).
  - `signal_detector.avg_portfolio_fraction` **changes** for cluster-
    active markets: was averaged across raw wallet fractions
    (`current_value / wallet_pv`), now averaged across identity
    fractions (`identity_total / MAX(wallet_pv)` per cluster). For a
    4-wallet cluster with $20k each on YES against a $200k MAX wallet
    PV, the cluster contributes 0.40 to the average instead of four
    rows of 0.10 each. MAX is used instead of SUM(PV) because sybil
    wallets typically share funding — SUM would double-count capital.
  - `counterparty` count behavior **changes** materially: a 4-wallet
    cluster on the opposite side counts as 1 entity (was 4), and a
    cluster of 4 wallets each at $4k ($16k entity total) clears the
    $5k floor (was: 4 separate wallets each below floor → false
    negative).
  - `exit_detector` SUM/COUNT consistency **improves**: when a cluster
    has wallets in different states, the entity-level HAVING filter
    drops fully-flat identities cleanly. Numerically identical to the
    pre-fix path on typical scenarios; legacy `peak_aggregate_usdc`
    rows in `signal_log` were written with raw-wallet SUM and post-fix
    `cur_agg` is identity-summed — same numerical value for one-sided
    clusters; small differences possible on cluster-active markets.
    The TRIM threshold absorbs the noise.
- **Decision call-out (one-time)**: the audit's framing of #1 (the
  dollar-skew floor R2 is "silently broken" by sybils) overstates the
  fix — `aggregate_usdc` and `total_dollars_in_market` already had the
  right totals, and the dollar-skew ratio was correct. The plan's own
  worked example notes "the fix doesn't change firing behavior here"
  for the audit's flagship scenario. The real material wins are:
  (a) per-entity `avg_portfolio_fraction`, (b) cluster-aware
  counterparty counting, (c) cleaner SUM/COUNT alignment for the exit
  detector. Documented in commit message + this FIXES entry so future
  readers don't expect false-positive signals to disappear.
- **Live verification**: 13 smoke suites — **706/706 passing** (was
  657 before this commit, +49 new in `smoke_phase_pass5_cluster_collapse.py`).
  Test fixture inserts a synthetic cluster (`wallet_clusters` +
  `cluster_membership` + portfolio_value_snapshots + positions) with a
  unique `__pass5_cc_test__` tag, then restores any pre-existing rows
  for the 5 borrowed traders during teardown.
- **Scope deviations from the plan**: (a) `direction_wallets` is a new
  separate CTE rather than the plan's `UNNEST(wallets_in_identity)`
  cross-join (which would have multiplied rows). (b) `MAX(portfolio_
  value)` per identity follows the plan as written; alternative `SUM`
  would assume non-shared funding which is the worse default for
  sybil clusters.

### Tier B — item #3 — specialist Bayesian prior over winners only

- **Status**: fixed (commit 3 of the Pass 5 plan)
- **Source**: `review/PASS5_AUDIT.md` item #3 (Critical). Same shape as
  the F1 bug (Pass 1) but relocated to specialist mode.
- **Files**:
  - `app/services/trader_ranker.py` — `_rank_specialist`: new
    `prior_pool` CTE that drops the candidate-restricting filters
    (`pnl > 0`, `active_recently`, `resolved_trades >= $5`, F9
    `last_trade_at`) and keeps only the data-quality filters (snapshot
    date, category, time_period, order_by, specialist `vol >= $3`
    floor, contamination exclusion). `cat_avg` now computes
    `prior_roi = SUM(pnl)/SUM(vol)` from `prior_pool`. `base` (the
    candidate set being ranked) is unchanged.
  - `app/services/trader_ranker.py` — `gather_union_top_n_wallets`:
    same pattern, scaled to the multi-category bulk query. New
    `prior_pool` CTE drops the `recent_overall` recency filter (which
    `base` retains for its own purposes); keeps the contamination
    exclusion. `cat_avg` reads from `prior_pool` per category.
  - `scripts/smoke_phase_pass5_specialist_prior.py` — 17 new tests:
    code-shape regressions (both `_rank_specialist` and
    `gather_union_top_n_wallets` have `prior_pool` CTE; `base` still
    filters `pnl>0` / `active_recently` / `recent_overall`),
    behavioral test against live DB with synthetic 'finance' category
    data (6 winners + 4 losers + 1 candidate), and a hybrid-mode sanity
    check (Hybrid path unchanged, candidate present with raw 20% ROI).
- **Behavioral change**: the prior the shrinkage pulls toward changes
  from "average ROI of qualifying winners" to "average ROI of the full
  specialist-eligible universe." On the synthetic test scenario
  (winners with 5% ROI on $12M vol, losers with -7.5% ROI on $4M vol,
  candidate at $5k pnl on $25k vol):
  - **Pre-fix prior** (winners only): 5.03%. Candidate's `shrunk_roi`
    = (5000 + 50000 × 0.0503) / 75000 = **0.1002**.
  - **Post-fix prior** (full pool): 1.90%. Candidate's `shrunk_roi`
    = (5000 + 50000 × 0.0190) / 75000 = **0.0794**.
  - Difference: ~2 percentage points lower under the honest prior, so
    lucky tiny-volume specialists no longer get over-promoted. The
    raw `roi` field (display) is unchanged at 0.20.
- **Live verification**: 14 smoke suites — **723/723 passing** (was
  706 before this commit, +17 new in `smoke_phase_pass5_specialist_prior.py`).
  Test fixture inserts 11 synthetic wallets + leaderboard_snapshots +
  trader_category_stats rows for `SNAP_DATE = 2099-01-15`, then cleans
  up everything (DELETE by proxy_wallet across `traders`,
  `leaderboard_snapshots`, `trader_category_stats`,
  `wallet_classifications`).
- **Scope deviation from the plan**: the plan suggested dropping
  contamination exclusion from `prior_pool`. We kept it — including
  market-makers / arb bots / known sybils in the prior would deflate
  the baseline (they have high volume + ~0% ROI), giving specialists
  the wrong target. The exclusion is about data quality, not about
  candidate restriction — keeping it is the right call.

### Tier B — item #8 — bootstrap_p persisted to slice_lookups

- **Status**: fixed (commit 4 of the Pass 5 plan; depends on
  migration 018 from Tier A).
- **Source**: `review/PASS5_AUDIT.md` item #8 (Critical). F21 (Pass 2)
  added the empirical bootstrap p-value to `BacktestResult` to replace
  a Gaussian-from-CI approximation that's broken on skewed P&L
  distributions. F21 deferred persisting the value to
  `slice_lookups` — so every prior session entry returned NULL for
  `bootstrap_p` in `compute_corrections`, which then fell back to the
  broken approximation for every comparator. BH-FDR ranking was
  unstable across sessions: two CIs of identical width could rank
  differently depending on whether the result pre- or post-dated F21.
- **Files**:
  - `app/db/crud.py` — `insert_slice_lookup` gains a `bootstrap_p:
    float | None = None` kwarg (defaults to None for back-compat);
    INSERT writes the new column (positional `$7`).
    `get_session_slice_lookups` SELECTs `bootstrap_p` and includes it
    in each returned dict.
  - `app/api/routes/backtest.py` — both call sites updated:
    `get_summary` passes `bootstrap_p=result.pnl_bootstrap_p`;
    `get_slice` passes `bootstrap_p=br.pnl_bootstrap_p` per bucket.
    Smoke regression-checks the count of `bootstrap_p=` keyword passes
    matches the count of `insert_slice_lookup(` call sites in the file
    so we can't silently regress one of them in a future edit.
  - `scripts/smoke_phase_pass5_bootstrap_p.py` — 19 new tests:
    code-shape regressions (signature has the kwarg, INSERT writes the
    column, SELECT pulls it, both route call sites pass it,
    `compute_corrections` still prefers `bootstrap_p`), DB round-trip
    (insert with 0.04 + insert without; verify both via direct DB
    query AND via the session helper), and a behavioral test on
    `compute_corrections` proving the persisted column is actually
    consumed (scenario A with persisted small-p comparators produces
    a narrower BH-FDR widened CI than scenario B with NULL/Gaussian-
    fallback comparators; ratio ~1.27x matches theoretical
    `z_{0.0125}/z_{0.05}`).
- **Behavioral change**: `compute_corrections` now reads the persisted
  `bootstrap_p` for prior session entries instead of falling through
  to `_pvalue_from_ci` for every comparator. BH-FDR ranks based on
  the empirical bootstrap p, so heavy-tailed P&L distributions are
  correctly handled. Existing slice_lookup rows persisted before
  migration 018 (any row before 2026-05-07) have `bootstrap_p IS NULL`
  in the DB and continue to use the Gaussian fallback — back-compat
  is intact.
- **Live verification**: 15 smoke suites — **742/742 passing** (was
  723, +19 new). DB round-trip uses a unique
  `_pass5_8_test_roundtrip` slice_definition tag with cleanup; no
  pollution of real session data.

### Tier B — items #9 + #10 — engine integration (dedup view + exit slippage)

- **Status**: fixed (commit 5 of the Pass 5 plan; bundles two related
  engine-layer fixes).
- **Source**: `review/PASS5_AUDIT.md` items #9 (Critical) and
  #10 (Critical). Bundled because both are engine-layer fixes
  in `app/services/backtest_engine.py` and the smoke for #9 is the
  engine-consumer integration test that the migration-only smoke
  in commit 1 didn't cover.

**#9 — dedup view skips unavailable first-fires**

- The migration (019) was applied in commit 1 (Tier A) and the view
  is fully fixed structurally. This commit adds the engine-layer
  integration test that proves `_fetch_signals(dedup=True)` returns
  the clean row from a (cid, direction) pair where an earlier fire
  had `signal_entry_source='unavailable'`. Three call paths verified:
  - `dedup=True`: view filters before DISTINCT ON; engine sees clean.
  - `dedup=False, include_pre_fix=False`: view irrelevant; the
    engine's own `WHERE signal_entry_source != 'unavailable'` filter
    on `signal_log` is load-bearing and drops the unavailable row.
  - `dedup=False, include_pre_fix=True`: filter disabled; both rows
    visible.
- **Engine code change**: none. The redundant
  `signal_entry_source != 'unavailable'` filter in `_fetch_signals`
  (`backtest_engine.py:671`) is left in place — it's a no-op on the
  dedup path but load-bearing on the non-dedup path. Removing it
  would have complicated the conditional for no real win.

**#10 — symmetric exit-side slippage in compute_pnl_per_dollar_exit**

- `app/services/backtest_engine.py:469-525` (`compute_pnl_per_dollar_exit`)
  now applies the slippage symmetrically:
  - `effective_entry = min(0.999, entry_price + slip)` (unchanged)
  - **`effective_exit = max(0.001, exit_bid_price - slip)` (NEW)**
  - Revenue is `effective_exit / effective_entry` (was raw
    `exit_bid_price / effective_entry`).
  - `exit_fee` runs over `effective_exit` (the price actually
    received) — was `exit_bid_price` (the displayed bid).
- The clamp `max(0.001, ...)` prevents division-blowup when the slip
  exceeds the bid (extreme-illiquidity edge case; tested explicitly).
- Resolution-path P&L (`compute_pnl_per_dollar`, used when a market
  has settled at $1/$0) is unaffected — settlement is at fixed $1,
  not on a book.

**Behavioral change on plan's worked examples (`Politics`, rate=0.04):**

| Scenario | trade | liquidity | entry | exit_bid | Pre-fix P&L | Post-fix P&L | Diff |
|---|---|---|---|---|---|---|---|
| Thick book | $100 | $50k | 0.40 | 0.55 | 0.32328 | 0.32104 | -0.00223 |
| Thin book | $100 | $5k | 0.40 | 0.55 | 0.31689 | 0.30984 | -0.00705 |

The post-fix P&L is always lower than pre-fix (slippage on the way
out is real cost we weren't counting). On a $100 trade per signal,
the thick-book bias is ~$0.22; thin-book is ~$0.71. Aggregated
across hundreds of backtest signals, the bias adds up.

- **Files**:
  - `app/services/backtest_engine.py` — `compute_pnl_per_dollar_exit`
    rewritten with `effective_exit` + post-slippage fee curve.
  - `scripts/smoke_phase_pass5_engine.py` — 20 new tests:
    code-shape regressions (effective_exit + post-slippage fee
    used; resolution path doesn't reference effective_exit), the
    two plan worked examples (thick + thin book; analytical
    expectations within 1e-6 tolerance; pre/post diff matches plan
    targets ~0.0022 and ~0.007), invalid-input regressions
    (entry<=0, entry>=1, exit<=0 → None), the `max(0.001, ...)`
    clamp on extreme illiquidity, resolution-path determinism, and
    the #9 engine-consumer integration test (4 sub-checks across
    view, dedup-path engine, non-dedup-path engine, include_pre_fix
    restoration).
- **Live verification**: 16 smoke suites — **762/762 passing** (was
  742, +20 new). #9 fixture inserts two synthetic
  `mode='__pass5_9_engine_test'` signal_log rows referencing a real
  open binary market `condition_id`, then cleans up by mode tag.

### Tier C — item #14 — `markets.closed` and `events.closed` monotonic

- **Status**: fixed (commit 6 of the Pass 5 plan).
- **Source**: `review/PASS5_AUDIT.md` item #14 (Critical). F18
  acknowledged the risk but didn't address it.
- **Files**:
  - `app/db/crud.py` — `upsert_market` and `upsert_event` ON CONFLICT
    clauses changed `closed = EXCLUDED.closed` to
    `closed = (markets.closed OR EXCLUDED.closed)` /
    `closed = (events.closed OR EXCLUDED.closed)`. Two-line behavior
    change; surrounding code left intact.
  - `scripts/smoke_phase_pass5_closed_monotonic.py` — 13 new tests:
    code-shape regression (both upserts use the OR-merge pattern;
    bare `EXCLUDED.closed` is gone for the closed column), forward
    direction (`false → true` legitimate close still works for both
    markets and events), and the actual fix (re-upserting with
    `closed=false` after a `closed=true` row exists does NOT flip
    back). Uses unique synthetic `condition_id` and `event_id`
    (`__pass5_14_test_event__`) for clean tear-down.
- **Behavioral change**: a transient gamma response with
  `closed=false` (stale cache during a reorg, brief blip while
  resolving disputes) used to flip a closed=true row back to false.
  signal_detector filters `WHERE m.closed = FALSE`, so this would
  re-admit a resolved market into the live signal pool until the
  next sync corrected it. Post-fix the flip is impossible by SQL
  invariant — once true, stays true.
- **Reverse-flip risk explicitly accepted**: in the rare case gamma
  incorrectly flags a still-live market as `closed=true`, the manual
  recovery is one SQL — `UPDATE markets SET closed = FALSE WHERE
  condition_id = '...'` (or `events`/`id`). Documented in inline SQL
  comments at both call sites for operator discoverability.
- **Live verification**: 17 smoke suites — **775/775 passing** (was
  762, +13 new).

### Tier C — items #6 + #16 — operational visibility

- **Status**: fixed (commit 7 of the Pass 5 plan; bundles two
  related operational fixes that share `/system/status`).
- **Source**: `review/PASS5_AUDIT.md` items #6 (High) + #16 (High).

**#6 — `trader_category_stats` freshness gate**

- The recency filter in trader_ranker drops every wallet whose overall
  `last_trade_at` is >RECENCY_MAX_DAYS old. If the nightly trader-stats
  job dies (02:30 UTC), every row's `last_trade_at` ages past the
  threshold and the ranker silently returns `[]`. F25's 72h
  `signals_health` would catch "no signals" but couldn't distinguish
  a quiet weekend from a dead pipe.
- **Files**:
  - `app/db/crud.py` — new `get_stats_freshness(conn) -> {seeded,
    fresh, last_refresh}`. Single-row SELECT off
    `trader_category_stats`. `STATS_FRESHNESS_MAX_DAYS = 7`.
    Bootstrap-safe: not seeded → trivially fresh.
  - `app/services/trader_ranker.py` — `stats_fresh` CTE added
    alongside `stats_seeded` in all 4 ranker SQL sites
    (`_rank_absolute`, `_rank_hybrid`, `_rank_specialist`,
    `gather_union_top_n_wallets`). Recency-filter clauses now
    bypass on `NOT stats_seeded.has_data OR NOT stats_fresh.is_fresh
    OR <recency clause>`.
    New `_record_stats_staleness_if_needed(conn)` helper called at the
    top of `rank_traders` and `gather_union_top_n_wallets`. On stale
    state, ticks `STATS_STALE` health counter + emits a WARN log.
    Best-effort: probe failures don't block the ranker (the SQL gate
    is the actual fix; this is observability).
  - `app/services/health_counters.py` — `STATS_STALE` constant + 1h
    retention + included in `snapshot()`.
- **Behavioral change**: when the nightly stats job stops running,
  the rankers continue to return results (recency filter bypasses
  past 7 days of staleness) AND the operator sees a `STATS_STALE`
  counter ticking on `/system/status`. Before this fix, the rankers
  silently returned `[]` and the only signal was "no signals fired"
  three days later.

**#16 — `snapshot_runs` completeness ledger**

- Migration 020 (Tier A) added the table; this commit wires producers
  + consumers.
- **Files**:
  - `app/db/crud.py` — three new helpers:
    `insert_snapshot_run(conn, snapshot_date, started_at, completed_at,
    total_combos, succeeded_combos, failed_combos, failures,
    duration_seconds)` (UPSERT on `snapshot_date` PK so re-runs
    overwrite); `latest_snapshot_run(conn)` returning the most-recent
    row by `completed_at`; `latest_complete_snapshot_date(conn)`
    returning the most-recent date where `failed_combos = 0`.
  - `app/scheduler/jobs.py` — `daily_leaderboard_snapshot` now
    captures `completed_at` and calls `crud.insert_snapshot_run` at
    the end of the run with the actual results. Best-effort: a
    ledger-write failure logs a warning but doesn't invalidate the
    snapshot rows already committed in `leaderboard_snapshots`.
  - `app/api/routes/system.py` — `/system/status.daily_snapshot` gains
    `latest_run` (snapshot_date, complete bool, total_combos,
    succeeded_combos, failed_combos, duration_seconds, completed_at)
    and `last_complete_date`. `counters` gains
    `stats_stale_last_hour`.
- **Behavioral change**: the operator now sees in real time whether
  the latest snapshot run was complete. Downstream readers that need
  strict completeness can gate on
  `crud.latest_complete_snapshot_date()` instead of
  `MAX(snapshot_date)` to avoid mixing today's incomplete partial
  with yesterday's full data.

**Tests**:

- `scripts/smoke_phase_pass5_ops_visibility.py` — 36 new tests:
  - Code-shape regressions: `stats_fresh` CTE in all 4 SQL sites,
    `STATS_STALE` constant + counter inclusion, all 4 crud helpers
    present, `STATS_FRESHNESS_MAX_DAYS = 7`, jobs hook calls
    `crud.insert_snapshot_run`, `/system/status` surfaces
    `latest_run` + `last_complete_date` + `stats_freshness`.
  - `get_stats_freshness` round-trip: empty path (seeded=False, fresh
    trivially True), synthetic NOW() seed (seeded=True, fresh=True),
    synthetic 14-days-ago seed (when only seed row, seeded=True,
    fresh=False).
  - `_record_stats_staleness_if_needed` with monkey-patched freshness:
    stale → counter ticks; fresh → no tick; not seeded → no tick.
  - `insert_snapshot_run` round-trip + UPSERT idempotence; jsonb
    failures payload preserved; `latest_complete_snapshot_date`
    skips runs with `failed_combos > 0` even when they're more
    recent.
- All 18 smoke suites pass: **811/811** (was 775, +36 new).
