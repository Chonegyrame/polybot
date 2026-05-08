# Session State

> Updated each session. Read this first when resuming work.
>
> **For full per-commit behavioral detail (problem statement, fix summary,
> files changed, scope deviations, smoke deltas), also read `review/FIXES.md`.**
> This file is the orientation; FIXES.md is the source of truth for what
> each commit actually did and why.

**Pass 5 CLOSED (2026-05-08). All 16 plan items + 1 new endpoint shipped across 11 commits on `main` (`5f7e81b`..`b38f9aa`); plus the pre-plan rate-limiter fix in `ad44c26`. 921 smoke tests across 22 suites, all green. 20 migrations applied to live Supabase (001-013, 015-020). Pass 3 + Pass 4 + Pass 5 all complete. The system is at the "ready for UI build" milestone per UI-SPEC.md.**

---

## Tomorrow's first task — build the trading-strategy scrutinizer skill

Before anything else, the user is building a custom Claude Code skill — a
**post-Pass-5 trading-strategy code scrutinizer** — to break out of the
audit-treadmill loop. Five prior audit passes each returned 17-25 findings
with several "Critical" labels, but open-ended "review this code for issues"
prompts will always produce a list. The fix is a single purpose-built
skill with a strict bar that allows "no findings" as a valid result.

**Refine the prompt with the user; don't pre-draft it solo.** The aspects
to discuss together (also captured at
`memory/project_post_pass5_scrutinizer_skill.md`):

1. **Strict-confidence prompt structure.** Only surface findings where the
   reviewer has >70% confidence the issue is real and material — would lose
   money or miss a real signal in the next 30 days. Stylistic concerns,
   refactoring opportunities, and future-V2 architecture get explicitly
   excluded. Empty findings list is a valid output (and expected for
   a healthy system).

2. **Multi-agent consensus pattern.** Run 3-5 review agents in parallel
   with the same strict prompt; only act on findings flagged by ≥2 agents.
   This filters out the audit-noise where a single agent latches onto a
   stylistic concern the others ignore.

3. **Trading-specific review lens.** Domain-bound the reviewer to
   trading-logic concerns:
   - Fee math correctness (per-category rates, share-vs-dollar formulas)
   - Slippage symmetry (entry vs exit, resolution path)
   - Signal/exit threshold alignment (TRIM/EXIT/cohort recompute)
   - Fill realism (book depth, latency, spread)
   - Survivorship and selection bias (top-N reconstructed pre-fix)
   - Multiple-testing hygiene (BH-FDR, bootstrap p, session window)
   - Data integrity at the API seam (zombie filter, paginator shape errors)

4. **Output format that respects the bar.** Each finding must include:
   confidence %, money-loss/missed-signal mechanism, repro scenario,
   concrete fix sketch. No severity labels (Critical/High/Medium) — the
   strict prompt makes them all "actionable" or they don't pass the bar.

5. **Scope-bounded.** The skill explicitly does NOT review:
   - Code style / formatting / naming
   - Test coverage gaps (unless they hide a logic bug)
   - Architectural refactor opportunities
   - V2 features (multi-process, Redis, alternate venues)
   - Anything the reviewer can't tie to a money or signal-quality
     consequence within 30 days

**Falsifiable "done" condition:** the user ships the UI build only after
running this skill on the post-Pass-5 codebase and getting a finding count
of 0-2 (with full agent consensus on each). That's the exit criterion that
breaks the audit loop — currently no audit has had a "done" gate, which is
why every pass produces another list.

**Where to put it:** TBD with the user. Options: project `.claude/`
skill (lives with the repo, version-controlled with the codebase), or
user-global `~/.claude/` (reusable across other trading projects).

---

## Pass 5 closure summary (2026-05-08)

**Commits (11):**

| Commit | Tier | Items | Smoke |
|---|---|---|---|
| `5f7e81b` | A | Migrations 018, 019, 020 | 623 → 657 |
| `668ae70` | B | #1 + #2 + #5 cluster-collapse family | 657 → 706 |
| `3f8b558` | B | #3 specialist Bayesian prior | 706 → 723 |
| `e5b4d0d` | B | #8 bootstrap_p persistence | 723 → 742 |
| `43284fa` | B | #9 + #10 engine integration | 742 → 762 |
| `95629fe` | C | #14 closed monotonic | 762 → 775 |
| `d482f38` | C | #6 + #16 ops visibility | 775 → 811 |
| `8ce1b0f` | C | #17 zombie filter incomplete-metadata | 811 → 831 |
| `8566f8e` | D | #4 + #11 + #12 + #13 math correctness | 831 → 862 |
| `5730542` | E | #18 iter_trades fail-loud | 862 → 877 |
| `b38f9aa` | E | `GET /signals/{id}/contributors` endpoint | 877 → 921 |

