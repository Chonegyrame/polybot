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
- **Market question** (full text)
- **Net direction badge** — large, color-coded YES/NO with skew %
- **Trader count** — "23 of 50"
- **Average portfolio fraction allocated** — "8.2%" (conviction indicator)
- **Current market price** + **Total $ aggregate**

**Quality indicators (NEW — critical for honest decisioning):**
- **Gap to smart money** — color-coded prominently:
  - 🟢 **<+5%** ("Early — gap still open")
  - 🟡 **+5 to +20%** ("Reachable — partial move priced in")
  - 🔴 **>+20%** ("Likely already moved — entering near smart money's profit zone")
- **Liquidity tier badge** — Small / Medium / Large with USDC depth on hover. Lets the user judge whether they can actually size into the trade.
- **Lens count badge** — e.g. "Confirmed by 5 lenses" with tooltip listing which (mode, category) combos agree. Replaces showing the same market 5 times.
- **Counterparty warning** — if any seller in recent CLOB fills is in current top-N: "⚠ Smart money on the other side" badge in red.
- **Freshness label** — "Formed 4h ago" / "Stale (>4h since refresh)" — stale signals get **strikethrough on the direction badge** + reduced opacity + an explicit age warning. The card remains visible but visually de-prioritised.

**Backend:** all of these come from existing fields on `signal_log` + new `counterparty_warning` boolean + `lens_count` from the deduped view. UI does not compute thresholds — backend tags each signal with its quality state.

#### Signal eligibility floors (backend filters before sending to UI)

A market only fires a signal if ALL three are true:
- ≥5 distinct top traders are in the market
- Aggregate USDC at risk across those traders ≥ $25k (configurable)
- Net direction skew ≥ 60% (one side has 60%+ of involved top traders)

The UI doesn't enforce these — the API only returns markets that already pass. UI just renders what arrives.

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
- User name + verified badge if present
- Truncated wallet (with copy)
- Total PnL ($), ROI (%), volume ($), # positions

**Per-category breakdown** — table:
- One row per category (Politics, Sports, …) — PnL, volume, # positions in that category. Lets the user judge whether the trader is a generalist or a specialist.

**Open positions** — table:
- Market question (clickable → opens that market in the signals feed if currently visible)
- Direction (YES / NO)
- **Position size USDC**
- **% of trader's portfolio** — conviction indicator (bigger % = stronger trader belief)
- **Cost basis** (avg_price)
- **Current price**
- **Current value ($)**, **Unrealized PnL ($, %)**
- First seen at (when we first observed the position)

When this modal is opened from a signal card (clicking a trader pill), the row for that specific market should be visually highlighted so the user immediately sees the trader's stake in the signal they're investigating.

**Recent trades** — last 50, table:
- Timestamp, market, side (BUY/SELL of YES/NO), size, price, total $

Backend endpoint: `GET /traders/{wallet}` returns `{ profile, per_category_stats[], open_positions[], recent_trades[] }`.

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

**Smart Money panel (right column):**
- Heading: "Tracked traders in this market: N"
- For each tracked trader present:
  - User name + verified badge
  - Direction (YES / NO)
  - Position size USDC
  - **% of trader's portfolio** (key conviction indicator)
  - Cost basis
  - First seen at
- Sorted by position size descending
- If a fired signal is active: show **quality indicators** — gap to smart money, liquidity tier, lens count, counterparty warning

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

Stats displayed:
- `n_signals`, `n_eff` (after clustering by parent event)
- Win rate + Wilson 95% CI
- **Mean P&L per dollar** — three columns side-by-side:
  - **Raw 95% CI** (from cluster bootstrap)
  - **Bonferroni-corrected CI** (widened for N session queries)
  - **BH-FDR-corrected CI** (less conservative, also widened)
- Profit factor (or "n/a" if no losses)
- Max drawdown
- **Underpowered banner** if n_eff < 30: "Sample too small — interpret with caution"
- `n_session_queries` shown inline: "Based on N queries this session"

##### B1 — Exit strategy toggle

Segmented control: **Hold to resolution** (default) | **Smart-money exit**

- **Hold**: settle every signal at market resolution (YES=+$1, NO=$0, per-share). Optimistic baseline — assumes you can hold.
- **Smart-money exit**: settles at the exit bid captured when smart money dropped ≥30% from peak, falling back to resolution outcome if no exit was recorded. Honest strategy — mirrors what you'd actually face.

Side-by-side compare view: both strategies shown in columns when "Compare" mode is on. The two strategies look **identical until real exit events accumulate** (takes weeks of live operation). Surface this fact in an info tooltip: "Comparison becomes meaningful after ~30 exit events."

