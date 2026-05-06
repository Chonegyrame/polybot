# Backtest Engine + Stats Review

Audit scope: `app/services/backtest_engine.py`, `app/services/half_life.py`. Read against
`session-state.md` (B4, B7, B8 hot-fix, B10, B11) and the smoke harnesses
`scripts/smoke_phase_b78.py` and `scripts/smoke_phase_b2.py`.

## Critical

### Half-life convergence math mixes YES-space and direction-space prices
- **File**: `app/services/half_life.py:104-114` (loop in `compute_half_life_summary`),
  with the function contract at `app/services/half_life.py:63-71`.
- **Finding**:
  `_yes_price_for_direction(price, direction)` is documented as translating a
  *YES-token price* into the chosen direction's space (so for `direction="NO"`
  it does `1 - price`). The loop calls it on three different inputs:
    - `r.fire_price` — equals `signal_entry_offer`, which the rest of the code
      treats as **direction-space** (see `_favorite_direction` at
      `backtest_engine.py:1097-1104`: "NO signal: signal_entry_offer is the NO
      ask, so YES price ≈ 1 − offer").
    - `r.smart_money_entry` — equals `first_top_trader_entry_price`, also
      direction-space (`gap_to_smart_money` at `backtest_engine.py:281-292`
      compares it to `signal_entry_offer` directly without translation).
    - `r.snapshot_price` — explicitly **YES-space** ("All snapshots are stored
      as YES prices", line 65; B4 spec also says snapshot column is
      `yes_price`).
  So for `direction="NO"`, two of the three inputs are passed through the
  wrong-direction translation and the third is passed through the right one.
  All three values end up in DIFFERENT price spaces before being compared by
  `_moved_toward_smart_money`, which assumes a single common axis.
- **Impact**: Convergence rate for NO-direction signals is computed by
  comparing apples to oranges. For a NO signal where snapshot YES = 0.40,
  fire NO ask = 0.55, smart-money NO entry = 0.50:
    - Function output for fire = `1 - 0.55 = 0.45` (this is YES-space)
    - Function output for snapshot = `1 - 0.40 = 0.60` (this is NO-space)
    - Function output for smart-money = `1 - 0.50 = 0.50` (YES-space)
  Then `|fire - sm| = 0.05` (YES-space), `|snap - sm| = 0.10` (mixed): the
  comparison is meaningless. YES signals happen to be unaffected (the function
  is identity), so the bug only corrupts NO signals — a silent ~50%
  systematic distortion in the half-life table.
- **Suggested fix**: Pick one canonical space (either always YES-space or
  always direction-space) and document where each upstream column lives,
  then translate only the inputs that don't already live there. The cleanest
  patch is: always do everything in YES-space — `r.fire_price` and
  `r.smart_money_entry` are direction-space, so for `direction="NO"`
  translate them via `1 - x` *before* comparison; leave `r.snapshot_price`
  alone. The current `_yes_price_for_direction` either has a wrong docstring
  (claims input is YES, but for fire_price input is direction-space and
  output happens to be YES) or wrong call sites — they need to be reconciled
  with hard knowledge of each column's storage convention.

### Three of four latency profiles always fall back to the optimistic baseline
- **File**: `app/services/backtest_engine.py:55-68` and
  `app/services/backtest_engine.py:888-934`.
- **Finding**: The latency profiles are
  `active=(1,3)`, `responsive=(5,10)`, `casual=(12,20)`, `delayed=(30,60)`.
  The snapshot offsets we actually capture are `(30, 60, 120)` with
  `LATENCY_OFFSET_TOLERANCE_MIN = 5`. `_nearest_snapshot_offset` rejects
  any sampled minute whose distance to the closest canonical offset exceeds
  5. For `active`, `responsive`, `casual` the closest offset is always 30,
  and the closest sample (3, 10, 20 respectively) is at least 10 minutes
  away from 30 — so EVERY row in those profiles falls through to the
  fallback branch (line 928 `new_rows.append(r)`), which keeps the
  original `signal_entry_offer`. The profile becomes a no-op masquerading
  as a real adjustment.
- **Impact**: A user picks "responsive (5–10 min)", sees the same numbers
  as no latency, and concludes "latency doesn't matter" — exactly the
  wrong takeaway. The `latency_stats.fallback` counter does surface this
  but only as raw counts; nothing in the response says "this profile
  cannot be honored with current snapshot coverage." The session-state
  file already flags it as expected, but the engine should reflect it.
- **Suggested fix**: Either (a) capture additional snapshot offsets that
  cover the active/responsive/casual windows (e.g. add 2, 7, 15 minutes),
  or (b) when `n_fallback / (n_fallback + n_adjusted) > 0.5`, set a
  `latency_unavailable: True` flag on the response so the UI can show
  "this profile has no real data behind it yet" instead of pretending
  the latency was simulated.

### Win-rate Wilson CI uses raw n, not cluster-effective n_eff
- **File**: `app/services/backtest_engine.py:702`.
- **Finding**: `wr_lo, wr_hi = wilson_ci(wins, len(pnl_pairs))`. The engine
  goes to the trouble of cluster-bootstrap-ing the P&L mean using
  `cluster_id` (Cameron-Gelbach-Miller, line 700), and reports `n_eff` as
  the distinct-cluster count (line 696-698). But the win-rate Wilson CI is
  computed against the unclustered observation count. With Trump-2024-style
  clusters (hundreds of correlated sub-markets that all resolve together),
  `len(pnl_pairs)` can be 5–10× `n_eff`, and the Wilson CI shrinks by
  roughly √(n / n_eff) more than it should.
- **Impact**: Win-rate confidence intervals are systematically too narrow
  in the presence of cluster correlation. The Bonferroni / BH-FDR
  widenings inherit this narrow base, so the corrected CIs are also too
  tight. The user reads "win rate 0.62 ± 0.04" when honestly it's
  "0.62 ± 0.10".
- **Suggested fix**: Cluster-bootstrap the win rate the same way as the
  mean (treating each pnl_pair as a 0/1 outcome), or compute Wilson on
  effective wins/n where `n_eff_wr = n_eff` and
  `wins_eff = round(wins × n_eff / n)`. Either way, label it consistently
  with how `n_eff` is used elsewhere.

## High

### Cluster bootstrap point estimate ignores cluster weighting; resamples K clusters from K
- **File**: `app/services/backtest_engine.py:316-350`.
- **Finding**: Two related issues:
  1. The reported point estimate `point = sum(values) / len(values)` (line
     347) is the unweighted observation mean, not a cluster-weighted mean.
     If one cluster contains 100 obs and 30 others contain 1 each, the
     point is dominated by the big cluster, but the bootstrap distribution
     resamples cluster keys and so its CI center is closer to the
     cluster-weighted mean. This makes the point estimate land slightly
     outside its own CI in skewed-cluster scenarios.
  2. The bootstrap resamples exactly `len(keys)` clusters per iteration.
     This is the standard Cameron-Gelbach-Miller approach but it produces
     samples whose total observation count equals the cluster mean times
     K — generally NOT equal to N. That's fine for the mean but means
     the empirical distribution of the resampled mean has a different
     (typically larger) variance than is asymptotically right when
     cluster sizes vary substantially.
- **Impact**: For balanced clusters, both effects are negligible. With
  the kind of skew Polymarket actually has (one mega-event spawning 100+
  sub-markets), the CI is biased and the point estimate can be
  inconsistent with it.
- **Suggested fix**: Use the cluster-mean-of-means (one mean per cluster,
  then average) as the point estimate, OR document that "point" is the
  observation-weighted mean and "ci_lo/hi" is an approximation thereof.
  Keep n_iter at 5000 (acceptable for 95% CI tail accuracy).

### `_pvalue_from_ci` Gaussian SE breaks on skewed bootstrap CIs
- **File**: `app/services/backtest_engine.py:113-123`, used by
  `compute_corrections` (line 990, 994).
- **Finding**: SE is approximated as `(hi - lo) / (2 × 1.96)`, which
  assumes a SYMMETRIC Gaussian CI. The cluster bootstrap CI is from
  empirical quantiles of resampled means — for P&L per dollar (heavy
  right tail when a few signals win big at low entry), the CI is
  noticeably asymmetric. A CI like `(0.02, 0.30)` around point `0.08`
  yields SE ≈ 0.071 and z ≈ 1.13, p ≈ 0.26, but the true
  bootstrap-distributional p might be 0.10 or 0.40 depending on tail
  shape. Then BH-FDR ranks all session entries by these approximate
  p-values, and the rank ordering can flip pairs with similar
  significance.
- **Impact**: Direction of correction is right (small p → smaller alpha
  cap), but the BH ranks are noisy in close cases. Bonferroni isn't
  affected (doesn't use ranks). For a tool whose reason-for-existence
  is honest CIs, "the p-value used to rank queries is approximate" is
  worth flagging in the response payload.
- **Suggested fix**: Either (a) when running the bootstrap, also store
  the fraction of resampled means below 0 — that's the exact
  one-sided bootstrap p-value, and `2 × min(p, 1-p)` is the two-sided
  version, no Gaussian assumption needed; or (b) acknowledge in the
  response that BH-FDR rank ordering is approximate.

### BH-FDR rank uses `<=` (ties → highest rank), comment promises "ties → lowest"
- **File**: `app/services/backtest_engine.py:1001`.
- **Finding**: The comment says "Rank of current query among all session
  queries (1-indexed, ties → lowest)" but the code is
  `current_rank = max(1, sum(1 for p in sorted_p if p <= current_pnl_p))`.
  That counts the number of session p-values ≤ the current one — which
  for ties returns the HIGHEST rank, not the lowest. (Lowest-rank
  semantics for ties would be `sum(1 for p in sorted_p if p < current_pnl_p) + 1`.)
- **Impact**: When many session queries are underpowered (p=1.0) and the
  current query is also p=1.0, all of them get rank=N → alpha_bh=0.05 →
  no correction. With "ties go to lowest", they'd all get rank=1 →
  alpha_bh=0.05/N → maximum correction. The CURRENT behavior is
  arguably more permissive than the BH spec intends in pathological
  cases (reduces type-I error control when many trivial queries pile
  up).
- **Suggested fix**: Either fix the code to match the comment, or fix
  the comment to match the code. The code's behavior (ties → highest
  rank) is the more common BH variant in software, but the docstring
  needs to reflect it accurately. The smoke tests don't exercise this
  edge.

### Holdout filter SQL boundary uses date comparison against timestamptz
- **File**: `app/services/backtest_engine.py:576-577`.
- **Finding**: `parts.append(f"AND s.first_fired_at < ${len(args)}")` with
  `holdout_from: date | None`. Postgres implicitly casts `date` to
  `timestamp without time zone` at midnight, then compares to
  `timestamptz`. This is technically a session-timezone-dependent
  comparison — if `TimeZone` is `UTC` (most server defaults), date
  `2026-03-01` becomes `2026-03-01 00:00:00 UTC`, and "fired_at <
  cutoff" excludes signals fired exactly at midnight UTC on that
  date. If the Supabase session timezone ever drifts (e.g. accidentally
  set to Europe/Stockholm via a connection-string param), the cutoff
  shifts by hours.
- **Impact**: For a test set defined by date, ~24 minutes-of-day worth
  of edge-case signals could leak into or out of training. Subtle but
  real for a holdout regime.
- **Suggested fix**: Pass `datetime(holdout_from.year, holdout_from.month,
  holdout_from.day, tzinfo=UTC)` from the route, OR cast in SQL:
  `AND s.first_fired_at < ($N::date)::timestamptz AT TIME ZONE 'UTC'`,
  OR document that the cutoff is "the start of `holdout_from` UTC".

### `pick_offset_for_age` ambiguity at overlap boundaries
- **File**: `app/services/half_life.py:29-40`.
- **Finding**: With `OFFSET_TOLERANCE_MIN = 5`, an age of 32.5 min is
  within ±5 of 30 only (32.5 - 30 = 2.5 ≤ 5; 60 - 32.5 = 27.5 > 5),
  so it maps to 30 — fine. But age 27.5 maps to 30 (close), age 25 to
  30 (exactly within ±5). However ages 25-35 ALL map to 30, then there's
  a gap 35-55 with no canonical offset, then 55-65 maps to 60. The
  scheduler runs every 30 min — if a tick is missed, a signal aged 90
  min won't fall in any tolerance window (90 is 30 min from 60 and 30
  from 120) and gets skipped entirely. The 30-min cadence is JUST
  matched to the offsets, with zero slack.
