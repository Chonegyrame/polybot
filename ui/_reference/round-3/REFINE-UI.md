# Polybot UI — Refinement Spec (round 2)

> Hand this to Claude Design as the prompt for the next iteration.
> Context: this is **additive** to the existing UI in `/ui/`, not a rewrite.

---

## Why this document exists

The first UI build (delivered as `/ui/`) faithfully implemented `UI-SPEC.md`. After that delivery, an audit found that the backend exposes more functionality than the UI surfaces. The backend has now been enriched so every field the original UI mock expects is delivered live; that work is complete. **This document is the next step**: adding UI surfaces for backend features the current UI doesn't yet expose.

Concretely, the gap is:
- **Backtest tab** — backend supports latency profiles, all 11 slice dimensions, 5 benchmarks (not 3), 8 filter chips (not 4), Bonferroni + BH-FDR side by side, an empirical p-value, multiplicity-tracker thresholds, and edge-decay quality flags. The current UI mock surfaces a subset.
- **System status** — backend returns a 5-way breakdown of zombie-position drops + a two-axis stats-freshness state. The UI shows totals only.
- **Insider wallets** — `/insider_wallets` CRUD endpoints exist; the UI shows the `has_insider` chip on signal cards but has no management screen.
- **Field naming** — a few small renames between the UI mock data and the actual backend response shape.

Each section below:
1. Says *where* the addition goes (which page / component)
2. Describes *what* it does for the user
3. Specifies the *backend contract* (exact endpoint + response fields to bind to)
4. Gives *UI design pointers* (placement, styling, tooltips, copy)

The doc ends with an **explicit list of things NOT to change**, an **endpoints reference**, and **field-name mapping** for the small UI-side renames.

---

## Section 1 — Backtest: Latency profile selector (NEW)

### Where
`/testing` route → Backtest sub-tab → Stats panel header (above the headline numbers, alongside the existing "Hold to resolution / Smart-money exit" segmented control).

### What it does
Lets the user model how their real reaction speed affects strategy P&L. Today the backtest assumes you fill at the price captured at signal-fire time (zero-latency, best-case). In reality you see the signal a few minutes after it fired and the price has already moved. This selector lets the user pick a realistic latency window and the backtest re-prices each signal against the snapshot captured N minutes after fire.

### Profiles to expose

| Profile id | Label | Window | When it fits |
|---|---|---|---|
| (none) | "Best case (fire-time)" | 0 | Default, baseline |
| `active` | "Active trader" | 1–3 min | User watches the dashboard live |
| `responsive` | "Responsive" | 5–10 min | Glances a few times an hour |
| `casual` | "Casual" | 12–20 min | Checks once or twice an hour |
| `delayed` | "Delayed" | 30–60 min | Sees signals via email / browser notification |
| `custom` | "Custom..." | user input | Lets the user type `min` and `max` minutes |

### Backend contract
Append `?latency_profile=<name>` to any `/backtest/summary`, `/backtest/slice`, or `/backtest/edge_decay` call. For `custom`, also send `?latency_min_min=<float>&latency_max_min=<float>`.

The response gains a `latency_stats` block:

```json
{
  "latency_stats": {
    "adjusted": 0.84,         // fraction of rows that found a snapshot at the profile's window
    "fallback": 0.16,         // fraction that fell back to fire-time pricing
    "n_adjusted": 73,
    "n_fallback": 14,
    "latency_unavailable": false  // true when fallback > 20% (coverage too thin to trust)
  },
  "latency_profile": "responsive"  // echoed
}
```

### UI pointers
- Segmented control with the 5 profile labels + a "Custom..." option that opens a small popover with two number inputs.
- When `latency_profile` is set, render a small caption under the headline P&L: "Profile: Responsive (5-10 min) · 84% of signals priced at offset, 16% fell back to fire-time."
- When `latency_unavailable=true`, show a yellow banner above the stats: **"⚠ Profile coverage too thin — more than 20% of signals fell back to fire-time pricing. Numbers below are dominated by best-case fills, not the requested latency."**
- Tooltip on the control header: "How quickly do you act on signals? The backtest re-prices each fill against the actual market price N minutes after the signal fired."

