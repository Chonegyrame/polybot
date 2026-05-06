# Signal Detection + Ranking Review

## Critical

### Bayesian shrinkage formula is dimensionally wrong (pnl prior used as ROI prior)
- **File**: `app/services/trader_ranker.py:216-228` (Hybrid `_rank_hybrid`), `app/services/trader_ranker.py:318-331` (Specialist `_rank_specialist`), `app/services/trader_ranker.py:430-440` (`gather_union_top_n_wallets`)
- **Finding**: `cat_avg_pnl = AVG(pnl)` is the pool's average **absolute dollar PnL** (e.g. $50k–$500k). The shrinkage formula then computes `(pnl + k * cat_avg_pnl) / (vol + k)` with `k = 50_000` USDC. Dimensionally this is `(USDC + USDC * USDC) / USDC`, not a rate. The correct Bayesian-shrunk ROI is `(pnl + k * prior_roi) / (vol + k)` where `prior_roi` is a dimensionless rate, e.g. `SUM(pnl)/SUM(vol)` or `AVG(pnl/vol)`. With the current formula, every trader's `shrunk_roi ≈ (k * cat_avg_pnl) / (vol + k)` for small `vol`, which is dominated by `cat_avg_pnl` and inversely proportional to `vol` — so ROI ranking becomes "small-vol traders rank highest" regardless of skill. For large-vol traders it collapses to `pnl/vol`. The intended "shrink lucky one-shot wonders toward the pool ROI" never happens.
- **Impact**: Hybrid mode's ROI rank is corrupted; the `(pnl_rank + roi_rank)` average becomes essentially `pnl_rank` plus a random small-trader bias. Specialist mode's primary sort key (`shrunk_roi DESC`) is even more affected — it picks up wallets with vol just above the floor and noisy positive pnl. Trader top-N selection across the entire system is degraded; signals fire from the wrong wallet pool.
- **Suggested fix**: Replace the `cat_avg` CTE with a true ROI prior: `SELECT NULLIF(SUM(pnl), 0)::NUMERIC / NULLIF(SUM(vol), 0) AS prior_roi FROM base`. Then keep the shrinkage formula as `(b.pnl + $6 * c.prior_roi) / NULLIF(b.vol + $6, 0)`. Same fix applied identically in all three locations. Add a smoke test that asserts `shrunk_roi ≈ prior_roi` when `vol << k` and `shrunk_roi ≈ pnl/vol` when `vol >> k`.

### Counterparty check is "any maker on the book", not "maker on the side we're buying from"
- **File**: `app/services/counterparty.py:38-72`, `app/scheduler/jobs.py:594` (token_id selection)
- **Finding**: The implementation pulls `maker_address` from every fill in the recent CLOB trades for the YES token (or NO token if signal direction is NO). But CLOB fills happen on both sides of the book — a maker can be a maker-bid (someone resting an order to **buy** YES) or a maker-ask (someone resting an order to **sell** YES). The docstring claims "makers = sellers of the token you're about to buy" but that's only true for half the fills. Without filtering by `side`, any tracked-pool wallet who recently was a maker-bid (i.e., **also accumulating YES**) will trigger the warning — exactly the opposite of the intended signal. The `_extract_maker_addresses` function ignores side entirely.
- **Impact**: Counterparty warnings fire for wallets that are on the SAME side as the signal, not the opposite. The "smart money is also selling" UI badge becomes random noise — sometimes correct, often inverted. Users disregarding signals based on this warning are acting on garbage data.
- **Suggested fix**: In `_extract_maker_addresses`, only include makers whose fill side indicates the maker was selling. CLOB fills typically expose `side` ("BUY"/"SELL") or `maker_side` — a maker on a SELL fill (taker bought from maker) is a true counterparty. Add a unit test using realistic fill dicts: maker with `side=BUY` should NOT be flagged; maker with `side=SELL` should. Also handle the inverse for NO direction signals (or just always test against both YES and NO tokens with correct side semantics).

## High

### `traders_any_direction` denominator includes multi-outcome rows; skew can be wrong on misclassified markets
- **File**: `app/services/signal_detector.py:272-277` (market_totals CTE), `:138-147` (skew computation)
- **Finding**: `market_totals` counts `DISTINCT identity` across **every** position outcome on the market, then divides `trader_count` (this YES/NO direction only) by it. Multi-outcome markets (e.g., team-name outcomes) have positions that get filtered out by `_outcome_to_direction(...) is None` at the row level, but their wallets still contribute to `traders_any_direction`. For a binary market that has stray non-YES/NO rows in `positions` (e.g., legacy data, edge-case outcome strings, or a market that was reclassified), the denominator inflates and skew falsely drops below 0.6.
- **Impact**: Some legitimate signals don't fire because the denominator is contaminated. Conservative bias, not a false-positive risk, but degrades coverage. Worse for any market type with non-canonical outcome strings.
- **Suggested fix**: Filter `pool_positions` (or apply a WHERE in `market_totals`) to only count rows whose `outcome` maps cleanly to YES/NO. Add the filter `WHERE LOWER(outcome) IN ('yes','no')` in the `market_totals` CTE.