- **Impact**: When the scheduler skips a tick (rare but possible during
  Railway redeploy / Postgres advisory lock contention), some signals
  silently lose their 30/60/120 snapshot. Subsequent half-life
  calculations omit those rows (which is correct), but the user has
  no signal that "I lost N% of my snapshots due to a skipped tick."
- **Suggested fix**: Widen the tolerance to ±15 min (or capture more
  offsets) so a single skipped tick doesn't drop snapshots; OR record
  attempted-but-missed snapshots so the user can see snapshot coverage
  per offset.

## Medium

### `compute_pnl_per_dollar_exit` doesn't filter VOID, but the resolved-only path does
- **File**: `app/services/backtest_engine.py:358-404` vs
  `app/services/backtest_engine.py:407-472`.
- **Finding**: `compute_pnl_per_dollar` returns `None` for VOID
  resolutions (line 439). `compute_pnl_per_dollar_exit` doesn't check
  the resolution at all because the exit-bid is the realized cash. But
  in `summarize_rows` (line 657) the routing decision is:
    - `exit_strategy == "smart_money_exit"` AND `r.exit_bid_price is not None`
      → use `_exit` (no VOID check).
    - else if `r.resolved_outcome in ("YES","NO","50_50")` → use `compute_pnl_per_dollar`.
  Since VOID isn't in the resolved-set check, VOID rows with no exit
  fall through to `continue` (line 670). VOID rows WITH an exit get
  settled at the exit bid — correct (the cash was realized before VOID).
  No bug, but the asymmetry is surprising and worth a comment.