---

## Section 2 — Backtest: Filter chip set extension (EXTEND)

### Where
`/testing` → Backtest → existing Filters chip row.

### What it does
The backtest currently exposes 4 filter chips. The backend supports 8 more that materially change the result. Add them all so the user can build honest, slicing-disciplined queries.

### Filters to add (one chip each)

| Chip label | Query param | Type | UI control |
|---|---|---|---|
| Direction | `direction` | `YES` \| `NO` | Toggle pair, both-on by default |
| Market category | `market_category` | string | Dropdown of 7 categories — distinct from "lens category" |
| Min trader count | `min_trader_count` | int | Number input, default 5 |
| Min aggregate USDC | `min_aggregate_usdc` | float | Number input, default 25_000 |
| Min portfolio fraction | `min_avg_portfolio_fraction` | float 0..1 | Slider 0–20% |
| Liquidity tiers | `liquidity_tiers` (multi) | subset of `("thin","medium","deep","unknown")` | Multi-select pills |
| Max gap to smart money | `max_gap` | float | Slider, label "≤ +N% above smart money entry"; range -10% to +50% |
| Skew bounds | `min_skew` / `max_skew` | float 0..1 each | Range slider, default 0.65 to 1.0 |
| Trade size assumption | `trade_size_usdc` | float | Number input, default 100, label "Sizing for fee/slippage modeling" |
| Exit strategy | `exit_strategy` | `hold` \| `smart_money_exit` | Already exists ✓ — leave as is |
| Holdout cutoff | `holdout_from` | date | Date picker, label "Training data ends:" |
| Include unavailable rows | `include_pre_fix` | bool | Toggle, default OFF, tooltip "Includes signals fired before the entry-pricing fix landed. Off by default for honest results." |
| Include multi-outcome markets | `include_multi_outcome` | bool | Toggle, default OFF, tooltip "Includes scalar / neg-risk markets. Most users want binary YES/NO only." |
| Dedup across lenses | `dedup` | bool | Toggle, default OFF, tooltip "Collapses signals to one row per (market, direction). Off by default; turn on for the headline." |

All filters can be applied to `/backtest/summary`, `/backtest/slice`, and `/backtest/edge_decay`.

### UI pointers
- Group chips into three rows: **Strategy** (direction, exit_strategy, dedup), **Filters** (everything else), **Sizing** (trade_size_usdc, holdout_from).
- Each chip shows the current value when set, "—" when not set; click to open a small popover for editing.
- "Reset all filters" button at the right end of the row.

---

## Section 3 — Backtest: Slice dimension picker (EXTEND)

### Where
`/testing` → Backtest → existing Slice explorer.

### What it does
The current UI mock shows one slice dimension (`gap_bucket`). The backend supports 11. Make the dimension a dropdown so the user can pivot through all of them.

### Dimensions to expose (full list)

| `dimension` value | Label | Bucket labels |
|---|---|---|
| `mode` | Ranking mode | absolute / hybrid / specialist |
| `category` | Lens category | overall / politics / sports / crypto / culture / tech / finance |
| `direction` | Direction | YES / NO |
| `market_category` | Market category | (the 7 categories — distinct from lens) |
| `liquidity_tier` | Liquidity tier | thin / medium / deep / unknown |
| `skew_bucket` | Headcount skew | `<60% / 60-69% / 70-79% / 80-89% / 90-100%` |
| `trader_count_bucket` | Trader count | `<5 / 5-9 / 10-14 / 15-19 / 20+` |
| `aggregate_bucket` | Aggregate USDC | `<$100k / $100k-$500k / $500k-$1M / $1M+` |
| `entry_price_bucket` | Entry price | `0-20¢ / 20-40¢ / 40-60¢ / 60-80¢ / 80-100¢` |
| `gap_bucket` | Gap to smart money | `<-10% (cheaper than smart money) / near smart money entry / 10-50% gap / >50% gap` |
| `lens_count_bucket` | Lens count | `1 / 2-3 / 4-5 / 6+` (only meaningful when `?dedup=true`) |

### Backend contract
`GET /backtest/slice?dimension=<value>&<all the filter params from Section 2>`. Response shape:

```json
{
  "dimension": "gap_bucket",
  "holdout_from": null,
  "latency_profile": null,
  "n_session_queries": 7,
  "multiplicity_warning": true,
  "buckets": {
    "<-10% (cheaper than smart money)": { /* full BacktestResult — see endpoints reference */ },
    "near smart money entry":           { /* ... */ },
    "10-50% gap":                       { /* ... */ },
    ">50% gap":                         { /* ... */ }
  }
}
```

### UI pointers
- Dropdown labeled "Slice by" with the 11 options. When the user changes it, refetch and re-render the table.
- For each bucket row, show: bucket label, `n_eff`, `win_rate`, `mean_pnl_per_dollar`, raw 95% CI, BH-FDR-corrected CI, `pnl_bootstrap_p`, ★ marker if BH-FDR CI excludes zero, "low sample" tag if `underpowered=true`.
- For `lens_count_bucket`, gray out unless `dedup=true` is set with a tooltip "Lens count is only meaningful with dedup ON."

---

## Section 4 — Backtest: Benchmarks side-by-side (EXTEND from 3 to 5)

### Where
`/testing` → Backtest → benchmarks comparison view.

### What it does
The current UI mock shows 3 benchmarks. The backend supports 2 more. Add them so the user can build a complete picture.

### Benchmarks to expose

| `benchmark` value | Label | What it tests |
|---|---|---|
| `buy_and_hold_yes` | Buy-and-hold YES | "Does direction matter, vs just top-trader attention?" |
| `buy_and_hold_no` | Buy-and-hold NO | Mirror of above. Useful when YES has been crushed across the universe. |
| `buy_and_hold_favorite` | Buy-and-hold favorite | The "go with the crowd" baseline — buys whichever side is priced ≥$0.50 at fire time. **Most important benchmark to beat.** If the strategy can't beat this, the smart-money signal isn't adding info beyond the prior. |
| `coin_flip` | Coin flip | Random direction, seeded per market. Expected P&L ≈ −fees−slippage. Strategy must beat this to claim any edge. |
| `follow_top_1` | Follow top-1 | Raw consensus signal direction, no extra filters. |

### Backend contract
`GET /backtest/summary?benchmark=<value>` adds a `benchmark` block to the response:

```json
{
  "benchmark": {
    "name": "buy_and_hold_favorite",
    "n_signals": 142, "n_resolved": 87, "n_eff": 34,
    "mean_pnl_per_dollar": 0.012, "pnl_ci_lo": -0.034, "pnl_ci_hi": 0.058,
    "win_rate": 0.51, "win_rate_ci_lo": 0.41, "win_rate_ci_hi": 0.61
  }
}
```

### UI pointers
- Add a second selector "Compare against:" with the 5 benchmarks (default: `buy_and_hold_favorite` — the strongest test).
- Side-by-side table: Strategy column | Benchmark column. Show n_eff, win_rate ± CI, pnl/$ ± CI for each.
- Strategy column gets ✓ (green) if its CI lower bound exceeds benchmark's CI upper bound by ≥2× the strategy's CI half-width; ✗ (red) otherwise. Tooltip: "Strategy beats benchmark with statistical confidence."
- Below the table, a one-line summary: "Strategy beats Buy-and-hold favorite by +5.1pp on mean P&L" (or "fails to beat" / "ties").

---

## Section 5 — Backtest: Bonferroni + BH-FDR side-by-side (EXTEND)

### Where
`/testing` → Backtest → headline stats panel + slice explorer rows.

### What it does
Both correction schemes are returned by the backend; the UI mock only shows BH-FDR. Show both so the user can pick their stance: Bonferroni (strict) or BH-FDR (less conservative, rank-based).

### Backend contract
On `/backtest/summary` the `corrections` block already returns:

```json
{
  "corrections": {
    "n_session_queries": 7,
    "multiplicity_warning": true,
    "bonferroni_pnl_ci_lo": -0.005, "bonferroni_pnl_ci_hi": 0.131,
    "bonferroni_win_rate_ci_lo": 0.41, "bonferroni_win_rate_ci_hi": 0.74,
    "bh_fdr_pnl_ci_lo": 0.008, "bh_fdr_pnl_ci_hi": 0.118,
    "bh_fdr_win_rate_ci_lo": 0.45, "bh_fdr_win_rate_ci_hi": 0.71
  }
}
```