### Recency filter inconsistency between Specialist and Absolute/Hybrid
- **File**: `app/services/trader_ranker.py:276-316` (Specialist), `:115-170` (Absolute), `:173-255` (Hybrid)
- **Finding**: Absolute and Hybrid require `tcs.last_trade_at >= NOW() - 60 days` against the `overall` category. Specialist uses a different proxy: presence in the most recent monthly per-category leaderboard via `active_recently`. These aren't equivalent — a trader can be missing from this month's per-category top monthly leaderboard yet have recent overall trades, or vice versa. The CLAUDE.md spec says recency filter applies via `last_trade_at within 60d`. Specialist deviates silently.
- **Impact**: Specialist top-N can include traders whose last-trade-at is older than 60d (if they happen to still appear in monthly category leaderboard, e.g. due to a single huge old trade that dominates monthly PnL). Conversely it may exclude traders who traded the category yesterday but didn't make the monthly top.
- **Suggested fix**: Add the same recency filter as Hybrid/Absolute to Specialist (use category-specific `tcs.last_trade_at` for stronger semantics, or `overall` for consistency). Decide which and document. Keep `active_recently` as an additional filter or drop it.

### `gather_union_top_n_wallets` recency filter ignores per-category last_trade_at
- **File**: `app/services/trader_ranker.py:396-401, 423-427`
- **Finding**: The bulk union query uses `recent_overall` (filtered on `category='overall'` last_trade_at) for ALL three modes' recency gate. But Specialist's per-mode recency intent was per-category activity (`active_recently` from monthly leaderboard). This inconsistency between `gather_union_top_n_wallets` (one bulk query, used by counterparty pool + position refresh) and `_rank_specialist` (single-category, used by signal detector) means the union pool can include/exclude different wallets than would be selected by the single-mode rankers. Counterparty checks against a pool that doesn't match the actual signal-firing pool.
- **Impact**: Counterparty pool has a different membership than the union of the actual rankers. Edge-case false positives/negatives where a wallet IS in the per-mode top-N but not in the bulk union (or vice versa). Position refresh might miss tracking a wallet that the per-mode signal detector then can't aggregate from.
- **Suggested fix**: Decide on a single canonical recency definition (either category-specific or overall) and apply identically in both call sites. Add an integration test asserting: for each (mode, category, top_n), the per-mode top-N is a subset of `gather_union_top_n_wallets(top_n, all_categories)`.

### Watchlist mutual exclusion is per (mode, category, top_n, cid, direction), not per (cid, direction)
- **File**: `app/services/signal_detector.py:152-165` (logic per pass), `app/scheduler/jobs.py:540-556` (per-lens persistence), `migrations/008` watchlist table UNIQUE key per session-state.md
- **Finding**: The locked decision in CLAUDE.md says "watchlist mutual exclusion: per (cid, direction)". The implementation enforces it within a single (mode, category, top_n) lens pass. Across lenses, a (cid, direction) can be `official` in one lens (e.g. Hybrid/Politics/50) and `watchlist` in another (e.g. Specialist/Tech/25). The watchlist table's UNIQUE key is `(mode, category, top_n, condition_id, direction)` — also per-lens. The /watchlist/active endpoint surfaces these per-lens. The /signals/active endpoint surfaces signal_log per-lens. So a single market can appear in BOTH feeds simultaneously when the user toggles lenses or aggregates them.
- **Impact**: When the UI shows "watchlist + signals" combined views, the same (cid, direction) appears in both feeds. Defeats the "mutually exclusive" UX guarantee. Could also cause double-firing alerts.
- **Suggested fix**: Either (a) document that mutual exclusion is intentionally per-lens (and update CLAUDE.md), or (b) at write time, query whether any official signal exists for (cid, direction) across all lenses and skip watchlist persistence if so. (b) is closer to the spec.