##### Boring benchmarks (side-by-side)

Three benchmarks computed from the same signal universe (same filters as strategy):
- **Buy-and-hold YES** — always buy YES regardless of signal direction. Tests whether direction matters at all vs. just top-trader attention.
- **Coin flip** — random direction (seeded per market). Expected P&L ≈ −fees−slippage. Strategy must beat this to claim any edge.
- **Follow top-1** — raw consensus signal direction, no additional filters. When no extra filters are applied this equals the strategy; diverges when the user has applied filters (shows unfiltered baseline).

Each benchmark shows the same 5 numbers as the strategy. Strategy column gets ✓ if it beats benchmark by ≥2× the bootstrap CI; ✗ otherwise.

API: `GET /backtest/summary?benchmark=buy_and_hold_yes|coin_flip|follow_top_1`

##### Slice explorer

Dropdown to pick a slice dimension:
- mode / category / direction / lens_count / gap_bucket / liquidity_tier / skew_bucket / trader_count_bucket / aggregate_bucket / entry_price_bucket / portfolio_fraction_bucket

Per-bucket table:
- Columns: n_eff | win_rate | pnl/$ | raw 95% CI | BH-FDR-corrected CI
- Buckets where corrected CI excludes zero get a ★ marker
- Underpowered buckets (n_eff < 30) tagged "low sample"

API: `GET /backtest/slice?dimension=<dim>` — response includes `n_session_queries` + `multiplicity_warning` at top level.

##### Saved queries

The slice explorer supports naming and saving a filter combo:
- "Save query" button → prompt for a label → stored in `localStorage`
- Saved queries listed in a sidebar; click to reload the full filter state
- Purpose: let the user rerun identical queries across sessions without re-entering filters

No server-side persistence needed — `localStorage` is sufficient for a personal tool.

##### Filter chips

Active filters as removable chips:
- mode = all / specific
- category = all / specific
- date range (training_cutoff, end_date)
- `holdout_from` — reserves data from that date onward as untouched out-of-sample
- include_pre_fix_rows (default off)
- min_avg_portfolio_fraction (slider)
- liquidity_tiers (multi-select)

API: `GET /backtest/summary?holdout_from=YYYY-MM-DD` excludes signals fired on/after that date.

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

- **Half-life summary** (table) — per-category median half-life in minutes. Categories with <30min half-life flagged "⚠ Likely unreachable manually."
- **Signal coverage stats** — how many signals fired per category over time
- **Resolution rate** — how many fired signals have resolved

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

| Method | Path | Purpose |
|---|---|---|
| GET | `/wallet` | `{ balance, available, deployed, total_realized_pnl }` |
| POST | `/wallet/deposit` | Body `{ amount_usdc }` — adds to balance |
| POST | `/wallet/reset` | Closes all open trades at current bid, resets balance to default |
| GET | `/markets/search?q=&category=&has_signal=&user_holds=&sort=` | Browse/search markets |
| GET | `/markets/:condition_id/trading_view` | Enriched per-market view: question, prices, mini orderbook, recent fills, smart-money panel data, active signal quality indicators |
| POST | `/paper_trades` | Open paper trade. Body: `{condition_id, direction, size_usdc, thesis, signal_log_id?, notes?}` — server validates wallet balance, computes effective entry, deducts cost |
| GET | `/paper_trades?status=open\|closed_resolved\|closed_manual\|closed_exit` | List trades |
| GET | `/paper_trades/:id` | Single trade detail with cost breakdown |
| POST | `/paper_trades/:id/close` | Manual close at current bid |
| GET | `/backtest/summary?filters=...&benchmark=&holdout_from=` | Headline + Bonferroni/BH-FDR corrections + optional benchmark; `holdout_from=YYYY-MM-DD` excludes signals on/after that date |
| GET | `/backtest/slice?dimension=&filters=...&holdout_from=` | Per-bucket breakdown; response includes `n_session_queries` + `multiplicity_warning` |
| GET | `/backtest/edge_decay` | Rolling 7-day alpha by cohort; includes `decay_warning: bool` |
| GET | `/backtest/half_life` | Per-category half-life summary |

---

### Section 6 — Watchlist tier

A secondary feed for **consensus-building** markets that don't yet meet the official signal floors. Lives on the same `/dashboard` route as a toggle/tab next to the active signals feed.

#### What it shows

Markets where:
- ≥2 distinct top traders are in (vs 5 for full signals)
- Aggregate ≥ $5k (vs $25k for full signals)
- Net skew ≥ 60% (same as full signals)