On `/backtest/slice` each bucket has its own `corrections` block at `buckets[<label>].corrections.*` with the same fields.

### UI pointers
- For mean P&L per dollar, show **three columns side by side**: Raw 95% CI / Bonferroni 95% / BH-FDR 95%. Same for win rate.
- Each correction column has a small info icon: Bonferroni tooltip "Strict — divides α by N session queries. Wider CIs, fewer false positives, more false negatives." BH-FDR tooltip "Less conservative — rank-based. Standard for exploratory backtest work."
- When `multiplicity_warning=true`, show a callout above the headline: **"⚠ Multiple testing — you've run N queries this session. The corrected CIs are the numbers to trust, not raw."**

---

## Section 6 — Backtest: Empirical p-value (NEW)

### Where
`/testing` → Backtest → headline stats panel, secondary line below the CI.

### What it does
The backend returns `pnl_bootstrap_p` — an empirical 2-sided p-value from the cluster bootstrap. Used internally as input to BH-FDR. Surfacing it lets the user read significance directly.

### Backend contract
On `/backtest/summary`: top-level `pnl_bootstrap_p` (float 0..1).
On `/backtest/slice`: each bucket has its own `pnl_bootstrap_p`.

### UI pointers
- Show as a small secondary line under the mean P&L: `p = 0.04`. Color it: green if `p < 0.05`, neutral otherwise.
- Tooltip: "Empirical p-value from the cluster bootstrap. p < 0.05 means the result is unlikely to be chance — but only if you haven't run lots of queries (see multiplicity warning)."
- **Don't lead with this.** CIs are still the primary frame. The p-value is a supporting number.

---

## Section 7 — Backtest: Multiplicity tracker thresholds (EXTEND)

### Where
`/testing` → Backtest → page header banner.

### What it does
Persistent header that counts how many distinct backtest queries the user has run in the current 4-hour session. Backend tracks this via `n_session_queries`. Three threshold tiers drive different UI severity.

### Thresholds

| Queries this session | UI state | Banner text |
|---|---|---|
| 0–5 | None | (no banner) |
| 6+ | Amber banner | "⚠ Multiple testing: BH-FDR-corrected CIs are the numbers to trust, not raw." |
| 20+ | Red banner | "⚠⚠ Heavy slicing — results are exploratory only. Run a formal holdout test before acting." |

### Backend contract
`/backtest/summary` and `/backtest/slice` both return `corrections.n_session_queries` and `corrections.multiplicity_warning` (true at 6+).

### UI pointers
- Banner is sticky at the top of the Backtest page.
- For the red tier, also dim the headline P&L (reduce opacity to 0.6) so the user feels the friction before reading numbers.

---

## Section 8 — Backtest: Edge decay quality flags (EXTEND)

### Where
Diagnostics → Edge decay panel.

### What it does
The backend's `/backtest/edge_decay` returns more than just a list of cohort weeks. It carries quality flags that say "is this even worth interpreting?" — surface them so the user doesn't read meaningless lines.

### Backend contract

```json
{
  "min_n_per_cohort": 5,           // floor; cohorts with fewer rows excluded
  "decay_warning": false,           // true if last 3 cohorts trend below preceding ones
  "insufficient_history": true,     // true if weeks_of_data < min_weeks_needed
  "weeks_of_data": 3,
  "min_weeks_needed": 8,
  "cohorts": [
    {
      "week": "2026-W11", "n_eff": 8,
      "mean_pnl_per_dollar": 0.092,
      "pnl_ci_lo": 0.012, "pnl_ci_hi": 0.183,
      "win_rate": 0.65, "win_rate_ci_lo": 0.42, "win_rate_ci_hi": 0.85,
      "underpowered": true
    }
  ]
}
```

