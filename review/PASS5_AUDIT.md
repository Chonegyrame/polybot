# Pass 5 Audit — complete findings list

**Baseline:** `main` at `2de1248`. **579/579 smoke tests pass across all 10 suites.** All 17 migrations live.

**Method:** verified the 10 findings from a previous (out-of-date) audit against the current code, then spawned 3 fresh code-side audit agents (signal/ranking, backtest/stats, ingestion/orchestration). Web research agent was sandbox-blocked and produced nothing.

This report has three parts:

1. **Status of the 10 prior findings** — fixed vs still open.
2. **Complete catalog of every open finding** — 19 items, each with a plain-English explanation, a concrete example of how it hurts your edge, and a fix sketch.
3. **Priority order** with effort and trading impact.

---

## Part 1 — Status of the 10 prior findings

| # | Finding | Status |
|---|---|---|
| 1 | Exit detector — 5-trader rotation flat aggregate fires no exit | **FIXED** (R3 cohort rewrite) |
| 2 | `signal_price_snapshots` CHECK stuck at (30,60,120) | **FIXED** (migration 010) |
| 3 | Counterparty / signal_detector don't dedup sybil clusters | **STILL OPEN** → items **#1** and **#2** below |
| 4 | Specialist Bayesian prior over winners-only | **STILL OPEN** → item **#3** below |
| 5 | `daily_snapshot` partial failures pollute reads | **STILL OPEN** → item **#16** below |
| 6 | `smart_money_exit` P&L charges fee on the sell only | **FIXED** (D1 + paper_trade_close.py) |
| 7 | NULL `cluster_id` rows treated as singletons in n_eff | **STILL OPEN** → item **#11** below |
| 8 | BH-FDR `bootstrap_p` column missing from `slice_lookups` | **STILL OPEN** → item **#8** below |
| 9 | `upsert_market` `closed = EXCLUDED.closed` unconditional | **STILL OPEN** → item **#14** below |
| 10 | Rate limiter is per-`PolymarketClient` instance | **STILL OPEN** → item **#15** below |

**2 fixed, 8 still open.** The 8 open items are all spelled out below alongside 11 new findings.

---

## Part 2 — Complete catalog (every open finding, plain English)

19 items total. Severity reflects how much it lies to your real-world buy decision.

---

