# Pass 5 Implementation Plan

> Read order for any agent picking this up cold:
> 1. `CLAUDE.md` (project rules)
> 2. `session-state.md` (current state, latest commit)
> 3. `review/PASS5_AUDIT.md` (the audit findings this plan implements)
> 4. `review/FIXES.md` (every prior fix — do not duplicate or regress)
> 5. **This file** (the build plan)

---

## Context for a fresh session

**Branch:** `main`. **Latest commits:**
- `ad44c26` Pass 5 R17: rate-limiter consolidation (per-host shared bucket) — **shipped**
- `37e76b9` Pass 5 audit report + UI-SPEC follow-ups — **shipped**

**Smoke baseline:** 623 tests passing across 11 suites. Every change in this plan must keep that count green and add new tests for the fix it implements. Final target post-plan: ~750+ tests across 11 suites.

**Migrations live in Supabase:** 001-013, 015-017 (no 014). Next sequential numbers available: **018, 019, 020**.

**Project rules (from CLAUDE.md, not negotiable):**
- All Polymarket API calls go through `app/services/polymarket.py` only.
- Business logic in `services/`, not `routes/`.
- Routes are thin — call a service, return result.
- All DB access through `app/db/crud.py`.
- Never commit `.env`. Never hardcode hostnames or paths.
- Every fix follows test-first rigor: read code → write failing test → apply minimal fix → test passes → run full smoke suite → no regressions → update `review/FIXES.md` → commit.

**Decisions made during the audit chat that this plan honors (do not re-litigate):**
- **#1 cross-side cluster handling:** signals fire as the math computes (no auto-filtering). The UI surfaces contributing wallets + counterparty wallets + a hedge flag so the user can judge manually. New endpoint `GET /signals/{id}/contributors`. UI-SPEC Section 2 already documents the contract.
- **#5** is folded into the **#1** fix family (identity-collapse SUM in signal_detector + counterparty + exit_detector). Not a separate item.
- **#7** dropped — the user judged the finding too weak to act on.
- **#19** dropped — pure dead-code deletion, can be done as an appendix anytime; not on the critical path.
- **#15** **already shipped** in commit `ad44c26` (rate-limiter consolidation). Not in this plan.
- **#14 reverse-flip risk** acknowledged: monotonic-up `closed` is the right default; a brief mis-flag is rarer than the blip-back-to-false case. Manual SQL fix path documented.

**16 open items** remain after the audit chat decisions:
**#1, #2, #3, #4, #6, #8, #9, #10, #11, #12, #13, #14, #16, #17, #18** + the new **`/signals/{id}/contributors`** endpoint.

---

## How to use this plan

Each item below has:

- **Audit ref:** the finding number from `PASS5_AUDIT.md` and severity.
- **What's wrong:** one-paragraph plain-English recap.
- **The fix:** exact code/SQL or a precise pseudocode sketch.
- **Files to touch:** explicit paths.
- **Migration?** Yes/no. If yes, the migration filename and SQL.
- **Tests:** what to add or modify in which smoke suite, with exact assertions.
- **Verification:** how to confirm it actually works.

Items are listed in **execution order** that respects dependencies. Items inside the same tier are independent and can be done in any order or in parallel.

**Execution tier overview:**
- **Tier A (do first, safest):** Migrations 018, 019, 020. Schema-only, no behavior change.
- **Tier B (Tier 0 critical from audit):** #1+#2+#5 family, #3, #8, #9, #10. These move actual edge.
- **Tier C (operational + observability):** #6, #14, #16, #17.
- **Tier D (math/correctness):** #4, #11, #12, #13.
- **Tier E (defense in depth + endpoint):** #18, contributors endpoint.

Suggested commit grouping at the end.

---

## Tier A — Migrations (do first, no behavior change)

Three additive migrations. Apply in order to live Supabase via `mcp__supabase__apply_migration`. Smoke tests don't need to wait for DB application — they assert against the migration file content.

### Migration 018 — `slice_lookups.bootstrap_p` column

**For audit item #8.** Adds the missing column F21 deferred.

`migrations/018_slice_lookups_bootstrap_p.sql`:
```sql
-- Migration 018 — Pass 5 — slice_lookups.bootstrap_p column
--
-- F21 (Pass 2) added empirical bootstrap p-values to BacktestResult so
-- BH-FDR ranking would not depend on a Gaussian-from-CI approximation
-- that breaks down on skewed P&L distributions. F21 deferred persisting
-- the value into slice_lookups (would need this migration). Pass 5 #8
-- closes the gap: the current query's p is already accurate, but every
-- prior session entry was returning None for bootstrap_p (the column
-- did not exist), so compute_corrections fell back to the broken
-- approximation for every comparator.

ALTER TABLE slice_lookups
    ADD COLUMN IF NOT EXISTS bootstrap_p NUMERIC;

COMMENT ON COLUMN slice_lookups.bootstrap_p IS
    'Empirical 2-sided bootstrap p-value vs H0: mean=0. F21 + Pass 5 #8. '
    'NULL on rows persisted before this migration — compute_corrections '
    'falls back to _pvalue_from_ci for those (Gaussian-from-CI approx).';
```

### Migration 019 — `vw_signals_unique_market` rebuild excluding unavailable first-fires

**For audit item #9.** Replaces the existing view from migration 007. CREATE OR REPLACE doesn't work for views with column changes — drop and recreate.

`migrations/019_dedup_view_skip_unavailable.sql`:
```sql
-- Migration 019 — Pass 5 #9 — dedup view skips unavailable first-fires
--
-- The original vw_signals_unique_market (migration 007) picked the
-- earliest fire per (condition_id, direction) regardless of whether
-- the order book was readable at that moment. The engine then filtered
-- WHERE signal_entry_source != 'unavailable' AFTER the dedup, dropping
-- the entire (cid, direction) pair when the canonical row had a
-- glitched book — even if a later re-fire of the same market was clean.
-- This non-randomly drops re-fired markets, which correlate with
-- stronger signals.
--
-- The fix: filter unavailable rows BEFORE the DISTINCT ON, so dedup
-- picks the earliest *executable* fire.

DROP VIEW IF EXISTS vw_signals_unique_market;

CREATE VIEW vw_signals_unique_market AS
WITH first_fired AS (
    SELECT DISTINCT ON (condition_id, direction)
        s.id, s.condition_id, s.direction,
        s.first_fired_at,
        s.peak_trader_count, s.peak_aggregate_usdc,
        s.first_top_trader_first_seen_at,
        s.first_top_trader_entry_price, s.first_net_skew,
        s.first_aggregate_usdc, s.first_avg_portfolio_fraction,
        s.first_signal_entry_offer, s.first_signal_entry_mid,
        s.signal_entry_source, s.signal_entry_offer, s.signal_entry_mid,
        s.first_book_liquidity_5c_usdc, s.cluster_id, s.market_type,
        s.counterparty_count, s.counterparty_warning,
        s.first_net_dollar_skew, s.contributing_wallets,
        s.book_liquidity_5c_usdc, s.book_spread_bps,
        s.median_top_trader_entry, s.lens_count, s.lens_list
    FROM signal_log s
    WHERE COALESCE(s.signal_entry_source, '') != 'unavailable'
    ORDER BY s.condition_id, s.direction, s.first_fired_at ASC, s.id ASC
)
SELECT * FROM first_fired;

COMMENT ON VIEW vw_signals_unique_market IS
    'Dedup canonical-row view per (condition_id, direction). Pass 5 #9: '
    'unavailable first-fires are filtered BEFORE the DISTINCT ON so the '
    'view picks the earliest executable fire, not the earliest fire '
    'period. Markets where every fire was unavailable are absent (correct).';
```