### UI pointers
- When `insufficient_history=true`: chart is greyed with overlay text **"Insufficient history — needs ≥{min_weeks_needed} weeks of live operation, currently has {weeks_of_data}."**
- When `decay_warning=true`: amber banner **"⚠ Edge may be decaying — recent cohorts underperforming earlier ones."**
- Per-cohort dot/line: cohorts with `underpowered=true` rendered hollow / desaturated; tooltip "Low sample (n_eff < 30 / under min_n_per_cohort)."

---

## Section 9 — System status: Zombie drops 5-way breakdown (EXTEND)

### Where
Dashboard → header health pill → expanded panel.

### What it does
Backend returns the breakdown of why positions got dropped during the last 24h refresh cycles. The UI mock collapses this to a single number. Show the 5-way split — different reasons mean different things.

### Backend contract
`/system/status` returns:

```json
{
  "counters": {
    "zombie_drops_last_24h": {
      "redeemable": 320,
      "market_closed": 12,
      "dust_size": 4,
      "resolved_price_past": 0,
      "incomplete_metadata": 0,
      "total": 336
    }
  }
}
```

### UI pointers
- In the expanded health panel, replace the single "336 zombie drops" line with a small horizontal bar chart or breakdown table:
  - **Redeemable** — resolved markets where the user/wallet hasn't claimed yet. Normal — should be ~200-400/day.
  - **Market closed** — market-level closed flag set after position was last seen.
  - **Dust size** — position size below $10 (rounding noise).
  - **Resolved price past** — current price is already 0.0 or 1.0 (final resolution priced in).
  - **Incomplete metadata** — Polymarket returned a position with missing fields. **A spike here suggests a Polymarket API change.**
- Tooltips for each (above text).
- If `incomplete_metadata > 50` in 24h, show an amber dot next to the counter — possible API change worth investigating.

---

## Section 10 — System status: stats_freshness clarity (EXTEND)

### Where
Dashboard → header health pill → expanded panel.

### What it does
Backend's `stats_freshness` has TWO booleans (`seeded` and `fresh`) representing distinct conditions. The UI mock collapses to one health flag.

### States to expose

| `seeded` | `fresh` | UI label | Meaning |
|---|---|---|---|
| `false` | (any) | "🔵 Stats bootstrapping" (info) | Trader stats table is empty/being built — recency filter is no-op until populated |
| `true` | `false` | "🟡 Stats stale (>7 days)" (warning) | Nightly refresh hasn't run; ranker bypasses recency check |
| `true` | `true` | "🟢 Stats fresh" (ok) | All good |

### Backend contract
`/system/status.components.stats_freshness` returns `{seeded: bool, fresh: bool, last_refresh: ISO datetime | null}`.

### UI pointers
- Show as a single chip in the expanded panel with the colored label above.
- Below the chip, render: "Last refreshed: {last_refresh formatted as relative time}" if `last_refresh` is non-null; "Never refreshed" otherwise.

---

## Section 11 — Insider wallet management screen (NEW)

### Where
Top nav → Settings → "Insider wallets" sub-page (new). Or, alternatively, a side-drawer accessible from any `has_insider` chip on a signal card. Pick whichever fits the nav model — both work.

### What it does
Lets the user manually curate a list of "insider" wallets (sports specialists with leaks, weather oracles, court-ruling leakers, etc.). Wallets in this list cause the `has_insider` chip to fire on signal cards when they're contributing.