These are **NOT** signals. They're early indicators — directional pressure forming before it reaches the official threshold.

#### Visual treatment

- Faded/muted relative to the active signals feed
- No NEW pills, no browser notifications
- **No "Paper trade" button** — watchlist items aren't tradeable signals
- Tag: "Watchlist" badge on each row

If a watchlist item later qualifies as a full signal, it graduates to the active feed (its full history remains accessible via the market drill-down).

#### Backend

`GET /signals/watchlist?mode=&category=&top_n=` — same shape as `/signals/active` with the relaxed floors.

---

### Section 7 — Smart-money exit alerts

When the system detects that an active signal's `trader_count` or `aggregate_usdc` has dropped ≥30% from its peak within 4 hours, an exit event fires.

#### UI behavior

1. **Banner on the signal card** — "⚠ Smart money exiting (trader count dropped 38% from peak)" in red, displayed inline on the signal card.
2. **Prominent toast notification** when a new exit fires while user is on the dashboard.
3. **Browser notification** if enabled — priority high.
4. **Auto-close any open paper trades** on that signal at current bid. Trade row updates to `status='closed_exit'` with realized P&L. The user sees the closed trade next time they visit `/paper-trades`.
5. **Exit history panel** — accessible from the signal card or from a "Recent exits" section in the header. Shows all exit events in the last 24h with timestamps and magnitudes.

#### Backend

`GET /signals/exits?since=<iso8601>` returns recent exit events. Same scheduler job that fires exits also auto-closes paper trades.

---

## Phase 2 / Future features (mention to UI builder, do not build yet)

- **Insider watchlist** — manually maintained list of suspected insider wallets (sports specialists, weather oracles, court ruling leakers). Insider signals would have lower floors and surface in their own feed. Highest-priority Phase 2 item.
- Email / Slack alerts (currently UI + browser notifications only)
- Real-money trading via signed CLOB API
- Mobile app
- Multi-user / accounts

---

## API endpoints the UI will consume

> All list endpoints accept three query parameters that map 1:1 to the UI controls:
> - `mode` = `absolute` | `hybrid`
> - `category` = `overall` | `politics` | `sports` | `crypto` | `culture` | `tech` | `finance`
> - `top_n` = integer between 20 and 100, step 5
>
> Servers will reject other category values with HTTP 400.

**Dashboard endpoints**

| Endpoint | Returns |
|---|---|
| `GET /traders/top?mode=&category=&top_n=` | Ranked list of top traders |
| `GET /traders/:wallet` | Single-trader profile + per-category breakdown + open positions (with portfolio %) + recent trades |
| `GET /signals/active?mode=&category=&top_n=` | Active consensus signals (deduped per (market, direction) with `lens_count` + `lens_list`); each carries gap, liquidity tier, counterparty warning, freshness state |
| `GET /signals/watchlist?mode=&category=&top_n=` | Lower-floor pre-signals (≥2 traders, ≥$5k aggregate) |
| `GET /signals/new?since=<iso8601>&mode=&category=&top_n=` | Signals whose `first_fired_at > since` — drives the new-signals badge |
| `GET /signals/exits?since=<iso8601>` | Smart-money exit events on previously-fired signals |
| `GET /markets/:condition_id` | Single market detail (drill-down view) |
| `GET /system/status` | Full health summary — last refresh, cycle duration, dropped positions, missing markets, classifier last-run, scheduler health (green/amber/red) |

**Testing endpoints**

| Endpoint | Returns |
|---|---|
| `GET /wallet` | `{ balance, available, deployed, total_realized_pnl }` |
| `POST /wallet/deposit` | Body `{ amount_usdc }` — adds to balance |
| `POST /wallet/reset` | Closes all open trades at current bid, resets balance to default |
| `GET /markets/search?q=&category=&has_signal=&user_holds=&sort=` | Browse/search markets |
| `GET /markets/:condition_id/trading_view` | Enriched per-market view: question, prices, mini orderbook, recent fills, smart-money panel data, active signal quality indicators |
| `POST /paper_trades` | Open paper trade. Body `{condition_id, direction, size_usdc, thesis, signal_log_id?, notes?}`. Server validates wallet balance, computes effective entry, deducts cost. |
| `GET /paper_trades?status=open\|closed_resolved\|closed_manual\|closed_exit` | List paper trades |
| `GET /paper_trades/:id` | Single trade detail with cost breakdown |
| `POST /paper_trades/:id/close` | Manual close at current bid |
| `GET /backtest/summary?filters=...` | Canonical headline strategy + boring benchmarks |
| `GET /backtest/slice?dimension=&filters=...` | Per-bucket breakdown with raw + BH-FDR-corrected CIs |
| `POST /backtest/holdout/begin` | Register holdout test session + hypothesis list |
| `POST /backtest/holdout/run` | Execute registered hypotheses on holdout, return final results |
| `GET /backtest/holdout/sessions` | History of past holdout tests |
| `GET /backtest/edge_decay` | Rolling 7-day alpha by signal cohort week |
| `GET /backtest/half_life` | Per-category median half-life |