**Note:** the SELECT column list must match the source-of-truth columns on `signal_log`. If `signal_log` has columns not listed above (especially Pass 3+ additions), adjust the list. Check with `\d signal_log` or the equivalent before applying. The current list is based on session-state.md's Pass 3 schema notes — verify by reading [crud.py upsert_signal](app/db/crud.py).

### Migration 020 — `snapshot_runs` completeness table

**For audit item #16.**

`migrations/020_snapshot_runs.sql`:
```sql
-- Migration 020 — Pass 5 #16 — snapshot_runs completeness ledger
--
-- daily_leaderboard_snapshot runs 28 sub-combos sequentially. Pre-fix,
-- partial failures (one combo fails, the others commit) left a half-
-- populated leaderboard with no completeness flag. Downstream readers
-- doing MAX(snapshot_date) GROUP BY category mixed today's incomplete
-- combos with yesterday's complete data, with no operator-visible
-- signal except log inspection.
--
-- This table records each run's completeness so readers can gate on
-- failed_combos = 0 and so the /system/errors page (UI-SPEC Section 8)
-- can surface the failure with full context.

CREATE TABLE IF NOT EXISTS snapshot_runs (
    snapshot_date    DATE PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL,
    completed_at     TIMESTAMPTZ NOT NULL,
    total_combos     INTEGER NOT NULL,
    succeeded_combos INTEGER NOT NULL,
    failed_combos    INTEGER NOT NULL,
    failures         JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_seconds NUMERIC NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshot_runs_completed_at
    ON snapshot_runs (completed_at DESC);

COMMENT ON TABLE snapshot_runs IS
    'One row per daily_leaderboard_snapshot run. Pass 5 #16: failures '
    'JSONB list of {combo_label, error_repr}. Downstream readers gate '
    'on failed_combos = 0 to avoid mixing partial data.';
```

**Smoke check for all three migrations:**

`scripts/smoke_phase_pass5_migrations.py` (new file or append to existing pass5 suite):
- Read `migrations/018_slice_lookups_bootstrap_p.sql` — assert `bootstrap_p NUMERIC` substring present.
- Read `migrations/019_dedup_view_skip_unavailable.sql` — assert `signal_entry_source` filter is `WHERE` clause **before** any `ORDER BY`/`DISTINCT ON`.
- Read `migrations/020_snapshot_runs.sql` — assert `failed_combos INTEGER NOT NULL` and `failures JSONB`.
- DB integration: against the live Supabase, assert each new column / view / table exists.

---

## Tier B — Tier 0 critical from audit (move actual trading edge)

### Item #1+#2+#5 — Sybil cluster collapse in signal_detector + counterparty + exit_detector

**Audit ref:** PASS5_AUDIT.md #1 (Critical), #2 (Critical), #5 (High).
**Severity:** the dollar-skew floor R2 is silently broken on cluster-active markets; counterparty count is N× inflated when sybils oppose; exit_detector recompute produces phantom trims when cluster composition shifts.

**One conceptual fix in three files: identity-collapse the dollar SUM (and the multi-position COUNT) before the outer aggregate so a multi-wallet cluster contributes once per direction at its real exposure level.**

#### 1A — `signal_detector._aggregate_positions`

**File:** `app/services/signal_detector.py`, the `_aggregate_positions` SQL (currently `direction_agg` and `market_totals` CTEs).

**The fix:** add an `identity_positions` CTE between `pool_positions` and `direction_agg` that pre-aggregates `current_value` and `size` per `(identity, condition_id, outcome)`. Downstream CTEs sum from this collapsed input. Same for `market_totals` denominator.

Replace the SQL skeleton (current at signal_detector.py:243-352) with:

```sql
WITH wallet_pool AS (...unchanged...),
wallet_identity AS (...unchanged...),
latest_pv AS (...unchanged...),
pool_positions AS (...unchanged...),
-- NEW: collapse multi-wallet positions in the same cluster down to one
-- row per (identity, market, outcome). Cluster's "real exposure" on a
-- side is the sum of its wallets' positions on that side, treated as
-- one entity going forward.
identity_positions AS (
    SELECT
        identity,
        condition_id,
        outcome,
        SUM(current_value) AS current_value,
        SUM(size)          AS size,
        AVG(cur_price)     AS cur_price,
        MIN(first_seen_at) AS first_seen_at,
        SUM(avg_price * size) / NULLIF(SUM(size), 0) AS avg_entry_price,
        MAX(portfolio_value) AS portfolio_value,
        ANY_VALUE(question)  AS question,
        ANY_VALUE(slug)      AS slug,
        ANY_VALUE(category)  AS category,
        ANY_VALUE(event_id)  AS event_id,
        ARRAY_AGG(DISTINCT proxy_wallet) AS wallets_in_identity
    FROM pool_positions
    GROUP BY identity, condition_id, outcome
),
direction_agg AS (
    SELECT
        condition_id, outcome,
        ANY_VALUE(question) AS question,
        ANY_VALUE(slug)     AS slug,
        ANY_VALUE(category) AS category,
        ANY_VALUE(event_id) AS event_id,
        COUNT(DISTINCT identity) AS trader_count,
        SUM(current_value)       AS aggregate_usdc,
        AVG(CASE WHEN portfolio_value > 0
                 THEN current_value / portfolio_value
                 ELSE NULL END) AS avg_portfolio_fraction,
        AVG(cur_price)           AS current_price,
        MIN(first_seen_at)       AS earliest_first_seen_at,
        CASE WHEN SUM(size) > 0
             THEN SUM(avg_entry_price * size) / SUM(size)
             ELSE NULL
        END AS avg_entry_price,
        ARRAY_AGG(DISTINCT w) AS contributing_wallets
    FROM identity_positions, UNNEST(wallets_in_identity) AS w
    GROUP BY condition_id, outcome
),
market_totals AS (
    SELECT
        condition_id,
        COUNT(DISTINCT identity) AS traders_any_direction,
        SUM(current_value)       AS total_dollars_in_market
    FROM identity_positions
    WHERE LOWER(outcome) IN ('yes', 'no')
    GROUP BY condition_id
)
SELECT ...same final SELECT...
```