- **Impact**: Cosmetic / clarity.
- **Suggested fix**: Add an inline comment explaining the routing for
  VOID with-vs-without exit.

### `min_n_per_cohort=5` filters on `n_eff`, not on row count
- **File**: `app/services/backtest_engine.py:1147-1170`.
- **Finding**: A cohort with 100 signals all from one event-cluster has
  `n_eff = 1` and is dropped. A cohort with 5 signals across 5 distinct
  clusters survives. This is the right semantics (5 independent
  observations beats 100 correlated ones) but is more conservative than
  the docstring suggests, which talks about "n_eff per cohort".
- **Impact**: When the user has lots of cluster-correlated signals,
  many weeks may show zero cohorts, and `decay_warning` quietly stays
  False because `weeks_of_data` never reaches 4.
- **Suggested fix**: Surface why each excluded cohort was dropped (or
  return excluded cohorts with `underpowered: True` so the user can
  see "we have data here but not enough independent clusters").

### Edge-decay `decay_warning` is unweighted-cohort-mean comparison
- **File**: `app/services/backtest_engine.py:1184-1193`.
- **Finding**: `recent_avg = mean of last 3 cohort means; prec_avg =
  mean of preceding cohort means`. Both are unweighted by `n_eff`. A
  preceding cohort with 200 signals contributes equally to a tiny
  cohort with 5. This makes the warning noisy; one fluke week can
  flip the comparison.