### #1 — Sybil cluster wash-trading inflates `aggregate_usdc` and `dollar_skew` on the same side
**Severity: Critical** — `app/services/signal_detector.py:300-339`
**Status: fixed** in commit `668ae70` (Pass 5 Tier B #1+#2+#5). The audit's framing of this finding overstated the magnitude — `aggregate_usdc` and `total_dollars_in_market` were already correct sums across wallet rows. The actual material change is per-entity `avg_portfolio_fraction` and the structural alignment with the cluster-aware COUNT. See `review/FIXES.md` Tier B #1+#2+#5 for the honest behavioral diff.

**What's wrong**
The signal detector correctly counts a 4-wallet sybil cluster as 1 trader (it joins through `cluster_membership` for the headcount), but it sums dollars over the raw wallet positions. So one entity holding $20k on each of 4 wallets shows up as `trader_count = 1` but `aggregate_usdc = $80k`.

**Example**
A cluster has $70k YES + $20k NO (one entity, partly hedged). Four honest retail traders each hold $5k YES. The detector sees:
- `trader_count_YES = 5` (cluster + 4 retail) — looks great
- `aggregate_YES = $90k` (cluster $70k + retail $20k)
- `aggregate_NO = $20k`
- Dollar-skew = 90 / 110 = **82%** → fires an official signal

Reality: one entity put on a partially-hedged $50k net YES, plus four retail $5k bets. The 65% dollar-skew floor (R2) is supposed to catch exactly this kind of whale-vs-retail mismatch and it's defeated by the cluster the sybil detector flagged in the first place.

**Fix**
In `direction_agg`, sum `current_value` per **identity** (cluster-collapse) before the outer aggregation. Apply the same to `market_totals.total_dollars_in_market`. About 30 lines of SQL.

---

### #2 — Counterparty count includes each sybil wallet separately
**Severity: Critical** — `app/services/counterparty.py:110-148`
**Status: fixed** in commit `668ae70` (Pass 5 Tier B #1+#2+#5). Real material change: 4-wallet cluster on opposite side now counts as 1 entity (was 4); 4-wallet cluster at $4k each ($16k total) clears the $5k floor as one entity (was: false negative).

**What's wrong**
The counterparty check queries `positions` by raw `proxy_wallet` and never joins `cluster_membership`. A 4-wallet cluster on the opposite side counts as 4 separate counterparties.

**Example**
A YES signal fires. Théo's 4-wallet cluster holds $20k each on NO ($80k total, one entity). The counterparty check sees 4 wallets each clearing the $5k floor at high concentration → `counterparty_count = 4`. The UI surfaces "strong warning, 4 top traders hold opposite side." Reality: 1 entity holding $80k on the other side.

The mirror failure is also real: same cluster with $4k each ($16k entity exposure) fails the per-wallet $5k floor 4 times → false negative, no warning at all.

**Fix**
Mirror the `wallet_identity` CTE pattern from `signal_detector._aggregate_positions`. Sum same-side and opposite-side USDC per identity before applying `is_counterparty`. ~15 lines.

---

### #3 — Specialist's Bayesian prior is computed over winners only
**Severity: Critical** — `app/services/trader_ranker.py:296-353`
**Status: fixed** in commit `3f8b558` (Pass 5 Tier B #3). New `prior_pool` CTE in `_rank_specialist` and `gather_union_top_n_wallets` reflects the full specialist-eligible universe (winners + losers). Synthetic test: pre-fix prior 5.03% → candidate shrunk to 0.10; post-fix prior 1.90% → shrunk to 0.0794.

**What's wrong**
Specialist mode filters its `base` CTE to `pnl > 0` (only winning specialists), then computes the Bayesian shrinkage prior `prior_roi = SUM(pnl) / SUM(vol)` from that same base. So the "average" the prior pulls each trader toward is the average of *winners only* — a structurally inflated number.

**Example**
A category has 100 specialists meeting the volume floor:
- 60 winners with $20M PnL on $400M volume
- 40 losers with –$15M PnL on $200M volume

True category ROI = $5M / $600M = **0.83%**. The bug computes prior over winners only = $20M / $400M = **5%**.

A small specialist with $5k PnL on $25k volume (raw 20% ROI) gets shrunk:
- With biased prior 5%: shrunk = (5000 + 50000·0.05) / 75000 = **10%**
- With honest prior 0.83%: shrunk = (5000 + 50000·0.0083) / 75000 = **7.2%**

Every specialist's `shrunk_roi` is 2-3 percentage points too high. Since `shrunk_roi` is the primary sort key, **lucky tiny-volume traders get promoted into the specialist top-N** — exactly the F1 bug, just relocated to specialist mode. Hybrid mode does NOT have this flaw.

**Fix**
Split into two CTEs. `prior_pool` runs the same base query without the `pnl > 0`, recency, and resolved-trades filters → that's the honest category baseline. Compute `prior_roi` from `prior_pool`. Keep `base` filtered as-is for the candidate set. Mirror in `gather_union_top_n_wallets`.

---

### #4 — TRIM tier (20% drop) fires on routine API noise at typical cohort size
**Severity: High** — `app/services/exit_detector.py:55-119`
**Status: fixed** in commit `8566f8e` (Pass 5 Tier D bundle). `TRIM_THRESHOLD` raised 0.20 → 0.25 — needs ≥2 of 5 cohort wallets to actually go flat before TRIM fires.

**What's wrong**
With a 5-wallet cohort (the floor for an official signal), losing 1 wallet to a transient API blip is a 20% drop on `trader_count` → a TRIM event fires. The cohort-recompute uses a 30-min `last_updated_at` TTL, which is too short to absorb the kind of "200 OK with empty list" failure modes F13/F14 acknowledged are common.

**Example**
A 5-wallet signal cohort. Wallet #3's `/positions` fetch returns the F13 weird-shape response one cycle. Within 30 minutes that wallet's positions age past the TTL. `cur_traders = 4`, drop = 20%, **TRIM fires**. UI shows "smart money trimming." Reality: all 5 wallets still hold their full position.

**Fix**
Either require BOTH metrics over threshold for TRIM (currently it's either-or), or raise `TRIM_THRESHOLD` to 0.30 to leave a one-wallet noise buffer at n=5. Both are one-line changes.

---

### #5 — Exit detector's cohort recompute SUMs raw positions (same root cause as #1)
**Severity: High** — `app/services/exit_detector.py:140-169`
**Status: fixed** in commit `668ae70` (folded into the Pass 5 Tier B #1+#2+#5 cluster-collapse family per the plan's decision). Inner `identity_agg` CTE with `HAVING SUM > 0` collapses positions by identity before the outer COUNT/SUM. Numerically identical to the pre-fix path on typical scenarios; the structural alignment is what matters.

**What's wrong**
Same shape as #1. The recompute correctly does `COUNT(DISTINCT identity)` but `SUM(current_value)` runs over RAW wallet rows. The `peak_aggregate_usdc` watermark was written by `signal_detector` with the same bug, so peak and current are *consistent at fire time* — but cluster composition changes over time.

**Example**
At fire: 4-wallet cluster on YES, $20k each → peak_aggregate = $80k logged. Three days later, one wallet sells out (the others still hold). cur_agg = $60k (3 wallets × $20k) → 25% drop vs peak → **TRIM fires**. Reality: cluster is still 75% deployed on YES, no real exit happening.

**Fix**
Same identity-collapse pattern as #1. Re-derive peak and current both off identity-summed values so they stay consistent across composition shifts.

---

### #6 — Stale `trader_category_stats` silently empties the entire signal pool
**Severity: High** — `app/services/trader_ranker.py:131-155, 318-336, 422-426`
**Status: fixed** in commit `d482f38` (Pass 5 Tier C #6+#16). New `stats_fresh` CTE in all 4 ranker SQL sites bypasses the recency filter when stats are seeded but >7 days stale. `STATS_STALE` health counter ticks on detection; surfaced at `/system/status.counters.stats_stale_last_hour`.

**What's wrong**
Every ranking mode applies `tcs.last_trade_at >= NOW() - 60 days`. The `stats_seeded` flag only checks if the table has any rows at all — it never checks freshness. If the nightly trader-stats job (02:30 UTC) breaks for 60+ days, every wallet's `last_trade_at` ages past threshold → recency filter rejects everyone → `gather_union_top_n_wallets` returns `[]` → zero signals fire.

**Example**
Nightly job dies on 2026-03-01. Today is 2026-05-08. `NOW() - 60 days = 2026-03-09`. Every `last_trade_at` is ≤ 2026-03-01, so every wallet fails the filter. Position refresh skips everyone, signal_detector returns empty, watchlist empties. The F25 72h `signals_health` window catches "no signals" but doesn't distinguish a quiet weekend from a dead pipeline.

**Fix**
Add a freshness gate alongside `stats_seeded`: if `MAX(last_trade_at) < NOW() - 7 days`, fall through with the bootstrap path (no recency filter) AND record a `health_counters` warning surfaced at `/system/status`. ~10 lines plus a counter constant.

---

### #7 — Specialist's `active_recently` accepts a single old monthly-leaderboard row as proof of activity
**Severity: High** — `app/services/trader_ranker.py:287-294, 409-419`
**Status: dropped** per the user's audit-chat decision (finding judged too weak to act on). If a future audit surfaces "specialists with zero recent positions appearing in top-N," revisit; the F9 layered `last_trade_at` filter already gates most degenerate cases.

**What's wrong**
Specialist mode's "active recently" check just asks "is this wallet on the latest monthly leaderboard for this category?" Polymarket's monthly leaderboard reflects the calendar-month aggregate, so a trader who closed a huge position on April 30 and did nothing in May still appears on the May monthly leaderboard. The F9 layered `last_trade_at >= NOW() - 60d` filter doesn't catch this — that wallet's last_trade_at is 9 days ago, well within 60.

**Example**
Trader X has one $100k Crypto position closed April 28, +$40k profit. May 7 today: X is on the May Crypto monthly leaderboard. `active_recently` includes X. Recency passes. X gets ranked into specialist top-N for Crypto despite zero May activity. Their slot displaces a more genuinely active specialist.

**Fix**
Replace the static-monthly check with a positions-based test: `EXISTS (SELECT 1 FROM positions WHERE proxy_wallet = ls.proxy_wallet AND last_updated_at >= NOW() - 7 days AND size > 0)`. ~5 lines.

---

### #8 — `bootstrap_p` is never persisted to `slice_lookups`; BH-FDR ranking uses the broken Gaussian approximation for every prior session entry
**Severity: Critical** — `app/db/crud.py:727-787`, `migrations/002_backtest_schema.sql:170-179`
**Status: fixed** in commits `5f7e81b` (migration 018) + `e5b4d0d` (Pass 5 Tier B #8 — crud + routes plumbing). `compute_corrections` now reads the persisted column for prior session entries instead of the Gaussian fallback. Behavioral test verified BH-FDR widened CI is 1.27× narrower with the persisted path (matches theoretical `z_{0.0125}/z_{0.05}`).

**What's wrong**
Pass 2 F21 fixed the p-value computation by adding empirical bootstrap p (works correctly on skewed P&L distributions) instead of the Gaussian-from-CI approximation. But the fix only applies to the *current* result. The `slice_lookups` table where prior session queries are stored has columns `reported_value, ci_low, ci_high` — no `bootstrap_p` column. `insert_slice_lookup` doesn't accept it. `get_session_slice_lookups` doesn't return it. So in `compute_corrections`, `e.get("bootstrap_p")` is **always None for every prior session entry** → falls through to `_pvalue_from_ci`, the very approximation F21 said was wrong.

**Example**
You run a backtest with a heavy-tailed P&L distribution; bootstrap p = 0.04 (truly significant). Earlier in the session you ran 3 quick queries each with CIs straddling zero; their Gaussian approximations give p ≈ 0.95 each. The BH-FDR rank for the current query is 1 of 4 → `alpha_BH = 0.05 × 1/4 = 0.0125` → CI widens 2.5×. If your prior 3 queries had been computed with the proper bootstrap p (say 0.30, 0.40, 0.50), the rank could have been different. Two CIs of identical width can rank differently depending purely on whether the result pre- or post-dates F21 — and that's a stability problem when you're pressure-testing slices.

**Fix**
New migration adds `bootstrap_p NUMERIC` column to `slice_lookups`. Pass `pnl_bootstrap_p` through `insert_slice_lookup`; select it in `get_session_slice_lookups`. NULL is treated as the Gaussian fallback (back-compat). ~30min plus migration.

---

### #9 — Dedup view drops `(cid, direction)` pairs when the first-fired row had a glitched book
**Severity: Critical** — `migrations/007_dedup_view.sql:14-34`, `app/services/backtest_engine.py:651-652`
**Status: fixed** in commits `5f7e81b` (migration 019 rebuilds the view with the unavailable filter inside the `first_fired` CTE — the entire structural fix) + `43284fa` (engine-consumer integration test). The engine's redundant filter is left in place as load-bearing on the non-dedup path.

**What's wrong**
The dedup view picks the canonical row per `(condition_id, direction)` using `DISTINCT ON ... ORDER BY first_fired_at ASC, id ASC`. The engine THEN applies `WHERE COALESCE(s.signal_entry_source,'') != 'unavailable'`. Order matters: when the canonical (earliest) fire happened to have `signal_entry_source = 'unavailable'` (CLOB book glitched at fire time), the dedup view picks it, then the engine filter throws it out — and the *whole pair* is gone, even if a clean re-fire happened later.

**Example**
A market fires at 10:00 with a glitched CLOB call → signal_entry_source = 'unavailable'. Same market re-fires at 10:30 with a clean book. With `dedup=true` the backtest loses this market entirely. With `dedup=false` the 10:30 row gets used. Re-fired markets correlate with sustained smart-money interest, so the dedup'd backtest is systematically losing markets that re-fired (i.e. the *stronger* signals).

**Fix**
Move the `WHERE signal_entry_source != 'unavailable'` filter INSIDE the view's `first_fired` CTE so dedup picks the earliest **executable** fire, not the absolute earliest. ~5 lines of SQL in a new migration.

---

### #10 — Smart-money-exit P&L is missing exit-side slippage
**Severity: Critical** — `app/services/backtest_engine.py:469-517`
**Status: fixed** in commit `43284fa` (Pass 5 Tier B #9+#10). `compute_pnl_per_dollar_exit` now applies symmetric slippage: `effective_exit = max(0.001, exit_bid_price - slip)`. Worked example ($100 trade, $50k liquidity, entry 0.40 → 0.55, Politics): pre-fix P&L 0.32328 → post-fix 0.32104 (-0.00223 / dollar). Thin-book scenario: -0.00705 / dollar.

**What's wrong**
The entry side correctly bumps the price by `slip` to model market-impact: `effective_entry = entry_price + slip`. The exit side uses the raw `exit_bid_price` directly — there's no symmetric impact cost for *selling* shares back into the book.

**Example**
$100 trade in a market with $50k of liquidity: per the slippage formula, `slip ≈ 0.000894` per share. Entry at $0.40 → effective $0.4009. Exit_bid at $0.55 → revenue / $1 stake = 0.55 / 0.4009 = 1.3719. With symmetric exit slippage, effective exit ≈ $0.5491 → revenue / $ = 1.3697. **Per-dollar P&L over-states by ~22 bps per round trip.** With thinner liquidity ($5k), the gap balloons to ~70 bps.

Worse: the comparison between "hold to resolution" and "smart-money exit" strategies is rigged in favor of the exit strategy, since resolution doesn't have the symmetry issue (it's a $1 settlement, not a market sale).

**Fix**
In `compute_pnl_per_dollar_exit`, compute `exit_slip = _slippage_per_dollar(...)` and use `effective_exit = max(0.001, exit_bid_price - exit_slip)` everywhere `exit_bid_price` appears. ~10 lines.

---

### #11 — Kish n_eff treats NULL `cluster_id` as singletons, inflating effective sample size
**Severity: High** — `app/services/backtest_engine.py:337-365`
**Status: fixed** in commit `8566f8e` (Pass 5 Tier D bundle). NULL keys collapse to one shared `__null__` cluster in both `compute_kish_n_eff` and `cluster_bootstrap_mean_with_p`. Tests verify 30 NULLs → n_eff = 1.0 (was 30); 70xA + 30xNone → n_eff ≈ 1.72 (was ≈ 2.03).

**What's wrong**
The Kish formula correction itself is right (D3 fix). But for rows with `cluster_id = NULL` (gamma's `event_id` was missing at sync time and F26 logs but doesn't backfill), the code maps each NULL to a unique synthetic key `_solo_{i}` — i.e. each NULL is its own independent cluster. That's optimistic by construction.

**Example**
100 backtest rows: 70 share `cluster_id = "trump-2024"`, 30 are NULL (actually all sub-markets of an uncategorized US-elections event). Current code: `n_eff = 100² / (70² + 30·1²) = 2.03`. If NULLs are really one shared event: `n_eff = 100² / (70² + 30²) = 1.72`. With 30% NULLs spread evenly across signals, the reported `n_eff` can cross MIN_SAMPLE_SIZE = 30 and the "powered" flag turns green when the data really isn't.

**Fix**
Map all NULL keys to a single shared cluster key `__null__` (worst-case correlation assumption). One line.

---

### #12 — Latency fallback threshold (50%) is too lenient
**Severity: High** — `app/services/backtest_engine.py:1032-1097`
**Status: fixed** in commit `8566f8e` (Pass 5 Tier D bundle). `LATENCY_FALLBACK_WARN_FRACTION` lowered 0.50 → 0.20. Route response surfaces `n_adjusted` + `n_fallback` explicitly alongside the existing `adjusted` / `fallback` fields (back-compat).

**What's wrong**
`latency_unavailable` only flips True when `n_fallback / total > 0.5`. So a backtest where 49% of rows fall back to the optimistic `signal_entry_offer` (no snapshot within ±5 min of the sampled offset) is reported as "fully adjusted" with no warning.

**Example**
100 rows, 49 fall back to optimistic baseline, 51 use real ask snapshots at +5/+15. With a typical 2¢ spread between optimistic and realistic, mean P&L understates the true latency cost by ~1¢/$ — meaningful given typical realized P&L lives at 1-3¢/$.

**Fix**
Lower threshold to 0.20. Also expose `n_adjusted` and `n_fallback` directly in the response so the UI can show the breakdown rather than a binary flag. ~5 lines.

---

### #13 — Win-rate point estimate is unweighted; CI is cluster-weighted; they disagree
**Severity: High** — `app/services/backtest_engine.py:802, 826-834`
**Status: fixed** in commit `8566f8e` (Pass 5 Tier D bundle). `cluster_bootstrap_mean_with_p` returns the bootstrap median as the `point` estimate; `summarize_rows`'s `wr` is now clamped from `wr_point_raw` (bootstrap), not `wins / len(pnl_pairs)`. Honest call-out: the audit's quantitative claim (0.59 → 0.65) overstated the magnitude — actual shift on the synthetic scenario is ~0.01 because cluster-bootstrap-of-mean is unbiased for the population mean in expectation.

**What's wrong**
`wr = wins / len(pnl_pairs)` — straight count-weighted point estimate. The CI for the same win rate is cluster-bootstrap (F8 fix). Same inconsistency exists between `mean_pnl_per_dollar` (unweighted point) and its cluster-bootstrap CI.

**Example**
100 rows: one cluster of 70 with 50% win rate, 30 singletons with 80% win rate. Reported `wr = 59 / 100 = 0.59`. The bootstrap distribution centers around 0.65 (because resampling clusters gives them ≈equal weight). UI displays `0.59` as the point estimate but a CI centered around 0.65 — looks weird, and the CI looks asymmetric. More importantly, the displayed win-rate disagrees with the displayed CI by half a width.

**Fix**
Use the bootstrap median (or mean of resampled means) as the headline point estimate so the point and CI use consistent weighting. ~5 lines in two places.

---

### #14 — `markets.closed` flips back to FALSE on gamma blips
**Severity: Critical** — `app/db/crud.py:392`, `:348` for events
**Status: fixed** in commit `95629fe` (Pass 5 Tier C #14). `closed = (markets.closed OR EXCLUDED.closed)` / `closed = (events.closed OR EXCLUDED.closed)`. Once true, stays true. Reverse-flip risk explicitly accepted; manual recovery SQL documented inline at both call sites.

**What's wrong**
`upsert_market` does `closed = EXCLUDED.closed` unconditionally. `resolved_outcome` is properly `COALESCE`-protected (once written, it stays), but `closed` will overwrite. A gamma response that briefly serves `closed=false` for a resolved market (UMA dispute, replication lag) flips our row back to live. `events.closed` has the same flaw.

**Example**
A market resolves YES at 14:00. Gamma sets `closed=true`, our DB updates correctly, `auto_close_resolved_paper_trades` lists it for settlement. At 14:07 gamma serves `closed=false` for 3 seconds (replication blip). Our row flips. Now: `signal_detector` includes the market in its pool again (it filters `m.closed = FALSE`), so a fresh signal can fire on a paid-out market — user buys YES at $0.99 expecting resolution, except it already paid out. And `auto_close_resolved_paper_trades` skips the trade because it queries `closed=true`. User holds an open paper trade that should have been settled.

**Fix**
`closed = (markets.closed OR EXCLUDED.closed)` — once true it stays true. Same on `events.closed`. Two-line change.

---

### #15 — Effective rate limit is N× configured rate (one TokenBucket per `PolymarketClient` instance)
**Severity: Critical** — `app/services/polymarket.py:165`, `app/services/rate_limiter.py:9`
**Status: fixed** in commit `ad44c26` (Pass 5 R17 — shipped before the Pass 5 plan was written, hence the "R17" naming). Module-level `_BUCKETS` registry keyed by hostname + `_DecorrelatedJitterWait` + `Retry-After` honoring + default lowered 10 → 8 r/s. See `review/FIXES.md` Pass 5 R17 entry.

**What's wrong**
`TokenBucket` is created fresh in every `PolymarketClient.__init__`. The codebase has 12 distinct `async with PolymarketClient()` sites. There's no module-level singleton. APScheduler runs different jobs concurrently — they don't compete for tokens.

**Example**
`record_signal_price_snapshots` (10-min cron) and `refresh_and_log` (10-min cron) are different jobs with no cross-job lock. They can run simultaneously. Each instantiates its own client → 2 buckets at default 10 r/s = effective 20 r/s outgoing. Add a manual `scripts/run_position_refresh.py` invocation and you have 30 r/s. During a Polymarket overload, this triples your 429 rate; retries burn the bucket faster; downstream book fetches needed by `detect_and_persist_exits` start failing; paper trades miss their auto-close window.

**Fix**
Hoist the `TokenBucket` to module level in `rate_limiter.py` (or as a singleton in `polymarket.py`). Constructor arg becomes a test-only override. ~15 lines + a smoke test that asserts singleton-ness.

---

### #16 — `daily_leaderboard_snapshot` partial failures pollute downstream reads
**Severity: High** — `app/scheduler/jobs.py:110-182`
**Status: fixed** in commits `5f7e81b` (migration 020 creates the `snapshot_runs` table) + `d482f38` (Pass 5 Tier C #6+#16 — crud helpers + jobs hook + `/system/status` surface). Downstream readers can gate on `crud.latest_complete_snapshot_date()` instead of `MAX(snapshot_date)` to avoid mixing partial today + complete yesterday.

**What's wrong**
The job runs 28 combos sequentially. Failures are tracked in an in-memory list — no `snapshot_runs` table, no `is_complete` flag. Per-combo transactions commit independently, so partial data lands in `leaderboard_snapshots`. Downstream readers using `MAX(snapshot_date) GROUP BY category` mix today's incomplete combos with yesterday's complete ones.

**Example**
2026-05-08 nightly run hits a 5-min Polymarket outage during combo 18 (`crypto/all/PNL`). Combos 1-17 succeed, 18-22 fail, 23-28 succeed. The DB now has `snapshot_date = 2026-05-08` for 23 of 28 combos. Yesterday is fully complete. Hybrid mode for crypto reads from a half-populated pool today; UI shows a narrower-than-real leaderboard. Operator only finds out via log inspection.

**Fix**
Persist a `snapshot_runs(snapshot_date PK, total_combos, failed_combos, completed_at, failures JSONB)` row at run end. Downstream readers gate on `failed_combos = 0`. New migration plus ~20 lines in jobs.py.

---

### #17 — Pass 4 zombie filter has a fall-open path on incomplete metadata
**Severity: Medium** — `app/services/polymarket_types.py:155-207`
**Status: fixed** in commit `8ce1b0f` (Pass 5 Tier C #17). New 5th predicate fires when `raw.redeemable IS NULL AND raw.closed IS NULL AND cur_price IS NULL AND _end_date_in_past()`. Reads the raw dict (not the dataclass field, which is bool-coerced) so we distinguish "API didn't send" from "explicitly False." New `ZOMBIE_DROP_INCOMPLETE_METADATA` counter surfaced at `/system/status`.

**What's wrong**
`Position.drop_reason()` returns the first matching reason from a 4-predicate ladder. If a position has `redeemable=None`, `raw['closed']=None`, `cur_price=None`, and `size > 1`, none of the 4 predicates match → kept. Polymarket's data-api occasionally serves stale rows mid-resolution with these fields blank.

**Example**
A position arrives with `redeemable=None`, `raw.closed=None`, `cur_price=None`, `size=4`. The filter keeps it (fail-open). Phase 2 of `refresh_top_trader_positions` calls JIT discovery, which fetches gamma — if gamma also briefly returns nothing (the F26 case), the market gets persisted with `event_id = NULL`, `category = NULL`. Combined with #14, a resolved market briefly looking live can re-enter the signal pool via this path.

**Fix**
Add a 4th predicate: `redeemable is None AND raw.get('closed') is None AND cur_price is None AND _end_date_in_past()` → drop with reason `incomplete_metadata_resolved`. Add the corresponding health counter. ~10 lines.

---

### #18 — `iter_trades` paginator uses the silent `_safe_list_or_empty` path
**Severity: Low/Medium** — `app/services/polymarket.py:386-402`
**Status: fixed** in commit `5730542` (Pass 5 Tier E #18). `get_trades` gains `_paginator_mode: bool = False` kwarg; True path uses `_safe_list_from_response` (raises `ResponseShapeError`). `iter_trades` passes True and re-raises with a logged `iter_trades: aborted at offset=N` so a malformed mid-pagination response can no longer masquerade as clean exhaustion.

**What's wrong**
`iter_trades` paginates `get_trades`, which uses `_safe_list_or_empty` — the version that swallows shape errors and returns `[]`. So when Polymarket returns a 200-OK-with-garbage during overload (R15's exact scenario), pagination silently terminates at end-of-list. Currently constrained: `classify_tracked_wallets` and `compute_trader_category_stats` only take page 1, so the bug only bites if a future caller actually iterates. But `iter_trades` is exported and named to invite that.

**Example**
Some future caller uses `async for trade in pm.iter_trades(wallet)` to process a wallet's full trade history. During a 30-second Polymarket overload, page 2 returns `{"error": "overloaded"}`. The helper returns `[]`, the paginator stops, and the caller silently sees only page 1 (50 trades) instead of the wallet's actual 800 trades. Trader-stats job under-counts by 90%.

**Fix**
Add a paginator-mode parameter to `get_trades` so `iter_trades` raises `ResponseShapeError` and aborts loudly, mirroring how `get_leaderboard` already does it. ~10 lines.

---

### #19 — Dead method `get_market_trades` invites accidental re-introduction of the F12 bug
**Severity: Low** — `app/services/polymarket.py:548-584`
**Status: dropped** per the user's audit-chat decision (off the critical path). Pure dead-code deletion can be done as a one-line cleanup commit anytime; not load-bearing for the UI build.

---

### NEW endpoint — `GET /signals/{signal_log_id}/contributors`

**Status: shipped** in commit `b38f9aa`. Cluster-aware contributors + counterparty panel that backs UI-SPEC.md Section 2's expandable signal card. Returns one row per identity with `cluster_size`, full underlying `wallets` list, dollar fields summed across the FULL cluster's positions on this market, `is_hedged` flag, lifetime PnL/ROI from the latest overall leaderboard. The user's audit-chat decision: do not auto-filter cross-side clusters; expose the cluster's dual-side activity via this panel so the user judges cluster-active markets manually.

---

## Part 4 — Closure (2026-05-08)

Pass 5 closed across 11 commits on `main` (`ad44c26`..`b38f9aa`). All 16 in-scope items shipped + 1 endpoint + 2 explicitly dropped (#7, #19). Smoke baseline grew 579 → **921 across 22 suites**. See `review/FIXES.md` for the per-commit behavioral diffs and `session-state.md` for the closure summary.

**What's wrong**
F12+R4+R7 retired the trades-based counterparty path in favor of positions-based. `get_market_trades` has no production caller. Leaving it around invites a future change to wire it back in with the wrong semantics.

**Fix**
Delete the method (and any test that imports it). 5 minutes.

---

## Part 3 — Priority order for fixing

Ordered by **how badly each item lies to your real-world buy decision**, not by code complexity. Effort estimates assume you write a smoke test for each before applying the fix (the project's standard rigor).

### Tier 0 — read-the-data-wrong before you click Buy (do these first)

| Item | What it lies about | Effort |
|---|---|---|
| **#1** signal_detector wash-trading aggregate | Dollar-skew floor R2 silently broken on cluster-active markets | ~1.5h |
| **#2** counterparty no cluster dedup | Counterparty count is 4× inflated when sybils oppose | ~45min |
| **#3** specialist prior over winners only | Specialist ranking promotes lucky tiny-vol traders | ~45min |
| **#8** bootstrap_p never persisted | BH-FDR comparator pool uses broken Gaussian approximation | ~1h |
| **#9** dedup view excludes unavailable first-fires | Dedup'd backtest non-randomly drops re-fired (stronger) markets | ~30min |
| **#10** exit-strategy missing exit-side slippage | Smart-money-exit overstates edge by 22-70 bps per round trip | ~20min |

### Tier 1 — silent infrastructure that erases your data baseline

| Item | What goes wrong | Effort |
|---|---|---|
| **#14** markets.closed flips back to FALSE | Resolved markets re-enter signal pool; auto-close skips paid-out trades | ~15min |
| **#15** rate limiter per-instance | Concurrent jobs blow past Polymarket's 429 limit; downstream fetches fail | ~30min |
| **#16** daily_snapshot partial failures pollute reads | One 5-min outage during nightly run leaves you reading half-populated leaderboard for 24h | ~45min |
| **#6** stale trader_category_stats silent collapse | Dead nightly job → zero signals fire within 60 days, no visible alert | ~30min |

### Tier 2 — biased readings on metrics you trust

| Item | What it biases | Effort |
|---|---|---|
| **#5** exit_detector cohort recompute SUMs raw | Phantom TRIM events on cluster composition shifts | included with #1 fix |
| **#11** NULL cluster_id → singletons | n_eff inflated when a chunk of rows have NULL gamma event_id | ~10min |
| **#12** latency fallback threshold too lenient | "Latency-adjusted" reading silently 49% un-adjusted | ~15min |
| **#13** win_rate point unweighted vs CI cluster-weighted | Headline number disagrees with its CI in clustered datasets | ~30min |
| **#4** TRIM 20% threshold fires on noise at n=5 | False "smart money trimming" alerts on transient API blips | ~10min |
| **#7** specialist active_recently accepts old monthly | Wastes specialist top-N slots on dormant traders | ~30min |
| **#17** zombie filter fall-open on incomplete metadata | Stale resolved-market rows leak into signal pool | ~30min |

### Tier 3 — defense in depth

| Item | Why bother | Effort |
|---|---|---|
| **#18** iter_trades silent truncation | Future paginator caller will silently truncate without warning | ~15min |
| **#19** delete dead get_market_trades | Prevents accidental F12 regression | ~5min |

---

## If you fix only the top 5 things this week

#1, #2, #3, #8, #10. About **4-5 hours of work** total. After those:

- The dollar-skew floor stops lying on wash-traded markets.
- The counterparty warning tier matches reality (and the "strong warning, 4 traders opposite" you've been seeing on cluster-active markets actually means 4 distinct entities).
- Specialist mode stops promoting lucky tiny-volume lottery winners.
- The BH-FDR correction stops mis-ranking your skewed-distribution slices.
- Smart-money-exit vs hold-to-resolution comparison becomes honest.

Concretely: the markets where you'd be MOST tempted to follow consensus (high count, high dollar-skew, "strong" counterparty signal) are exactly where these bugs compound the most. Real edge on signals you actually trade should improve by single-digit bps on average and meaningfully more on cluster-heavy markets. The Tier 1 items don't change your edge in normal weather but are the difference between "I noticed quickly when something broke" and "I traded against stale data for a week."

---

## Web research note

Step 3 included a comparative-research subagent (web-only, no filesystem) to surface 2025–2026 published failure modes for Polymarket smart-money trackers. It was sandbox-blocked from both `WebFetch` and `WebSearch` and produced no findings. The 19 code-side findings above stand without web corroboration.

---

## Files referenced

- [signal_detector.py:300](app/services/signal_detector.py:300)
- [counterparty.py:110](app/services/counterparty.py:110)
- [trader_ranker.py:296](app/services/trader_ranker.py:296)
- [exit_detector.py:140](app/services/exit_detector.py:140)
- [backtest_engine.py:337](app/services/backtest_engine.py:337) — Kish n_eff
- [backtest_engine.py:469](app/services/backtest_engine.py:469) — exit P&L
- [backtest_engine.py:651](app/services/backtest_engine.py:651) — dedup filter
- [backtest_engine.py:1032](app/services/backtest_engine.py:1032) — latency
- [crud.py:392](app/db/crud.py:392) — upsert_market
- [crud.py:727](app/db/crud.py:727) — slice_lookups insert
- [polymarket.py:165](app/services/polymarket.py:165) — rate limiter
- [polymarket_types.py:155](app/services/polymarket_types.py:155) — zombie filter
- [jobs.py:110](app/scheduler/jobs.py:110) — daily_snapshot
- [migrations/002_backtest_schema.sql:170](migrations/002_backtest_schema.sql:170)
- [migrations/007_dedup_view.sql:14](migrations/007_dedup_view.sql:14)
