# Polymarket Smart Money Tracker — UI Specification

> Living document. Hand this to the third-party UI builder.
> The backend exposes a REST API (see `API` section). The UI is a dashboard on top of that API.

---

## Purpose of the app

Personal dashboard that tracks Polymarket's top traders, surfaces markets where many of them have overlapping positions ("smart money consensus signals"), and lets the user explore by market category.

The user is a manual trader — the app helps them spot good entries, it does NOT auto-trade.

---

## Core concepts the UI must communicate clearly

The user needs to feel these three ideas at all times:

1. **Who is "top"?** — the trader ranking changes based on the user's chosen mode.
2. **Top in what?** — overall, or within a specific category (politics, sports, etc.).
3. **What's the signal?** — which markets have meaningful consensus among that filtered top group.

---

## CRITICAL — Sectors vs. Markets (must understand before building)

These are TWO different concepts. The UI builder must not conflate them.

| Term | Meaning | Cardinality |
|---|---|---|
| **Sector / Category** | A broad bucket the user filters by. There are exactly 7 (Overall, Politics, Sports, Crypto, Culture, Tech, Finance). | A few |
| **Market** | A single binary YES/NO prediction question. | Hundreds per sector |

A sector **contains many markets**. Inside *Finance*, for example, there are markets like:
- "Will Bitcoin hit $200k by EOY 2026?"
- "Will the Fed cut rates in March 2026?"
- "Will Tesla close above $300 on Dec 31?"
- "Will Apple market cap exceed $4T by Q3?"

Each market is independent — its own YES/NO question, its own price, its own resolution.

### How this affects the signals feed

When the user picks **Finance + Top 50 + Absolute**, the system:
1. Identifies the 50 top traders in Finance
2. Looks at every Finance market those 50 traders hold positions in
3. For EACH market, computes whether consensus is strong enough to fire a signal
4. **Returns ALL firing markets simultaneously** — typically several at once

**The signals feed is a LIST, not a single result.** The UI must render it as a vertical list of signal cards/rows, one per firing market. Multiple signals within the same sector is the normal, expected case — not an edge case.

Example: with **Finance + Top 50 + Absolute**, the user might see 3, 8, or 20 different markets firing, all listed together. Each is its own row with its own consensus number, direction, and metadata.

The same trader (e.g. Theo4) can count toward multiple signals at once — he might be on YES of one market and NO of another simultaneously. Each market's consensus is computed independently.

---

## Primary controls (always visible at the top of the dashboard)

### Control 1 — Ranking Mode toggle

Three modes the user can switch between:

| Mode | Label | What it does |
|---|---|---|
| A | **"Absolute PnL"** | Ranks traders purely by total dollar profit. No filters. Lets through low-frequency big-size traders (whales, insiders). |
| B | **"Hybrid (PnL + ROI)"** | Ranks by a combined score that rewards both raw profit and capital efficiency. Filter: minimum cumulative volume ($5,000) — excludes lucky outliers and tiny accounts. |
| C | **"Specialist"** | Ranks per-category specialists by ROI. Floors: ≥$20k category volume, positive category PnL, recent activity (last month). Surfaces sharp small-bankroll traders that A and B structurally miss. |

UI: segmented control / toggle, three options. Mode A is default.

Info-icon explanations:
- *Absolute PnL: biggest dollar winners. Great for finding whales and insiders.*
- *Hybrid: balances profit with capital efficiency. Excludes lucky outliers and tiny accounts.*
- *Specialist: small-bankroll traders who consistently win in this category. Different population — they hide behind the whales in absolute rankings.*

All modes automatically exclude wallets the system has classified as market makers or arbitrage bots (their "positions" are inventory, not directional bets), and dedupe sybil clusters (multi-wallet operators count as one entity, not many).

### Control 2 — Category selector

Dropdown or tab bar with the user's chosen category. **Exact options exposed by the API (locked in):**

- **Overall** (default) — across all markets, no filter
- **Politics**
- **Sports**
- **Crypto**
- **Culture**
- **Tech**
- **Finance**

These are the only seven valid values. Other topical labels visible on Polymarket's website (Iran, Geopolitics, Esports, Breaking, Trending, etc.) are tag-based event filters — they exist for browsing markets but **cannot be used to rank traders**. The leaderboard API rejects them. UI must not show those as options in this control.