**Plus** `ad44c26` (Pass 5 R17 rate-limiter consolidation, shipped before the plan was written; covered as audit item #15).

**Audit decisions:**
- **#7** specialist `active_recently` strictness: dropped per user (finding judged too weak).
- **#19** dead `get_market_trades` deletion: dropped (off critical path; can be one-line cleanup anytime).
- **#1+#2+#5** bundled into one cluster-collapse commit per the plan.
- **#9+#10** bundled because both are engine-layer fixes; #9's structural fix shipped in commit 1's migration 019.
- **#6+#16** bundled because both are operational-visibility fixes that share `/system/status` surface area.
- **#4+#11+#12+#13** bundled per the plan ("math correctness bundle, all small independent changes").

**Smoke baseline:** 579 (pre-Pass-5) → **921 across 22 suites**.

**Honest call-outs documented in `review/FIXES.md`:**
- Audit #1 framing overstated the magnitude — `aggregate_usdc` and `total_dollars_in_market` were already correct sums; real material wins are per-entity `avg_portfolio_fraction` (#1), cluster-aware counterparty (#2), exit_detector SUM/COUNT alignment (#5).
- Audit #13 quantitative claim (0.59 → 0.65) overstated — actual shift is ~0.01 because cluster-bootstrap-of-mean is unbiased for the population mean in expectation. Structural fix is correct; magnitude is modest.
- Migration 019 column list kept verbatim from migration 007 instead of adding the speculative Pass 3+ columns the plan sketched (those would have broken the engine's existing reads).
- `_rank_specialist`'s `prior_pool` keeps the contamination-exclusion clause despite the plan's suggestion to drop it (including MM/arb/sybils would deflate the baseline).

**Where to resume:**

System is production-ready for the third-party UI build per `UI-SPEC.md`. The new `GET /signals/{signal_log_id}/contributors` endpoint is the last backend deliverable — UI Section 2's expandable signal panel can now be built. Optional next steps:

1. **Run a fresh-session audit** with the strict-confidence prompt (see project memory `project_post_pass5_scrutinizer_skill.md`) — refine the scrutinizer skill once and use its output as the gate for shipping the UI.
2. **Live cycle dry-run** to confirm steady-state cadence after Pass 5: `./venv/Scripts/python.exe scripts/run_cycle_once.py`. Pre-Pass-5 baseline was 3.3 min/cycle; Pass 5 added a few SQL CTEs but should not regress >50%.
3. **Phase 8 data wipe** (still optional). Pass 4's zombie filter + `upsert_positions_for_trader`'s DELETE-stale clause keep the system clean without it.
4. **Begin UI build** per `UI-SPEC.md` against the now-frozen API surface.

---

## Pass 5 R17 — rate-limiter consolidation (2026-05-07)

**Critical-class fix from `review/PASS5_AUDIT.md` finding #15. Shipped first because it is structurally independent from the other 16 audit items (none of which touch the API client) and stabilizes the network layer all subsequent live-cycle verification depends on.**

### Problem

`TokenBucket` was created fresh in every `PolymarketClient.__init__`. With 12 distinct `async with PolymarketClient()` call sites and APScheduler running concurrent jobs without cross-job locks (e.g. `record_signal_price_snapshots` + `refresh_and_log` both on 10-min crons), the effective outgoing rate to Polymarket was 2-5× the configured 10/s. Tenacity retries on the resulting 429s amplified the burn — the local bucket refilled while sibling clients kept firing at full rate, extending the 429 window indefinitely.

### Fix

1. **Module-level `_BUCKETS` registry** in `rate_limiter.py` keyed by hostname. `get_bucket(host, rate)` is lazy + first-write-wins.
2. **Per-host scoping** (data-api / gamma-api / clob get separate buckets) so one slow host doesn't starve callers of another.
3. **Lazy lock binding** in `TokenBucket` — `asyncio.Lock` is created on first `acquire()` and rebound if the event loop changes (test-safe across multiple loops).
4. **`PolymarketClient._bucket_for(url)`** uses the registry by default; the `rate_limit_per_second` constructor arg becomes a per-instance override (private bucket) for tests.
5. **`_DecorrelatedJitterWait`** replaces `wait_exponential` — AWS-recommended `min(cap, uniform(base, prev*3))`. Tighter p99, desynchronizes concurrent retries at the same boundary.
6. **`Retry-After` honoring** — on 429, `_parse_retry_after` extracts seconds (numeric or HTTP-date, capped 60s) and stashes on the exception; `_DecorrelatedJitterWait` uses it instead of jitter on the next retry attempt.
7. **Default rate lowered 10.0 → 8.0** in `app/config.py` so retries have 20% headroom inside Polymarket's per-IP ceiling. Env-var `RATE_LIMIT_PER_SECOND` overrides as before.

### Files changed

| File | Change | LOC |
|---|---|---|
| `app/services/rate_limiter.py` | Full rewrite — added `_BUCKETS` registry, `get_bucket(host, rate)`, `host_for_url(url)`, `reset_buckets()`. `TokenBucket` keeps lazy lock + loop rebinding. | ±90 |
| `app/services/polymarket.py` | Dropped `self._limiter`. Added `_bucket_for(url)`, `_parse_retry_after()`, `_DecorrelatedJitterWait` class, Retry-After stashing on `HTTPStatusError`. | ±90 |
| `app/config.py` | `rate_limit_per_second` default 10.0 → 8.0. | 1 |
| `scripts/smoke_phase_pass5_rate_limiter.py` | New file — 44 tests covering host extraction, registry sharing, per-host scoping, reset_buckets, lazy lock binding, pacing integration, multi-loop safety, client registry path, override path, Retry-After parsing, jitter formula bounds + Retry-After precedence, code-shape regressions. | +470 |

### Verification

- **All 11 smoke suites pass: 623/623, 0 failures.** Counts: phase_a 51, phase_a2 56, phase_a3 27, phase_b1 28, phase_b2 115, phase_b56 33, phase_b78 87, pass2_routes 16, pass3_helpers 76, pass3_fixes 90, pass5_rate_limiter 44.
- **Live probe (`scripts/probe_polymarket_endpoints.py`) clean** — all 11 sections complete, 5 rapid CLOB book calls all 200 OK with no 429s, JSON shapes intact across data-api / gamma-api / clob.

### Production impact

- Effective outgoing rate is now structurally bounded at 8 r/s **per host** regardless of how many `PolymarketClient` instances are alive concurrently. Cron-overlap and manual-script-races no longer multiply the rate.
- Retries can no longer cascade because the shared bucket is a single chokepoint — a 429 paces the *global* outflow, not just one local caller.
- When Polymarket sends `Retry-After`, the client honors it. When they don't, decorrelated jitter prevents synchronized-retry thundering herds.

### What remains from Pass 5 audit

16 open findings + UI doc updates — see `review/PASS5_AUDIT.md` for the full priority-ordered list. Tier 0 critical: #1 sybil cluster wash inflation, #2 counterparty cluster dedup, #3 specialist winners-only prior, #8 bootstrap_p column missing, #9 dedup view excludes unavailable first-fires, #10 exit-side slippage missing.

---

---

## Where to resume — fresh-session audit, then choose Phase 8 (now optional) or UI

Stable checkpoint. Status:

- ✅ **Code**: all Pass 3 + Pass 4 fixes implemented + tested. 9 commits on `pass3-hardening`, all pushed to https://github.com/Chonegyrame/polybot
- ✅ **Migrations**: 010-013, 015, 016, 017 all applied to live Supabase
- ✅ **Smoke tests**: 579/579 passing across 10 suites (437 baseline + ~113 Pass 3 + 29 Pass 4)
- ✅ **Live cycle dry-run**: verified twice on 2026-05-07. Pre-Pass-4: 24.5 min, 36k positions written, 4 new signals fired, all Pass 3 columns populated. Post-Pass-4: 3.3 min, 8.5k positions, same correctness, comfortably under 9-min cadence threshold.
- ✅ **Phase 7 verification**: writes confirmed working live. The previous session's "exit 0 / no writes" finding was a transient (presumed killed before reaching write phase, or a one-off API/DB hiccup) — reproduced clean today.
- ⏳ **Phase 8 (now OPTIONAL)**: wipe DB tables + first clean cycle. Pass 4 zombie filter prevents new resolved-market accumulation, and `upsert_positions_for_trader`'s DELETE-stale clause self-cleans existing zombie position rows. The remaining bloat is 21,663 inert resolved-market metadata rows in the `markets` table — `signal_detector` already filters them via `m.closed = FALSE`, so they don't poison signals. **Wipe is tidiness, not necessity.**
- ⏳ **Step 10**: UI build per UI-SPEC.md
- ⏳ **Step 11+**: Railway deploy

### To resume the next session

1. **Checkout the right branch**: `git checkout pass3-hardening`
2. **Verify smoke tests**: run all 10 suites. Commands in "Verification" section below. Expected: 579/579, 0 failed.
3. **Re-audit option**: spawn parallel review agents for an independent check (the 4-domain split worked well for Pass 1+2). Anything flagged should map to either a known fix in `review/FIXES.md` or a genuinely new finding.
4. **Decide on Phase 8 wipe**:
   - If wiping: `scripts/wipe_database.py` does NOT exist yet. Plan: hardcoded TABLES_TO_WIPE list, hardcoded TABLES_TO_PRESERVE (`_migrations`, `insider_wallets`), single-transaction TRUNCATE with RESTART IDENTITY CASCADE, post-wipe schema verification (rolls back if `_migrations` is empty). Confirmation prompt: must type `WIPE` literally.
   - If skipping: go straight to Step 10 (UI build) — system is production-ready as-is.

### Phase 7 dry-run mystery — RESOLVED (2026-05-07)

A previous version of this file flagged a SUSPICIOUS outcome: the prior session's `run_cycle_once.py` reported exit 0 after ~22 min but no DB writes appeared (timestamps stuck on 2026-05-05, all new Pass 3 columns empty). **Investigated and resolved 2026-05-07:**

- 12:10 UTC re-run of the same cycle (no code changes): completed in 24.5 min (1490s), wrote 36,236 positions across 486/486 wallets, fired 4 new signals, populated all Pass 3 columns correctly:
  - `signal_log.contributing_wallets` non-null: 4 (matches 4 new signals)
  - `signal_log.first_net_dollar_skew` non-null: 4
  - `watchlist_signals.dollar_skew` non-null: 344
  - `traders.dropout_count > 0`: 1,164 (matches log: "phase 4: dropout counters: 1164 incremented")
- The four "suspect failure modes" in the prior note (R12 `all_fresh` refactor, `update_wallet_dropout_counters`, `_capture_book_for_signal` pool change, `make_interval(mins => $1)`) all check out clean on inspection. The R12/R13/R8/R2 code paths work as designed.
- Most likely explanation for the original failure: the cycle was killed by an outside actor (sleep/system) before the write phase completed, or a transient API timeout that the per-wallet `try/except` swallowed. Not reproducible.

The 24.5-min runtime did expose the REAL bottleneck: Polymarket's `/positions` endpoint returns ~25k cids per cycle (mostly resolved-unredeemed zombie positions), forcing Phase 2 to fetch gamma metadata for ~21k zombie markets. **Solved by Pass 4 below.**

---

## Pass 4 — zombie/dust position filter (2026-05-07, commit `8ad8279`)

**One-shot fix shipped today. Cycle time 24.5 min → 3.3 min. Markets table stopped accumulating zombies. Production-ready cadence.**

### Problem

Polymarket's `/positions?user=` endpoint includes resolved-unredeemed positions indefinitely — every market a trader has ever held a position in and not manually clicked Redeem. Top traders carry hundreds across years of activity. Across 486 wallets, deduped → ~25k distinct condition_ids per cycle, of which ~21k are zombies. Phase 2 spent ~15 min/cycle fetching gamma metadata for these dead markets, blowing the 9-min cadence threshold ("10-min cycle took 1490.3s -- pipeline falling behind cadence").

### Fix

Multi-signal filter at the API client seam (`PolymarketClient.get_positions`). `Position.drop_reason()` returns first matching reason (priority order matters for counter attribution):

| Reason | Predicate | Justification |
|---|---|---|
| `redeemable` | `self.redeemable is True` | Polymarket's authoritative resolved+claimable flag; primary signal |
| `market_closed` | `raw['closed'] is True` | Embedded market record says no longer trading; catches resolved markets where `redeemable` hasn't propagated yet |
| `dust_size` | `0 < size <= 1.0` | At max $1/share × 1 share = $1 max value; never a smart-money signal |
| `resolved_price_past` | `cur_price ∈ {0.0, 1.0}` AND `end_date < now()` | Defensive backup if `redeemable` ever drifts. AND-clause prevents false-positive on live markets temporarily pricing at extremes |

Defaults to `include_resolved=False`. Diagnostic scripts opt-in to raw via `include_resolved=True` (no consumer in production code uses this). Per-reason health counters (24h retention) exposed at `/system/status.counters.zombie_drops_last_24h`. Fail-open default for missing `redeemable` field.

### Files changed (all in commit `8ad8279`)

| File | Change | LOC |
|---|---|---|
| `app/services/polymarket_types.py` | +`DUST_SIZE_THRESHOLD`, +`RESOLVED_PRICES`, +`Position.redeemable` field, +`drop_reason()` method, +`_end_date_in_past()` helper | +74 |
| `app/services/polymarket.py` | `get_positions(...,include_resolved=False)`: filter via `drop_reason()`, record per-reason counters, defensive fail-open on unknown reason labels | +72 |
| `app/services/health_counters.py` | +4 zombie-drop counter constants, +24h retention entries, +snapshot extension | +24 |
| `app/api/routes/system.py` | Surface zombie counters in `/system/status.counters.zombie_drops_last_24h` (per-reason + total) | +15 |
| `scripts/smoke_phase_pass3_helpers.py` | +29 new tests (parsing, predicate priority, `_end_date_in_past` edge cases, integration with mock client, `include_resolved` opt-out, per-reason counter recording) | +214 |

### Verification (2026-05-07)

- Smoke suite: **579/579, 0 regressions** across all 10 files (was 437 baseline + ~113 Pass 3 + 29 Pass 4)
- Live cycle (post-Pass-4): **197.7s, 485/486 wallets, 8463 positions persisted, 59 new markets discovered** (down from 21,931 pre-fix). `markets_closed` count held at exactly 21,663 — confirmed zero new zombies entering DB.
- `upsert_positions_for_trader`'s DELETE-stale clause auto-swept ~17,500 existing zombie position rows on the same cycle. **No migration needed** for the cleanup.

### Self-healing properties

- Filter at API seam → every consumer (signal detector, market sync, paper trades, future code) inherits without duplication.
- Failed-open default for missing `redeemable` field → other 3 predicates serve as safety net; per-reason counter shifts flag upstream API drift (e.g., if `redeemable` bucket suddenly drops to ~0 with the others unchanged, Polymarket has likely renamed the field).
- Defensive `if reason not in counter map`: keeps the position and logs loudly rather than silently dropping (visible at `polymarket.py:get_positions`).

### What the wipe still cleans (if you choose Phase 8)

After Pass 4, only one DB-bloat artifact remains: the 21,663 inert resolved-market rows in the `markets` table. They don't affect signals (`signal_detector` filters `m.closed = FALSE`) but they bloat the row count. Phase 8 wipe would purge them. Trade-off: wipe also nukes the 24 historical signals + paper trade history.

---

## Pass 3 fixes shipped (this session)

**Schema migrations applied to live Supabase:**
- 010: relax signal_price_snapshots CHECK to allow offsets 5/15 (R1)
- 011: signal_log.contributing_wallets TEXT[] for cohort-aware exits (R3b)
- 012: signal_price_snapshots.direction for direction-side token (R8)
- 013: signal_exits.event_type ('trim'/'exit') two-tier (R3a)
- 015: signal_log.first_net_dollar_skew + watchlist_signals.dollar_skew (R2)
- 016: signal_log.counterparty_count INT (R4+R7)
- 017: traders.dropout_count INT for R13 grace period
- (no 014 — gap from initial plan)

**Tier 0 fixes (10):**
- R1: snapshot offsets 5+15 now actually persist (migration + verification)
- R2: signal eligibility requires BOTH count-skew AND dollar-skew >= 65%
- R3a: TRIM/EXIT two-tier exit detector (TRIM = 20-50% drop, EXIT = >=50%)
- R3b: exit detector recomputes against historical contributing_wallets cohort
- R3c: removed F18 activity guard; signal monitored as long as market open + cohort still positioned
- R4 + R7: counterparty rewritten positions-based (not fills); requires >=$5k AND >=75% concentration
- R5: signal_detector latest_pv requires fetched_at >= NOW() - 1h; jobs always writes PV (even when 0)
- R6: orderbook.compute_book_metrics rejects crossed/locked books
- R8: signal_price_snapshots use direction-side token; half_life math direction-aware
- R9: get_session_slice_lookups dedupes by slice_definition (no Bonferroni inflation from refresh)
- R10: unified close P&L formula in app/services/paper_trade_close.py used by manual + auto-resolved + smart-money-exit paths

**Tier 1 fixes (5):**
- R11: Hybrid ranker tiebreaker uses roi_rank (not pnl DESC whale bias)
- R12: log_signals refactored to release DB conn during HTTP (Phase 1/2 split with all_fresh)
- R13: 3-cycle (30 min) dropout grace via traders.dropout_count before sweeping positions
- R14: cleanup_watchlist_promoted_to_signal + upsert_watchlist_signal both scoped to last 24h
- R16: covered by R3b cohort recompute using 30-min TTL

**Design fixes (3):**
- D1: app/services/fees.py with correct Polymarket formula (stake × rate × (1-price)) per real per-category rates. Old TAKER_FEES dict (Politics 0%, Crypto 1.8% etc.) deleted; backtest engine + paper trade open + all close paths now use compute_taker_fee_usdc.
- D3: compute_kish_n_eff used for n_eff in CIs + underpowered flag (was distinct-cluster count, overstated power on heterogeneous clusters)
- D5: app/services/health_counters.py thread-safe in-memory counters; /system/status exposes rate_limit_hits_last_hour, cycle_duration_warnings_last_24h, api_failures_last_hour
- D4: edge decay rolling 3-vs-3 with min 20% drop threshold + 6-week minimum history (was recent-3-vs-all-time, 4-week min)

**New foundation helpers:**
- app/services/fees.py — single source of truth for Polymarket fee math
- app/services/paper_trade_close.py — unified close formula used by all 3 close paths
- app/services/health_counters.py — operational counters for /system/status
- app/services/counterparty.py — fully rewritten positions-based with concentration threshold
- backtest_engine.compute_kish_n_eff — Kish effective sample size

**New migration files:**
- migrations/010-013, 015-017 (7 new migrations)

**Smoke test additions:**
- scripts/smoke_phase_pass3_helpers.py: 47 tests for fees + Kish + ResponseShapeError
- scripts/smoke_phase_pass3_fixes.py: 90 tests for all R-class + D-class fixes
- Existing suites updated for breaking changes (b1 _classify_drop tuple return, b2 counterparty rewrite, a2 F18 -> R3c repurpose)

---

## After Pass 3 → before UI build

The Phase 8 wipe + Phase 9 first cycle items are still TODO. The remaining production caveats from Pass 1+2 are now obsolete because the wipe will eliminate all data they apply to.

**Pre-Pass-3 caveats now obsolete (will be cleared by Phase 8 wipe):**
- Pre-Pass-2 counterparty_warning rows (whole table about to be empty)
- Pre-F6 markets with inverted outcomes (markets table being wiped + JIT will re-discover with R8 + correct token mapping)
- Pre-F3 portfolio_value_snapshots biased high (whole table empty)
- Pre-F4 signal_price_snapshots bid-only (whole table empty)
- Pre-F21 slice_lookups Gaussian-from-CI p (whole table empty)

After Phase 8, the only production caveat to know about is: cluster-bootstrap CIs assume cluster-of-clusters resampling produces an unbiased mean estimator on heterogeneous cluster sizes. With Kish n_eff (D3) the "underpowered" flag is now honest, so the user will see the warning when applicable.

---

---

## What was done — Pass 1 + Pass 2 summary

**Context**: After Phase B closed, the user requested a thorough code-quality review before going live. We spawned 4 parallel review agents covering ingestion, signal logic, backtest/stats, and orchestration. They surfaced 26 findings (11 Tier-1 edge-corrupting + 15 Tier-2 operational). Two passes of fixes followed, each with the same rigor: read code → verify finding → write failing test → apply minimal fix → verify test passes → diff-review by an independent agent.

### Pass 1 (5 fixes — math + side bugs)
| ID | What was wrong | Fix |
|---|---|---|
| F1 | Bayesian shrinkage used dollar-pnl as ROI prior; tiny-volume traders ranked #1 regardless of skill | Replaced `cat_avg_pnl = AVG(pnl)` with `prior_roi = SUM(pnl)/SUM(vol)` in 3 places (`trader_ranker.py`) |
| F2 | Counterparty check ignored fill side; flagged buyers as sellers | Added `_maker_was_seller` helper that derives side from fill metadata (later replaced — see Pass 2 F12) |
| F5 | Half-life math mixed YES-space + direction-space for NO signals; ~50% of half-life table garbage | Renamed helper to `_to_yes_space`, only translates direction-space inputs; snapshot stays YES-space |
| F6 | YES/NO token mapping assumed `clob_token_ids[0]==YES`; sports markets with `["No","Yes"]` order silently corrupted | New `pair_yes_no_tokens(outcomes, clob_token_ids)` helper pairs by label, returns `(None, None)` for non-binary |
| F8 | Win-rate Wilson CI used raw n, not cluster-effective n_eff; CIs too tight by √(n/n_eff) | Replaced Wilson call with `cluster_bootstrap_mean` on win indicators; clamped to [0,1] |

### Pass 2 (18 more fixes + 1 verified-rejection + probe + migration 009)
| ID | What was wrong | Fix |
|---|---|---|
| Probe | No live-API contract verification | New `scripts/probe_polymarket_endpoints.py` hits every endpoint, dumps shape, validates assumptions |
| **F12 + F2 redo** | **CLOB `/trades` requires API auth; B2 was silent no-op since shipping** | Switched to public `data-api/trades?market=<conditionId>`; rewrote counterparty rule with direction-aware `(outcome, side)` semantics |
| F13 | List-returning client methods silently coerced wrong-shape responses to `[]` | New `_safe_list_from_response` helper logs WARN on suspicious shape, used in 8 list-returning methods |
| F3 | Portfolio fraction denominator excluded USDC cash | `_fetch_one_wallet` now also calls `pm.get_portfolio_value`; phase 3 prefers API value over sum-of-positions |
| **F4 + F7** | **B4 captured only bid (vs ask entry); short latency profiles were no-ops** | **Migration 009** added `bid_price` + `ask_price`; offsets now `(120,60,30,15,5)`; cadence 30→10 min; `latency_unavailable` flag |
| F9 | Three different "tracked pool" depths in one cycle (50 vs 100); inconsistent recency filter | Counterparty bumped to 100; specialist ranker layers `last_trade_at` filter on top of monthly-leaderboard presence |
| F10 | Watchlist mutual exclusion was per-lens only; same market in both feeds across lenses | `upsert_watchlist_signal` skips when `signal_log` has a row; per-cycle `cleanup_watchlist_promoted_to_signal` |
| F11 | `/paper_trades?status=closed_exit` returned 400 | Whitelist extracted to `VALID_PAPER_TRADE_STATUSES` constant including `closed_exit` |
| F14 | Tenacity retried all 4xx errors burning 4× rate-limit on 400/401/404 | New `_should_retry` predicate retries TransportError + 429 + 5xx only |
| F15 | Custom-label binary resolutions returned None, silently excluded from backtest | Now returns "VOID" + WARN log so magnitude is visible |
| F16 | Position upserts were per-row N+1 (~7min for 530 wallets) | `upsert_positions_for_trader` uses `executemany` (~30s now) |
| F17 | `traders_any_direction` denominator counted multi-outcome rows; binary skew falsely dropped below 60% | Added `WHERE LOWER(outcome) IN ('yes', 'no')` to `market_totals` CTE |
| F18 | Exit detector used last-fire peak as permanent watermark; emitted stale exits 24h+ later | New `EXIT_ACTIVITY_GUARD_HOURS = 2`; window capped to 2h |
| F19 | (Audit concern) Storage convention worry on `gap_to_smart_money` | **Verified-rejection** — both inputs ARE direction-space; docstring updated |
| F20 | BH-FDR comment said "ties → lowest" but code did "ties → highest" | Comment aligned to code (matches statsmodels) |
| F21 | BH-FDR p-value reconstructed from CI via Gaussian SE; broken on skewed bootstrap CIs | New `cluster_bootstrap_mean_with_p` returns empirical 2-sided p; `BacktestResult.pnl_bootstrap_p` field; consumed by `compute_corrections` |
| F22 | Holdout date filter timezone-fragile (Postgres implicit cast on `date` vs `timestamptz`) | Wrapped in `datetime(y,m,d, tzinfo=timezone.utc)` before query |
| F23 | 16 inline SQL queries across 6 route files (CLAUDE.md rule violation) | 13 new crud helpers; routes refactored to call them; new regression smoke suite (`smoke_phase_pass2_routes.py`) |
| F24 | Daily snapshot held one DB connection across 28 sequential HTTP calls | Connection acquired per-combo inside the inner loop |
| F25 | `signals_health` flipped amber on quiet-market days (48h window) | `SIGNALS_AMBER_MAX_HOURS` 48 → 72 |
| F26 | JIT discovery silently dropped markets when event refetch glitched | Logs missing event_ids + affected market cids loudly |

---

## How testing worked (so a fresh agent can replicate the rigor)

**Test-first process for every fix:**
1. Read the actual code, verify the bug exists (catches agent-overreach findings)
2. Write a smoke test that demonstrates the buggy behavior with concrete inputs
3. Run the test against current code — must FAIL (proves it captures the bug)
4. Apply the minimal code fix
5. Run the test again — must PASS
6. Run the full smoke suite for the affected file — no regressions
7. Update FIXES.md with the diff summary + test name

**Test types used:**
- **Pure-function tests** for math/parsing/helpers (F1, F2, F5, F6, F8, F11, F13, F14, F15, F17, F20, F25 + most of F4/F7 + F12)
- **DB integration tests** for CRUD + queries (F1 ranker round-trip, F10 watchlist exclusion, F23 route response shape)
- **Source-inspection tests** for structural fixes (F3 phase-3 wiring, F9 specialist SQL, F16 executemany, F18 guard, F22 timezone)
- **Live-API contract test** for F12 — hits real Polymarket every smoke run, fails loudly if their API shape changes (regression net for future drift)

**Independent diff-review:** after each pass, an independent subagent compared FIXES.md claims against the actual code and verified all fixes. Pass 1: 5/5 ✅. Pass 2: 19/19 ✅.

---

## Verification — commands a fresh agent should run

**1. Smoke suite (all 8 files, expect 437 passing, 0 failed):**
```
./venv/Scripts/python.exe scripts/smoke_phase_a.py
./venv/Scripts/python.exe scripts/smoke_phase_a2.py
./venv/Scripts/python.exe scripts/smoke_phase_a3.py
./venv/Scripts/python.exe scripts/smoke_phase_b1.py
./venv/Scripts/python.exe scripts/smoke_phase_b2.py
./venv/Scripts/python.exe scripts/smoke_phase_b56.py
./venv/Scripts/python.exe scripts/smoke_phase_b78.py
./venv/Scripts/python.exe scripts/smoke_phase_pass2_routes.py
```

Expected counts:
- smoke_phase_a: 48
- smoke_phase_a2: 55
- smoke_phase_a3: 27
- smoke_phase_b1: 24
- smoke_phase_b2: 147
- smoke_phase_b56: 33
- smoke_phase_b78: 87
- smoke_phase_pass2_routes: 16
- **Total: 437**

**2. Live-API probe (verifies our endpoint assumptions still match Polymarket reality):**
```
./venv/Scripts/python.exe scripts/probe_polymarket_endpoints.py
```
Expected: completes without errors, shows valid responses for leaderboard/positions/value/markets/book/data-api-trades, and the manual checklist at the bottom matches expectations.

**3. Live cycle dry-run (optional but recommended — exercises the full pipeline end-to-end):**
```
./venv/Scripts/python.exe scripts/run_cycle_once.py
```
Expected: positions refresh → signals detected → watchlist updated → exits checked → paper trades auto-closed. Cycle should complete in well under 9 minutes (F16's executemany fix dropped this from ~7min to ~30s).

**4. API surface (start the server and curl a few routes):**
```
./venv/Scripts/python.exe scripts/run_api.py
# In another shell:
curl 'http://localhost:8000/system/status'
curl 'http://localhost:8000/traders/top?mode=hybrid&category=overall&top_n=10'
curl 'http://localhost:8000/signals/active?mode=hybrid&category=overall&top_n=50'
```

---

## How the original audit was structured (to replicate)

A fresh agent who wants to do an independent re-audit can follow the same approach that found the original 26 issues:

Spawn 4 parallel `general-purpose` subagents, each focused on one slice of the backend. The exact prompts used are reconstructable from `review/01_ingestion.md`, `02_signal_logic.md`, `03_backtest_stats.md`, and `04_orchestration.md` (each report opens with its scope). Briefly:

1. **Ingestion + API client**: `app/services/polymarket.py`, `polymarket_types.py`, `rate_limiter.py`, `market_sync.py`, `orderbook.py`, position-refresh + leaderboard-snapshot + JIT-discovery + B4-snapshot portions of `scheduler/jobs.py`
2. **Signal logic + ranking**: `signal_detector.py`, `trader_ranker.py`, `trader_stats.py`, `counterparty.py`, `exit_detector.py`, `sybil_detector.py`, `wallet_classifier.py`
3. **Backtest engine + stats**: `backtest_engine.py`, `half_life.py`
4. **DB + scheduler + API**: `db/connection.py`, `db/crud.py`, `scheduler/jobs.py` (orchestration), `scheduler/runner.py`, `api/main.py`, `api/deps.py`, all `api/routes/*.py`, `config.py`, `migrations/*.sql`

Each agent should produce findings prioritized as Critical/High/Medium/Low with file:line references. Cross-reference any new findings against `review/FIXES.md` — anything already there is pinned by a test.

---

## Production caveats (data quality notes carried forward)

These are honest limitations the user should know about:

1. **Pre-Pass-2 `counterparty_warning` rows are unreliable** — they were all `False` regardless of true state because the CLOB endpoint we called was returning 401. New rows fired after Pass 2 are honest.
2. **Pre-F6 `markets` rows with inverted outcomes** (e.g., sports markets with `["No","Yes"]` order) have swapped `clob_token_yes` / `clob_token_no` values. Re-sync via `scripts/run_market_sync.py` to clean up affected rows.
3. **Pre-F3 `portfolio_value_snapshots` rows are biased high** — they used sum-of-positions which excluded USDC cash. New rows use the `/value` endpoint and are honest.
4. **Pre-F4 `signal_price_snapshots` rows have only `bid_price` populated** (`ask_price IS NULL`), so half-life mid math falls back to bid-only for these rows. Going forward both are captured.
5. **`slice_lookups` rows pre-F21 use the Gaussian-from-CI p-value approximation**, not the empirical bootstrap p. Cross-session BH-FDR ranking still uses the approximation for these older entries; the current query's p is exact.
6. **`signal_log.counterparty_warning` for the new direction-aware logic** only starts producing meaningful values for signals fired AFTER Pass 2 was deployed. Treat older rows as if the column is NULL.

---

## Where to resume — UI build (Step 10)

Backend V1 is feature-complete and hardened. Step 10 (third-party UI build against the FastAPI surface, see UI-SPEC.md) and Step 11 (Railway deploy) are the only items left for V1 launch.

The notes below for Session 7 (and earlier) are preserved as reference for what shipped — they're the implementation briefs that were built out, not pending work. Pass 1 + Pass 2 details live in `review/FIXES.md`.

---

## Session 7 — shipped 2026-05-06 (all 6 items + smoke suite)

| Item | Files | Notes |
|---|---|---|
| migration 008 | `migrations/008_phase_b2.sql` | counterparty_warning column on signal_log + watchlist_signals + signal_price_snapshots + insider_wallets tables |
| **B2** counterparty | `app/services/counterparty.py` (new), `app/services/polymarket.py` (+`get_clob_trades`), `app/db/crud.py` (+`set_counterparty_warning`), `app/scheduler/jobs.py` (per-cycle pool gather + per-signal check), `app/api/routes/signals.py` (+counterparty_warning enrichment) | Non-blocking. Pool = `gather_union_top_n_wallets` once per cycle. CLOB /trades fetched per fresh signal; defensive across response-shape variations. |
| **B3** watchlist | `app/services/signal_detector.py` (`detect_signals_and_watchlist` + `SignalDetectionResult`), `app/db/crud.py` (+watchlist CRUD + cleanup), `app/scheduler/jobs.py` (folded into log_signals), `app/api/routes/watchlist.py` (new) | One DB pass yields BOTH official + watchlist (mutually exclusive). Watchlist rows do NOT trigger book capture. Per-lens cleanup deletes rows that fall below floors. |
| **B4** half-life | `app/services/half_life.py` (new), `app/db/crud.py` (+price-snapshot CRUD), `app/scheduler/jobs.py` (+`record_signal_price_snapshots`), `app/scheduler/runner.py` (+30-min interval job), `app/api/routes/backtest.py` (+/half_life endpoint) | Captures YES bid at +30/60/120 min after fire (±5 min tolerance). Convergence rate per category × offset; underpowered until n ≥ 30. |
| **B10** latency | `app/services/backtest_engine.py` (+latency fields on BacktestFilters, `_resolve_latency_window`, `_sampled_latency_minutes`, `_nearest_snapshot_offset`, `_apply_latency`; `backtest_with_rows` returns 3-tuple now), `app/api/routes/backtest.py` (+latency_profile/min/max query params on summary + slice) | Profiles: active 1–3, responsive 5–10, casual 12–20, delayed 30–60, custom. Deterministic per condition_id (sha256-seeded). Falls back to original signal_entry_offer when no snapshot is within ±5 min of sampled offset. |
| **B11** edge decay | `app/services/backtest_engine.py` (`compute_edge_decay`, EdgeDecayResult/Cohort), `app/api/routes/backtest.py` (+`/backtest/edge_decay` endpoint) | Groups signal rows by ISO-week of first_fired_at, runs `summarize_rows` per cohort. decay_warning = recent 3 mean < preceding mean; needs ≥4 cohort-weeks (else `insufficient_history`). |
| **B12** insider | `app/db/crud.py` (+CRUD + `insider_holdings_for_markets`), `app/api/routes/insider.py` (new), `app/api/main.py` (+routers), `app/scheduler/jobs.py` (`_gather_tracked_wallets` unions in insider proxies), `app/api/routes/signals.py` (+has_insider enrichment) | CRUD only in V1. Insiders included in position-refresh pool unconditionally. has_insider computed at /signals/active query time (no new column). |
| smoke suite | `scripts/smoke_phase_b2.py` | 90 tests covering schema, pure-function logic, and DB round-trips for every item. |

### Critical implementation details

**Counterparty pool gathering**: `gather_union_top_n_wallets` is called once at the top of `log_signals` (top_n=50, all 7 categories). Reused across every fresh signal in the cycle. Failures here just disable the check (warning stays FALSE); they never abort signal logging.

**Watchlist mutual exclusion**: `detect_signals_and_watchlist` filters once at the loose floor, then bumps any row that ALSO clears the official floors into `official` (and out of `watchlist`). The two output lists are guaranteed disjoint per (cid, direction).

**B4 snapshot offsets**: enforced in two places that must stay in sync — `app/services/half_life.py:SNAPSHOT_OFFSETS_MIN` (`(120, 60, 30)`) and `app/services/backtest_engine.py:LATENCY_SNAPSHOT_OFFSETS` (`(30, 60, 120)`). A smoke test pins them together.

**B10 fallback semantics**: when `latency_profile` is set but no snapshot exists within ±5 min of the sampled offset, the row keeps its original `signal_entry_offer` (current/optimistic baseline). The route response surfaces `latency_stats: {adjusted, fallback}` so the UI can warn when a profile is mostly falling back. With current data (no historical snapshots yet), `delayed` is the only profile that hits real snapshots; the others will all fall back until B4 has accumulated.

**B11 ISO-week**: cohorts are keyed by Monday-of-week (UTC). `decay_warning` requires ≥4 cohort-weeks; below that, `insufficient_history=True` and `decay_warning` stays False. Min n_eff per cohort defaulted to 5 — too small to be honest in isolation, but enough to see the trend.

**B12 has_insider**: computed live in `/signals/active` via `crud.insider_holdings_for_markets(condition_ids)` — single DISTINCT query joining positions ↔ insider_wallets. Multi-outcome rows (where `outcome` isn't YES/NO) are excluded so the (cid, direction) tuple is well-defined.

---

## Session 7 spec (preserved)

**6 items. Estimated ~6h. Each item below is written as a precise implementation brief.**

---

### B2 — Counterparty diagnostic

**What it does:** At signal fire time, check whether any wallet in the current top-N pool has recently been a SELLER in the CLOB for this market (i.e., smart money is on the other side of the same market). Surface as a boolean warning so the UI can show "⚠ Smart money also selling."

**Implementation steps:**

1. **Migration 008**: Add `counterparty_warning BOOLEAN NOT NULL DEFAULT FALSE` to `signal_log`. Also add to the `vw_signals_unique_market` view (already an aggregation — no change needed there since the bool will be in signal_log rows the view reads from).

2. **CLOB fills query**: For each newly fired signal (condition_id + direction), call `clob.polymarket.com/trades?market={token_id}&limit=100` (or similar recent fills endpoint — check `spike/FINDINGS.md`). This returns recent fills with maker/taker wallet addresses. The relevant token is the YES token if signal direction is YES, NO token if NO.

3. **Counterparty check logic** (in `signal_detector.py` or new `counterparty_checker.py`):
   - Collect the union of ALL 21 mode×category leaderboard top-N pools at signal-fire time (i.e., every tracked wallet in the `traders` table that appeared in any recent leaderboard snapshot — we already have this from the position refresh)
   - From the CLOB fills, extract maker wallet addresses (makers = liquidity providers = sellers of the token you're about to buy)
   - If any maker wallet is in the tracked pool → `counterparty_warning = True`
   - Decision: use union of ALL 21 pools (locked in decisions table), not just the specific mode×category lens that fired the signal

4. **Wire in**: After `signal_detector.detect_and_log_signals()` fires a new signal, fetch fills and update the row. Alternatively, do it inline in the signal write (before the INSERT/upsert returns). Keep it non-blocking — a failed fills fetch should not prevent signal logging (just leave `counterparty_warning=False` with a logged warning).

5. **API**: `/signals/active` already SELECTs all signal_log columns — the field will appear automatically once added to the table. No route change needed.

6. **Smoke test**: `scripts/smoke_phase_b2.py` — pure-function test for the match logic; DB test verifying the column exists and can be set; integration test verifying the field appears in `/signals/active` response.

---

### B3 — Watchlist tier

**What it does:** A secondary feed for markets that are building consensus but haven't crossed the official signal floors yet. Floors: ≥2 traders (vs 5), ≥$5k aggregate (vs $25k), ≥60% skew (same). These are **not** signals — not eligible for paper trading or backtest.

**Implementation steps:**

1. **Migration 008** (same migration as B2): Add table:
   ```sql
   CREATE TABLE IF NOT EXISTS watchlist_signals (
       id              BIGSERIAL PRIMARY KEY,
       mode            TEXT NOT NULL,
       category        TEXT NOT NULL,
       top_n           INTEGER NOT NULL,
       condition_id    TEXT NOT NULL REFERENCES markets(condition_id),
       direction       TEXT NOT NULL CHECK (direction IN ('YES','NO')),
       trader_count    INTEGER NOT NULL,
       aggregate_usdc  NUMERIC(14,2) NOT NULL,
       net_skew        NUMERIC(5,4) NOT NULL,
       avg_portfolio_fraction NUMERIC(8,6),
       first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       UNIQUE (mode, category, top_n, condition_id, direction)
   );
   CREATE INDEX ON watchlist_signals (last_seen_at DESC);
   ```

2. **Detection logic**: In `signal_detector.py`, add `detect_watchlist_candidates(pool_snapshot, positions_map, markets_map)` — same math as `detect_signals` but with looser floors. Upserts to `watchlist_signals` table. Markets that cross the official signal floors are NOT in watchlist — the two sets are mutually exclusive.

3. **Scheduler**: Wire into the 10-min `refresh_and_log` cycle as step 2.5 (between signals and exits): positions → signals → **watchlist** → exits → auto-close.

4. **Dropout cleanup**: When a market drops below even watchlist floors, delete from `watchlist_signals` (same pattern as position dropout cleanup).

5. **API**: New `GET /watchlist/active?mode=&category=&top_n=` — thin route, same shape as `/signals/active` but simpler (no backtest, no exit enrichment).

6. **Smoke test**: verify table exists; verify detect logic applies looser floors; verify official signals don't appear in watchlist (mutual exclusion); verify API route.

---

### B4 — Signal half-life data collection

**What it does:** For each active signal, record the YES market price at +30min, +60min, +120min after fire. This builds the raw data for half-life analysis — how long does the "gap to smart money" stay open after a signal fires? Visible from day 1, labelled `underpowered: true` until n ≥ 30 per category.

**Implementation steps:**

1. **Migration 008**: Add table:
   ```sql
   CREATE TABLE IF NOT EXISTS signal_price_snapshots (
       id              BIGSERIAL PRIMARY KEY,
       signal_log_id   BIGINT NOT NULL REFERENCES signal_log(id),
       snapshot_offset_min INTEGER NOT NULL,  -- 30, 60, or 120
       snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       yes_price       NUMERIC(8,6),
       token_id        TEXT NOT NULL,
       UNIQUE (signal_log_id, snapshot_offset_min)
   );
   CREATE INDEX ON signal_price_snapshots (snapshot_at DESC);
   ```

2. **Price snapshot job**: New scheduler job `record_signal_price_snapshots`, cron every 30 min. Logic:
   - Find all signal_log rows where `first_fired_at` is between `NOW() - 125min` and `NOW() - 25min` (catching ≈30, ≈60, ≈90min marks, with overlap for missed ticks)
   - For each, compute how many minutes since fire; if close to 30/60/120 and snapshot not yet written for that offset → fetch current YES price from CLOB (`clob.polymarket.com/book?token_id=...` → `best_bid`) → upsert to `signal_price_snapshots`
   - Skip markets where `m.closed = TRUE` (already resolved)

3. **Half-life endpoint**: `GET /backtest/half_life?category=` — for each category, compute: for signals where price at +30min is closer to smart-money entry than at fire time, what fraction "converged" within 30/60/120 min? Return per-category median half-life in minutes. Flag `underpowered: true` when n < 30.

4. **Smoke test**: synthetic test that the offset bucketing math is correct (signals at exactly 30min, 61min, 119min map to correct offset buckets); DB test for table existence; test that endpoint handles underpowered case.

**Note on B10 dependency**: B10 (latency simulation) needs B4 price snapshots to be meaningful. B10 can still be built independently but its output will be synthetic until B4 has accumulated data.

---

### B10 — Realistic execution latency simulation

**What it does:** The backtest currently assumes instantaneous entry at `signal_entry_offer`. In reality the user gets notified, decides, places the order — taking 1-60 min. During that window the price may drift. This adds a configurable latency model to the backtest.

**Implementation steps:**

1. **`BacktestFilters` field**: Add `latency_profile: str | None = None` — one of `active`, `responsive`, `casual`, `delayed`, `custom`. When None: no latency adjustment (current behavior).

2. **Profile windows** (from locked decisions):
   - `active`: 1–3 min
   - `responsive`: 5–10 min (default when profile is set)
   - `casual`: 12–20 min
   - `delayed`: 30–60 min
   - `custom`: user-specified `latency_min_min` + `latency_max_min` fields on BacktestFilters

3. **Price lookup**: When latency_profile is set, for each signal row: sample a latency offset uniform within the profile window, seeded by `hash(condition_id)` for reproducibility. Look up YES price at `first_fired_at + offset` from `signal_price_snapshots`. If no snapshot exists for that offset: skip the latency adjustment for this row (fall back to `signal_entry_offer`) and log a warning. Use this as `effective_entry_price` instead of `signal_entry_offer`.

4. **`compute_pnl_per_dollar`**: Already accepts `entry_price` — no change needed. Just pass the latency-adjusted price from the caller.

5. **API**: Add `?latency_profile=` to `/backtest/summary` and `/backtest/slice`. Returns same response shape — latency is transparent to the caller.

6. **Smoke test**: verify profile → window mapping; verify deterministic offset from condition_id; verify fallback when no snapshot; verify response includes latency_profile echo.

---

### B11 — Edge decay endpoint

**What it does:** Rolling 7-day mean P&L per dollar, grouped by the week the signal first fired. Shows whether the strategy's edge is stable, improving, or decaying over time. `decay_warning: true` when the most recent 3 cohorts average below the preceding cohorts.

**Implementation steps:**

1. **No new table needed.** Uses existing `signal_log` + `markets.resolved_outcome` + the same P&L math as the backtest engine.

2. **New endpoint** `GET /backtest/edge_decay?mode=&category=&min_n_per_cohort=5`:
   - SQL: `SELECT DATE_TRUNC('week', s.first_fired_at) AS cohort_week, s.*, m.resolved_outcome, e.category AS market_category FROM signal_log s JOIN markets m ON ... LEFT JOIN events e ON ... WHERE s.first_fired_at > NOW() - INTERVAL '6 months'`
   - Group by `cohort_week`, compute `summarize_rows` per cohort (reuse the engine's pure function)
   - Only include cohorts with n_eff ≥ `min_n_per_cohort` (default 5; too small to be honest but enough to see the trend)
   - Apply same filters as BacktestFilters (mode, category, etc.)

3. **Decay warning logic**: From the returned cohort list (sorted by `cohort_week` ascending), check: does the mean of the last 3 cohorts' `mean_pnl_per_dollar` lie below the mean of the preceding cohorts? If yes → `decay_warning: True`. Requires ≥4 weeks of cohorts to fire (otherwise `decay_warning: False` with a `insufficient_history: True` flag).

4. **Response shape**:
   ```json
   {
     "cohorts": [
       {"week": "2026-04-07", "mean_pnl_per_dollar": 0.14, "n_eff": 12, "pnl_ci_lo": 0.02, "pnl_ci_hi": 0.26},
       ...
     ],
     "decay_warning": false,
     "insufficient_history": true,
     "weeks_of_data": 2,
     "min_weeks_needed": 4
   }
   ```

5. **Wire into route layer**: New `GET /backtest/edge_decay` in `app/api/routes/backtest.py`.

6. **Smoke test**: synthetic cohort data with known decay pattern → verify `decay_warning=True`; flat pattern → `decay_warning=False`; <4 cohorts → `insufficient_history=True`.

---

### B12 — Insider watchlist CRUD

**What it does:** A manually curated list of wallets the user has specifically identified as interesting (beyond the leaderboard top-N). Could be wallets spotted in Polymarket's UI, referrals, or tipsters. Auto-detector deferred to V2.

**Implementation steps:**

1. **Migration 008**: Add table:
   ```sql
   CREATE TABLE IF NOT EXISTS insider_wallets (
       proxy_wallet    TEXT PRIMARY KEY,
       label           TEXT,
       notes           TEXT,
       added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       last_seen_at    TIMESTAMPTZ
   );
   ```

2. **CRUD endpoints** in new `app/api/routes/insider.py`:
   - `GET /insider_wallets` — list all
   - `POST /insider_wallets` — body `{proxy_wallet, label?, notes?}` — add to seed
   - `DELETE /insider_wallets/{proxy_wallet}` — remove
   - No auto-detection logic in V1

3. **Integration with position refresh**: The 10-min cycle already refreshes positions for all tracked wallets from `traders` table. Insider wallets should also be tracked even if they fall off the leaderboard top-N. Add: at startup and on each daily snapshot, upsert all `insider_wallets.proxy_wallet` values into the `traders` table so they're picked up by the position refresh.

4. **Signal enrichment**: When a signal fires, check if any of the contributing wallets (from `signal_book_snapshots`) is in `insider_wallets` → add `has_insider: bool` field to signal_log response (not a new column — computed at query time via LEFT JOIN).

5. **Smoke test**: CRUD round-trip; verify insider wallets get upserted into traders; verify signal enrichment logic.

---

## Locked decisions for Session 7

| Item | Decision |
|---|---|
| **B2** counterparty scope | Union of ALL tracked wallets (all who appeared in any recent top-N snapshot), not just the specific lens that fired |
| **B2** CLOB fills source | `clob.polymarket.com/trades?market={token_id}` recent fills; maker address = seller |
| **B3** watchlist floors | ≥2 traders / ≥$5k aggregate / ≥60% skew — mutual exclusion with signal_log (a market can't be in both) |
| **B4** snapshot offsets | 30, 60, 120 minutes after `first_fired_at`; job runs every 30 min with overlap window |
| **B10** latency profiles | active=1-3min, responsive=5-10min (default), casual=12-20min, delayed=30-60min, custom=user-specified |
| **B11** decay trigger | Recent 3 cohorts average < preceding cohorts; needs ≥4 cohort-weeks of data |
| **B12** insider seed | Empty; manual population only; auto-detector deferred to V2 |

All 6 items can share a single migration file (`migrations/008_phase_b2.sql`).

---

## What was built this session (Session 6 — B7 + B8 + B13)

### Files changed

| File | Change |
|---|---|
| `app/services/backtest_engine.py` | +~200 lines: imports (dataclasses, hashlib, date), VALID_BENCHMARKS constant, _norm_cdf/_norm_ppf/_Z_RAW/_pvalue_from_ci/_ci_gaussian stat helpers, MultipleTestingCorrections dataclass, holdout_from field on BacktestFilters, holdout filter in _fetch_signals, compute_corrections(), compute_benchmark(), backtest_with_rows() |
| `app/db/crud.py` | +~55 lines: insert_slice_lookup(), get_session_slice_lookups() |
| `app/api/routes/backtest.py` | Full rewrite: _build_filters() now accepts holdout_from, _filters_to_json_dict() helper, get_summary() now calls backtest_with_rows, inserts to slice_lookups, fetches session, calls compute_corrections, optionally calls compute_benchmark; get_slice() inserts one row per bucket, returns n_session_queries + multiplicity_warning |
| `scripts/smoke_phase_b78.py` | New file, 55 tests |
| `UI-SPEC.md` | Backtest section rewritten; stale-signal strikethrough added; book-depth check changed from warning to hard refuse; backend endpoints table updated |
| `session-state.md` | This file |

### Key implementation details (for a fresh session to understand without reading code)

**Multiple-testing corrections (B7)**

The engine now ships three CI variants side-by-side for every `/backtest/summary` call:

- **Raw**: standard 95% cluster-bootstrap CI (unchanged)
- **Bonferroni**: alpha/N where N = session query count (4-hour window). Uses Gaussian SE approximation: `se = (raw_ci_hi - raw_ci_lo) / (2 × 1.96)`, then `corrected_lo = mean - z_{bonf} × se`.
- **BH-FDR**: Uses p-values from ALL session entries in `slice_lookups`. For each entry, computes approximate p-value: `p ≈ 2 × Φ(-|mean/se|)`. Sorts all p-values ascending; current query's rank k → `alpha_BH = min(0.05, 0.05 × k/N)`. Applied same way as Bonferroni via `_ci_gaussian`. BH is always between raw and Bonferroni (less conservative than Bonferroni). Win-rate CIs use the same Gaussian approximation, clamped to [0,1].

`_norm_ppf` and `_norm_cdf` are hand-rolled (Abramowitz & Stegun 26.2.17, max error <4.5e-4). No scipy dep added.

**Session window**: 4 hours. Every `/backtest/summary` call inserts one row into `slice_lookups`; every `/backtest/slice` call inserts one row per bucket. `get_session_slice_lookups(conn, window_hours=4)` fetches them. N counts toward Bonferroni AND the BH rank ordering.

**Multiplicity warning**: `n_session_queries > 5` → `corrections.multiplicity_warning = True`.

**Holdout**: `?holdout_from=YYYY-MM-DD` adds `AND s.first_fired_at < $N` to the signal fetch SQL. The `holdout_from` field is `date | None` on `BacktestFilters`. Echoed in the response as `holdout_from: "YYYY-MM-DD" | null`.

**Benchmarks (B8)**

`compute_benchmark(rows, benchmark, trade_size_usdc, exit_strategy)` reuses the exact same list[SignalRow] fetched for the strategy (no second DB hit — `backtest_with_rows()` returns both result and rows).

- `buy_and_hold_yes`: `dataclasses.replace(r, direction="YES")` for each row → runs through `summarize_rows`. Tests whether signal direction adds value vs. just buying YES on any top-trader-attention market.
- `coin_flip`: direction = `"YES" if sha256(condition_id) % 2 == 0 else "NO"` — deterministic, reproducible across runs. Expected P&L ≈ −fees−slippage; strategy must beat this to claim any edge.
- `follow_top_1`: rows unchanged. Equals strategy when no extra filters; diverges when filters narrow the universe (shows unfiltered signal baseline).

Response: `benchmark.name + same 5 numbers as BacktestResult`. UI checks if strategy beats benchmark by ≥2× CI overlap.

**`backtest_with_rows()`**: New async function in engine. Same as `backtest_summary()` but returns `(BacktestResult, list[SignalRow])`. The route calls this instead of `backtest_summary()` so it has rows for benchmark computation without a second fetch.

**UI-SPEC.md changes (B13)**

- Backtest section: hero P&L numbers moved out of the top hero position into a "Stats" sub-section. Primary display is CI exploration. Three-column CI display added (raw + Bonferroni + BH-FDR). B1 exit-strategy toggle: segmented "Hold to resolution | Smart-money exit" with side-by-side compare mode. Saved queries via localStorage. Holdout workflow simplified: just use `?holdout_from=` param (no server-side holdout session state). Multiplicity banner at 6+ (amber) and 20+ (red).
- B11 cohort decay chart: specified as top of Diagnostics tab, full-width, with `decay_warning` badge.
- Signal cards: stale signals (>4h) get **strikethrough on the direction badge** (was: just reduced opacity).
- Buy panel: book-depth >5% now **disables the Confirm button** (was: shows a warning but kept button enabled).
- Backend endpoints: holdout POST endpoints removed (they were aspirational, not built). Replaced by `?holdout_from=` note on `/backtest/summary`.

---

## Phase A — fully complete (Sessions 1–3, all 31 audit items)

### Session 1 (correctness fundamentals) — 9 items
A1 sybil writeback to wallet_classifications · A2 position TTL filter + drop-out cleanup · A3 paper-trade slippage double-count fix · A4 resolution 50_50 + VOID detection · A6 rate-limit acquire inside tenacity retry · A7 auto-close DB connection scope refactor · A22 win rate convention to pnl_per_dollar > 0 · A23 fee model on losing trades (fee on payout) · A26 size-weighted avg entry price in signal_detector. **28 smoke tests** (`scripts/smoke_phase_a.py`).

### Session 2 (correctness + observability) — 15 items
A5 multi-outcome filter · A8 catch-up snapshot warning on multi-day gap · A9 entry-price overwrite guard · A10 wallet classifier scaling-out fix (10min window + size match) · A11 markets_per_day MM guard · A12 ROW_NUMBER deterministic tiebreakers · A13 cycle duration warning (≥9 min) · A14 Postgres advisory lock for refresh_cycle + daily_snapshot · A15 pool max 4 → 12 · A16 silent-gap counter for dropped positions · A17 daily_snapshot per-combo try/except · A24 entry_price ≥ 1.0 logging · A25 median liquidity slippage fallback · A27 profit factor inf → None · A30 dropped raw_snapshots + alerts_sent. **25 smoke tests** (`scripts/smoke_phase_a2.py`).

### Session 3 (sybil v2 + N+1 + API surface) — 7 items
A18 status endpoint composite health (overall = worst-of-five components) · A19 sybil Scope 2 (sliding 60s windows + 3+ wallet group co-entry) · A20 cluster_id self-heal in upsert + sweep helper · A21 backtest filters expose `min_avg_portfolio_fraction` + `liquidity_tiers`, `/signals/active` enriched with liquidity_tier, `/markets/{cid}` per-trader detail · A28 paper-trade auto-close handles markets disappearing from gamma · A29 `discover_and_persist_markets` partial-result detection + closed=true retry · A31 N+1 fix in `_gather_tracked_wallets` (single bulk SQL replaces 21 round-trips). **27 smoke tests** (`scripts/smoke_phase_a3.py`).

---

## Phase B — in progress

### Session 4 — B1 (smart-money exit detector) ✅

- Migration 005: `signal_exits` table; `paper_trades.status` + `exit_reason` extended
- `app/services/exit_detector.py` — `_classify_drop` (pure) + `detect_exits` (DB-bound): recomputes current trader_count + aggregate_usdc vs peak; fires when either drops ≥30% from peak
- Wired as 3rd step in 10-min cycle: positions → signals → exits → resolution-settlement
- Auto-closes open paper trades at current bid, `status='closed_exit'`, `exit_reason='smart_money_exit'`
- Backtest `exit_strategy=hold|smart_money_exit`; `compute_pnl_per_dollar_exit` settles at exit_bid_price
- `/signals/active` enriched with `has_exited` + `exit_event`; `/signals/exits/recent`
- **24 smoke tests** (`scripts/smoke_phase_b1.py`)

### Session 5 — B5 + B6 + B9 ✅

- Migration 006: indexes on trader_category_stats
- `app/services/trader_stats.py`: `aggregate_trades_per_category` — buckets wallet trades by category, counts resolved-only
- `compute_trader_category_stats` nightly job (02:30 UTC): bulk-upserts per-wallet category stats
- Trader ranker: recency filter (last_trade_at within 60d), Bayesian shrinkage `(pnl + 50k × cat_avg) / (vol + 50k)`, Specialist floor ≥30 resolved trades in category
- Migration 007: `vw_signals_unique_market` — one row per (cid, direction), earliest-fired, aggregates lens_count + lens_list
- `BacktestFilters.dedup=True` reads from view; `?dedup=true` on both backtest endpoints
- `lens_count_bucket` dimension added to slice engine and VALID_SLICE_DIMENSIONS
- **26 smoke tests** (`scripts/smoke_phase_b56.py`)

### Session 6 — B7 + B8 + B13 ✅

See "What was built this session" section above for full detail.

**55 smoke tests** (`scripts/smoke_phase_b78.py`).

---

## Schema state (Supabase project `kfslgdjabfwvsbopsuib`, eu-west-3)

**Tables in use** (post-007):
- `_migrations`, `traders`, `leaderboard_snapshots`
- `events`, `markets`, `positions`, `portfolio_value_snapshots`
- `signal_log`, `signal_book_snapshots`, `signal_exits` (B1)
- `wallet_classifications`, `wallet_clusters`, `cluster_membership`
- `trader_category_stats` (B5 nightly batch)
- `slice_lookups` (B7 — now auto-populated on every backtest call)
- `paper_trades`

**Views**:
- `vw_signals_unique_market` (B6)

**Migrations applied** (all 9 live in Supabase):
- 001 initial schema
- 002 backtest schema (+12 columns on signal_log, +7 tables including slice_lookups)
- 003 phase A hardening
- 004 drop unused tables
- 005 smart-money exit
- 006 trader_category_stats indexes
- 007 dedup view
- 008 phase B2 (counterparty_warning column + watchlist_signals + signal_price_snapshots + insider_wallets)
- **009 pass2 snapshot columns** (Pass 2 F4) — added `bid_price` + `ask_price` columns to `signal_price_snapshots`; backfilled `bid_price = yes_price` on existing rows; `yes_price` kept for back-compat (deprecated, mirrors bid_price on new rows)

---

## Scheduler jobs (current state)

| Job | Trigger | What it does |
|---|---|---|
| `refresh_and_log` | every 10 min | positions → signals + watchlist → exits → paper-trade auto-close. `job_lock("refresh_cycle")`. Warns if ≥9 min. B2 counterparty check runs inline per fresh signal (Pass 2 F12: switched from CLOB endpoint to data-api `/trades?market=`). F10 cleanup of cross-lens promoted watchlist rows runs at end of cycle. |
| `daily_snapshot` | cron 02:00 UTC | 28-combo leaderboard snapshot. Pass 2 F24: connection acquired per-combo (was held across full run). |
| `daily_trader_stats` | cron 02:30 UTC | Refreshes trader_category_stats for all tracked wallets |
| `weekly_classify` | cron Mon 03:00 UTC | Refreshes wallet_classifications |
| `weekly_sybil` | cron Mon 03:15 UTC | Refreshes sybil clusters + writeback |
| `signal_price_snapshots` | **every 10 min** (Pass 2 F7: was 30 min) | B4/F4/F7 — captures **bid + ask** (was: just bid) at **+5/+15/+30/+60/+120 min** (was: just +30/60/120) after each signal's first fire. Cadence dropped to 10 min so the +5 window is reliably hit. Window expanded from 25-125 to 0-125 min. |
| `catch_up_snapshot_if_stale` | startup | Catch-up if last snapshot >24h |

---

## Smoke test inventory

**All 10 suites green as of Pass 4 (2026-05-07):**

| Suite | File | Tests | Coverage |
|---|---|---|---|
| Phase A Session 1 | `scripts/smoke_phase_a.py` | 51 | A1-A4, A6-A7, A22-A23, A26 + Pass 1 F6 + Pass 2 F13 + Pass 2 F15 |
| Phase A Session 2 | `scripts/smoke_phase_a2.py` | 56 | A5, A8-A17, A24-A25, A27, A30 + Pass 2 F3, F9, F11, F14, F16, F17, F18, F20, F22, F25 |
| Phase A Session 3 | `scripts/smoke_phase_a3.py` | 27 | A18-A21, A28-A29, A31 |
| Phase B B1 | `scripts/smoke_phase_b1.py` | 28 | B1 + Pass 3 R3a tuple-return changes |
| Phase B2 | `scripts/smoke_phase_b2.py` | 115 | B2 + B3 + B4 + B10 + B11 + B12 + Pass 1 F2/F5 + Pass 2 F4/F7/F10/F12 + Pass 3 counterparty rewrite (incl. live-API contract test) |
| Phase B B5+B6 | `scripts/smoke_phase_b56.py` | 33 | B5 + B6 + B9 + Pass 1 F1 |
| Phase B B7+B8 | `scripts/smoke_phase_b78.py` | 87 | B7 + B8 (incl. price-translation + 5 benchmarks) + Pass 1 F8 + Pass 2 F21 |
| Pass 2 routes | `scripts/smoke_phase_pass2_routes.py` | 16 | Pass 2 F23 — refactored route response shape regression |
| Pass 3 helpers | `scripts/smoke_phase_pass3_helpers.py` | 76 | D1 fees + D3 Kish n_eff + R15 ResponseShapeError + **Pass 4 zombie filter (29 tests: redeemable parsing, drop_reason predicate, _end_date_in_past, get_positions integration with mock client, include_resolved opt-out, per-reason counter recording)** |
| Pass 3 fixes | `scripts/smoke_phase_pass3_fixes.py` | 90 | All R-class + D-class fixes (R1-R14, R16, D1, D3, D4, D5) |
| **Total** | | **579** | |

Plus `scripts/probe_polymarket_endpoints.py` — manual diagnostic script that hits live Polymarket and dumps raw JSON for verification of endpoint contracts. Run it any time you suspect Polymarket changed something.

---

## Open questions / non-blocking flags

1. **Some events have `category=null`** — multi-choice events live in Overall only; no "Uncategorized" tab needed.
2. **`prices-history` interval semantics**: `interval=1d` returned 1440 points (minutes, not days). Verify `interval=1h`, `1m`, `max` if used for B4 drift labels.
3. ~~Phase 3 N+1~~ **FIXED in Pass 2 F16** via `executemany` batching; per-wallet upserts now take milliseconds.
4. **`SLIPPAGE_K` placeholder** = 0.02. Calibrate once paper-trade fills accumulate.
5. **TAKER_FEES** = educated guess. Verify against `polymarket.com/learn/fees` before locking.
6. **Sybil detector thresholds**: 0 detections in current pool — thresholds may need tuning once larger pool is observed.
7. **Multi-outcome detection** — V1 stays binary-only (CLAUDE.md spec). Pass 2 F15 surfaces custom-label binary resolutions as VOID + WARN log so the operator sees magnitude. Full multi-outcome support deferred to V2.
8. **`slice_lookups.bootstrap_p`** — Pass 2 F21 added empirical bootstrap p-values to `BacktestResult.pnl_bootstrap_p`, but didn't backfill the column into `slice_lookups` (would need a migration). BH-FDR uses bootstrap_p for the current query; falls back to Gaussian-from-CI for prior session entries. Improvement, not regression.

---

## Project status

**V1 backend complete + Pass 1/2/3/4 hardened.** Phase A done. Phase B done (B1-B13 all shipped). Pass 1 + Pass 2 closed (all 23 review findings addressed). Pass 3 closed (10 Tier 0 + 5 Tier 1 + 4 Design fixes, R1-R14, R16, D1, D3, D4, D5). Pass 4 closed (zombie/dust position filter at API seam; cycle 24.5min → 3.3min). **579 smoke tests passing across 10 suites.** Live cycle dry-run verified working 2026-05-07.

**Done:**
- Skeleton, venv, config, spike findings
- API client (`polymarket.py`) — rate limiter + tenacity retry
- Database — Supabase eu-west-3, asyncpg pool, 7 migrations
- Daily leaderboard snapshot — 28 combos, idempotent
- Trader ranker — 3 modes (absolute, hybrid, specialist) + B5 honest ranking (recency + Bayesian shrinkage)
- Position refresh — JIT market discovery + dropout cleanup
- Signal detector — consensus math + eligibility floors + sybil-aware dedup
- Signal log — first_*/peak_* + cluster_id self-heal + book-snapshot capture
- Backtest engine — Wilson CI + cluster bootstrap + fee/slippage + slicing + dedup + exit_strategy + Bonferroni/BH-FDR corrections + benchmarks + holdout_from
- Wallet classifier — rule-based v1
- Sybil detector v2 — sliding windows + 3-wallet group co-entry + writeback
- Smart-money exit detector (B1)
- Paper trades — open/close/auto-close-resolved/auto-close-on-exit
- FastAPI surface — ~20 endpoints across system/traders/signals/markets/backtest/paper_trades
- Status endpoint — composite health (5 subsystems → overall green/amber/red)
- Postgres advisory locks
- 185 smoke tests across 6 suites
- UI-SPEC.md — fully updated and ready for third-party UI builder

**Remaining for V1:**
- Step 10: third-party UI build against FastAPI (see UI-SPEC.md)
- Step 11+: Railway deploy

---

## Validated Polymarket endpoints (also `spike/FINDINGS.md`)
- **Leaderboard**: `data-api.polymarket.com/v1/leaderboard` (paginates via offset, 7 categories, sorts VOL or PNL)
- **Trades**: `data-api.polymarket.com/trades?user={proxy}`
- **Positions**: `data-api.polymarket.com/positions?user={proxy}` (includes resolved-unredeemed indefinitely)
- **Portfolio value**: `data-api.polymarket.com/value?user={proxy}`
- **Events**: `gamma-api.polymarket.com/events`
- **Markets**: `gamma-api.polymarket.com/markets` (default `closed=false`; pass `closed=true` for resolved)
- **Markets batch**: `?condition_ids=A&condition_ids=B&limit=N` (limit mandatory — gamma truncates at 20 by default)
- **Price history**: `clob.polymarket.com/prices-history?market={token_id}`
- **CLOB book**: `clob.polymarket.com/book?token_id={token_id}` (signal_entry_offer + B1 exit-bid)

---

## Decisions log

- 2026-05-04: Plan approved. See `CLAUDE.md`.
- 2026-05-04: Hosting = local laptop → Railway later.
- 2026-05-04: Two-metric signal display (count + avg portfolio fraction), not combined.
- 2026-05-04: Approximate freshness/drift via snapshot history.
- 2026-05-04: Step 0 spike complete. See `spike/FINDINGS.md`.
- 2026-05-04: Dropped pydantic for plain dataclasses — Python 3.14 had no prebuilt wheels. Re-added 2.13.3 in Step 9.
- 2026-05-04: Leaderboard endpoint discovered at `data-api.polymarket.com/v1/leaderboard`.
- 2026-05-04: Supabase project `polymarket` created (id `kfslgdjabfwvsbopsuib`, eu-west-3, Postgres 17).
- 2026-05-04: Snapshot only `all` + `month` time_periods (skip day/week — too noisy).
- 2026-05-04: Trader ranker dropped minimum-trade-count filter — volume floor alone is sufficient.
- 2026-05-04: Architectural refactor: dropped bulk `sync_active_markets`, replaced with JIT discovery.
- 2026-05-04: Email alerts dropped from V1 in favor of UI-native notifications.
- 2026-05-04: Trader drill-down promoted from Phase 2 to V1.
- 2026-05-04: Step 7 chose `MemoryJobStore` — jobs defined at startup, missed ticks immaterial.
- 2026-05-04: Step 8 simplifications — dropped UMA path, Polygon RPC sybil detection, trade-history nightly batch for Specialist.
- 2026-05-04: Backtest engine synthetic smoke proves win-rate-vs-payoff trap caught correctly.
- 2026-05-04: Specialist mode added — surfaces sharp small-bankroll traders invisible to absolute/hybrid.
- 2026-05-05: Step 9 complete (FastAPI surface, 17 endpoints).
- 2026-05-05: Paper trade auto-close on resolution shipped. Math verified: $1000 YES @ 0.95 → resolved YES → +$50.13.
- 2026-05-05: Phase A audit absorbed early — correctness fixes (sybil v2/status/API surface) shipped in A18-A21. Trimmed Phase B budget from ~25h to ~19h.
- 2026-05-05: Phase A complete (A1-A31). 130 smoke tests.
- 2026-05-05: Live cycle verified at 472.9s. 26,435 dropped positions confirmed benign (resolved markets).
- 2026-05-06: B1 complete. Smart-money exit detector. 24 smoke tests.
- 2026-05-06: B5+B6+B9 complete. Honest ranker + dedup view + lens_count_bucket. 26 smoke tests. Live verified: 20 signal_log → 10 unique via dedup.
- 2026-05-06: migration 002 already had trader_category_stats — adapted crud to existing schema.
- 2026-05-06: Locked Phase B2 decisions (B2 union-of-21-pools, B3 same cycle, B10 configurable latency, B11 recent-3 decay trigger, B12 empty seed).
- 2026-05-06: Session 6 complete. B7 (multiple-testing), B8 (benchmarks), B13 (UI-SPEC). 55 smoke tests. slice_lookups auto-populated. BH-FDR uses rank-based alpha. Holdout via ?holdout_from=. UI-SPEC ready for third-party UI build.
- 2026-05-06: B8 benchmark hot-fix (pre-Session 7 review). Found in spot-check: `compute_benchmark` was flipping `direction` on rows without translating `signal_entry_offer` to the opposite token's price (NO ask ≠ YES ask, but they were being used interchangeably). Fix: new `_retarget` helper translates entry_offer/entry_mid via `1 − x` and nulls smart-money-exit fields when flipping (since exit was for the original side's position). Added two new benchmarks for symmetry/meaningfulness: `buy_and_hold_no` (mirror of `buy_and_hold_yes`) and `buy_and_hold_favorite` (always buy whichever side is priced ≥ $0.50 — the "go with the crowd" baseline). 21 new smoke tests including magnitude-check for the bug (asserts P&L ≈ +1.22 not +0.82 on a flipped row). VALID_BENCHMARKS now: yes, no, favorite, coin_flip, follow_top_1.
- 2026-05-06: Session 7 complete. All 6 Phase B2 items shipped (B2 + B3 + B4 + B10 + B11 + B12). Migration 008 applied. 90 new smoke tests in `scripts/smoke_phase_b2.py`. Backend V1 feature-complete; total 296 tests across 7 suites all green.
- 2026-05-07: Pass 3 verification re-run resolved the prior session's "exit 0 / no writes" mystery — cycle wrote correctly, all R-class + D-class code paths verified. The 24.5-min runtime exposed the zombie-position bottleneck (Polymarket `/positions` returning ~21k resolved-unredeemed cids per cycle).
- 2026-05-07: Pass 4 shipped (commit `8ad8279`, pushed to `pass3-hardening`). Multi-signal zombie/dust filter at `PolymarketClient.get_positions` boundary with per-reason health counters. Cycle 24.5 min → 3.3 min. `markets_closed` table stopped accumulating. 29 new smoke tests; total now 579 across 10 suites. Phase 8 wipe demoted to optional (system production-ready as-is).