**Behavioral notes:**
- `aggregate_usdc` no longer double-counts a cluster's wallets. A 4-wallet cluster with $20k each on YES contributes one row to `identity_positions` with `current_value = $80k`. Same total, but now correctly attributed to one identity.
- `total_dollars_in_market` on a wash-trading cluster (cluster on both sides) is unchanged numerically (the cluster's both legs still both contribute), but the identity is counted once. **The user's call from the chat:** signals still fire as the math computes; the UI surfaces the cluster's dual-side activity via the contributors endpoint so the user can judge manually. Do not auto-filter cross-side clusters.
- `contributing_wallets` array is now derived from the collapsed identity rows but unnested back to wallets so the downstream R3b cohort tracking continues to work (the `signal_log.contributing_wallets` column stores raw wallet addresses, not cluster IDs — the exit detector resolves clusters at recompute time via `cluster_membership`).

**Test additions** (smoke_phase_b56.py or smoke_phase_b2.py — new section "Pass 5 #1 cluster-collapse"):

1. Synthetic 4-wallet cluster, all on YES at $20k each, plus 1 retail at $5k on YES.
   - Pre-fix: `aggregate_usdc` = $85k, `trader_count` = 2 (cluster + retail).
   - Post-fix: `aggregate_usdc` = $85k (unchanged for one-sided cluster), `trader_count` = 2.
   - **No behavior change for one-sided clusters.** Verify.

2. Synthetic cluster with $70k YES (3 wallets) + $20k NO (1 wallet), plus 4 retail at $5k on YES.
   - Pre-fix: `aggregate_YES` = $90k, `aggregate_NO` = $20k, `total_dollars` = $110k, dollar_skew_YES = 82%.
   - Post-fix: Same numbers (the cluster's dollars are still there on each side), but `trader_count_YES` = 5 (cluster identity + 4 retail) and `traders_any_direction` = 5 (cluster counted once even though on both sides).
   - **The fix doesn't change firing behavior here — the dual-side cluster is a UX problem solved by the contributors endpoint, not a math fix.**

3. Pure wash-trading cluster: same cluster on both sides at equal $$ (e.g. $50k YES + $50k NO), no other traders.
   - `trader_count_YES` = 1, `trader_count_NO` = 1, `traders_any_direction` = 1.
   - Below the 5-trader floor → no signal fires. **Verify the cluster alone cannot fire a signal.**

#### 1B — `counterparty.find_counterparty_wallets`

**File:** `app/services/counterparty.py`, `find_counterparty_wallets` SQL (currently lines 110-148).

**The fix:** add `cluster_membership` join, collapse same/opposite USDC per identity, evaluate `is_counterparty` per identity (not per wallet). Return one row per counterparty *entity*, not per wallet.

Replace the SQL with:

```sql
WITH wallet_identity AS (
    SELECT
        proxy_wallet,
        COALESCE(cm.cluster_id::text, proxy_wallet) AS identity
    FROM unnest($4::TEXT[]) AS proxy_wallet
    LEFT JOIN cluster_membership cm USING (proxy_wallet)
),
agg AS (
    SELECT
        wi.identity,
        SUM(CASE WHEN LOWER(p.outcome) = LOWER($1) THEN p.current_value ELSE 0 END) AS same_usdc,
        SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END) AS opposite_usdc,
        ARRAY_AGG(DISTINCT p.proxy_wallet) AS wallets
    FROM positions p
    JOIN wallet_identity wi USING (proxy_wallet)
    WHERE p.condition_id = $3
      AND p.size > 0
      AND LOWER(p.outcome) IN ('yes', 'no')
    GROUP BY wi.identity
    HAVING SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END) > 0
)
SELECT identity, same_usdc, opposite_usdc, wallets FROM agg;
```

Then in Python, iterate identity rows applying `is_counterparty(same_usdc, opposite_usdc)`. The returned dict gains a `wallets` field (list of raw addresses) so the contributors endpoint can show "Cluster A · 4 wallets" with the underlying wallet list.

**Test additions** (smoke_phase_b2.py "Pass 5 #2 counterparty cluster"):
1. 4-wallet cluster on opposite side at $20k each → `count = 1` (not 4).
2. 4-wallet cluster at $4k each ($16k total) → `count = 1` (clears $5k floor at the entity level).
3. 4-wallet cluster at $1k each ($4k total) → `count = 0` (below floor).
4. Two distinct entities (1 cluster of 3 + 1 lone wallet) both on opposite side → `count = 2`.

#### 1C — `exit_detector._recompute_one_signal_aggregates_for_cohort`

**File:** `app/services/exit_detector.py`, function at lines 122-169.

**The fix:** the existing function already does cluster-collapse for COUNT. Make the SUM consistent with the new identity-collapse pattern from #1A. Two-step inner aggregate:

```sql
WITH cohort AS (
    SELECT proxy_wallet FROM unnest($1::TEXT[]) AS proxy_wallet
),
wallet_identity AS (
    SELECT
        c.proxy_wallet,
        COALESCE(cm.cluster_id::text, c.proxy_wallet) AS identity
    FROM cohort c
    LEFT JOIN cluster_membership cm USING (proxy_wallet)
),
identity_agg AS (
    SELECT
        wi.identity,
        SUM(p.current_value) AS identity_usdc
    FROM positions p
    JOIN wallet_identity wi USING (proxy_wallet)
    JOIN markets m ON m.condition_id = p.condition_id
    WHERE p.condition_id = $2
      AND p.size > 0
      AND p.last_updated_at >= NOW() - INTERVAL '30 minutes'
      AND m.closed = FALSE
      AND UPPER(p.outcome) = UPPER($3)
    GROUP BY wi.identity
    HAVING SUM(p.current_value) > 0
)
SELECT
    COUNT(*)::INT                AS trader_count,
    COALESCE(SUM(identity_usdc), 0)::NUMERIC AS aggregate_usdc
FROM identity_agg;
```

**Note:** The change makes `aggregate_usdc` add up identity dollar exposure (same total as before for one-sided clusters; differs only when one wallet of a cluster goes flat — the identity's `identity_usdc` decreases consistently with the COUNT staying at 1, instead of the old SUM dropping while COUNT stayed flat). This makes peak vs current comparison consistent at the entity level — the audit's #5 concern.

**Important:** `peak_aggregate_usdc` was previously written at fire time with the old (raw-sum) SQL in signal_detector. After #1A lands, future signals are written with identity-collapsed peaks. **Pre-existing signal_log rows have raw-sum peaks** — they are slightly different by construction. Either backfill `peak_aggregate_usdc` from a one-shot SQL, or accept the inconsistency for legacy rows. **Recommendation:** accept the inconsistency. The old peaks are within ~5-10% of identity-collapsed values for typical clusters; the exit detector's 25% TRIM threshold (after item #4 lands) absorbs the noise. Document in FIXES.md.

**Test additions** (smoke_phase_b1.py "Pass 5 #5 exit cohort cluster-collapse"):
1. 4-wallet cluster all on YES, peak fixture `peak_aggregate_usdc = $80k`. Recompute with all 4 wallets still holding $20k each → `cur_agg = $80k`, no drop.
2. Same cluster but one wallet sold out → `cur_agg = $60k` (identity-summed), 25% drop. With `TRIM_THRESHOLD = 0.25` (item #4) this is the boundary — should NOT fire TRIM (drop must be `>= threshold`, so 25% does fire — adjust threshold or test to `>` boundary; clarify with the threshold change in #4).
3. Pre-Pass-5 peak (raw-sum) vs post-Pass-5 cur (identity-sum) edge: document but don't fail-test.

---

### Item #3 — Specialist Bayesian prior over winners only

**Audit ref:** PASS5_AUDIT.md #3 (Critical).

**File:** `app/services/trader_ranker.py`, `_rank_specialist` at lines 264-373, plus `gather_union_top_n_wallets` at lines 376-534.

**The fix:** split the `base` CTE in two. `prior_pool` runs the same SELECT but without the `pnl > 0` filter, the `resolved_trades >= $5` filter, and the `active_recently` filter — that's the honest category baseline used as the shrinkage target. `base` keeps all the candidate-restricting filters and is what we rank.

**`_rank_specialist` rewrite sketch:**

```sql
WITH stats_seeded AS (...),
active_recently AS (...),
-- NEW: prior pool is the FULL category, not just specialists who happen
-- to be winning. F1 fixed this for hybrid; #3 fixes the same bug
-- relocated to specialist mode.
prior_pool AS (
    SELECT ls.pnl, ls.vol
    FROM leaderboard_snapshots ls
    JOIN traders t USING (proxy_wallet)
    WHERE ls.snapshot_date = $2
      AND ls.category = $1
      AND ls.time_period = 'all'
      AND ls.order_by = 'PNL'
      AND ls.vol >= $3
      -- DELIBERATELY no pnl > 0, no active_recently, no resolved_trades
      -- floor — this is the population baseline, not the candidate set.
      {_EXCLUDE_CONTAMINATED_SQL}
),
cat_avg AS (
    SELECT COALESCE(SUM(pnl)::NUMERIC / NULLIF(SUM(vol), 0), 0) AS prior_roi
    FROM prior_pool
),
base AS (
    -- Candidate set: keeps all the original specialist filters
    SELECT ...same as before with pnl > 0, active_recently, resolved_trades floor...
),
shrunk AS (
    SELECT b.*,
           ...,
           (b.pnl + $6 * COALESCE(c.prior_roi, 0)) / NULLIF(b.vol + $6, 0) AS shrunk_roi
    FROM base b
    CROSS JOIN cat_avg c
)
SELECT ... FROM shrunk ...
```

**Same change pattern in `gather_union_top_n_wallets`'s `cat_avg` CTE** at lines 454-460. There the prior is currently computed from `base` which has its own filters — those filters are looser (the recency filter only, not pnl > 0), but verify by reading and apply the same split: `prior_pool` per category from leaderboard snapshots without recency or contamination filters, `base` filtered as before, prior computed from `prior_pool`.

**Tests** (smoke_phase_b56.py "Pass 5 #3 specialist prior over full pool"):
1. Synthetic category: 60 winners with $20M PnL on $400M vol, 40 losers with -$15M on $200M vol.
   - Buggy prior: 5M / 400M = 0.0125? Actually $20M/$400M = 0.05 (5%).
   - Honest prior: $5M / $600M = 0.0083 (0.83%).
   - Synthetic specialist with $5k pnl on $25k vol → assert their `shrunk_roi` is ~0.072 not ~0.10 (post-fix prior brings them down).
2. Hybrid mode unchanged (`_rank_hybrid` already correct from F1) — sanity-check that hybrid's prior remains over its full vol-floor pool.

---

### Item #8 — Persist and read `bootstrap_p` in `slice_lookups`

**Audit ref:** PASS5_AUDIT.md #8 (Critical). **Depends on migration 018.**

**Files:**
- `app/db/crud.py:727-787` — `insert_slice_lookup` and `get_session_slice_lookups`.
- `app/api/routes/backtest.py` — wherever `insert_slice_lookup` is called (search for it; probably in `get_summary` and `get_slice` route handlers).
- `app/services/backtest_engine.py:1149-1170` — `compute_corrections` already prefers `e.get("bootstrap_p")` (F21 left this in place); after this fix every prior session entry will have a real value to return.

**`insert_slice_lookup` signature change:**
```python
async def insert_slice_lookup(
    conn,
    slice_definition: dict,
    n_signals: int,
    reported_metric: str,
    reported_value: float | None,
    ci_low: float | None,
    ci_high: float | None,
    bootstrap_p: float | None = None,  # NEW
) -> None:
    await conn.execute(
        """
        INSERT INTO slice_lookups
            (slice_definition, n_signals, reported_metric, reported_value,
             ci_low, ci_high, bootstrap_p)
        VALUES ($1::jsonb, $2, $3, $4, $5, $6, $7)
        """,
        json.dumps(slice_definition),
        n_signals, reported_metric, reported_value, ci_low, ci_high,
        bootstrap_p,
    )
```

**`get_session_slice_lookups` SQL update:**
```sql
WITH deduped AS (
    SELECT DISTINCT ON (slice_definition)
        ran_at, reported_value, ci_low, ci_high, bootstrap_p
    FROM slice_lookups
    WHERE ran_at >= $1
    ORDER BY slice_definition, ran_at DESC
)
SELECT reported_value, ci_low, ci_high, bootstrap_p
FROM deduped
ORDER BY ran_at
```

Update the Python list-of-dicts return shape to include `bootstrap_p`.

**Route changes:** `app/api/routes/backtest.py` calls `insert_slice_lookup`. Pass through `result.pnl_bootstrap_p` from the `BacktestResult` returned by `backtest_with_rows`.

**Tests:**
- `smoke_phase_b78.py` (or pass5 file) — append a test that inserts a slice with `bootstrap_p=0.04`, fetches via `get_session_slice_lookups`, asserts the value round-trips.
- Append a test that runs `compute_corrections` against a synthetic session entry with `bootstrap_p=0.04` and verifies the BH-FDR alpha consumes it (not the Gaussian fallback).

---

### Item #9 — Dedup view skips unavailable first-fires

**Audit ref:** PASS5_AUDIT.md #9 (Critical). **Depends on migration 019.**

**File-side work:** none — the migration 019 SQL is the entire fix. The engine already filters `WHERE signal_entry_source != 'unavailable'` (backtest_engine.py:651-652) which becomes a no-op redundancy after the view filters first; safe to leave or remove.

**Tests** (smoke_phase_pass5_migrations.py or new section):
- Insert two synthetic signal_log rows for the same (cid, direction): row A at t=10:00 with `signal_entry_source='unavailable'`, row B at t=10:30 with `signal_entry_source='clob_book'`.
- Query `vw_signals_unique_market` for that (cid, direction).
- Pre-fix: returns row A (which the engine then discards → market lost).
- Post-fix: returns row B (clean fire, used in backtest).

---

### Item #10 — Smart-money-exit P&L missing exit-side slippage

**Audit ref:** PASS5_AUDIT.md #10 (Critical).

**File:** `app/services/backtest_engine.py:469-517`, function `compute_pnl_per_dollar_exit`.

**The fix:** mirror the entry slippage on the exit side. The slippage helper already exists; just call it.

```python
def compute_pnl_per_dollar_exit(
    entry_price: float,
    exit_bid_price: float,
    liquidity_5c_usdc: float | None,
    trade_size_usdc: float,
    category: str | None,
) -> float:
    entry_slip = _slippage_per_dollar(trade_size_usdc, liquidity_5c_usdc)
    effective_entry = min(0.999, entry_price + entry_slip)

    # NEW: symmetric exit-side slippage. Selling shares back into the
    # book pushes the price down — the realistic exit price is below
    # the displayed bid by the same impact factor as entry.
    exit_slip = _slippage_per_dollar(trade_size_usdc, liquidity_5c_usdc)
    effective_exit = max(0.001, exit_bid_price - exit_slip)

    revenue_per_dollar = effective_exit / effective_entry
    entry_fee_per_dollar = compute_taker_fee_per_dollar(effective_entry, category)
    # NEW: exit-side taker fee uses effective_exit (post-slippage)
    exit_fee_per_dollar = (
        compute_taker_fee_per_dollar(effective_exit, category)
        * revenue_per_dollar
    )
    return revenue_per_dollar - 1.0 - entry_fee_per_dollar - exit_fee_per_dollar
```

**Tests** (smoke_phase_b78.py "Pass 5 #10 exit slippage symmetric"):
1. $100 trade, $50k liquidity, entry $0.40, exit_bid $0.55. Pre-fix per-dollar P&L = X; post-fix per-dollar P&L = X - ~0.0022. Worked example numbers from the audit; reproduce.
2. $100 trade, $5k liquidity (thinner) → pre/post diff ~0.007 per dollar.
3. Resolution path (`compute_pnl_per_dollar`) unchanged — no exit slippage on a $1 settlement. Sanity test.

---

## Tier C — Operational + observability

### Item #6 — Stale `trader_category_stats` freshness gate

**Audit ref:** PASS5_AUDIT.md #6 (High).

**Files:** `app/services/trader_ranker.py` (every `stats_seeded` site), `app/services/health_counters.py` (new counter), `app/api/routes/system.py` (surface counter).

**The fix:** add a `stats_fresh` CTE alongside `stats_seeded`. If stats are seeded but stale (>7 days old), behave as if not seeded (skip the recency filter) AND record a `STATS_STALE` counter so the operator sees it in `/system/status` and the upcoming Errors page.

**`_rank_absolute`, `_rank_hybrid`, `_rank_specialist`, `gather_union_top_n_wallets` all need the same change:**

```sql
WITH stats_seeded AS (
    SELECT EXISTS (SELECT 1 FROM trader_category_stats LIMIT 1) AS has_data
),
stats_fresh AS (
    SELECT COALESCE(MAX(last_trade_at), 'epoch'::TIMESTAMPTZ) >= NOW() - INTERVAL '7 days'
        AS is_fresh
    FROM trader_category_stats
),
-- ...rest unchanged...
WHERE ...
  AND (
      NOT (SELECT has_data FROM stats_seeded)
      OR NOT (SELECT is_fresh FROM stats_fresh)
      OR tcs.last_trade_at >= NOW() - make_interval(days => $5)
  )
```

The recency filter is now bypassed in two cases: not seeded yet (bootstrap), or seeded but stale (recovery from a dead nightly job).

**`health_counters.py`:**
```python
STATS_STALE = "stats_stale"
# Rolling 1h retention; when set, indicates trader_category_stats hasn't
# been refreshed in >7 days (nightly job dead or stuck).
```

**`/system/status`:** surface as `stats_freshness: {seeded: bool, fresh: bool, last_refresh: timestamp}`. The Errors page (UI-SPEC Section 8) will surface it as a high-severity entry.

**Tests** (smoke_phase_pass5_*.py or smoke_phase_b56):
1. Synthetic seed with `last_trade_at` from 9 days ago → ranker bypasses recency filter (returns wallets).
2. Seed with `last_trade_at` from 5 days ago → ranker enforces recency.
3. Health counter is set when stale.

### Item #14 — `markets.closed` and `events.closed` monotonic

**Audit ref:** PASS5_AUDIT.md #14 (Critical).

**File:** `app/db/crud.py:392` (`upsert_market`) and `:348` (`upsert_event`).

**The fix:** change `closed = EXCLUDED.closed` to `closed = (markets.closed OR EXCLUDED.closed)` in both upserts. Once closed=true is written, subsequent gamma blips with closed=false cannot flip it back.

```sql
-- in upsert_market ON CONFLICT clause
closed = (markets.closed OR EXCLUDED.closed),

-- in upsert_event ON CONFLICT clause
closed = (events.closed OR EXCLUDED.closed),
```

**Tests** (smoke_phase_a2.py "Pass 5 #14 markets.closed monotonic"):
1. Insert market with `closed=true`. Re-upsert with `closed=false` (simulating gamma blip). Assert row's `closed` stays `true`.
2. Insert market with `closed=false`. Upsert with `closed=true`. Assert flips to `true`.
3. Insert event with `closed=true`. Re-upsert with `closed=false`. Assert stays `true`.

**Manual remediation note for FIXES.md:** in the rare case gamma incorrectly flags closed=true on a still-live market, manual fix is one SQL: `UPDATE markets SET closed = FALSE WHERE condition_id = '...';`. Document.

### Item #16 — `snapshot_runs` completeness ledger

**Audit ref:** PASS5_AUDIT.md #16 (High). **Depends on migration 020.**

**Files:**
- `app/db/crud.py` — new helpers `insert_snapshot_run(snapshot_date, started_at, completed_at, total_combos, succeeded_combos, failed_combos, failures, duration_seconds)` and `latest_complete_snapshot_date()`.
- `app/scheduler/jobs.py:182` — at the end of `daily_leaderboard_snapshot`, call `insert_snapshot_run` with the actual run results.
- `app/api/routes/system.py` — surface `latest_snapshot: {date, complete: bool, failed_combos: int}` in `/system/status`. The Errors page consumer will list partial runs.
- Anywhere downstream code reads `MAX(snapshot_date)` — gate on completeness if appropriate. Search for `MAX(snapshot_date)` and audit each call site.

**Tests** (smoke_phase_a2.py "Pass 5 #16 snapshot_runs completeness"):
1. After a synthetic full success run, assert a `snapshot_runs` row with `failed_combos = 0`.
2. After a partial-failure run (mock 5 failures), assert the row has `failed_combos = 5` and `failures` JSON contains the labels.
3. `latest_complete_snapshot_date()` returns yesterday (full-success) when today's run is partial.

### Item #17 — Pass 4 zombie filter fall-open on incomplete metadata

**Audit ref:** PASS5_AUDIT.md #17 (Medium).

**Files:** `app/services/polymarket_types.py:155-207` (`Position.drop_reason`), `app/services/health_counters.py` (new counter), `app/services/polymarket.py:36-41` (counter map).

**The fix:** add a 5th predicate. A position with all metadata fields blank AND end_date in the past is almost certainly a stale resolved-market row.

```python
def drop_reason(self) -> str | None:
    # ... existing 4 predicates unchanged ...
    if (
        self.redeemable is None
        and self.raw.get("closed") is None
        and self.cur_price is None
        and self._end_date_in_past()
    ):
        return "incomplete_metadata_resolved"
    return None
```

Add `ZOMBIE_DROP_INCOMPLETE_METADATA` to health_counters and the `_ZOMBIE_DROP_COUNTERS` map in polymarket.py.

**Tests** (smoke_phase_pass3_helpers.py "Pass 5 #17 incomplete metadata predicate"):
1. Position with `redeemable=None`, `raw['closed']=None`, `cur_price=None`, `size=4`, `end_date=2026-01-01` (past) → drop with `incomplete_metadata_resolved`.
2. Same position with `end_date=2030-01-01` (future) → kept (live market with weird metadata, fail open).
3. Same position with `cur_price=0.5` (live) → kept (existing path; predicates 1-4 don't match).

---

## Tier D — Math / correctness

### Item #4 — Raise `TRIM_THRESHOLD` to 0.25

**Audit ref:** PASS5_AUDIT.md #4 (High).

**File:** `app/services/exit_detector.py:57`.

**Change:**
```python
TRIM_THRESHOLD = 0.25  # was 0.20
```

**Tests** (smoke_phase_b1.py): update existing TRIM tests to use 0.25 boundary. Add an explicit test: 5-wallet cohort, lose 1 → 20% drop → no TRIM (was: TRIM fired). Lose 2 → 40% drop → TRIM fires (unchanged).

### Item #11 — NULL `cluster_id` collapsed to single shared cluster in Kish n_eff

**Audit ref:** PASS5_AUDIT.md #11 (High).

**File:** `app/services/backtest_engine.py:357-358` (`compute_kish_n_eff`).

**Change:**
```python
def compute_kish_n_eff(cluster_keys: list[str | None]) -> float:
    if not cluster_keys:
        return 0.0
    by_cluster: dict[str, int] = {}
    for k in cluster_keys:
        # Pass 5 #11: NULLs collapse to one shared cluster (worst-case
        # correlation assumption) instead of being treated as distinct
        # singletons. NULL cluster_id means gamma's event_id was missing
        # at sync time — those rows are likely all from the same
        # uncategorized event, not independent observations.
        key = k if k is not None else "__null__"
        by_cluster[key] = by_cluster.get(key, 0) + 1
    sizes = list(by_cluster.values())
    total = sum(sizes)
    if total == 0:
        return 0.0
    sum_sq = sum(s * s for s in sizes)
    return (total * total) / sum_sq
```

**Tests** (smoke_phase_pass3_helpers.py "Pass 5 #11 NULL cluster handling"):
1. `compute_kish_n_eff([None] * 30)` → 1.0 (was 30.0).
2. `compute_kish_n_eff(["A"] * 70 + [None] * 30)` → ~1.72 (was ~2.03).
3. `compute_kish_n_eff(["A", "B", "C"])` → 3.0 (no NULLs, unchanged).

**Note on `cluster_bootstrap_mean_with_p`:** the same "_solo_{i}" pattern at backtest_engine.py:410 should also be updated to use `__null__` for consistency. Audit which behavior is desired:
- For Kish n_eff (this fix): NULLs are correlated → one shared cluster.
- For bootstrap resampling: NULLs as one cluster means the bootstrap will pull all NULL-rows together when resampling — that's the conservative correlated behavior. **Recommended.** Apply the same change to `cluster_bootstrap_mean_with_p`.

### Item #12 — Lower latency fallback warning threshold

**Audit ref:** PASS5_AUDIT.md #12 (High).

**File:** `app/services/backtest_engine.py:1081-1097` (search for `latency_unavailable` and the threshold constant).

**Changes:**
1. Lower the threshold to 0.20.
2. Expose `n_adjusted` and `n_fallback` in the `latency_stats` response payload alongside the boolean flag.

**Route response addition** in `app/api/routes/backtest.py`'s summary handler — surface the counts so UI can display "X of Y rows fell back to fire-time pricing."

**Tests** (smoke_phase_b78.py "Pass 5 #12 latency fallback threshold"):
1. 100 rows, 25 fallback → `latency_unavailable=True` (was False at 25%).
2. 100 rows, 19 fallback → `latency_unavailable=False`.
3. Response includes `n_adjusted=75, n_fallback=25`.

### Item #13 — Win-rate / mean point estimate uses bootstrap median

**Audit ref:** PASS5_AUDIT.md #13 (High).

**File:** `app/services/backtest_engine.py:802, 826-834` (and the parallel mean point estimate around the same area).

**The fix:** replace the unweighted point estimate with the bootstrap median (or the first return value of `cluster_bootstrap_mean` which is already the bootstrap mean). The CI-vs-headline mismatch becomes a non-issue.

```python
# Pass 5 #13: align point estimate with cluster-weighted CI.
# Pre-fix: wr = wins/n (count-weighted) but CI bootstraps clusters.
# Post-fix: use the bootstrap point estimate so the headline number
# matches its confidence interval's weighting.
wr_point, wr_lo, wr_hi = cluster_bootstrap_mean(win_indicators, cluster_keys)
wr = max(0.0, min(1.0, wr_point))  # clamp to [0,1] for win rate
```

Same shape for `mean_pnl_per_dollar` if it's affected (audit and align — currently `point` from `cluster_bootstrap_mean_with_p` is already the bootstrap point per F21, so check whether the *displayed* `mean_pnl_per_dollar` field uses that value or an unweighted `sum(values)/len(values)`. If the latter, switch.

**Tests** (smoke_phase_b78.py "Pass 5 #13 point matches CI weighting"):
1. Synthetic 100 rows: cluster of 70 with 50% win rate, 30 singletons with 80% win rate. Pre-fix `wr=0.59`, post-fix `wr~0.65` (matches bootstrap CI center).
2. Pure singletons (no clustering) → point unchanged (cluster bootstrap with each row its own cluster ≈ unweighted mean).

---

## Tier E — Defense in depth + new endpoint

### Item #18 — `iter_trades` paginator-mode flag

**Audit ref:** PASS5_AUDIT.md #18 (Low/Medium).

**File:** `app/services/polymarket.py:386-402`.

**The fix:** add a kwarg `_paginator_mode: bool = False` to `get_trades`. When True, call `_safe_list_from_response` (the loud version that raises `ResponseShapeError`) instead of `_safe_list_or_empty`. `iter_trades` passes True.

```python
async def get_trades(
    self,
    proxy_wallet: str,
    limit: int = 500,
    offset: int = 0,
    _paginator_mode: bool = False,  # NEW
) -> list[Trade]:
    url = f"{settings.data_api_base}/trades"
    data = await self._get_json(url, params={"user": proxy_wallet, "limit": limit, "offset": offset})
    if _paginator_mode:
        items = _safe_list_from_response(data, "data-api/trades")  # raises on shape err
    else:
        items = _safe_list_or_empty(data, "data-api/trades")
    return [Trade.from_dict(d) for d in items]


async def iter_trades(
    self,
    proxy_wallet: str,
    page_size: int = 500,
) -> AsyncIterator[Trade]:
    offset = 0
    while True:
        try:
            page = await self.get_trades(proxy_wallet, limit=page_size, offset=offset, _paginator_mode=True)
        except ResponseShapeError:
            log.error("iter_trades: aborted at offset=%d for wallet=%s due to API shape error", offset, proxy_wallet)
            raise
        if not page:
            break
        for t in page:
            yield t
        if len(page) < page_size:
            break
        offset += page_size
```

**Tests** (smoke_phase_a.py "Pass 5 #18 iter_trades fail-loud"):
1. Mock `_get_json` to return a dict (shape error). `get_trades(..., _paginator_mode=False)` returns `[]`. `get_trades(..., _paginator_mode=True)` raises `ResponseShapeError`.
2. Mock `iter_trades` to fail on page 2 → exception propagates, no silent truncation.

---

### NEW endpoint — `GET /signals/{signal_log_id}/contributors`

**For UI-SPEC Section 2 contributors panel and the #1 transparency story.**

**Files:**
- `app/db/crud.py` — new helper `get_signal_contributors_and_counterparty(conn, signal_log_id)`.
- `app/api/routes/signals.py` — new route handler.
- Smoke tests in `smoke_phase_pass2_routes.py` extending the existing route-level regression suite.

**Crud helper signature:**
```python
async def get_signal_contributors_and_counterparty(
    conn: asyncpg.Connection,
    signal_log_id: int,
) -> dict:
    """Return contributors + counterparty wallets for a signal, with
    cluster grouping and hedge flags.

    Looks up signal_log row → contributing_wallets array → joins to
    positions + cluster_membership + traders + leaderboard for each
    wallet's labels and current YES/NO position on the signal's market.
    Then runs the same counterparty pool query (top-N tracked, opposite
    side, cluster-aware) for the counterparty list.

    Response shape: matches UI-SPEC Section 2 exactly.
    """
```

**Route handler:** thin — calls crud, returns JSON.

**Response shape (per UI-SPEC.md Section 2):**
```json
{
  "contributors": [
    {
      "proxy_wallet": "0x...",
      "user_name": "Théo",
      "verified_badge": true,
      "cluster_id": 42,
      "cluster_label": "Cluster A",
      "cluster_size": 4,
      "same_side_usdc": 70000,
      "opposite_side_usdc": 20000,
      "is_hedged": true,
      "net_exposure_usdc": 50000,
      "avg_entry_price": 0.40,
      "lifetime_pnl_usdc": 12000000,
      "lifetime_roi": 0.18
    }
  ],
  "counterparty": [...same shape...],
  "summary": {
    "n_contributors": 5,
    "n_hedged_contributors": 1,
    "n_counterparty": 1,
    "total_same_side_usdc": 90000,
    "total_opposite_side_usdc": 80000
  }
}
```

**Crud SQL** (cluster-aware via `cluster_membership` joins):

```sql
-- 1. Pull signal_log row + contributing_wallets array
SELECT contributing_wallets, condition_id, direction
FROM signal_log WHERE id = $1;

-- 2. Per contributor: join cluster_membership, group positions by
-- (identity, outcome), aggregate same/opposite USDC.
WITH cohort AS (
    SELECT proxy_wallet FROM unnest($contributing_wallets::TEXT[]) AS proxy_wallet
),
wallet_identity AS (
    SELECT
        c.proxy_wallet,
        cm.cluster_id,
        COALESCE(cm.cluster_id::text, c.proxy_wallet) AS identity
    FROM cohort c
    LEFT JOIN cluster_membership cm USING (proxy_wallet)
),
identity_positions AS (
    SELECT
        wi.identity,
        wi.cluster_id,
        SUM(CASE WHEN UPPER(p.outcome) = UPPER($direction) THEN p.current_value ELSE 0 END) AS same_usdc,
        SUM(CASE WHEN UPPER(p.outcome) = UPPER(CASE WHEN $direction='YES' THEN 'NO' ELSE 'YES' END) THEN p.current_value ELSE 0 END) AS opposite_usdc,
        ARRAY_AGG(DISTINCT p.proxy_wallet) AS wallets,
        ARRAY_AGG(DISTINCT t.user_name) FILTER (WHERE t.user_name IS NOT NULL) AS user_names,
        BOOL_OR(t.verified_badge) AS verified_badge,
        AVG(p.avg_price) AS avg_entry_price,
        ANY_VALUE(ls.pnl) AS lifetime_pnl_usdc,
        ANY_VALUE(CASE WHEN ls.vol > 0 THEN ls.pnl/ls.vol ELSE 0 END) AS lifetime_roi
    FROM positions p
    JOIN wallet_identity wi USING (proxy_wallet)
    LEFT JOIN traders t ON t.proxy_wallet = p.proxy_wallet
    LEFT JOIN LATERAL (
        SELECT pnl, vol FROM leaderboard_snapshots
        WHERE proxy_wallet = p.proxy_wallet
          AND time_period = 'all'
          AND order_by = 'PNL'
        ORDER BY snapshot_date DESC LIMIT 1
    ) ls ON TRUE
    WHERE p.condition_id = $condition_id
      AND p.size > 0
      AND LOWER(p.outcome) IN ('yes', 'no')
    GROUP BY wi.identity, wi.cluster_id
)
SELECT * FROM identity_positions;

-- 3. Counterparty list: same query but seeded with the current top-N
-- pool (gather_union_top_n_wallets) and inverted same/opposite definitions.
```

**Tests** (smoke_phase_pass2_routes.py "Pass 5 contributors endpoint"):
1. Synthetic signal with 4-wallet cluster on YES + 4 retail on YES → `n_contributors=5` (cluster as 1 entity), `n_hedged_contributors=0`.
2. Same setup with cluster split YES+NO → `is_hedged=true` for cluster row, `n_hedged_contributors=1`.
3. Counterparty list returns one entity per cluster on opposite side, with cluster_size populated.
4. Endpoint returns 404 for non-existent signal_log_id.

**Wire to UI-SPEC:** the spec at Section 2 (`UI-SPEC.md`) is the source of truth for response shape. If a field is added/renamed during implementation, update UI-SPEC.md in the same commit.

---

## Suggested commit grouping

Each commit must pass all smoke suites and a smoke pre-flight before commit. Migrations apply to live Supabase before the matching code commit.

1. **Commit 1 — Migrations 018, 019, 020 + smoke schema checks.** Schema-only. Apply to live Supabase. Smoke tests verify file content + DB structure.
2. **Commit 2 — Tier B item #1+#2+#5 (cluster-collapse family).** SQL changes in three services + extensive smoke coverage. Single conceptual fix → single commit even though it touches multiple files.
3. **Commit 3 — Tier B item #3 (specialist prior fix).** Isolated SQL change in one file.
4. **Commit 4 — Tier B item #8 (bootstrap_p persistence).** Depends on migration 018. crud + routes + engine consumer.
5. **Commit 5 — Tier B item #9 (dedup view fix).** Depends on migration 019. Tests only — migration is the fix.
6. **Commit 6 — Tier B item #10 (exit-side slippage).** Single function change.
7. **Commit 7 — Tier C item #14 (closed monotonic).** Two-line crud change + tests.
8. **Commit 8 — Tier C item #6 + #16 (operational visibility).** Stale-stats freshness gate + snapshot_runs ledger. Depends on migration 020.
9. **Commit 9 — Tier C item #17 (zombie filter incomplete metadata).** Predicate addition + counter.
10. **Commit 10 — Tier D items #4, #11, #12, #13 (math correctness bundle).** All small independent changes; bundling keeps the commit log clean.
11. **Commit 11 — Tier E item #18 (iter_trades fail-loud).**
12. **Commit 12 — `/signals/{id}/contributors` endpoint** (final, depends on #1+#2 cluster machinery being in place).
13. **Final commit 13 — `session-state.md` and `review/FIXES.md` updates documenting Pass 5 closure.**

**Estimated total work:** ~10-15 hours of focused engineering with smoke tests. Tier B items dominate (~6h). Tier C ~2h. Tier D ~1h. Endpoint + Tier E ~3h.

---

## Verification protocol (every commit)

Before any commit:

1. **All 11 smoke suites green.** Existing 623 tests + every new test added in this commit. Zero failures.
2. **Live probe (`scripts/probe_polymarket_endpoints.py`)** runs cleanly if the commit touches `polymarket.py` or anything API-facing.
3. **Live cycle dry-run (`scripts/run_cycle_once.py`)** completes within the 9-min cadence threshold if the commit touches scheduler jobs or signal_detector. Compare cycle duration before/after — significant regressions block the commit.
4. **`/system/status` endpoint healthy** if the commit touches health counters or system routes.
5. **Independent diff-review** — re-read the diff before committing. If it touches behavior outside the stated scope, split the commit.
6. **`review/FIXES.md` entry appended** with the audit-item ref, files touched, test names, and a one-paragraph behavior-change summary. Pin to the next commit number for cross-reference.

After the final commit:

- `session-state.md` updated with new total smoke count, Pass 5 closure summary, and the next-step (UI build).
- All 16 audit items closed in `review/PASS5_AUDIT.md` (mark each Status: fixed with the commit hash).

---

## Risks and watch-outs the next session should know

1. **Cluster-collapse migration drift.** Items #1, #2, #5 share an identity-collapse pattern but with subtly different SQL (signal_detector groups by `(condition_id, outcome)`, counterparty groups per market, exit_detector is per signal). Test each independently; do not assume the patterns are interchangeable.

2. **Pre-Pass-5 `peak_aggregate_usdc` rows** in `signal_log` were written with raw-sum SQL (current bug). Post-fix peaks use identity-collapse. The exit detector compares post-fix current vs pre-fix peak for legacy rows → small noise. Acceptable (the 25% threshold absorbs it). Document in FIXES.md.

3. **`vw_signals_unique_market` column list** in migration 019 must match the live `signal_log` schema exactly. Pull `\d signal_log` (or query `information_schema.columns`) before applying — if `signal_log` has columns the SELECT misses, the view loses them and downstream consumers break.

4. **`compute_kish_n_eff` and `cluster_bootstrap_mean_with_p` NULL handling** must change together (item #11). They use the same pattern at backtest_engine.py:357 and :410. Update both for consistency.

5. **Routes touching `slice_lookups` insert** — search for every `insert_slice_lookup(` call site in `app/api/routes/backtest.py`. Pass `bootstrap_p` to all of them. Missing one site means session entries from that endpoint go in with NULL bootstrap_p and BH-FDR comparator falls back to Gaussian.

6. **`/signals/{id}/contributors` is read-heavy** — the SQL joins positions, cluster_membership, traders, and a lateral subquery per contributor. Add an index on `positions(condition_id, proxy_wallet)` if not present; check existing indexes first.

7. **UI-SPEC.md is the contract** — any field name/shape change in the contributors endpoint must update both the route response AND the UI-SPEC Section 2 doc in the same commit.

8. **Decisions deferred / dropped that may resurface:**
   - **#7** specialist `active_recently` was dropped per the user's call. If a future audit surfaces "specialists with zero recent positions appearing in top-N," revisit.
   - **#19** dead `get_market_trades` deletion was dropped from the critical path. Can be done as a one-line cleanup commit anytime.
   - **#15** rate-limiter is shipped — do not re-research.
   - **Multi-process rate-limiting** (Railway with separate scheduler + API processes) is a known V2 limitation; the in-process bucket doesn't span processes. If/when deploying with multiple workers, revisit with a Redis-backed rate limiter.

9. **Test isolation** — many of the new SQL changes are in CTEs with deep nesting. The smoke test process: write the failing test FIRST against current code, confirm it fails on the targeted bug, THEN apply the fix and confirm it passes. Following this catches false-positive fixes that don't actually address the underlying bug.

10. **Live cycle baseline:** post-Pass-4, the 10-min cycle runs in ~3.3 min. After all Pass 5 items land, the cycle should still run well under 9 min. If duration regresses >50% on any commit, suspect added DB load (e.g. the cluster-collapse joins) and add an index.

---

## Out of scope for this plan

- **UI build.** UI-SPEC.md is the contract; the actual frontend is built externally.
- **Phase 8 data wipe.** Optional; user has not committed to wiping.
- **Railway deploy.** V1 deploy work, not audit work.
- **V2 multi-process rate-limiter (Redis).** Documented as known limitation.
- **#7, #19, #15.** See "Decisions deferred / dropped" above.

---

## Done condition

Pass 5 is closed when:
- All 16 items in this plan are shipped on `main`.
- `review/PASS5_AUDIT.md` has every item marked `Status: fixed` with commit hashes.
- `review/FIXES.md` has a Pass 5 section with one entry per item.
- `session-state.md` updated to reflect Pass 5 closure and the next-step (UI build).
- Smoke count reaches ~750+ across 11 suites, all green.
- One clean live cycle dry-run passes end-to-end.
- `/system/status` healthy across all subsystems.

At that point the backend is ready for the third-party UI build (per UI-SPEC.md) and Railway deploy.