### Backend contract

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/insider_wallets` | — | `{count, wallets: [{proxy_wallet, label, notes, added_at, last_seen_at}]}` |
| POST | `/insider_wallets` | `{proxy_wallet: string (42-char 0x...), label?: string, notes?: string}` | The upserted record |
| DELETE | `/insider_wallets/{proxy_wallet}` | — | `{deleted: bool, proxy_wallet: string}` |

### UI pointers
- Table of all curated insiders: short address, copy button, label, notes (truncated, click to expand), added date, last seen.
- "Add insider" button → modal with fields: wallet address (validated as 42-char `0x...`), label (optional), notes (optional, multiline).
- Each row has a small trash icon → confirm modal → DELETE.
- Empty state: **"No insider wallets curated yet. Add wallets you've identified as having information edge — sports specialists, weather oracles, etc. Signals where they're contributing will be flagged with a 🔮 chip on the dashboard."**
- This is a low-traffic page; doesn't need polling or fancy interactions.

---

## Things explicitly NOT to change

The following surfaces work today and shouldn't be touched in this round:

1. **Signal card layout** — fields and quality indicators are correct.
2. **Trader drill-down modal** — backend now matches the mock's expected shape (classification.features, cluster.evidence flat, portfolio_fraction). Already matched.
3. **Per-market trading view (State B)** — the orderbook and recent fills sections will stay mocked because the live endpoint isn't built yet (Phase 2 work). Don't try to wire them up.
4. **Wallet sparkline / Wallet endpoints** — `/wallet`, `/wallet/deposit`, `/wallet/reset` are NOT built. Keep the mocks. The "Reset" and "Deposit" buttons can stay non-functional placeholders for now.
5. **Markets browser (Trade sub-tab State A)** — `/markets/search` is NOT built. Keep the mock list.
6. **Health pill states (green/amber/red)** — already correct.
7. **Top traders list / sort** — already correct.
8. **Color palette / typography / dark mode** — leave it.

---

## Endpoints reference (for the additions above)

All endpoints accept the standard query params `mode` (absolute|hybrid|specialist), `category` (overall|politics|sports|crypto|culture|tech|finance), and `top_n` (20-100) where relevant.

### `/backtest/summary` — full param set

```
GET /backtest/summary
  ?mode=&category=&direction=&market_category=
  &min_skew=&max_skew=&min_trader_count=
  &min_aggregate_usdc=&min_avg_portfolio_fraction=
  &liquidity_tiers=thin&liquidity_tiers=medium    (multi)
  &max_gap=&include_pre_fix=&include_multi_outcome=
  &trade_size_usdc=&exit_strategy=hold|smart_money_exit
  &dedup=true|false
  &holdout_from=YYYY-MM-DD
  &latency_profile=active|responsive|casual|delayed|custom
  &latency_min_min=&latency_max_min=  (when latency_profile=custom)
  &benchmark=buy_and_hold_yes|buy_and_hold_no|buy_and_hold_favorite|coin_flip|follow_top_1
```

### `BacktestResult` shape (returned by `/summary` and embedded inside `/slice` buckets and `/edge_decay` cohorts)

```json
{
  "n_signals": 142, "n_resolved": 87, "n_eff": 34,
  "underpowered": false,
  "mean_pnl_per_dollar": 0.063,
  "pnl_ci_lo": 0.018, "pnl_ci_hi": 0.108,
  "win_rate": 0.58,
  "win_rate_ci_lo": 0.47, "win_rate_ci_hi": 0.69,
  "profit_factor": 1.84,    // null when there are no losses → render "n/a"
  "max_drawdown": -0.12,
  "median_entry_price": 0.42,
  "median_gap_to_smart_money": 0.05,
  "by_direction": { "YES": { /* same shape */ }, "NO": { /* ... */ } },
  "by_resolution": { "YES": {...}, "NO": {...}, "50_50": {...}, "VOID": {...}, "PENDING": {...} },
  "pnl_bootstrap_p": 0.04
}
```

### `/backtest/slice` response

```json
{
  "dimension": "gap_bucket",
  "holdout_from": null,
  "latency_profile": null,
  "n_session_queries": 7,
  "multiplicity_warning": true,
  "buckets": {
    "near smart money entry": { /* full BacktestResult + own corrections block */ }
  }
}
```

### `/backtest/edge_decay` response

```json
{
  "min_n_per_cohort": 5,
  "decay_warning": false,
  "insufficient_history": false,
  "weeks_of_data": 8,
  "min_weeks_needed": 8,
  "cohorts": [
    { "week": "2026-W11", "n_eff": 8,
      "mean_pnl_per_dollar": 0.092,
      "pnl_ci_lo": 0.012, "pnl_ci_hi": 0.183,
      "win_rate": 0.65,
      "win_rate_ci_lo": 0.42, "win_rate_ci_hi": 0.85,
      "underpowered": true }
  ]
}
```

### `/insider_wallets` shapes (Section 11)

```json
// GET response
{ "count": 3,
  "wallets": [
    { "proxy_wallet": "0x...",
      "label": "NBA insider",
      "notes": "Hit 4 of 5 last playoffs",
      "added_at": "2026-04-15T12:00:00Z",
      "last_seen_at": "2026-05-08T09:31:00Z" }
  ]
}