Iran-themed or geopolitics markets will naturally surface under **Politics** (their parent category in Polymarket's taxonomy).

The category selector affects EVERYTHING below it:
- The top traders list filters to traders ranked within that category
- The signals feed shows only signals on markets within that category
- "Overall" means no category filter — top traders ranked across all markets, signals shown for all markets

### Control 3 — Top-N slider

Slider letting the user choose how many top traders to consider. **Range: 20 to 100, step of 5. Default: 50.**

Affects the top traders list and the consensus calculation for signals. The user must be able to set this independently of the other two controls — e.g. "top 20 in Absolute / Finance" and "top 100 in Hybrid / Sports" are both valid and produce completely different views.

---

## How the three controls interact

The product is a **3D selection** of (Ranking Mode × Category × Top-N). Every distinct combination produces a different top-trader list and therefore a different set of consensus signals.

**Examples of valid user selections:**

| Mode | Category | Top-N | What the user gets |
|---|---|---|---|
| Absolute PnL | Overall | 50 | The 50 biggest dollar winners across all markets |
| Hybrid | Finance | 70 | The 70 most consistent performers within finance markets |
| Absolute PnL | Sports | 100 | The 100 biggest dollar winners specifically in sports |
| Hybrid | Crypto | 25 | The 25 most consistent performers within crypto markets |

The user must be able to flip any of the three independently. The UI re-renders the top-trader list and signals feed each time. Show a clear loading state during the transition.

**Important:** the three controls don't lock to each other — switching mode doesn't reset category or top-N. Each holds its own state.

---

## Page layout (recommended)

Two top-level routes, switched via the global nav bar:

- **`/dashboard`** — the live signal feed + watchlist tier + top traders + drill-down modal (Sections 1–4, 6, 7)
- **`/testing`** — the user's testing & analysis hub: wallet, paper-trade portfolio, market browser, per-market trading view, backtest, diagnostics (Section 5, with sub-tabs)

The nav bar should be visible on every page with two tabs: **Dashboard** and **Testing**.

**Avoid** putting a big "P&L: +$X" hero number on the nav. Recency bias is real — if the user sees a green number every page load, they over-trust the system. Wallet/balance lives inside the Testing page; nav stays clean.

### Section 1 — Header / Controls (Dashboard)
Sticky header with:
- App title
- Ranking Mode toggle
- Category selector
- Top-N slider
- **Status row** (right-aligned, see below)

#### Status row — system health pill + new-signals badge

**Health pill** — single colored pill in the header, click expands a details panel.

States:
- 🟢 **Green** ("All systems healthy") — last cycle clean, fresh data, no warnings
- 🟡 **Amber** ("System degraded") — last cycle ran long, dropped some data, or system is recovering
- 🔴 **Red** ("Stale / failing") — last refresh >60min, repeated failures, or gamma is down — **do not trust signals while red**

Click expands a small panel showing:
- Last position refresh timestamp
- Last cycle duration (warn at >9 min)
- Consecutive long cycles count
- Dropped positions in the last cycle
- Markets that failed to discover in the last cycle
- Last sybil/classifier batch run

Backend: `GET /system/status` returns the full health summary. UI polls every 60 seconds.

**New-signals badge** — small chip next to the health pill:
- "**3 new signals** since 14:42  · [Mark all read]"
- Click "Mark all read" → store the current ISO timestamp in `localStorage.lastReadSignalsAt`; badge resets to zero
- Counter computed via `GET /signals/new?since=<timestamp>` filtered to current mode/category selection

#### Per-signal NEW pill (in the feed)

Any signal whose `first_fired_at > localStorage.lastReadSignalsAt` shows a small "NEW" pill on its card. Pressing "Mark all read" in the header instantly hides every NEW pill currently visible. **The pill is a UI marker only — the underlying signal data does not change. The same market keeps appearing in the feed across future loads, just without the pill, and stats update normally each refresh.**

#### Optional desktop notifications

When the dashboard tab is open, request browser Notification permission once on first visit. If granted, fire one native `Notification` per new signal as it arrives via the next `/signals/new` poll. Permission is opt-in — never auto-prompt without an affordance. Default to off.

### Section 2 — Active Signals Feed (the most important section)

The headline product. **A vertical scrollable list of signal cards** — one card per market where the filtered top-N has meaningful consensus. There can be anywhere from zero to dozens of cards visible at once depending on the current selection.

#### What each signal row/card displays

**Headline data:**
- **Market question** (full text) — `market_question`
- **Net direction badge** — large, color-coded YES/NO. Show **both skews** on hover: headcount skew (`direction_skew`) and dollar skew (`direction_dollar_skew`). Both must clear 65% for the signal to fire — display the lower of the two on the badge so the user sees the binding constraint.
- **Entity count** — "23 of 50" (`trader_count` of `top_n`). Note: cluster-collapsed — a 4-wallet sybil cluster contributes 1 to this count.
- **Average portfolio fraction allocated** — "8.2%" (conviction indicator) — `avg_portfolio_fraction`. Per-entity, not per-wallet.
- **Current market price** — `current_price` (the latest snapshot price)
- **Executable entry price** — `signal_entry_offer` (the actual ask captured at signal-fire from the CLOB book). Display as "Entry: $0.69" alongside `current_price`. The gap between these two is what the user actually pays vs. what the smart money paid.
- **Entry-source badge** — `signal_entry_source` ∈ `clob_l2 | gamma_fallback | unavailable`. Show a small badge: `clob_l2` = book-derived (trustworthy), `gamma_fallback` = price-only fallback (caveat), `unavailable` = no entry recorded (signal still valid but no executable price). Filter the backtest by `?include_pre_fix=false` to exclude `unavailable` rows.
- **Spread** — `signal_entry_spread_bps` shown on hover ("entry spread: 47 bps").
- **Total $ aggregate** — `aggregate_usdc`

**Quality indicators (NEW — critical for honest decisioning):**
- **Gap to smart money** — color-coded prominently:
  - 🟢 **<+5%** ("Early — gap still open")
  - 🟡 **+5 to +20%** ("Reachable — partial move priced in")
  - 🔴 **>+20%** ("Likely already moved — entering near smart money's profit zone")
- **Liquidity tier badge** — `liquidity_tier` ∈ `thin | medium | deep | unknown`. Show with USDC depth (`liquidity_at_signal_usdc`) on hover. Thresholds: `thin` < $5k depth at ±5¢ from mid, `medium` $5k-$25k, `deep` ≥ $25k. Lets the user judge whether they can actually size into the trade.
- **Lens count badge** — e.g. "Confirmed by 5 lenses" with tooltip listing which (mode, category) combos agree. Replaces showing the same market 5 times.
- **Counterparty warning** — count-based, with tier:
  - "⚠ 1-2 top traders hold opposite side" (mild — amber)
  - "⚠ 3+ top traders hold opposite side" (strong — red)
  - Backed by `counterparty_count` int (cluster-aware — multiple wallets in one sybil cluster count as one entity, not multiple counterparties).
- **Hedge warning** — if any contributing wallet (or their cluster) ALSO holds a meaningful position on the OPPOSITE side of this market: "⚠ 1 of 5 contributing entities is hedged" badge in amber. This is distinct from the counterparty warning — counterparty is "different top traders on the other side"; hedge is "the same entity firing this signal also holds the opposite side."
- **Freshness label** — "Formed 4h ago" / "Stale (>4h since refresh)" — derived from `first_fired_at` and `last_seen_at`. Stale signals get **strikethrough on the direction badge** + reduced opacity + an explicit age warning. The card remains visible but visually de-prioritised.
- **Inline exit state** — if `has_exited=true`, render the exit banner inline on the card with `exit_event` details (see Section 7 for the full banner spec). Two tiers: `event_type=trim` (amber) or `event_type=exit` (red, indicates auto-close already happened on any open paper trades).
- **Insider flag** — if `has_insider=true`, render a small purple "🔮 Insider involved" pill. Means at least one of the contributing wallets is in the user's manually-curated insider list (see `/insider_wallets`). Phase-2 from a *feature* perspective (the insider list itself is user-managed), but the flag is wired today.

**Backend:** all of these come from existing fields on `signal_log` (entry-time + peak metrics + `signal_entry_*` + `liquidity_*` + `counterparty_count` + `has_exited` + `has_insider`) + `lens_count` / `lens_list` from the deduped view + a hedge flag computed from the contributors endpoint described below.

**Note on `first_*` vs `peak_*` fields:** the API exposes both `first_trader_count`/`first_aggregate_usdc`/`first_net_skew`/`first_avg_portfolio_fraction` (frozen at signal-fire time, canonical for backtest) and `peak_trader_count`/`peak_aggregate_usdc`/`peak_net_skew`/`peak_avg_portfolio_fraction` (lifetime max while the signal was live, diagnostic only). The signal card's "Trader count" and "Aggregate" should display the **current observed values** from the live aggregator (the `trader_count` and `aggregate_usdc` fields without prefix), with `peak_*` available as a hover/tooltip ("peaked at 31 of 50 four hours ago"). The `first_*` columns are mostly relevant in the backtest view, not on the live card.

#### Contributors panel (expandable section on each card)

Below the headline indicators, each signal card has a collapsible **"Show contributors"** section. When expanded, it lists every wallet that fired this signal — and every top-N wallet currently on the opposite side as counterparty — with their actual position sizes. This lets the user verify the system's read manually instead of trusting the aggregate numbers.

**For each contributing wallet (signal side):**
- Wallet name (`user_name` if set, else short address `0xab...cd`) + verified badge if applicable
- Cluster label if part of a known sybil/co-entry cluster (e.g. "Cluster A · 4 wallets" — wallets in the same cluster grouped together visually so the user sees "this is one entity")
- Same-side position: `$70,000 on YES (size 175k shares @ avg $0.40)`
- Opposite-side position if any: `also $20,000 on NO ⚠ hedged` — visually flagged so it's hard to miss
- Lifetime PnL + ROI (small, secondary)
- Click wallet name → opens trader drill-down modal (Section 4)

**For each counterparty wallet (opposite side):**
- Same shape as contributors, but on the other side
- Sorted by opposite-side dollar amount descending (biggest counterparties first)
- Cluster grouping applies here too — a 4-wallet sybil cluster on the opposite side is shown as one entity, not four

**Worked example (what the user actually sees):**

> Signal: YES on "Will Trump nominate Rubio for Sec State?" — 5 entities, $90k aggregate, 82% dollar-skew
> 
> **Contributors (5 entities, click to expand):**
> - **Théo (Cluster A · 4 wallets):** $70k on YES — ⚠ also $20k on NO (hedged, net +$50k YES)
> - **0xab...cd:** $5k on YES
> - **0xef...01:** $5k on YES
> - **0x12...34:** $5k on YES
> - **0x56...78:** $5k on YES
> 
> **Counterparty (1 top trader on NO):**
> - **Whale_X:** $80k on NO

With this view the user can immediately see: "the 82% YES skew is mostly one hedged whale plus four small bets — and there's an $80k counterparty whale on the other side." Their decision becomes a real read of the situation, not a trust exercise in the aggregate number.

**Backend endpoint:** `GET /signals/{signal_log_id}/contributors`. Returns:

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
    },
    ...
  ],
  "counterparty": [
    {
      "proxy_wallet": "0x...",
      "user_name": "Whale_X",
      "cluster_id": null,
      "same_side_usdc": 0,
      "opposite_side_usdc": 80000,
      "is_hedged": false,
      ...
    }
  ],
  "summary": {
    "n_contributors": 5,
    "n_hedged_contributors": 1,
    "n_counterparty": 1,
    "total_same_side_usdc": 90000,
    "total_opposite_side_usdc": 80000
  }
}
```

The card's badges (`hedge warning`, `counterparty count`) are derived from `summary` so they can be rendered without expanding the panel. The full contributor list is fetched lazily on first expand to keep the dashboard light.

#### Signal eligibility floors (backend filters before sending to UI)

A market only fires a signal if ALL four are true:
- ≥5 distinct top **entities** in the market (cluster-collapsed: a 4-wallet sybil cluster counts as 1)
- Aggregate USDC at risk across those entities ≥ $25k
- Net direction **headcount skew** ≥ 65% (one side holds 65%+ of involved entities)
- Net direction **dollar skew** ≥ 65% (same side holds 65%+ of involved USDC)

Both skews must clear independently — a market with 4 small YES bets and 1 big NO whale fails the dollar gate even if the headcount gate passes. The UI doesn't enforce any of this — the API only returns markets that already pass. UI just renders what arrives.

#### Sorting controls

Sort dropdown above the list. **Default sort: Gap to smart money (smallest first)** — small-gap signals are the only realistically tradeable ones; sorting by aggregate would lead the user to already-moved signals.

Options:
- **Gap to smart money** (smallest first — *default*)
- **Freshness** (newest first)
- **Lens count** (most-confirmed first)
- **Trader count**
- **Aggregate USDC**
- **Net direction skew**

Switching sort re-orders the existing list, doesn't fetch new data.

**Avoid:** "🔥 hottest signals" sorts that emphasize biggest-aggregate. Those are precisely the signals where the move has already happened.

#### Empty state

When the current filter produces zero firing signals (e.g. small top-N + obscure category), show a clear empty state:

> *"No signals firing in this view right now. Try widening the top-N, switching to Overall, or check back in 10 minutes."*

Do not show an empty list with no message — the user should know whether the system has nothing to show vs. is still loading.

#### Visual hierarchy

Strongest signal = small gap to smart money + fresh + multiple lenses confirm + no counterparty warning. **Not** the biggest-aggregate signals — those are usually already moved.

- **Best entries** (gap <5%, fresh <2h, ≥3 lenses, no counterparty warning): standard card with subtle outline highlight. **No fire emoji or oversized treatment.**
- **Solid signals** (gap 5-20%, fresh, no warnings): standard card.
- **Stale or high-gap** (>4h or gap >20%): faded card with explicit warning indicator.
- **Counterparty conflict** (smart money also selling): red-bordered card with "⚠ Smart money on the other side" prominently displayed.

**The visual style must NOT trigger FOMO.** The user should think before clicking, not feel rushed. No auto-refresh faster than 2 minutes. No persistent flashing/pulsing. No big "P&L: +$X" hero numbers on the dashboard top — bury those in a stats subview.

#### Signal card click behavior

Clicking anywhere on a signal card → navigates to `/testing/market/:condition_id` (the per-market trading view in the Testing route). The signal's direction is pre-highlighted in the buy panel; quality indicators are displayed prominently in the Smart Money panel. The user can size, write thesis, and confirm from there.

This replaces the older "small Paper Trade button on the card" pattern. The whole card is the call-to-action, but it leads to a thoughtful trading view, not a quick-confirm modal.

Secondary affordances on each signal card:
- **Trader pills** — click any trader name on the card → opens the trader drill-down modal (Section 4)
- **"Mark NEW" pill** — informational only, click does nothing

#### Concrete example

Selection: **Finance / Top 50 / Absolute**. Possible feed render:

```
┌──────────────────────────────────────────────────────────────────┐
│ Will Bitcoin hit $200k by EOY 2026?                  $0.67       │
│ ✦ YES 86%   ·   30 of 50 traders   ·   $4.2M total   ·   +3% drift│
│ Avg portfolio fraction: 8.1%   ·   Formed 4h ago                  │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ Will the Fed cut rates in March 2026?                $0.42       │
│   NO 71%    ·   22 of 50 traders   ·   $1.8M total   ·   +12% drift│
│ Avg portfolio fraction: 4.0%   ·   Formed 2 days ago              │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ Will Apple exceed $4T market cap by Q3?              $0.31       │
│   YES 64%   ·   18 of 50 traders   ·   $890k total   ·   +18% drift│
│ Avg portfolio fraction: 2.8%   ·   Formed 6 days ago (stale)      │
└──────────────────────────────────────────────────────────────────┘
```

Three different markets, all in Finance, all firing simultaneously. The UI renders all of them as a list. The user scrolls or sorts to explore.

### Section 3 — Top Traders List
The filtered top-N for the current Ranking Mode + Category.

Each row:
- Rank
- Wallet address (truncated, copy button)
- PnL ($)
- ROI (%) — only relevant in Hybrid mode but show in both
- # resolved trades
- # active positions
- Volume traded ($)

Click on a trader → opens **trader drill-down modal** (V1 — see below).

### Section 4 — Trader drill-down modal (V1)

Triggered by clicking any row in the Top Traders list, or any trader pill on a signal card. Modal/page (UI builder's choice — overlay preferred for minimal nav).

**Header**
- User name + verified badge (`profile.user_name`, `profile.verified_badge`)
- X / Twitter handle if known (`profile.x_username`)
- Profile image if known (`profile.profile_image`)
- Truncated wallet (with copy)
- First seen at, last seen at (`profile.first_seen_at`, `profile.last_seen_at`)
- Total PnL ($), ROI (%), volume ($), # positions

**Classification panel** (`classification` object on the response, may be null if wallet not yet classified):
- **Wallet class** — `directional` (the kind of trader you want to follow) | `market_maker` (excluded from all top-N pools — provides liquidity, no directional bet) | `arbitrage` (excluded from top-N — cross-market arb) | `likely_sybil` (excluded from top-N — flagged as part of a multi-wallet operator group) | `unknown`. Show a colored chip and a one-line tooltip explaining what the label means. If excluded class, also show "ℹ Excluded from top-N rankings."
- **Confidence** (0..1) — quality of the classification (low confidence = thin sample).
- **Classified at** — when the weekly classifier last evaluated this wallet.
- **Forensic features** (collapsible — most users won't open) — `n_trades`, `two_sided_ratio` (fraction of markets where the wallet has BOTH sides — high = MM behavior), `cross_leg_arb_ratio`, `median_trade_size_usdc`, `distinct_markets_per_day`, `buy_share`, `span_days`. Surfaces the actual numbers the classifier used to label.

**Cluster panel** (`cluster` object, may be null):
- **Cluster ID** + **detection method** (`time_correlation` | `funding_source` | `behavioral` | `manual`) + **cluster size** (number of wallets in this cluster).
- **Evidence** (collapsible) — `n_pair_edges`, `n_group_flags`, `detection_modes` (`["pair"]` | `["group"]` | `["pair","group"]`), `min_co_entry_rate`, `max_co_entry_rate`, `mean_co_entry_rate`, `max_group_shared_buckets`. Tooltip: "This wallet is grouped with N others as a single entity for top-N ranking and signal counting."
- "View other cluster members →" link (Phase 2 — opens a list of the cluster's other wallets).

**Per-category breakdown** — table from `per_category[]`:
- One row per category (Politics, Sports, …) — `pnl`, `vol`, `roi` (computed `pnl/vol`), `rank` (their rank within that category for the current mode). Lets the user judge whether the trader is a generalist or a specialist.

**Open positions** — table from `open_positions[]`:
- Market question (`question`) + slug — clickable → opens that market in the signals feed if currently visible
- Market category (`market_category`) — chip
- Direction (`outcome` "Yes"/"No")
- **Position size USDC** (`current_value`)
- **Cost basis** (`avg_price`) and **Current price** (`cur_price`)
- **Cash P&L** (`cash_pnl`), **Percent P&L** (`percent_pnl`)
- First seen at (`first_seen_at`)
- Closed-market flag (`closed`) — markets that have already resolved show a faded row

> Note: the current `/traders/{wallet}` response does NOT include `% of trader's portfolio` directly on each position. To compute it, divide `current_value` by the trader's portfolio value (latest `portfolio_value_snapshots` row). This is shown directly on `/markets/{condition_id}.tracked_positions_per_trader[]` as `portfolio_fraction` — UI may want to fetch the per-market view when this number matters.