- **Impact**: False alarms when an early small cohort had abnormal
  PnL; missed alarms when a recent flat cohort hides one bad mega-week.
- **Suggested fix**: Weight by `n_eff`: `weighted_mean = sum(c.n_eff *
  c.mean_pnl) / sum(c.n_eff)`. Document the change.

### Slice queries inflate `slice_lookups` rapidly, compounding Bonferroni
- **File**: route layer (per session-state) writes one row per bucket
  on `/backtest/slice`; `app/services/backtest_engine.py:986`.
- **Finding**: A single slice query on `lens_count_bucket` (4 buckets)
  inserts 4 rows; on `category` with 7 categories, 7 rows. After two
  slice queries the user is at N=11, multiplicity_warning trips, and
  Bonferroni alpha=0.05/11=0.0045 (z≈2.84) widens CIs ~45%. A user
  sweeping slices to "see what works" gets penalized heavily. The
  correction is technically correct, but UX-wise the user should
  understand "exploring slices counts as testing."
- **Impact**: User confusion: "I only ran two queries, why is N=11?"
- **Suggested fix**: In the response payload, surface a count that
  distinguishes summary queries from slice buckets, AND document that
  each bucket counts. Alternatively, count one slice query as one
  "family" with within-family BH and across-family Bonferroni. (Bigger
  change, V2.)

