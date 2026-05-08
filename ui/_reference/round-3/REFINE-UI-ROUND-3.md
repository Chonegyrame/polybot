# Polybot UI — Refinement Spec (round 3)

> Hand this to Claude Design as the prompt for the next iteration.
> Focused additive refinement. Touches only the Backtest filter panel.

---

## Context

Round 2 added a filter panel to the Backtest sub-tab. Audit against the
real backend (`app/api/routes/backtest.py`) revealed:

1. **Three checkboxes don't map to any backend parameter** — they look
   like filters but the backend ignores them. Need to be removed.
2. **Several range inputs ask for both min and max** but the backend
   only supports one direction. Need to be reduced to single inputs.
3. **One filter (entry_price) has no backend support at all** — needs
   to be removed.
4. **Liquidity is a single dropdown** but the backend takes a multi-select
   array — should let the user pick more than one tier.
5. **Five filters from the original spec are missing** and the backend
   does support them — need to be added.
6. **Custom latency is a single slider** but the backend requires two
   bounds (min and max). Needs splitting.

This doc lists the exact changes. Every parameter name and type below
is verified against the actual route signatures.

---

## Section A — Remove these three checkboxes

The current filter panel shows three checkboxes that send query
parameters the backend doesn't understand. Toggling them does nothing.

**Remove from the filters state object:**
- `require_active_only`
- `require_yes_market`
- `exclude_resolved_at_fire`

**Remove the three `<label className="cb">` elements** that render
these toggles in the filters panel.

---

## Section B — Reduce these range inputs to single inputs

The backend supports a min OR a max for each of these, not both. Drop
the unsupported half:

| Current UI control | Keep this side | Drop this side | Maps to backend param |
|---|---|---|---|
| Trader count (min + max) | min | drop max | `min_trader_count` |
| Aggregate USDC (min + max) | min | drop max | `min_aggregate_usdc` |
| Gap to smart money (min + max) | max | drop min | `max_gap` |

After this change, replace each range pair with a single labeled input.
Example: instead of "Trader count: min [...] max [...]", show
"Min trader count: [...]".

**Skew (min + max) stays as a range** — backend supports both
`min_skew` and `max_skew`.

---

## Section C — Remove the entry-price filter entirely

The backend has no `min_entry_price` / `max_entry_price` parameters.
Entry price IS a slice dimension (the user can group by entry-price
bucket in the slice explorer), but it can't be filtered by directly.

**Remove from the filters state object:**
- `entry_min`
- `entry_max`

**Remove the corresponding range input** from the filters panel.

---

## Section D — Convert liquidity to multi-select

The backend takes `liquidity_tiers` as a list — the user can include
multiple tiers at once (e.g. "show me both medium AND deep markets").
Current UI uses a single-value dropdown.

**Replace** the single `<select>` for `liquidity` with a multi-select
control: four toggleable pills labeled `thin`, `medium`, `deep`,
`unknown`. Each click toggles one tier in/out of the active set.

State shape: change `filters.liquidity` from `'all' | 'thin' | 'medium'
| 'thin'` to `filters.liquidity_tiers` as an array. Empty array =
"all tiers" (no filter applied).

Backend wire-up: send each selected tier as a repeated query param —
`?liquidity_tiers=thin&liquidity_tiers=medium`.

---

## Section E — Add five missing filters

These are valuable backend filters that the UI currently doesn't
expose. Add them as filter chips inside the existing filters panel.

### E.1 — Holdout cutoff (date picker)

Lets the user reserve recent data as out-of-sample. Crucial for
honest hold-out testing.

- **Control:** date picker labeled "Training data ends:" with a
  "(none)" option that clears the filter.
- **State key:** `holdout_from`
- **Backend param:** `holdout_from=YYYY-MM-DD` (ISO date)
- **Default:** unset (no holdout).
- **Tooltip:** "Excludes signals fired on or after this date.
  Useful for honest pre-registered hypothesis testing — you train on
  earlier data, then check the result against the held-out tail."

### E.2 — Trade size assumption (number input)

Lets the user model fees/slippage at their actual sizing.

- **Control:** number input labeled "Trade size assumption ($):"
- **State key:** `trade_size_usdc`
- **Backend param:** `trade_size_usdc=<float>` (float, must be > 0)
- **Default:** `100`
- **Tooltip:** "Assumed trade size used for fee + slippage modeling
  per signal. $100 is a reasonable retail default. Larger sizes
  trigger more slippage in deeper markets."

### E.3 — Min portfolio fraction (slider)

Filter to high-conviction signals only.

- **Control:** slider 0–20% (steps of 1%) labeled "Min avg portfolio
  fraction:" — empty / 0% means no filter.
- **State key:** `min_avg_portfolio_fraction`
- **Backend param:** `min_avg_portfolio_fraction=<float 0..1>`
- **Default:** unset
- **Tooltip:** "Only show signals where the involved traders had an
  average of at least N% of their portfolio committed to the bet.
  Higher = more conviction."

### E.4 — Include unavailable rows (toggle)

Default-off honesty toggle.

- **Control:** checkbox labeled "Include unavailable-book signals"
- **State key:** `include_pre_fix`
- **Backend param:** `include_pre_fix=true|false`
- **Default:** `false`
- **Tooltip:** "Off by default. Includes signals where the order book
  couldn't be read at fire time (mostly old signals before the
  entry-pricing fix landed). Turn on for max-coverage backtesting,
  off for honest results."

### E.5 — Include multi-outcome markets (toggle)

Default-off scope toggle.