When this modal is opened from a signal card (clicking a trader pill), the row for that specific market should be visually highlighted so the user immediately sees the trader's stake in the signal they're investigating.

**Recent trades** — *not currently exposed by `/traders/{wallet}` in V1.* The endpoint returns `profile`, `classification`, `cluster`, `per_category[]`, `open_positions[]` only. The "recent trades" panel is Phase 2 — backend would need to add a `recent_trades[]` field reading from the `/trades` Polymarket endpoint at request time.

Backend endpoint: `GET /traders/{wallet}` returns `{ profile, classification, cluster, per_category[], open_positions[] }`.

---

### Section 5 — Testing route

Lives at **`/testing`**. The user's lightweight Polymarket clone — wallet, paper-trade portfolio, market browser, per-market trading view, and backtest analysis — all in one tab. **No real money is ever moved.** Wallet balance is virtual.

#### Why this exists

The user wants 2–3 weeks (or months) of live observation before trusting real capital. Testing route lets them:
- Open and close trades on real markets at real prices, with real cost models, using fake money
- Browse the market universe and trade markets they discover (not just signals they're told about)
- Click through from a signal card → land on that market's trading view → trade from there
- Track running P&L and wallet balance like a real trading platform
- Run honest historical backtests once data accumulates

#### Wallet (always visible at top of `/testing`, all sub-tabs)

Persistent wallet card in the page header:
- **Balance** — e.g. `$8,450.20 available · $1,800 deployed = $10,250.20 total`
- **Total realized P&L** lifetime (color-coded green/red)
- **Deposit $** button — modal to add a chosen amount to wallet (simulated). Useful for resetting/topping up.
- **Reset wallet** button — confirmation modal warns: "This closes all open positions at current bid and resets balance to $10,000. Cannot be undone."

Default starting balance: **$10,000** (configurable later).

Wallet computation:
```
balance = starting_balance + Σ(realized_pnl) + Σ(deposits) - Σ(open_position_costs)
where open_position_cost = entry_size + entry_fee + entry_slippage
```

When a position closes (manual / resolution / smart-money exit), realized P&L is added back and the position cost is freed.

#### Sub-tab bar within `/testing`

- **Portfolio** (default landing) — open positions, history
- **Trade** — market browser → per-market trading view
- **Backtest** — historical signal analysis (headline + slices + holdout workflow + edge decay + half-life)
- **Diagnostics** — long-term monitoring (edge decay, half-life, signal coverage)

Each sub-tab preserves its own state (filters, sorts) when switching.

---

#### Sub-tab: **Portfolio** (default)

Lists every paper trade — open, closed, by exit reason.

**Filter chips:** Open / Closed (resolved) / Closed (manual) / Closed (smart-money exit) / All

**Trades table:**
- Columns: Market | Direction | Size | Entry (effective) | Current/Resolution | Unrealized/Realized P&L | Status | Action
  - **Open**: current mid-price + unrealized P&L. Action = "Sell" button.
  - **Closed (resolved)**: resolution outcome ("YES won at $1.00") + realized P&L. No action.
  - **Closed (manual)**: exit price + realized P&L. No action.
  - **Closed (smart-money exit)**: exit price + realized P&L + tag "Auto-closed: smart money exited."

**Row click → detail panel** showing: entry book metrics (offer, mid, spread, liquidity tier), originating signal (if any, with link to its trading view), thesis text, notes, full P&L breakdown (gross gain, fees paid, slippage cost).

**Sell flow:**
- Click "Sell" → confirm modal: "Sell $X of [market] [direction] at current bid $0.YY → estimated realized P&L: ±$Z (after exit fee)"
- POST `/paper_trades/:id/close` — adds realized P&L to wallet, closes position

**Auto-close paths:**
- Market resolves → status `closed_resolved`, payoff $1/$0/$0.50 by outcome
- Smart-money exit detected → status `closed_exit`, exit at current bid (see Section 7)

---

#### Sub-tab: **Trade** (market browser → per-market view)

Two states within this sub-tab:

##### State A: Browser (default when entering Trade)

- **Search bar** at top: "Search markets by question text..."
- **Filter chips:**
  - Category (Politics / Sports / Crypto / Culture / Tech / Finance / All)
  - Status (Active / Resolved / All — default Active)
  - "Has fired signal" toggle — only show markets with active signals
  - "I have a position" toggle — only show markets where user has an open paper trade
- **Sort:** Volume / Newest / Closing soon / Has signal / Last updated
- **Market list/grid:** each card shows:
  - Question text
  - Category tag
  - Current YES + NO prices
  - Total Polymarket volume
  - "📡 Active signal" badge if applicable (with direction)
  - "💼 Open position" badge if user is in this market

Click any market card → navigates to `/testing/market/:condition_id` (State B).

##### State B: Per-market trading view (`/testing/market/:condition_id`)

Full page for trading one specific market.

**Header:**
- Market question (large)
- Category tag, end date, total Polymarket volume
- Back link to browser

**Main content (left column):**
- **Price display** — large YES + NO prices with mid + spread bps
- **Mini orderbook** — top 5 bid + top 5 ask levels per outcome with size at each level
- **Recent CLOB fills** — last 10 trades (timestamp, side, size, price)
- **Resolution status** — "Resolves at end_date" or "Resolved YES/NO/50_50 on [date]"

**Smart Money panel (right column)** — driven by `/markets/{condition_id}` response which already exposes everything needed:
- Heading: "Tracked traders in this market: N" (from `tracked_positions_per_trader[].length`)
- **By-outcome aggregate strip** — from `tracked_positions_by_outcome[]`: for each outcome (Yes / No), show `trader_count`, `wallet_count` (raw wallets, before cluster collapse — useful for transparency), `aggregate_usdc`, `avg_entry_price`, `current_price`, `first_observed_at`. Lets the user see "10 traders / $400k on YES vs. 4 traders / $90k on NO" at a glance before drilling into individuals.
- **Per-trader rows** — from `tracked_positions_per_trader[]`. For each:
  - User name + verified badge (`user_name`, `verified_badge`)
  - **Wallet class chip** (`wallet_class`) — colors a market_maker wallet differently so the user knows that trader's "position" is inventory, not directional. Wallets in excluded classes shouldn't appear here in normal flow (the API filters them), but the chip is informational when they do.
  - **Cluster ID chip** (`cluster_id`) — grouping affordance: rows sharing a cluster_id should be visually grouped or share a color band.
  - Direction (`outcome` "Yes"/"No")
  - Position size (`current_value_usdc`) and initial size (`initial_value_usdc`)
  - **% of trader's portfolio** (`portfolio_fraction`) — key conviction indicator (precomputed by backend; do NOT recompute client-side)
  - Cost basis (`avg_entry_price`)
  - Current price (`current_price`)
  - Cash P&L (`cash_pnl_usdc`), percent P&L (`percent_pnl`)
  - First seen at (`first_seen_at`), last updated at (`last_updated_at`)
  - Portfolio total (`portfolio_total_usdc`) — denominator for the fraction
  - Sort by `current_value_usdc` descending by default
- **Signal history list** — from `signal_history[]` on the same response. Every time this market fired across any (mode, category, top_n) lens. Each entry: `mode`, `category`, `top_n`, `direction`, `first_fired_at`, `last_seen_at`, `peak_trader_count`, `peak_aggregate_usdc`, `peak_net_skew`, `first_trader_count`, `first_aggregate_usdc`, `first_net_skew`, `signal_entry_offer`, `liquidity_tier`, `resolution_outcome` (if resolved). This is the historical trail — when did this market first start firing, across which lenses, what was the peak conviction, did it resolve, etc. Great for "should I follow this even though my current lens isn't firing on it?"
- If a fired signal is active in the user's current lens: show **quality indicators** in a callout — `gap_to_smart_money` (computed: `signal_entry_offer / first_top_trader_entry_price - 1`), `liquidity_tier`, `liquidity_at_signal_usdc`, `lens_count`, `counterparty_count`, `has_exited`, `exit_event` (if exited), freshness state.

**Buy panel (sticky bottom or right column):**

Two large buttons side-by-side:
- **Buy YES @ $0.67**
- **Buy NO @ $0.34**

Click either → buy form expands inline:
- Direction confirmed (YES or NO)
- Size input (default $100, presets $50 / $100 / $500 / $1,000 / $5,000)
- **Effective entry display:** "You're buying $500 YES at **$0.69 effective** (ask $0.67 + slippage $0.02 + fee $X). Total cost: $510."
- **Wallet check:** if size > available balance, button disabled with "Insufficient wallet balance. Available: $X."
- **Depth check:** if size > 5% of book depth, **Confirm Buy is disabled** with message "Size too large — exceeds 5% of book depth at this price. Reduce size or check liquidity."
- **Counterparty warning** (if applicable): "⚠ Smart money is also selling at this price."
- **Mandatory thesis textarea:** "Why this trade in 1 sentence?" — confirm disabled until ≥10 chars typed
- "Confirm Buy" button → POST `/paper_trades`, deducts cost from wallet, success toast

**Existing-position panel** (if user already has an open trade on this market):
- Shows the open trade's size, direction, effective entry, current unrealized P&L
- "Sell" button → standard sell flow

#### Click-through from Dashboard signals

When the user clicks a signal card on `/dashboard`:
- Navigate to `/testing/market/:condition_id` (per-market trading view)
- Pre-highlight the signal's direction (e.g. "Buy YES" gets a subtle outline if signal is YES)
- Quality indicators displayed prominently in the Smart Money panel

User then sizes, writes thesis, confirms.

Alternative: open as a slide-out drawer from the right of the Dashboard. Either works; full navigation preferred for discoverability and bookmarkability.

---

#### Sub-tab: **Backtest** (historical signal analysis)

> **Honest measurement, not motivation.** Numbers should feel authoritative — wide CIs over flashy point estimates. Hero P&L is buried in a stats sub-section; the primary display is the confidence interval. No green-number dashboards here.

**Sparse for the first ~6 months** (need ≥30 resolved signals before headline test is meaningful). Empty state should clearly explain: "Backtest needs ≥30 resolved signals. Currently: N resolved. Estimated first useful date: ~YYYY-MM-DD."

##### Headline panel (stats sub-section — not the hero element)

The backtest stats live **below the fold or in a secondary "Stats" tab**, not as the hero element at the top. The primary purpose of the Backtest page is filter configuration + CI exploration, not surfacing a P&L number that induces recency bias.

Stats displayed (full `BacktestResult` shape):
- `n_signals` (raw row count after filters), `n_resolved` (subset that resolved), `n_eff` (Kish effective sample size after clustering by parent event — always ≤ `n_resolved`)
- `win_rate` + Wilson 95% CI (`win_rate_ci_lo`, `win_rate_ci_hi`)
- **Mean P&L per dollar** (`mean_pnl_per_dollar`) — three columns side-by-side from the response:
  - **Raw 95% CI** (`pnl_ci_lo`, `pnl_ci_hi`) — from cluster bootstrap
  - **Bonferroni-corrected CI** (`corrections.bonferroni_pnl_ci_lo/hi`) — widened for N session queries
  - **BH-FDR-corrected CI** (`corrections.bh_fdr_pnl_ci_lo/hi`) — less conservative, also widened
  - Both corrections apply to win-rate too: `corrections.bonferroni_win_rate_ci_*`, `corrections.bh_fdr_win_rate_ci_*`
- `pnl_bootstrap_p` — empirical 2-sided p-value from the cluster bootstrap. UI may surface as "p = 0.04" alongside the CIs (don't lead with this — CIs are the primary frame).
- `profit_factor` (`null` when there are no losing trades — render "n/a", do NOT render "∞")
- `max_drawdown` — stylized 1%-sizing equity-curve drawdown, chronological. Tooltip: "Simulated drawdown at 1% per-trade sizing — illustrative, not real."
- `median_entry_price` (median of `signal_entry_offer` across rows), `median_gap_to_smart_money` (median of `(signal_entry_offer / first_top_trader_entry_price) - 1`)
- `by_direction{}` — same stats split YES vs NO. Lets the user check whether one side carries the headline.
- `by_resolution{}` — same stats split by `resolution_outcome` (YES, NO, 50_50, VOID, PENDING). Verifies the strategy's edge isn't from a single coincidence on one resolution.
- **Underpowered banner** if `underpowered=true` (i.e. `n_eff < 30`): "Sample too small — interpret with caution"
- `corrections.n_session_queries` shown inline: "Based on N queries this session"
- `holdout_from` field — echoed when a holdout cutoff is in effect ("Training data: signals before [date] only")
- `latency_profile` — echoed when a latency profile is selected
- `latency_stats{}` — when a latency profile is set, the response carries `{adjusted, fallback, n_adjusted, n_fallback, latency_unavailable}`. Show "Profile not honored — coverage insufficient" when `latency_unavailable=true` (more than 20% of rows fell back to fire-time pricing because no snapshot at the chosen offset existed).

##### B1 — Exit strategy toggle

Segmented control: **Hold to resolution** (default) | **Smart-money exit**

- **Hold**: settle every signal at market resolution (YES=+$1, NO=$0, per-share). Optimistic baseline — assumes you can hold.
- **Smart-money exit**: settles at the exit bid captured when smart money dropped ≥30% from peak, falling back to resolution outcome if no exit was recorded. Honest strategy — mirrors what you'd actually face.

Side-by-side compare view: both strategies shown in columns when "Compare" mode is on. The two strategies look **identical until real exit events accumulate** (takes weeks of live operation). Surface this fact in an info tooltip: "Comparison becomes meaningful after ~30 exit events."

##### Boring benchmarks (side-by-side)

Five benchmarks available, computed from the same signal universe (same filters as strategy). Pass `?benchmark=<name>` and the response gets a `benchmark{}` block with the full `BacktestResult` shape under it:

- **Buy-and-hold YES** (`buy_and_hold_yes`) — always buy YES regardless of signal direction. Tests whether direction matters at all vs. just top-trader attention.
- **Buy-and-hold NO** (`buy_and_hold_no`) — mirror of the above. Useful when YES has been crushed across the universe.
- **Buy-and-hold favorite** (`buy_and_hold_favorite`) — always buy whichever side is priced ≥ $0.50 at fire time. The "go with the crowd" baseline. If the strategy can't beat this, the smart-money signal isn't adding info beyond the prior.
- **Coin flip** (`coin_flip`) — random direction (seeded per market for reproducibility). Expected P&L ≈ −fees−slippage. Strategy must beat this to claim any edge.
- **Follow top-1** (`follow_top_1`) — raw consensus signal direction, no additional filters. When no extra filters are applied this equals the strategy; diverges when the user has applied filters (shows unfiltered baseline).

Each benchmark shows the same `BacktestResult` numbers as the strategy. Strategy column gets ✓ if it beats benchmark by ≥2× the bootstrap CI; ✗ otherwise.

API: `GET /backtest/summary?benchmark=buy_and_hold_yes|buy_and_hold_no|buy_and_hold_favorite|coin_flip|follow_top_1`

##### Latency profile (NEW — honest fill realism)

The backtest can model the user's real reaction speed. Pass `?latency_profile=<name>` and the engine looks up the price snapshot that was captured at +N minutes after the signal fired, instead of using the fire-time price. Available profiles:

| Profile | Latency window | Use case |
|---|---|---|
| `active` | 1–3 min | Day-trader who watches the dashboard |
| `responsive` | 5–10 min | Glances at the dashboard a few times a day |
| `casual` | 12–20 min | Checks once or twice an hour |
| `delayed` | 30–60 min | Sees signals via email / browser notification |
| `custom` | user-supplied via `?latency_min_min=&latency_max_min=` | — |

If a snapshot at the chosen offset doesn't exist for a given signal, that signal **falls back** to fire-time pricing. The response's `latency_stats.fallback` count surfaces this; if `latency_unavailable=true` (>20% fell back) the UI should show a coverage warning and consider the result unreliable for that profile.

Pass no profile (default) and the engine uses fire-time pricing (best-case, equivalent to a 0-latency trader).

##### Slice explorer

Dropdown to pick a slice `dimension`. Full list (matches backend exactly):
- `mode` — absolute / hybrid / specialist
- `category` — overall / politics / sports / crypto / culture / tech / finance (the lens category)
- `direction` — YES / NO
- `market_category` — the actual category of the market (distinct from lens category — useful when lens=overall)
- `liquidity_tier` — thin / medium / deep / unknown
- `skew_bucket` — buckets of `first_net_skew`: `<60% / 60-69% / 70-79% / 80-89% / 90-100%`
- `trader_count_bucket` — `<5 / 5-9 / 10-14 / 15-19 / 20+`
- `aggregate_bucket` — `<$100k / $100k-$500k / $500k-$1M / $1M+`
- `entry_price_bucket` — `0-20¢ / 20-40¢ / 40-60¢ / 60-80¢ / 80-100¢`
- `gap_bucket` — `<-10% (cheaper than smart money) / near smart money entry / 10-50% gap / >50% gap`
- `lens_count_bucket` — `1 / 2-3 / 4-5 / 6+` (only meaningful when `?dedup=true`)

Per-bucket table:
- Columns: `n_eff` | `win_rate` | `mean_pnl_per_dollar` | raw 95% CI | Bonferroni CI | BH-FDR-corrected CI | `pnl_bootstrap_p`
- Buckets where corrected CI excludes zero get a ★ marker
- Underpowered buckets (`underpowered=true`, `n_eff < 30`) tagged "low sample"

API: `GET /backtest/slice?dimension=<dim>` — response includes top-level `n_session_queries` + `multiplicity_warning`, plus `holdout_from` and `latency_profile` echoes. Response shape: `{dimension, holdout_from, latency_profile, n_session_queries, multiplicity_warning, buckets: {<bucket_label>: BacktestResult, ...}}`.

##### Saved queries

The slice explorer supports naming and saving a filter combo:
- "Save query" button → prompt for a label → stored in `localStorage`
- Saved queries listed in a sidebar; click to reload the full filter state
- Purpose: let the user rerun identical queries across sessions without re-entering filters

No server-side persistence needed — `localStorage` is sufficient for a personal tool.

##### Filter chips

Active filters as removable chips. Full backend filter set (every chip below maps to a query param on `/backtest/summary` and `/backtest/slice` and `/backtest/edge_decay`):

- `mode`, `category`, `direction` (`YES|NO`)
- `market_category` (the actual market category, distinct from lens category)
- `min_skew`, `max_skew` — bounds on `first_net_skew` (0..1)
- `min_trader_count` — int floor on `first_trader_count`
- `min_aggregate_usdc` — float floor
- `min_avg_portfolio_fraction` — float floor 0..1
- `liquidity_tiers` — multi-select ⊆ `("thin","medium","deep","unknown")`
- `max_gap` — gap-to-smart-money ceiling (e.g. `0.10` = include only signals where the user's entry would be at most +10% above smart money's cost basis)
- `include_pre_fix` (default `false`) — when `true`, includes signals fired before the entry-source fix landed (i.e. rows where `signal_entry_source='unavailable'`). Off by default for honest results.
- `include_multi_outcome` (default `false`) — when `true`, includes scalar / neg-risk / conditional markets (anything with `market_type != 'binary'`). Off by default — most users want binary YES/NO only.
- `trade_size_usdc` — backtest-side sizing assumption (default $100). Affects fee + slippage modeling per row.
- `exit_strategy` — `hold` (default, settle at resolution) or `smart_money_exit` (settle at the bid captured when an exit was detected, fallback to resolution).
- `dedup` (default `false`) — when `true`, reads from `vw_signals_unique_market` instead of `signal_log` directly. Each (condition_id, direction) pair contributes one row instead of one row per (mode, category, top_n) lens. Use this for the "real" headline; leave off for slicing across lenses.
- `holdout_from=YYYY-MM-DD` — excludes signals fired on/after that date. Response echoes `holdout_from` so the UI can display "Training data: signals before [date] only."
- `latency_profile=<name>` and `latency_min_min=`, `latency_max_min=` — see Latency profile section above.

All filters can be passed to `/backtest/summary`, `/backtest/slice`, `/backtest/edge_decay`. `/backtest/half_life` only takes `?category=`.

##### Multiplicity tracker (persistent header notice within the Backtest page)

Counts every distinct `/backtest/summary` and `/backtest/slice` call made in the current 4-hour session window.

- **0–5 queries:** no notice
- **6+ queries:** amber banner — "⚠ Multiple testing: BH-FDR-corrected CIs are the numbers to trust, not raw"
- **20+ queries:** red banner — "⚠⚠ Heavy slicing — results are exploratory only. Run a formal holdout test before acting."

Backed by `slice_lookups` table (`GET /backtest/summary` and `/backtest/slice` auto-insert one row per call; route returns `corrections.n_session_queries`).

##### Holdout test workflow

Lightweight holdout: pass `?holdout_from=YYYY-MM-DD` to any backtest endpoint to exclude data from that date onward. The response's `holdout_from` field echoes the cutoff so the UI can display "Training data: signals before [date] only."

**Workflow suggestion** (UI convention, no special API):
1. User decides on a hypothesis + filter combo.
2. User picks a holdout_from date (e.g. 3 months ago).
3. User runs the backtest on the training window (`?holdout_from=<date>`). Reviews CI.
4. User notes the filter combo and hypothesis. Runs once on the full dataset (no holdout_from) to get "out-of-sample" result.
5. UI displays a "Holdout note" text field so the user can record their pre-registered hypothesis for their own records.

No server-side holdout session state needed for V1 — the discipline is on the user.

---

#### Sub-tab: **Diagnostics**

Long-term monitoring. Optional sub-tab — fold into Backtest if too sparse early on. **Requires ≥4 weeks of live data to be meaningful.**

##### B11 — Cohort decay chart (primary panel)

Large line chart — rolling 7-day mean P&L per $ by signal cohort week. X-axis = `first_fired_at` week. **The most important diagnostic.** Trending down = strategy dying.

- **Chart placement:** top of Diagnostics, full-width, taller than other panels (this is the one that matters most for ongoing system health)
- **Decay warning badge:** if the most recent 3 cohorts trend below the preceding cohorts, show an amber banner: "⚠ Edge may be decaying — recent cohorts underperforming earlier ones"
- **Empty state:** chart is visible but greyed with overlay text "Insufficient data — needs ≥4 weeks of live operation" until the backend reports enough cohorts
- Backend: `GET /backtest/edge_decay` — response includes `decay_warning: bool`

##### Other diagnostics

- **Half-life summary** (table from `/backtest/half_life`) — per (category, offset_min) the convergence rate. Each bucket: `category`, `offset_min` ∈ `{120, 60, 30, 15, 5}`, `n` (sample size), `convergence_rate` (fraction of signals where the price had already moved toward smart money by that offset), `underpowered` (true when `n < 30`). Categories where the +5min convergence rate is high flagged "⚠ Likely unreachable manually" — by the time the user reacts, the move has already happened.
- **Signal coverage stats** — how many signals fired per category over time. (Phase 2 — no dedicated endpoint today; UI may aggregate `/signals/active` history client-side.)
- **Resolution rate** — how many fired signals have resolved. (Phase 2.)

---

#### Cost model (unchanged across all paths)

Paper trades use the **exact same** entry-pricing, slippage, and fee model as the backtest engine. If you can't afford the modeled slippage, you couldn't have afforded the real one.

- **Entry price** = current CLOB ask + slippage + entry fee
- **Slippage** = √(size / book_depth) impact, capped 10¢
- **Entry fee** = per-category taker fee
- **Exit (manual)** = current CLOB bid + exit fee
- **Exit (resolution)** = $1.00 / $0.00 / $0.50 by outcome
- **Exit (smart-money exit)** = current CLOB bid + exit fee, status `closed_exit`

#### Backend endpoints (Testing)

See the **API endpoints** master table at the bottom of this spec for the canonical list (built vs not-yet-built). The Testing route consumes:

- `POST /paper_trades`, `GET /paper_trades`, `GET /paper_trades/{id}`, `POST /paper_trades/{id}/close` — built ✓
- `GET /backtest/summary` (with `?benchmark=` and `?holdout_from=`), `GET /backtest/slice`, `GET /backtest/edge_decay`, `GET /backtest/half_life` — built ✓
- `GET /wallet`, `POST /wallet/deposit`, `POST /wallet/reset` — ⚠ not built (Phase 2)
- `GET /markets/search`, `GET /markets/{cid}/trading_view` — ⚠ not built (Phase 2; use `GET /markets/{cid}` until then)

---

### Section 6 — Watchlist tier

A secondary feed for **consensus-building** markets that don't yet meet the official signal floors. Lives on the same `/dashboard` route as a toggle/tab next to the active signals feed.

#### What it shows

Markets where:
- ≥2 distinct top entities are in (vs 5 for full signals)
- Aggregate ≥ $5k (vs $25k for full signals)
- Headcount skew ≥ 65% AND dollar skew ≥ 65% (same as full signals)

These are **NOT** signals. They're early indicators — directional pressure forming before it reaches the official threshold.

#### Visual treatment

- Faded/muted relative to the active signals feed
- No NEW pills, no browser notifications
- **No "Paper trade" button** — watchlist items aren't tradeable signals
- Tag: "Watchlist" badge on each row

If a watchlist item later qualifies as a full signal, it graduates to the active feed (its full history remains accessible via the market drill-down).

#### Backend

`GET /watchlist/active?mode=&category=&top_n=` — same shape as `/signals/active` with the relaxed floors. (Note: prefix is `/watchlist`, not `/signals/watchlist`.)

---

### Section 7 — Smart-money exit alerts

When the system detects that an active signal's `trader_count` or `aggregate_usdc` has dropped from its peak (in either dimension), an exit event fires. There are **two tiers** with different UI consequences:

| event_type | Trigger | Meaning | UI consequence |
|---|---|---|---|
| `trim` | drop in [25%, 50%) on either metric | Smart money is reducing position but hasn't fully exited | Banner only — no auto-close |
| `exit` | drop ≥ 50% on either metric | Smart money has materially exited | Banner + auto-close of any open paper trades on this signal |

The window is **24 hours** from `first_fired_at` — exits older than 24h don't re-evaluate (avoids re-firing on stale signals).

`drop_reason` field on the exit event tells the UI which dimension triggered: `trader_count` (headcount drop), `aggregate` (dollar drop), or `both`.

#### UI behavior

1. **Banner on the signal card** (both tiers) — `event_type=trim` shows amber: "⚠ Smart money trimming (trader count dropped 38% from peak)". `event_type=exit` shows red: "⚠ Smart money exited (aggregate dropped 62% from peak — paper trade auto-closed)".
2. **Prominent toast notification** when a new exit fires while user is on the dashboard. Trim is informational; exit is high-priority.
3. **Browser notification** if enabled — priority high for `exit`, normal for `trim`.
4. **Auto-close any open paper trades** — only on `event_type=exit`. Trade row updates to `status='closed_exit'` with realized P&L. Trim does NOT auto-close.
5. **Exit history panel** — accessible from the signal card or from a "Recent exits" section in the header. Shows all exit events (both tiers) in the last 24h with timestamps and magnitudes.

#### Backend

`GET /signals/exits/recent?hours=<int 1-168>&limit=<int 1-500>` returns recent exit events (both tiers). Default `hours=24, limit=100`. Each event carries `event_type` (`trim|exit`), `drop_reason` (`trader_count|aggregate|both`), `exit_trader_count`, `peak_trader_count`, `exit_aggregate_usdc`, `peak_aggregate_usdc`, `exit_bid_price` (the bid captured at detection — used as the paper-trade settle price), plus the full signal context (`condition_id`, `direction`, `mode`, `category`, `top_n`, `first_fired_at`, `market_question`, `market_slug`).

Same scheduler job that fires exits also auto-closes paper trades — the auto-close only happens on `event_type=exit`, never on `trim`.

---

### Section 8 — Errors / data-quality page

A dedicated page (top-nav route, e.g. `/errors`) that surfaces every place in the pipeline where data fetching, persistence, or scheduled jobs failed or returned suspect data. The dashboard health pill (Section 1) gives a one-pill summary; this page gives the full breakdown, so when the pill goes amber/red the user knows exactly what failed.

#### Why this exists

The system has many independent layers that fetch from Polymarket:
- Position refresh (per-wallet `/positions` calls, hundreds per cycle)
- Portfolio value (`/value` calls, per wallet)
- Daily leaderboard snapshot (28 combos: 7 categories × 2 time-periods × 2 order-bys)
- Market metadata via JIT discovery (gamma `/markets`, `/events`)
- Order book snapshots for fired signals (CLOB `/book`)
- Signal-price snapshots (every 10 min for active signals)
- Trader-stats nightly batch
- Wallet-classifier and sybil-detector weekly jobs

Any of these can fail silently or partially. Without a centralized error page, the user has to read logs to know what's incomplete. With it, every failure is one click away.

#### Page structure

Vertical scrollable timeline grouped by recency. Each entry shows:
- Timestamp (when the failure happened)
- Subsystem (which job / endpoint)
- Severity (Info / Warning / Error)
- Short human description ("Daily snapshot completed with 5 of 28 combos failed", "Position fetch returned empty for wallet 0xabc... (suspected API blip)")
- Affected scope (e.g. "5 categories × time-periods" or "1 wallet" or "12 markets")
- Click row → expand to show raw context (failing endpoint, response excerpt, what was retried)

Top of page: a counter strip showing rolling totals — "Last 24h: 3 errors, 12 warnings, 47 info notes."

Filter chips at the top: by subsystem (snapshot, positions, signals, half-life, …) and by severity.

#### What feeds it

Backend already emits structured log entries and `health_counters`. This page just surfaces them through a dedicated endpoint:

`GET /system/errors?since=<iso8601>&severity=&subsystem=`

Returns recent entries from a unified errors view that joins:
- `health_counters` rolling 24h totals (rate-limit hits, API failures, cycle-duration warnings, zombie-drop reasons)
- `daily_snapshot_runs` row per day (partial-failure rows, see also Pass 5 finding #16)
- `signal_price_snapshots` failure log (when a snapshot was due but couldn't be captured)
- API client error log (when a `/positions`, `/value`, etc. call returned a non-list shape, hit a 4xx, or timed out)

#### Concrete cases the page must surface (initial set)

| Subsystem | Trigger | Severity |
|---|---|---|
| Daily snapshot | run completed with `failed_combos > 0` | Warning (or Error if >50% failed) |
| Position refresh | wallet fetch failed (transport error, shape error, 4xx) | Info per wallet, Warning if >10% of pool failed |
| JIT market discovery | gamma dropped event_ids; markets persisted with `event_id=NULL` | Warning |
| Signal-price snapshot | signal needed a snapshot at +5/15/30/60/120 but capture failed | Info per snapshot |
| Order-book API | crossed/locked book detected and rejected | Info |
| Rate limit | 429 from Polymarket | Info per hit, Warning if >threshold/hour |
| Trader-stats nightly | job ran but data is now >7 days stale (Pass 5 finding #6) | Error |
| Multi-page paginator | `iter_trades` raised ResponseShapeError mid-pagination (Pass 5 finding #18) | Error |
| Cycle duration | refresh cycle exceeded 9 min | Warning |
| Polymarket overload pattern | repeated 200-OK-with-garbage responses | Warning |

Add to this list as new failure modes are discovered. The page is the single canonical home for "did the data update correctly today."

#### Notification badge

The top nav "Errors" link displays a small numeric badge for `count(severity='error')` in the last 24h, plus an amber dot if any warnings in the last 24h. Click visit clears the badge (stores `localStorage.lastReadErrorsAt`).

#### Relationship to the health pill (Section 1)

The pill is the headline. The errors page is the detail.

- Pill stays green if errors page has zero recent errors and no warnings older than 1 cycle.
- Pill goes amber if any warnings in last 24h.
- Pill goes red if any errors in last 24h, OR if a critical subsystem (position refresh / signal detection) failed in the last 2 cycles.

Clicking the pill should deep-link to the errors page filtered to the relevant subsystem.

---

## Phase 2 / Future features (mention to UI builder, do not build yet)

- **Insider watchlist** — CRUD endpoints (`GET/POST/DELETE /insider_wallets`) AND the `has_insider` flag on signals are wired today, so the UI can render an insider chip on signal cards and a basic management screen now. The Phase 2 piece is **dedicated insider feeds with relaxed floors** (a separate /signals/insider endpoint that surfaces directionally-strong markets where ≥1 insider is in, even if the regular floors aren't met). Highest-priority Phase 2 item.
- Email / Slack alerts (currently UI + browser notifications only)
- Real-money trading via signed CLOB API
- Mobile app
- Multi-user / accounts

---

## API endpoints the UI will consume

> All list endpoints accept three query parameters that map 1:1 to the UI controls:
> - `mode` = `absolute` | `hybrid` | `specialist`
> - `category` = `overall` | `politics` | `sports` | `crypto` | `culture` | `tech` | `finance`
> - `top_n` = integer between 20 and 100 (server enforces range; UI should clamp to step-5 increments client-side per the slider control)
>
> Servers will reject other mode/category values with HTTP 400.

**Dashboard endpoints**

| Endpoint | Returns |
|---|---|
| `GET /traders/top?mode=&category=&top_n=` | Ranked list of top traders |
| `GET /traders/:wallet` | Single-trader profile + per-category breakdown + open positions (with portfolio %) + recent trades |
| `GET /signals/active?mode=&category=&top_n=` | Active consensus signals (one row per (mode, category, top_n, condition_id, direction) lens); each carries executable entry, liquidity tier, counterparty count, exit state, insider flag, freshness state. UI deduplication via the `vw_signals_unique_market` shape is applied automatically — fields `lens_count` + `lens_list` are present on the response. |
| `GET /watchlist/active?mode=&category=&top_n=` | Lower-floor pre-signals (≥2 entities, ≥$5k aggregate, both 65% skews) |
| `GET /signals/new?since=<iso8601>&mode=&category=&top_n=` | Count of signals whose `first_fired_at > since` — drives the new-signals badge |
| `GET /signals/exits/recent?hours=<int>&limit=<int>` | Recent smart-money exit events (`trim` + `exit` tiers) on previously-fired signals |
| `GET /signals/{signal_log_id}/contributors` | Per-signal contributor + counterparty entity list (cluster-collapsed) |
| `GET /markets/{condition_id}` | Single market detail — full event metadata, tracked positions per outcome, per-trader holdings with portfolio fraction, complete signal-history list across lenses |
| `GET /insider_wallets` / `POST /insider_wallets` / `DELETE /insider_wallets/{proxy_wallet}` | Manually curated insider watchlist — read, upsert, delete |
| `GET /system/status` | Full health summary — overall health pill, per-component health (position refresh, daily snapshot, stats freshness, classifier, tracked wallets, recent signals), counters (rate-limit hits, API failures, cycle warnings, zombie-drop reasons, stats stale) |

**Testing endpoints — built today**

| Endpoint | Returns |
|---|---|
| `POST /paper_trades` | Open paper trade. Body `{condition_id, direction, size_usdc, signal_log_id?, notes?}`. Server validates wallet balance (when wallet endpoints land — see below), computes effective entry, deducts cost. |
| `GET /paper_trades?status=open\|closed_resolved\|closed_manual\|closed_exit` | List paper trades. No status filter returns all. |
| `GET /paper_trades/{trade_id}` | Single trade detail with full cost breakdown |
| `POST /paper_trades/{trade_id}/close` | Manual close at current bid; returns updated trade row |
| `GET /backtest/summary?...` | Headline P&L + CI for chosen filter set; optional `?benchmark=<name>` adds a benchmark column |
| `GET /backtest/slice?dimension=&...` | Per-bucket breakdown with raw + Bonferroni + BH-FDR-corrected CIs |
| `GET /backtest/edge_decay?...` | Weekly cohort P&L cohorts + decay flag |
| `GET /backtest/half_life?category=` | Per-(category, offset) convergence rate from price snapshots |

**Testing endpoints — referenced by this spec but NOT yet built (UI builder must mock or backend must add)**

| Endpoint | Status | Note |
|---|---|---|
| `GET /wallet` | ⚠ NOT BUILT | UI may stub from `localStorage` until backend lands. Should expose `{ balance, available, deployed, total_realized_pnl }`. |
| `POST /wallet/deposit` | ⚠ NOT BUILT | Body `{ amount_usdc }`. |
| `POST /wallet/reset` | ⚠ NOT BUILT | Closes all open trades at current bid + resets balance. |
| `GET /markets/search?q=&category=&has_signal=&user_holds=&sort=` | ⚠ NOT BUILT | The Trade browser sub-tab depends on this. Suggested return: list of `{condition_id, question, category, current_yes_price, current_no_price, total_volume_usdc, has_active_signal, user_has_position}`. |
| `GET /markets/{condition_id}/trading_view` | ⚠ NOT BUILT (use `GET /markets/{condition_id}` for now) | The existing `/markets/{condition_id}` endpoint already returns market metadata + tracked-positions-per-trader + signal_history. The "trading_view" variant additionally needs a mini-orderbook and recent CLOB fills, which currently live only inside `signal_book_snapshots` (captured at signal-fire, not on demand). |
| `POST /backtest/holdout/begin`, `/run`, `GET /backtest/holdout/sessions` | ⚠ NOT BUILT — but not needed | The holdout pattern is implemented as a query param on existing endpoints (`?holdout_from=YYYY-MM-DD`). UI handles the workflow client-side; no server-side session state required. The discipline is on the user. |
| `GET /system/errors?since=&severity=&subsystem=` | ⚠ NOT BUILT | The errors page (Section 8) needs this. Today, error data lives in `snapshot_runs.failures` JSONB + `health_counters` rolling counters; an endpoint that joins them is needed for the error timeline. |

Refresh cadence: data updates every 10 minutes. UI polls `/signals/new`, `/signals/exits/recent`, and `/system/status` on the same cadence. **No polling faster than 60 seconds for any endpoint** — avoid slot-machine UX.

---

## Section 9 — Backend reference (single source of truth)

Everything below is verified against the actual codebase. When the prose sections above and this appendix disagree, **this appendix wins** — it's the authoritative reference for the UI builder.

### 9.1 Constants & thresholds (all the magic numbers)

UI tooltips, labels, and validation should mirror these. Numbers that change in the backend will be reflected here on each rebuild.

#### Signal eligibility (`app/services/signal_detector.py`)
| Constant | Value | Meaning |
|---|---|---|
| `MIN_TRADER_COUNT` | `5` | Min distinct cluster-collapsed entities for a signal to fire |
| `MIN_AGGREGATE_USDC` | `25_000.0` | Min aggregate USDC across contributing entities |
| `MIN_NET_DIRECTION_SKEW` | `0.65` | Headcount fraction on the dominant side |
| `MIN_NET_DIRECTION_DOLLAR_SKEW` | `0.65` | USDC-weighted fraction on the dominant side |
| `WATCHLIST_MIN_TRADER_COUNT` | `2` | Watchlist (sub-floor) min entities |
| `WATCHLIST_MIN_AGGREGATE_USDC` | `5_000.0` | Watchlist min aggregate |
| `WATCHLIST_MIN_NET_DIRECTION_SKEW` | `0.65` | Watchlist headcount skew |
| `WATCHLIST_MIN_NET_DIRECTION_DOLLAR_SKEW` | `0.65` | Watchlist dollar skew |
| Position TTL | `20 minutes` | Stale positions excluded from signal aggregator + counterparty |

#### Exit thresholds (`app/services/exit_detector.py`)
| Constant | Value | Meaning |
|---|---|---|
| `TRIM_THRESHOLD` | `0.25` | Drop ≥25% on `trader_count` OR `aggregate_usdc` → `event_type='trim'` (banner only) |
| `EXIT_THRESHOLD` | `0.50` | Drop ≥50% on either → `event_type='exit'` (banner + auto-close paper trades) |
| `EXIT_WINDOW_HOURS` | `24` | Don't re-evaluate signals older than 24h |

#### Trader ranking (`app/services/trader_ranker.py`)
| Constant | Value | Meaning |
|---|---|---|
| `HYBRID_MIN_VOLUME` | `5_000` | Hybrid mode volume floor (USDC) |
| `SPECIALIST_MIN_VOLUME` | `20_000` | Specialist mode category-volume floor |
| `RECENCY_MAX_DAYS` | `60` | Max days since `last_trade_at` (Hybrid+Specialist; bypassed when stats are bootstrapping) |
| `SPECIALIST_MIN_RESOLVED_TRADES` | `30` | Specialist mode min resolved trades |
| `BAYESIAN_K_USDC` | `50_000` | Shrinkage strength toward the per-category prior ROI |
| Excluded `wallet_class` values | `('market_maker', 'arbitrage', 'likely_sybil')` | Removed from all top-N pools |

#### Counterparty (`app/services/counterparty.py`)
| Constant | Value | Meaning |
|---|---|---|
| `MIN_OPPOSITE_USDC` | `5_000.0` | Floor for opposite-side USDC to count as a counterparty entity |
| `CONCENTRATION_THRESHOLD` | `0.75` | `opposite_usdc / (same + opposite)` must clear this for the entity to count |
| Position TTL | `20 minutes` | Same TTL as signal_detector (F29) |

#### Sybil detection (`app/services/sybil_detector.py`)
| Constant | Value | Meaning |
|---|---|---|
| `CO_ENTRY_BUCKET_SECONDS` | `60` | Time bucket size for co-entry detection |
| `CO_ENTRY_OFFSET_SECONDS` | `30` | Offset grid for sliding-window detection (catches t=59 / t=61 boundary) |
| `SYBIL_CO_ENTRY_THRESHOLD` | `0.30` | Pair co-entry rate that flags a 2-wallet edge |
| `MIN_TRADES_FOR_CLUSTERING` | `20` | Wallets with fewer trades skipped |
| `SYBIL_GROUP_MIN_SIZE` | `3` | Min wallet count for group co-entry detection |
| `SYBIL_GROUP_MIN_BUCKETS` | `5` | Min shared buckets to flag a group |
| `SYBIL_GROUP_MAX_BUCKET_SIZE` | `6` | Skip combinatorial expansion above this |

#### Wallet classifier (`app/services/wallet_classifier.py`)
| Constant | Value | Meaning |
|---|---|---|
| `MM_TWO_SIDED_RATIO_THRESHOLD` | `0.40` | `≥40%` of markets touched on BOTH sides → market_maker |
| `ARB_CROSS_LEG_RATIO_THRESHOLD` | `0.30` | `≥30%` cross-leg trades → arbitrage |
| `MIN_TRADES_TO_CLASSIFY` | `5` | Wallets with fewer trades stay `unknown` |
| `DIRECTIONAL_HIGH_CONFIDENCE_TRADES` | `50` | Above this, directional confidence = high |
| `CLASSIFIER_VERSION` | `"v1.1"` | Stamped on every row; lets the UI flag stale rows |

#### Liquidity tiers (`app/services/orderbook.py`)
| Constant | Value | Meaning |
|---|---|---|
| `LIQUIDITY_WINDOW` | `0.05` | ±5¢ from mid for depth measurement |
| `THIN_THRESHOLD` | `5_000` | depth < $5k = `thin` |
| `DEEP_THRESHOLD` | `25_000` | depth ≥ $25k = `deep` (between = `medium`) |

#### Per-category fees (`app/services/fees.py` — taker rates)
| Category | Rate |
|---|---|
| Crypto | `0.07` |
| Sports | `0.03` |
| Finance | `0.04` |
| Politics | `0.04` |
| Tech | `0.04` |
| Mentions | `0.04` |
| Economics | `0.05` |
| Culture | `0.05` |
| Weather | `0.05` |
| Other | `0.05` |
| Geopolitics | `0.00` |
| `DEFAULT_FEE_RATE` (unmapped) | `0.05` |

Formula: `fee_usdc = notional × rate × (1 − price)`. The `(1-price)` factor reflects that fees apply only to the "winning side payoff" portion. UI fee tooltips: "Polymarket charges N% on the upside payout; expected fee is shown after price is selected."

#### Slippage model
- Formula: `slippage_per_share = SLIPPAGE_K × √(notional / book_depth)`
- `SLIPPAGE_K = 0.02`
- Capped at `0.10` per share
- Backtest engine uses the same formula → live UI buy-form should match for honest comparability

#### Backtest constants (`app/services/backtest_engine.py`)
| Constant | Value | Meaning |
|---|---|---|
| `MIN_SAMPLE_SIZE` (underpowered threshold) | `30` | `n_eff < 30` → `underpowered=true` |
| `LATENCY_FALLBACK_WARN_FRACTION` | `0.20` | When >20% of rows fall back to fire-time pricing → `latency_unavailable=true` |
| `n_session_queries` warning | `> 5` | Flips `multiplicity_warning=true` (session window = 4h) |

| Latency profile | Window |
|---|---|
| `active` | 1–3 min |
| `responsive` | 5–10 min |
| `casual` | 12–20 min |
| `delayed` | 30–60 min |
| `custom` | user-supplied via `?latency_min_min=&latency_max_min=` |

#### Half-life (`app/services/half_life.py`)
| Constant | Value | Meaning |
|---|---|---|
| `SNAPSHOT_OFFSETS_MIN` | `(120, 60, 30, 15, 5)` | Snapshot offsets after fire (descending) |
| `OFFSET_TOLERANCE_MIN` | `5` | Acceptable delta in offset matching |
| `MIN_HALF_LIFE_SAMPLE` | `30` | `n < 30` per (category, offset) → `underpowered=true` |

#### Scheduler cadences (`app/scheduler/runner.py`)
| Job | Cadence | Notes |
|---|---|---|
| `refresh_and_log` | every 10 min (interval, 60s misfire grace) | Position refresh → signal log → exit detect → paper-trade auto-close. **The main cycle.** |
| `signal_price_snapshots` | every 10 min (300s misfire grace) | Captures bid+ask at +5/15/30/60/120 min for active signals |
| `daily_snapshot` | cron 02:00 UTC (24h misfire grace) | 28 leaderboard combos |
| `daily_trader_stats` | cron 02:30 UTC (24h misfire grace) | Per-category resolved trade counts + last_trade_at |
| `weekly_classify` | cron Mon 03:00 UTC (24h misfire grace) | Re-classifies wallets |
| `weekly_sybil` | cron Mon 03:15 UTC (24h misfire grace) | Re-detects sybil clusters |
| Startup hook | on boot | Catch-up of `daily_leaderboard_snapshot` if last >24h |

### 9.2 Concept dictionary (terms the UI builder must understand)

- **`first_*` vs `peak_*`** — `first_*` columns on `signal_log` (`first_trader_count`, `first_aggregate_usdc`, `first_net_skew`, `first_avg_portfolio_fraction`) are frozen at signal-fire time (canonical for backtest). `peak_*` columns are lifetime maxima while the signal was live (diagnostic). The live-aggregator fields without prefix (`trader_count`, `aggregate_usdc`, `direction_skew`, etc.) on `/signals/active` are the *current* observed values. UI default: show current; offer `peak_*` on hover; `first_*` lives mostly in backtest views.
- **Entity vs wallet** — A 4-wallet sybil cluster is **one entity**. `trader_count` counts entities. `wallets[]` on contributors / counterparty lists carries the underlying wallet membership. UI: show "23 of 50 traders" (entity count) by default; offer "31 wallets across 23 traders" on hover when at least one cluster is involved.
- **`n_signals` vs `n_resolved` vs `n_eff`** — `n_signals` is the raw row count after filters. `n_resolved` is the subset that has actually resolved (i.e. has `pnl_per_dollar` to compute). `n_eff` is the **Kish effective sample size** after clustering rows by `cluster_id` (= parent event id) — always ≤ `n_resolved` and typically much smaller (10 markets in one event ≈ 1 effective trial). All CIs and the `underpowered` flag are based on `n_eff`.
- **`pnl_bootstrap_p`** — empirical 2-sided p-value from the cluster bootstrap. Used as input to BH-FDR correction. Surface as a secondary number; CIs are the primary frame.
- **Bonferroni vs BH-FDR** — both correct for multiple testing. Bonferroni is strict (divide α by N tests); BH-FDR is less conservative (rank-based). UI shows both; the user picks. Default narrative: "trust the corrected CIs once you've explored 6+ slices in the session."
- **`profit_factor`** — gross_wins / gross_losses. **`null` when there are no losing trades** — render "n/a", never "∞".
- **`max_drawdown`** — stylized: simulated equity curve at 1% per-trade sizing, chronological. NOT a real drawdown — illustrative only. Tooltip the user hard.
- **`gap_to_smart_money`** — signed fraction `(signal_entry_offer / first_top_trader_entry_price) - 1`. **Negative = price moved AWAY from smart money** (cheaper than they entered = good entry for the user). **Positive = price moved TOWARD smart money's profit zone** (less edge left). Buckets: `<-10%` cheaper / `near smart money entry` / `10-50% gap` / `>50% gap`.
- **`signal_entry_source`** — `clob_l2` = derived from real CLOB order book at fire (trustworthy); `gamma_fallback` = price-only fallback when CLOB book unavailable; `unavailable` = no entry recorded (signal predates the F-fix or capture failed). Backtest excludes `unavailable` by default.
- **`event_type` (exits)** vs **`exit_reason` (paper trades)** — different vocabularies, don't conflate. `exit_event.event_type` ∈ `trim|exit`. `paper_trade.exit_reason` ∈ `resolved|manual_close|smart_money_exit`. The auto-close from a smart-money exit writes `exit_reason='smart_money_exit'`.
- **`drop_reason`** (on exits) — `trader_count` (headcount drop), `aggregate` (dollar drop), `both`. Different from the fire-time skew columns (`first_net_skew` / `first_net_dollar_skew`).
- **`cluster_id` on signal_log** — this is actually the **event_id** used as a clustering key for backtest n_eff. Distinct from `cluster_id` on `cluster_membership` / `wallet_clusters` (the sybil cluster UUID). UI: do not conflate; on signal_log this is just an event grouping, on the contributors endpoint it's the sybil-cluster identifier.
- **`stats_freshness.seeded` vs `fresh`** — two distinct conditions. `seeded=False` means the trader-stats table is empty / bootstrapping (recency filter is no-op so signals can fire while stats backfill). `seeded=True, fresh=False` means stats exist but the nightly refresh hasn't run recently — ranker bypasses the recency check. UI: show "Stats bootstrapping…" for `not seeded`; show "Stats stale (last refresh > 7 days)" for `seeded but not fresh`.
- **Zombie drop reasons** (`/system/status.counters.zombie_drops_last_24h`) — five buckets: `redeemable` (resolved, awaiting user redeem), `market_closed`, `dust_size` (size <$10), `resolved_price_past` (price already 0 or 1), `incomplete_metadata`. Tooltip each. A sudden zero across all five often indicates Polymarket renamed an API field — flag as a warning.
- **`fired_last_72h` and `fired_last_48h`** — same value, two field names. The `48h` alias is legacy back-compat; UI should migrate to `72h` and ignore `48h`.

### 9.3 Computed-but-not-currently-exposed (Phase 2 candidates)

These are useful pieces of data the backend already computes but no endpoint returns. Listed here so the UI builder knows what's *possible* even if not built today.

- **Cluster member list** — `wallet_clusters` + `cluster_membership` is queryable but no endpoint returns "give me all members of cluster X." UI's "View other cluster members" link in the trader drill-down would need this.
- **Per-job last-run results** — every scheduler job builds a rich result dataclass (`PositionRefreshResult`, `LogSignalsResult`, `ExitDetectionResult`, etc.) with diagnostics. None are persisted or surfaced. An `/system/jobs/last_runs` endpoint would back the errors page.
- **`signal_book_snapshots`** — full top-20 bids/asks captured at signal-fire, keyed by `signal_log_id`. Today no endpoint returns this; the only consumer is internal paper-trade simulation. Would back a per-signal "depth at fire" chart.
- **`signal_price_snapshots` per-signal trajectory** — captured every 10 min at +5/15/30/60/120 offsets. Used internally by half-life math. Not exposed. Would back a per-signal price drift mini-chart.
- **Classifier `features` on `/traders/{wallet}`** — the `wallet_classifications.features` JSONB is populated but `/traders/{wallet}` only returns the label + confidence + classified_at. Adding `features` to the response would make the trader-drill-down's classifier-explainability panel real.
- **`raw_response_hash`** on book snapshots — capture-only for audit; never surfaced. Useful for forensic "did Polymarket return the same shape twice?" comparisons.
- **`market_sync` status** — completely silent to the UI. There's no "when was the markets table last synced, how many events were missing tags, what's the cutoff?" surface.
- **`paper_trades` aggregates** — no `/paper_trades/summary` endpoint (open count, total realized P&L, total deployed). UI must aggregate client-side from `GET /paper_trades` (which returns all rows + count).

### 9.4 Endpoints (full surface — built today)

| Method | Path | Built? | Returns |
|---|---|---|---|
| GET | `/` | ✓ | `{app, version, docs}` |
| GET | `/traders/top?mode=&category=&top_n=` | ✓ | `{mode, category, top_n, traders: [{rank, proxy_wallet, user_name, verified_badge, pnl, vol, roi, pnl_rank, roi_rank}]}` |
| GET | `/traders/{wallet}` | ✓ | `{profile, classification, cluster, per_category[], open_positions[]}` (recent_trades is Phase 2) |
| GET | `/signals/active?mode=&category=&top_n=` | ✓ | `{mode, category, top_n, count, signals[]}` — see 9.5 for full signal shape |
| GET | `/signals/exits/recent?hours=&limit=` | ✓ | `{window_hours, count, exits[]}` |
| GET | `/signals/{signal_log_id}/contributors` | ✓ | `{signal_log_id, condition_id, direction, contributors[], counterparty[], summary}` |
| GET | `/signals/new?since=&mode=&category=&top_n=` | ✓ | `{mode, category, top_n, since, count}` (count only — not the list) |
| GET | `/markets/{condition_id}` | ✓ | `{market, tracked_positions_by_outcome[], tracked_positions_per_trader[], signal_history[]}` |
| GET | `/watchlist/active?mode=&category=&top_n=` | ✓ | `{mode, category, top_n, count, watchlist[]}` |
| GET | `/insider_wallets` | ✓ | `{count, wallets: [{proxy_wallet, label, notes, added_at, last_seen_at}]}` |
| POST | `/insider_wallets` | ✓ | Body `{proxy_wallet, label?, notes?}` → returns the upserted record |
| DELETE | `/insider_wallets/{proxy_wallet}` | ✓ | `{deleted: bool, proxy_wallet}` |
| GET | `/system/status` | ✓ | See 9.5 for full shape |
| POST | `/paper_trades` | ✓ | Body `{condition_id, direction, size_usdc, signal_log_id?, notes?}` → full paper_trades row + `effective_entry_price` |
| GET | `/paper_trades?status=` | ✓ | `{trades[], count}` |
| GET | `/paper_trades/{trade_id}` | ✓ | Full paper_trades row |
| POST | `/paper_trades/{trade_id}/close` | ✓ | Updated paper_trades row |
| GET | `/backtest/summary?<filters>&benchmark=&holdout_from=&latency_profile=` | ✓ | `BacktestResult` + `corrections{}` + optional `benchmark{}` + optional `latency_stats{}` |
| GET | `/backtest/slice?dimension=<name>&<filters>` | ✓ | `{dimension, holdout_from, latency_profile, n_session_queries, multiplicity_warning, buckets: {<label>: BacktestResult}}` |
| GET | `/backtest/edge_decay?<filters>&min_n_per_cohort=` | ✓ | `{min_n_per_cohort, decay_warning, insufficient_history, weeks_of_data, min_weeks_needed, cohorts[]}` |
| GET | `/backtest/half_life?category=` | ✓ | `{category_filter, offsets_min, buckets[]}` |

### 9.5 Full response shapes for the dense endpoints

#### `GET /signals/active` — each `signals[]` item

```json
{
  "condition_id": "0x...",
  "market_question": "Will ...?",
  "market_slug": "will-...",
  "market_category": "Politics",
  "event_id": "12345",
  "direction": "YES",
  "direction_skew": 0.82,
  "direction_dollar_skew": 0.78,
  "trader_count": 23,
  "aggregate_usdc": 4_200_000,
  "avg_portfolio_fraction": 0.081,
  "current_price": 0.67,
  "first_top_trader_first_seen_at": "2026-05-01T14:32:00Z",
  "avg_entry_price": 0.42,
  "contributing_wallets": ["0xab...", "..."],
  "liquidity_tier": "deep",
  "liquidity_at_signal_usdc": 87_500,
  "signal_entry_offer": 0.69,
  "signal_entry_source": "clob_l2",
  "counterparty_count": 1,
  "counterparty_warning": true,
  "has_exited": false,
  "exit_event": null,
  "has_insider": false,
  "lens_count": 3,
  "lens_list": ["absolute_overall", "absolute_politics", "hybrid_overall"]
}
```

When `has_exited=true`, `exit_event` carries:
```json
{
  "exited_at": "2026-05-08T16:00:00Z",
  "drop_reason": "trader_count",
  "exit_bid_price": 0.61,
  "exit_trader_count": 12,
  "peak_trader_count": 23,
  "exit_aggregate_usdc": 1_900_000,
  "peak_aggregate_usdc": 4_200_000,
  "event_type": "exit"
}
```

#### `GET /system/status`

```json
{
  "overall_health": "green",
  "components": {
    "position_refresh": {"health": "green", "last_at": "...", "minutes_since": 4},
    "daily_snapshot": {
      "health": "green", "last_date": "2026-05-08", "days_since": 0,
      "latest_run": {"snapshot_date": "2026-05-08", "complete": true, "total_combos": 28, "succeeded_combos": 28, "failed_combos": 0, "duration_seconds": 142.3, "completed_at": "..."},
      "last_complete_date": "2026-05-08"
    },
    "stats_freshness": {"seeded": true, "fresh": true, "last_refresh": "..."},
    "wallet_classifier": {"health": "green", "last_at": "...", "days_since": 1},
    "tracked_wallets": {"health": "green", "count": 530},
    "recent_signals": {"health": "green", "fired_last_72h": 14, "fired_last_48h": 14}
  },
  "counters": {
    "rate_limit_hits_last_hour": 0,
    "cycle_duration_warnings_last_24h": 0,
    "api_failures_last_hour": 0,
    "stats_stale_last_hour": 0,
    "zombie_drops_last_24h": {"redeemable": 320, "market_closed": 12, "dust_size": 4, "resolved_price_past": 0, "incomplete_metadata": 0, "total": 336}
  },
  "last_position_refresh_at": "...",
  "minutes_since_refresh": 4,
  "health": "green",
  "last_snapshot_date": "2026-05-08"
}
```

#### `GET /markets/{condition_id}` — full shape

```json
{
  "market": {
    "condition_id": "0x...",
    "gamma_id": "...",
    "event_id": "...",
    "slug": "...",
    "question": "...",
    "clob_token_yes": "...",
    "clob_token_no": "...",
    "outcomes": ["Yes", "No"],
    "end_date": "...",
    "closed": false,
    "resolved_outcome": null,
    "last_synced_at": "...",
    "event_title": "...",
    "event_category": "Politics",
    "event_tags": [{"id": "...", "label": "..."}]
  },
  "tracked_positions_by_outcome": [
    {"outcome": "Yes", "trader_count": 10, "wallet_count": 13, "aggregate_usdc": 400_000, "avg_entry_price": 0.40, "current_price": 0.67, "first_observed_at": "..."},
    {"outcome": "No", "trader_count": 4, "wallet_count": 4, "aggregate_usdc": 90_000, "avg_entry_price": 0.55, "current_price": 0.33, "first_observed_at": "..."}
  ],
  "tracked_positions_per_trader": [
    {
      "proxy_wallet": "0x...", "user_name": "Théo", "verified_badge": true,
      "wallet_class": "directional", "cluster_id": 42,
      "outcome": "Yes", "size": 175_000, "avg_entry_price": 0.40,
      "current_price": 0.67, "current_value_usdc": 117_250, "initial_value_usdc": 70_000,
      "cash_pnl_usdc": 47_250, "percent_pnl": 0.675,
      "first_seen_at": "...", "last_updated_at": "...",
      "portfolio_total_usdc": 1_450_000, "portfolio_fraction": 0.081
    }
  ],
  "signal_history": [
    {
      "mode": "absolute", "category": "overall", "top_n": 50,
      "direction": "YES", "first_fired_at": "...", "last_seen_at": "...",
      "peak_trader_count": 31, "peak_aggregate_usdc": 5_100_000, "peak_net_skew": 0.91,
      "first_trader_count": 23, "first_aggregate_usdc": 4_200_000, "first_net_skew": 0.82,
      "signal_entry_offer": 0.69, "liquidity_tier": "deep", "resolution_outcome": null
    }
  ]
}
```

#### `BacktestResult` (returned by `/summary` and embedded under `buckets[]` in `/slice` and `cohorts[]` in `/edge_decay`)

```json
{
  "n_signals": 142, "n_resolved": 87, "n_eff": 34, "underpowered": false,
  "mean_pnl_per_dollar": 0.063,
  "pnl_ci_lo": 0.018, "pnl_ci_hi": 0.108,
  "win_rate": 0.58, "win_rate_ci_lo": 0.47, "win_rate_ci_hi": 0.69,
  "profit_factor": 1.84, "max_drawdown": -0.12,
  "median_entry_price": 0.42, "median_gap_to_smart_money": 0.05,
  "by_direction": {"YES": {...}, "NO": {...}},
  "by_resolution": {"YES": {...}, "NO": {...}, "50_50": {...}, "VOID": {...}, "PENDING": {...}},
  "pnl_bootstrap_p": 0.04
}
```

#### `corrections{}` (top-level on `/backtest/summary`)
```json
{
  "n_session_queries": 7,
  "multiplicity_warning": true,
  "bonferroni_pnl_ci_lo": -0.005, "bonferroni_pnl_ci_hi": 0.131,
  "bonferroni_win_rate_ci_lo": 0.41, "bonferroni_win_rate_ci_hi": 0.74,
  "bh_fdr_pnl_ci_lo": 0.008, "bh_fdr_pnl_ci_hi": 0.118,
  "bh_fdr_win_rate_ci_lo": 0.45, "bh_fdr_win_rate_ci_hi": 0.71
}
```

#### `latency_stats{}` (only present when `?latency_profile=...` is set)
```json
{
  "adjusted": 0.84,
  "fallback": 0.16,
  "n_adjusted": 73,
  "n_fallback": 14,
  "latency_unavailable": false
}
```

#### Contributors endpoint shape — see Section 2 above for the full example.

### 9.6 Discrepancies between this spec and what the user sees today

(Items the UI builder should know are deliberate, not bugs.)

- `/signals/active` returns one row per (mode, category, top_n, condition_id, direction) lens. The same market firing under three lenses will appear three times unless the UI filters or aggregates client-side. The `lens_count` + `lens_list` fields are present so the UI can roll up, but the endpoint does not auto-dedup. If the UI wants the deduped version, use `?dedup=true` on backtest endpoints (which read `vw_signals_unique_market`); for live signals, dedup client-side by (`condition_id`, `direction`).
- `top_n` is server-validated as `20 ≤ top_n ≤ 100` (any integer). The CLAUDE.md and slider say "step 5, default 50" — that constraint lives only in the UI control.
- `/traders/top?top_n=N` returns up to N traders but may return fewer if the pool is small (small categories or tight floors). UI must handle "got 38 when I asked for 50" gracefully.
- The dashboard health pill polling at 60s is a minimum — backend computes /system/status on every request, no caching. Burst calls are fine; just don't loop faster than 60s.
- `cluster_id` on `signal_log` is the **event_id**. `cluster_id` on `cluster_membership` / `wallet_clusters` is the **sybil cluster UUID**. Two different things, same column name.

---

## Visual / UX guidelines

- Dark mode preferred (matches Polymarket's aesthetic, easier for long sessions).
- Numbers are the hero — large, clear typography for PnL, percentages, prices.
- Color: green for YES / positive, red for NO / negative, neutral gray for "no clear signal."
- Freshness uses temperature: bright/saturated for fresh signals, faded for stale.
- Avoid clutter — this is a dense data product, but the user should always know "what's the strongest signal right now?"

---

## Decisions still open (to be resolved before UI builder starts)

- Hosting environment (affects API base URL — local laptop initially, Railway later)

---

## Decisions locked in

- Three ranking modes: Absolute PnL, Hybrid (rank-average of PnL+ROI, ≥$5k volume floor), Specialist (per-category ROI ranking, ≥$20k category volume + positive category PnL + active in last month)
- Wallet classification + sybil cluster deduplication automatically applied across all modes
- Seven categories exposed in the UI: Overall, Politics, Sports, Crypto, Culture, Tech, Finance (the only values the leaderboard API supports)
- Top-N configurable 20–100, step 5, default 50
- Three controls (Mode, Category, Top-N) work independently — every combination is a valid view
- Two-metric signal display: trader count + average portfolio fraction (not a combined score)
- Net YES/NO direction with **65% headcount skew AND 65% dollar skew** minimum (both gates must clear)
- Eligibility floors: ≥5 entities (cluster-collapsed), aggregate USDC ≥ $25k
- Freshness + price-drift labels (approximate; accuracy improves after ~1 week of snapshot history)
- Daily leaderboard snapshots from day 1
- Signal log table records every fired signal for organic walk-forward backtest
- Backend-only V1; UI built externally on top of REST API
- **No email alerts in V1** — notifications surfaced via the dashboard's status pill + new-signals badge + optional browser Notification API. The `alerts_sent` table and Resend integration deferred to Phase 2.
- **Trader drill-down promoted to V1** — wallet click opens a modal showing profile + classification + cluster + per-category stats + open positions (`GET /traders/{wallet}`). Recent-trades panel is Phase 2 (not yet on the response).
- **Signal logging cadence:** every 10 min, the `log_signals` job runs `detect_signals` for all (mode × category) combos at top_n=50 and upserts into `signal_log`. `first_fired_at` is preserved across refreshes; peak metrics monotonically max forward.
- **Cross-mode dedup** — UI consumes a `vw_signals_unique_market` view that collapses signal_log to one row per (condition_id, direction). Each card shows `lens_count` and the list of mode-category combos that agreed.
- **Sybil cluster + classifier exclusion** — wallets in detected sybil clusters are flagged `'likely_sybil'` and excluded from all top-N pools. Sybil detection uses sliding 60-second windows + group co-entry detection (3+ wallets in same bucket) on top of the v1 pairwise rule.
- **Quality indicators on signal cards** — gap-to-smart-money (color-coded), liquidity tier, lens count, counterparty warning, freshness state. Backend tags every signal with these states; UI renders them as quality badges.
- **Default sort = smallest gap first** — biggest-aggregate signals are often the most-already-moved. UI does not lead with them.
- **Watchlist tier (V1)** — `GET /watchlist/active` returns lower-floor pre-signals (≥2 entities, ≥$5k aggregate, both 65% skews). UI shows them as a muted secondary feed, no buy affordance.
- **Smart-money exit alerts (V1)** — two-tier within a 24h window from `first_fired_at`. **TRIM** fires at ≥25% drop on `trader_count` or `aggregate_usdc`: banner only. **EXIT** fires at ≥50% drop: banner + auto-close of any open paper trades on that signal at the bid captured at detection. Both exposed via `GET /signals/exits/recent`; signal cards carry an inline `has_exited` + `exit_event` block.
- **Testing route (V1)** — combined `/testing` tab houses the virtual wallet, paper-trade portfolio, market browser, per-market trading view, backtest analysis, and diagnostics. Replaces the originally-planned separate `/paper-trades` and `/backtest` tabs.
- **Virtual wallet (V1)** — every Testing user starts at $10,000 simulated balance. Deposit / reset affordances available. Wallet balance computed deterministically from starting balance + realized P&L + deposits − open position costs. Backend exposes `/wallet` endpoint family.
- **Per-market trading view (V1)** — full page at `/testing/market/:condition_id` with mini orderbook, recent fills, smart-money panel (per-trader portfolio %), buy YES / buy NO buttons. Clicking a Dashboard signal card navigates here.
- **Paper-trade friction (V1)** — buy form requires effective entry display, mandatory ≥10-char thesis, depth check (block size > 5% of book depth), wallet balance check. Same cost model as backtest engine for honest comparison.
- **Backtest sub-view (V1)** — Backtest is a sub-tab of Testing, not a top-level nav. Empty/sparse state expected for first ~6 months until ≥30 resolved signals. Multiple-testing correction (BH-FDR) applied automatically; raw and corrected CIs both displayed.
- **Status pill (V1)** — green/amber/red health indicator, click to expand details. Replaces the dot+text from earlier draft. Polls `/system/status` every 60 seconds.
- **No FOMO patterns** — no fire emojis, no auto-refresh faster than 60 seconds, no big P&L hero numbers on dashboard or nav. Stale signals get faded styling, not alarming colors.