### `gap_to_smart_money` doesn't translate prices for direction
- **File**: `app/services/backtest_engine.py:280-292`.
- **Finding**: For consistency with the half-life conclusion above:
  if `first_top_trader_entry_price` is direction-space (NO entry for
  NO signal stored as the NO-side cost basis ~0.40-0.55), then comparing
  it to `signal_entry_offer` (also direction-space) is fine. **But** if
  the data pipeline stores `first_top_trader_entry_price` as YES-space
  uniformly (likely given Polymarket's API returns YES-side trade
  prices), then this gap is wrong for NO signals.
- **Impact**: I can't verify the storage convention from the engine
  alone. If YES-space, the `max_gap_to_smart_money` filter and
  `median_gap_to_smart_money` diagnostic are silently corrupted for
  NO signals.
- **Suggested fix**: Verify the storage convention (probably in the
  signal-detector code that populates `first_top_trader_entry_price`).
  If YES-space, translate via `1 - signal_entry_offer` for NO signals
  before computing the gap. Add a smoke test that pins the convention.

### Bonferroni Z-score precision degrades at large N
- **File**: `app/services/backtest_engine.py:95-107`,
  `app/services/backtest_engine.py:135-137`.
- **Finding**: A&S 26.2.17 has max p-error 4.5×10⁻⁴, which on
  `_norm_ppf(0.99975)` (N=100, alpha=0.0005) translates to about
  ±2-3% z-error around z≈3.48. CIs are widened by `± z × se`, so a
  3% z-error → 3% CI-width error. Acceptable, but a heavily-used
  tool would benefit from `scipy.stats.norm.ppf` precision (~1e-15).
  Project policy is "no scipy" — fine, but flag it.
- **Impact**: Negligible for typical N ≤ 50; visible at N > 100.
- **Suggested fix**: Either upgrade to A&S 26.2.23 (max error 7.5×10⁻⁸)
  or document the precision floor in `_norm_ppf`'s docstring with a
  worked example.

## Low / Nits

### `_iso_week_monday` uses an instance method as a class method
- **File**: `app/services/half_life.py` is unaffected;
  `app/services/backtest_engine.py:1135-1141`.
- **Finding**: `dt.date().fromordinal(dt.date().toordinal() - days_since_monday)` —
  `fromordinal` is a classmethod, calling it on an instance works but
  reads as if it were instance-bound. Stylistic.
- **Suggested fix**: `date.fromordinal(...)` for clarity.