Refresh cadence: data updates every 10 minutes. UI polls `/signals/new`, `/signals/exits`, and `/system/status` on the same cadence. **No polling faster than 60 seconds for any endpoint** — avoid slot-machine UX.

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
- Net YES/NO direction with 60% skew minimum
- Eligibility floors: ≥5 traders, aggregate USDC threshold
- Freshness + price-drift labels (approximate; accuracy improves after ~1 week of snapshot history)
- Daily leaderboard snapshots from day 1
- Signal log table records every fired signal for organic walk-forward backtest
- Backend-only V1; UI built externally on top of REST API
- **No email alerts in V1** — notifications surfaced via the dashboard's status pill + new-signals badge + optional browser Notification API. The `alerts_sent` table and Resend integration deferred to Phase 2.
- **Trader drill-down promoted to V1** — wallet click opens a modal showing profile + per-category stats + open positions (with portfolio %) + recent trades (`GET /traders/{wallet}`).
- **Signal logging cadence:** every 10 min, the `log_signals` job runs `detect_signals` for all (mode × category) combos at top_n=50 and upserts into `signal_log`. `first_fired_at` is preserved across refreshes; peak metrics monotonically max forward.
- **Cross-mode dedup** — UI consumes a `vw_signals_unique_market` view that collapses signal_log to one row per (condition_id, direction). Each card shows `lens_count` and the list of mode-category combos that agreed.
- **Sybil cluster + classifier exclusion** — wallets in detected sybil clusters are flagged `'likely_sybil'` and excluded from all top-N pools. Sybil detection uses sliding 60-second windows + group co-entry detection (3+ wallets in same bucket) on top of the v1 pairwise rule.
- **Quality indicators on signal cards** — gap-to-smart-money (color-coded), liquidity tier, lens count, counterparty warning, freshness state. Backend tags every signal with these states; UI renders them as quality badges.
- **Default sort = smallest gap first** — biggest-aggregate signals are often the most-already-moved. UI does not lead with them.
- **Watchlist tier (V1)** — `/signals/watchlist` returns lower-floor pre-signals (≥2 traders, ≥$5k aggregate). UI shows them as a muted secondary feed, no buy affordance.
- **Smart-money exit alerts (V1)** — when a fired signal's trader_count or aggregate drops ≥30% from peak within 4h, an exit event fires. UI surfaces a banner; open paper trades on that signal auto-close at current bid.
- **Testing route (V1)** — combined `/testing` tab houses the virtual wallet, paper-trade portfolio, market browser, per-market trading view, backtest analysis, and diagnostics. Replaces the originally-planned separate `/paper-trades` and `/backtest` tabs.
- **Virtual wallet (V1)** — every Testing user starts at $10,000 simulated balance. Deposit / reset affordances available. Wallet balance computed deterministically from starting balance + realized P&L + deposits − open position costs. Backend exposes `/wallet` endpoint family.
- **Per-market trading view (V1)** — full page at `/testing/market/:condition_id` with mini orderbook, recent fills, smart-money panel (per-trader portfolio %), buy YES / buy NO buttons. Clicking a Dashboard signal card navigates here.
- **Paper-trade friction (V1)** — buy form requires effective entry display, mandatory ≥10-char thesis, depth check (block size > 5% of book depth), wallet balance check. Same cost model as backtest engine for honest comparison.
- **Backtest sub-view (V1)** — Backtest is a sub-tab of Testing, not a top-level nav. Empty/sparse state expected for first ~6 months until ≥30 resolved signals. Multiple-testing correction (BH-FDR) applied automatically; raw and corrected CIs both displayed.
- **Status pill (V1)** — green/amber/red health indicator, click to expand details. Replaces the dot+text from earlier draft. Polls `/system/status` every 60 seconds.
- **No FOMO patterns** — no fire emojis, no auto-refresh faster than 60 seconds, no big P&L hero numbers on dashboard or nav. Stale signals get faded styling, not alarming colors.