// POST body
{ "proxy_wallet": "0xabc...123",  // 42-char, validated
  "label": "Sports specialist",
  "notes": "Optional free text" }
```

---

## Field-name mapping (small UI-side renames the existing mock needs)

The existing UI in `/ui/data.js` uses some field names that differ from the actual backend response. Update `data.js` (or whatever fetch layer replaces it) to read the backend names. These are the only renames needed:

### `PB.PAPER_TRADES` rows

| UI mock currently uses | Backend actually returns | Notes |
|---|---|---|
| `size_usdc` | `entry_size_usdc` | |
| `fee_paid` | `entry_fee_usdc` | (entry only — exit fee is computed at close) |
| `slippage_paid` | `entry_slippage_usdc` | |
| `realized_pnl` | `realized_pnl_usdc` | |
| `unrealized_pnl` | `unrealized_pnl_usdc` | |
| `opened_at` | `entry_at` | |
| `closed_at` | `exit_at` | |
| `thesis` | `notes` | Same column; either name works as long as the UI reads `notes` |

### `PB.SIGNALS` lens_list format

UI mock uses `'absolute_overall'` (underscore). Backend returns `'absolute/overall'` (slash). Update the parser if any code splits on the separator.

### `PB.SYSTEM_STATUS`

| UI mock currently uses | Backend actually returns | Notes |
|---|---|---|
| `last_cycle_duration_s` | (not exposed) | Backend doesn't compute. Hide the field or hardcode "—". |
| `consecutive_long_cycles` | (not exposed) | Same. |
| `dropped_positions_last_cycle` | (not exposed) | Same. |
| `zombie_drops_last_24h` (single number) | `counters.zombie_drops_last_24h.{redeemable, market_closed, dust_size, resolved_price_past, incomplete_metadata, total}` | Section 9 of this doc — render as breakdown |
| `components.daily_snapshot.succeeded` / `.failed` | `components.daily_snapshot.latest_run.succeeded_combos` / `.failed_combos` | Same data, one level deeper |

### `PB.BACKTEST_SLICE` per-bucket

UI mock has flat `bh_fdr_lo` / `bh_fdr_hi` per bucket. Backend nests inside `buckets[label].corrections.bh_fdr_pnl_ci_lo` / `.bh_fdr_pnl_ci_hi`. Same for Bonferroni.

### `PB.EDGE_DECAY`

UI mock cohort uses `n`. Backend returns `n_eff`. Trivial rename.

### `PB.SIGNALS[].is_new`

Backend doesn't return this. The UI computes it client-side from `first_fired_at > localStorage.lastReadSignalsAt`. Already correct in the existing UI logic — just needs `first_fired_at` (now provided by backend) instead of the mock `is_new` flag.

---

## Summary of UI surfaces this round adds or extends

| # | Section | Where | Type |
|---|---|---|---|
| 1 | Latency profile selector | Backtest header | NEW |
| 2 | Filter chip set (8 new chips) | Backtest filters row | EXTEND |
| 3 | Slice dimension picker (1 → 11) | Backtest slice explorer | EXTEND |
| 4 | Benchmarks (3 → 5) | Backtest compare view | EXTEND |
| 5 | Bonferroni + BH-FDR side-by-side | Backtest stats panel | EXTEND |
| 6 | `pnl_bootstrap_p` line | Backtest stats panel | NEW |
| 7 | Multiplicity tracker tiered banners | Backtest header | EXTEND |
| 8 | Edge decay quality flags | Diagnostics | EXTEND |
| 9 | Zombie drops 5-way breakdown | Health panel | EXTEND |
| 10 | stats_freshness 3-state chip | Health panel | EXTEND |
| 11 | Insider wallet management screen | Settings sub-page | NEW |

Field-name mapping work is a separate small pass on `data.js` (or its fetch-layer replacement).

---

End of refinement spec. Ship.