### `coin_flip` randomness over 20 cids: smoke test bound is wide
- **File**: `scripts/smoke_phase_b78.py:514-517` (smoke test, not engine).
- **Finding**: Test asserts `0.2 < win_rate < 0.8` with N=20 and 50/50
  hash. By binomial CLT, ±2σ is ±0.22 around 0.5, so 0.28-0.72. The
  test passes a wide tolerance. Engine code is fine; just noting the
  test.
- **Suggested fix**: Tighten to `0.3 < win_rate < 0.7` to actually
  catch a hash-imbalance regression.

### `LATENCY_OFFSET_TOLERANCE_MIN = 5.0` is float; `LATENCY_SNAPSHOT_OFFSETS` is int tuple
- **File**: `app/services/backtest_engine.py:67-68`.
- **Finding**: Mixed numeric types; `_nearest_snapshot_offset` returns
  `int | None` while comparisons happen in float. Works fine; minor
  inconsistency.

### `compute_pnl_per_dollar_exit` slippage formula doesn't apply to exit
- **File**: `app/services/backtest_engine.py:396-404`.
- **Finding**: Slippage is added to `entry_price` only. The docstring
  acknowledges this ("we don't model exit slippage separately because
  the bid we captured IS the price you'd actually clear at"). True for
  a market sell into the bid, but the captured bid is at fire-time, not
  at exit-time — `signal_exits.exit_bid_price` is the bid recorded at
  exit-detection. So this comment is correct; flagging only because
  the variable name `exit_bid_price` doesn't make clear it's
  "exit-time bid", not "fire-time bid".
- **Suggested fix**: Rename to `exit_bid_at_exit` or document.

### `wilson_ci` z-constant `1.959964` hardcoded vs `_Z_RAW = _norm_ppf(0.975)` elsewhere
- **File**: `app/services/backtest_engine.py:308` and
  `app/services/backtest_engine.py:110`.
- **Finding**: Two sources of truth for the same constant. They agree
  to 6 decimals so it's fine, but reuse `_Z_RAW`.

### `compute_pnl_per_dollar` log warning on entry≥1.0 has no rate limit
- **File**: `app/services/backtest_engine.py:430-438`.
- **Finding**: If a stale-data run produces 1000 rows with entry=1.0,
  this logs 1000 WARN lines. Not a math issue.

### Coin-flip win-rate balance is approximate; `sha256(cid) % 2` is unbiased only if hash is uniform
- **File**: `app/services/backtest_engine.py:1055-1058`.
- **Finding**: SHA-256 is uniform; modulo 2 is unbiased. No bug. But the
  same `sha256(cid)` is also used in `_sampled_latency_minutes` (line
  881) — those two uses are CORRELATED across the same condition_id.
  If `delayed` profile shifts a row's entry, the same cid's coin flip
  is unchanged. So coin_flip and latency-adjusted strategies are
  comparable within the same row, but if you ever hash for two
  independent uses you'd want different salts. Fine for V1.

---

## Empty section note
None — every section has at least one finding.

---

## 100-word top-3 summary

1. **Half-life math mixes price spaces for NO signals.** `_yes_price_for_direction`
   is applied to direction-space `fire_price` and `smart_money_entry`, but to
   YES-space `snapshot_price`. NO-direction half-life convergence is
   garbage; YES-direction is fine. ~50% of half-life table is silently wrong.
2. **Three of four latency profiles always fall back to optimistic baseline.**
   `active`, `responsive`, `casual` profile windows (1-20 min) sit outside
   the snapshot offsets (30, 60, 120) ± 5 min tolerance, so every row
   skips adjustment and keeps `signal_entry_offer`. Engine reports
   "latency simulated" with zero actual simulation.
3. **Win-rate Wilson CI uses raw n, not n_eff** — the cluster correction
   applied to P&L mean isn't applied to win rate. CIs are too tight by
   √(n/n_eff) under typical Polymarket cluster correlation.