### Exit detector `peak_*` peakiness depends on signal_log peak tracking being live-updated
- **File**: `app/services/exit_detector.py:140-180`
- **Finding**: Exit fires when current vs `peak_*` drops ≥30%. The "peak" is read from `signal_log.peak_trader_count` and `peak_aggregate_usdc` — these need to be monotonically updated on every cycle by signal logging. If the peak-update path is skipped (e.g. signal didn't fire this cycle because skew dropped just under 0.6), the peak could be stale or even lower than current_value. More importantly, after a signal stops re-firing (drops out of detection), the peak captured at the last fire becomes a permanent watermark and an exit may fire 24h later when current drops vs that frozen peak — even though the market was already drifting downward and the user already knows.
- **Impact**: Late or spurious exit events on signals that stopped re-firing. Peak being computed against last-fire metrics rather than rolling-window peak means the threshold's interpretation is "drop from last-known-active state," not "drop from peak intensity."
- **Suggested fix**: Document the semantic ("peak" = max during active firing windows). If you want true rolling peak, recompute on every cycle for ALL signals regardless of whether they fire that cycle, and update peak_* accordingly. Also add a guard: only emit an exit if `last_seen_at` is within ~2h (not 24h) — otherwise the signal has been dead too long for an exit to be actionable.

## Medium

### Avg portfolio fraction averages NULL-skipping; small-portfolio wallets dominate
- **File**: `app/services/signal_detector.py:261-262`
- **Finding**: `AVG(CASE WHEN portfolio_value > 0 THEN current_value / portfolio_value ELSE NULL END)` — wallets with no portfolio_value snapshot contribute NULL and are dropped from the AVG. So `avg_portfolio_fraction` is computed over a partially missing subset, and wallets with stale or missing `portfolio_value_snapshots` are silently excluded from this metric. UI shows "avg portfolio fraction" as if it represented the full pool but it represents only the portion with portfolio data.
- **Impact**: Misleading metric — for cohorts where many wallets have stale snapshots, the average is biased toward the (typically smaller-portfolio) wallets that did refresh. The two-metric display promised in CLAUDE.md becomes noisy on one dimension.
- **Suggested fix**: Either fall back to a default (e.g., median portfolio value of the pool) for missing rows, or surface a `coverage_fraction` alongside (e.g., "avg over 7/12 contributing wallets"). Add to UI tooltip if not in payload.

### `avg_entry_price` size-weighting uses `size` (shares), not USDC notional
- **File**: `app/services/signal_detector.py:265-268`
- **Finding**: `SUM(avg_price * size) / SUM(size)` — size-weighted by shares. Comment claims this is the correct cost-basis approximation. Since `avg_price * size = USDC notional` for the position (approximately), this DOES give a notional-weighted avg entry: `SUM(notional)/SUM(size)`. That's the right cost basis (total USDC paid / total shares). OK on closer read — but the convention is fragile if any row has `size=0` or `avg_price=NULL` (the DIVISION by SUM(size) breaks if SUM(size) == 0, guarded by CASE). Also: traders who scaled into the position at multiple prices have their `avg_price` from Polymarket (already notional-weighted across their personal trades), so this is "weighted average of weighted averages" — fine in expectation but biases toward larger position holders. Acceptable but should be documented.
- **Impact**: Avg entry price subtly biases toward whales' cost basis (since they hold larger positions at typically lower entries). Visible to user as the "cost basis approximation."
- **Suggested fix**: Document the bias in code comment. No formula change needed unless the spec wants equal-weighted entry — it doesn't, currently.

### Sybil group detection emits clique edges with identical "rate"; pair_rates evidence reporting omits group-only edges
- **File**: `app/services/sybil_detector.py:159-172, 200-232`
- **Finding**: Each flagged group produces N*(N-1)/2 group_edges, all tagged with the same `rate = n / min_buckets_among_group_members`. When reporting evidence (lines 205-229), `pair_rates` only contains rates from `pair_edges`, not from `group_edges`. So a cluster discovered solely via group co-entry (not pair) has empty `pair_rates`, and `min/max/mean co_entry_rate` are missing from evidence — only `max_group_shared_buckets` is reported. The forensic value of a group-only cluster is harder to inspect.
- **Impact**: Diagnostic only — clusters still get persisted correctly. Just makes auditing harder.
- **Suggested fix**: Include group-derived rates in evidence, or add a separate `group_co_entry_rate` field.

### Sybil detector pair `rate` denominator uses TOTAL bucket count, not co-entry-eligible buckets
- **File**: `app/services/sybil_detector.py:151`
- **Finding**: `denom = min(len(buckets_by_wallet[a]), len(buckets_by_wallet[b]))` counts ALL buckets the wallet appears in (each trade contributes 2 — regular + offset grid), so a wallet with 50 trades has ~100 entries in `buckets_by_wallet[w]`. The numerator `co_count[(a,b)]` ALSO double-counts when both wallets land in regular AND offset for the same trade pair. So the rate calculation is consistent (both 2x'd) — but it's not normalised to `0..1` and a "rate" above 1.0 is possible if wallets co-occur on offset and regular for every trade plus the offset grid catches additional boundary trades. The `SYBIL_CO_ENTRY_THRESHOLD = 0.30` threshold's interpretation depends on this scale.
- **Impact**: Threshold tuning is harder than it looks. Documentation says "min fraction" but it's not a true fraction. Could undertrigger or overtrigger depending on call patterns.
- **Suggested fix**: Either (a) deduplicate trades across grids when counting numerator, or (b) document that the "rate" is in `[0, 2]` due to dual-grid double-counting and recalibrate threshold accordingly.

### Wallet classifier MM rule misses pure scale-out market makers in calm periods
- **File**: `app/services/wallet_classifier.py:78-105, 182`
- **Finding**: `MM_PAIR_WINDOW_MINUTES = 10` + `MM_PAIR_SIZE_TOLERANCE = 0.30` is tight. A market maker that round-trips slowly (e.g. low-vol markets, holding inventory >10min before unwinding) gets classified as `directional`. Then `_EXCLUDE_CONTAMINATED_SQL` doesn't exclude them — they enter the top-N pool and contaminate signals. The MM_MIN_MARKETS_PER_DAY=0.5 floor partially compensates but a single-market MM (rare but possible) escapes.
- **Impact**: Some MM-style wallets leak into top-N. Not catastrophic but reduces signal quality.
- **Suggested fix**: Add a secondary feature like "mean inventory holding time" — true directional traders hold inventory >> MMs. Or look at gross flipping volume vs net delta change. V2 work though.

## Low / Nits

### `RECENCY_MAX_DAYS` placeholder in absolute SQL is `$4`, but the exclusion via `wallet_classifications` runs as a literal string interpolation through f-string
- **File**: `app/services/trader_ranker.py:127-156`
- **Finding**: The `_EXCLUDE_CONTAMINATED_SQL` is f-string-interpolated, which is fine because it contains no user input — but it sets a precedent. Future edits could accidentally interpolate user data. Annotate clearly that this is a static fragment.
- **Suggested fix**: Add a `# nosec B608: static SQL fragment, no user input` comment, or move to a CTE for clarity.

### `_outcome_to_direction` strips/lowercases — no handling of "Y"/"N" abbreviations
- **File**: `app/services/signal_detector.py:47-55`
- **Finding**: Some Polymarket outcomes might be "Yes "/"No" with whitespace (handled) but historical data could have "Y"/"N" or other variants. Currently those silently drop into the multi-outcome bucket and get filtered.
- **Impact**: Negligible if Polymarket sticks to "Yes"/"No". Defensive coding.
- **Suggested fix**: Optional — accept "y"/"n" as well, or log a counter for unknown outcomes to detect data drift.

### `detect_exits` reads `peak_aggregate_usdc::numeric` but compares as float
- **File**: `app/services/exit_detector.py:144, 158-160`
- **Finding**: Cast to float in Python after pulling NUMERIC. Standard and fine, just noting that NUMERIC->float on very large peaks can introduce float rounding at boundary cases (e.g., peak_agg=999999.99, cur_agg=699999.99 — exactly 30% drop). Comparison `>= threshold` could go either way due to float precision.
- **Impact**: Corner-case flapping at exactly 30%.
- **Suggested fix**: Either keep the comparison in NUMERIC (do the threshold check in SQL), or use `Decimal` in Python.

### `_classify_drop` returns `None` when both peak values are zero, but caller continues
- **File**: `app/services/exit_detector.py:67-75, 159-160`
- **Finding**: Line 159 already filters `peak_traders < 5 or peak_agg <= 0`, so this path is unreachable in practice. Defensive but redundant.
- **Impact**: None.
- **Suggested fix**: No action; just noting.

### `aggregate_trades_per_category` always emits `overall` row even with zero resolved trades, but Specialist floor only checks per-category resolved_trades
- **File**: `app/services/trader_stats.py:93-103`, `app/services/trader_ranker.py:312-315`
- **Finding**: `last_trade_at` recording in `overall` works for any trade, but `resolved_trades` count for `overall` only counts resolved markets. Specialist requires `resolved_trades >= 30` against the **per-category** stats row. A trader with 100 resolved trades in `overall` but only 20 in `politics` is correctly excluded from Politics specialist — that's the intent. OK on review, just noting the implicit dependency.
- **Impact**: None.
- **Suggested fix**: No action.

---

## 100-word summary

The single most damaging finding is the **Bayesian shrinkage formula**: `cat_avg_pnl` is absolute dollar PnL but it's used as if it were an ROI prior. This corrupts Hybrid's `roi_rank` and Specialist's primary sort, degrading the trader pool that feeds every signal. Second, the **counterparty check** flags any maker on the order book without filtering by side, so "smart money also selling" warnings include wallets that are actually accumulating — the diagnostic is roughly half-inverted. Third, **watchlist mutual exclusion is per-lens** rather than per (cid, direction), so a market can appear in both /signals/active and /watchlist/active under different mode×category lenses, breaking the spec's exclusivity guarantee.