- **Control:** checkbox labeled "Include multi-outcome markets"
- **State key:** `include_multi_outcome`
- **Backend param:** `include_multi_outcome=true|false`
- **Default:** `false`
- **Tooltip:** "Off by default. Includes scalar / categorical /
  conditional markets. Most users want binary YES/NO only."

---

## Section F — Fix custom-latency to two bounds

When `latencyProfile === 'custom'`, the backend requires both
`latency_min_min` AND `latency_max_min`. The current UI has a single
slider (`customLatency`).

**Replace the single slider** with two number inputs side by side:
- "Min minutes:" → `customLatencyMin` state, default 5
- "Max minutes:" → `customLatencyMax` state, default 15

Validation: max must be ≥ min. Show inline error if not.

Backend wire-up: send `?latency_profile=custom&latency_min_min=<n>&latency_max_min=<m>`.

---

## Section G — Recommended grouping

After all the above, the filters panel should be three rows:

**Row 1 — Strategy:**
- Direction (toggle YES/NO/both — keep)
- Exit strategy (segmented control hold/smart_money_exit — keep)
- Dedup (checkbox `require_dedup` → backend `dedup=true|false` — keep, just rename internal state to `dedup`)

**Row 2 — Filters:**
- Min skew + Max skew (range)
- Min trader count (single)
- Min aggregate USDC (single)
- Max gap to smart money (single)
- Min portfolio fraction (slider — Section E.3)
- Liquidity tiers (multi-select — Section D)
- Market category (dropdown — keep)

**Row 3 — Sizing & honesty:**
- Trade size assumption (Section E.2)
- Holdout cutoff (Section E.1)
- Include unavailable rows (Section E.4)
- Include multi-outcome markets (Section E.5)

---

## Section H — Reference: full backend filter set (verified)

Every parameter the backend's `/backtest/summary`, `/backtest/slice`,
and `/backtest/edge_decay` accept. Use this as the canonical list.

| Param | Type | Default | Notes |
|---|---|---|---|
| `mode` | string | none | absolute \| hybrid \| specialist |
| `category` | string | none | Lens category (overall / politics / sports / crypto / culture / tech / finance) |
| `direction` | string | none | YES \| NO |
| `min_skew` | float 0..1 | none | — |
| `max_skew` | float 0..1 | none | — |
| `min_trader_count` | int | none | — |
| `min_aggregate_usdc` | float | none | — |
| `min_avg_portfolio_fraction` | float 0..1 | none | — |
| `liquidity_tiers` | repeated string | none | Subset of (thin, medium, deep, unknown) |
| `market_category` | string | none | The actual market's category, distinct from `category` (the lens) |
| `max_gap` | float | none | gap = signal_entry_offer / first_top_trader_entry_price - 1 |
| `include_pre_fix` | bool | `false` | When true, includes signals with `signal_entry_source='unavailable'` |
| `include_multi_outcome` | bool | `false` | When true, includes scalar/neg-risk markets |
| `trade_size_usdc` | float > 0 | `100.0` | Sizing for fee/slippage modeling |
| `exit_strategy` | string | `hold` | hold \| smart_money_exit |
| `dedup` | bool | `false` | When true, reads from `vw_signals_unique_market` (one row per cid+direction) |
| `holdout_from` | date YYYY-MM-DD | none | Exclude signals on/after this date |
| `latency_profile` | string | none | active \| responsive \| casual \| delayed \| custom |
| `latency_min_min` | float | none | Required when latency_profile=custom |
| `latency_max_min` | float | none | Required when latency_profile=custom; must be ≥ latency_min_min |

For `/backtest/summary` only, also:
| `benchmark` | string | none | buy_and_hold_yes \| buy_and_hold_no \| buy_and_hold_favorite \| coin_flip \| follow_top_1 |

For `/backtest/edge_decay` only, also:
| `min_n_per_cohort` | int 1..50 | `5` | Drops cohorts smaller than this threshold |

---

## Section I — Things explicitly NOT to change

- Latency profile cards (none/active/responsive/casual/delayed/custom selector) — keep.
- Latency stats bar (adjusted vs fallback) — keep.
- Multiplicity tracker tiered banner (5/10/25 thresholds) — keep.
- Slice dimension dropdown (11 dimensions) — keep.
- Benchmarks side-by-side compare — keep.
- BH-FDR + Bonferroni stacked CI lines — keep.
- Edge decay chart + quality flags — keep.
- Anything outside the Backtest sub-tab — leave alone.

---

## Summary of changes

| # | Change | Type |
|---|---|---|
| 1 | Delete `require_active_only`, `require_yes_market`, `exclude_resolved_at_fire` checkboxes | DELETE |
| 2 | Reduce `trader_count` and `aggregate` ranges to single min input each | EDIT |
| 3 | Convert `gap` range to single `max_gap` input | EDIT |
| 4 | Delete `entry_min` / `entry_max` filter | DELETE |
| 5 | Convert liquidity dropdown to multi-select tier pills | EDIT |
| 6 | Add holdout cutoff (date picker) | NEW |
| 7 | Add trade size assumption (number input) | NEW |
| 8 | Add min avg portfolio fraction (slider) | NEW |
| 9 | Add include_pre_fix toggle | NEW |
| 10 | Add include_multi_outcome toggle | NEW |
| 11 | Split custom latency slider into min + max inputs | EDIT |
| 12 | Rename `require_dedup` state key to `dedup` for backend wire-up clarity | EDIT |
| 13 | Group the filter panel into three logical rows (Strategy / Filters / Sizing & honesty) | EDIT |

Ship.
