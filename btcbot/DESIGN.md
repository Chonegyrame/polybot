# BTC Up/Down Bot — Living Design Doc

Status: **design phase**. Infra harness is built and parked; the strategy is
not yet designed. This doc is the spec we edit as we go, so the plan lives in
the repo and not just in chat.

---

## What this is

A paper-first trading bot for Polymarket's recurring "BTC Up or Down" markets
(5m / 15m / 1h). One parameterized engine runs multiple horizons side by side so
we compare realized PnL and keep what works. Read-only against Polymarket today;
real execution (signed orders) is a later, deliberate step.

## Core priority (read this first)

**The edge must come from a better ENTRY MODEL and clean EXITS — not from
explaining why BTC moved.** Hunting macro reasons for direction is guesswork.
We invest effort in: how we price the bet, when we enter, and how we exit.
Regime/context analysis is a *secondary diagnostic*, not the project.

## Design principles

1. **Paper == live.** The paper engine must model real fills: pay the ask when
   buying, sell into the bid when exiting, may not fill when posting, fees
   included. No mid-price fantasy. If paper can't be trusted to mirror live, the
   backtest is fiction.
2. **Settle on truth.** PnL settles on Polymarket's *actual* resolution, not our
   own price comparison.
3. **No overfitting.** With only hundreds of windows, fine slicing invents fake
   edges. Require minimum sample per bucket; validate out-of-sample. Suspected
   cause of "edges that die in 3 days."
4. **Local-only data.** SQLite on disk, never Supabase.

---

## Architecture

```
Engine (loop)
 ├─ Discovery      resolve the live/next window for each horizon
 ├─ Reference      BTC/USD anchor (Coinbase/Binance now; Chainlink later)
 ├─ Fair value     P(up) model — THE ENTRY CORE, to be improved
 ├─ Strategy       entry decision + (later) exits/sizing
 ├─ Paper exec     faithful fills (entry at ask, exit at bid, fees)
 └─ Ledger         local SQLite: trades, bankroll, per-second snapshots
```

### Adopt: "early-bird" window handling (from KaustubhPatange's engine)
Don't scramble to catch the open *after* a window starts. **Register the NEXT
window before it opens**, subscribe to its book, and be ready at second 0. This
reliably captures the open (the strike) and the full book from the start, and
enables resting orders pre-open. Replaces the current "catch open within 8s or
skip" hack.

### Resolution facts (verified 2026-06-01)
- 5m / 15m settle on **Chainlink BTC/USD** ("Up" if end ≥ start price).
- 1h settles on **Binance BTC/USDT** 1h candle (close ≥ open).
- Strike ("price to beat") is NOT published → capture at window open ourselves.
- Slug encodes window start unix, horizon-aligned: `btc-updown-5m-<unixstart>`.

---

## Entry model (the core — current state vs. direction)

**Current (placeholder, not agreed):** P(up) = Φ(ln(spot/line)/(σ·√τ)); buy a
side when fair − ask − fee ≥ edge_threshold. Knobs hardcoded by guess:
σ (vol), edge_threshold (3¢), stake ($100), timing. This is a stand-in, NOT our
strategy.

**Direction to improve (to design):** a better entry signal than naive
mispricing. Candidates to evaluate against collected data — order-book pressure,
the lag between a Binance move and Polymarket repricing (documented 30–90s),
book imbalance, odds-drift. TBD — this is the next real design conversation.

## Exits & positioning

- **v1:** stop-loss — exit (sell into the bid) when the position moves against us
  past a threshold. Requires faithful exit-fill modeling.
- **Later:** scaling in / pyramiding, dynamic sizing, late-entry tactics.
- Principle: not about winning every round; good exits that cap losses matter as
  much as entries.

## Data & research method

- Log per-second snapshots (spot, both books' bid/ask, secs-left) + outcome.
  This is the spine; already implemented.
- Context factors (EMA200, ATR, time-of-day, momentum) are **re-computable from
  exchange kline history after the fact** — never decided up front, never
  locked in. Used as a light diagnostic to see *when* a model works, subject to
  the no-overfitting rule. Secondary to entry-model design.

## UI (later)

A "BTC Bot" section in the existing Polymarket dashboard: per-window charts (BTC
vs line, UP/DOWN bid/ask, our entries/exits) + a performance table. Bridge
needed: dashboard (Supabase) reads the bot's local SQLite.

---

## What's built vs. parked

- **Built & verified:** discovery, reference feed, fair-value math, faithful
  buy-fill sim, ledger (bankroll + snapshots), runner loop, `btcbot.bat`.
- **Parked:** nothing is running; no trades placed (one placeholder paper trade
  was logged and will be wiped on the next clean-slate run).
- **Not started:** the actual entry model, exits, early-bird rewrite, websocket,
  UI, signed execution.

## Roadmap

1. **Research** (current) — study how others detect entry/exit edges (code +
   write-ups). Focus: entry signals and exit mechanics, not regime theories.
2. **Design the entry model** — pick a concrete, testable entry signal.
3. **Build v1** — early-bird windows + chosen entry + stop-loss, paper only.
4. **Collect & validate** — run paper, analyze on real data, guard overfitting.
5. **Iterate / add horizons / UI / websocket** as the data justifies.

## Idea bank — candidate edges (from others; NOT yet adopted)

Recorded as leads to test against our own data, not as endorsed strategies.

### A) Late-entry momentum confirmation (claimed 98% win / 2 months)
- Markets: 5m **BTC and SOL**.
- ~15s before expiry: if the winning side is priced **90–95¢** AND **Binance has
  moved >0.06%** in that direction since the window open → buy that side.
- Plus a **time-of-day filter** (skip certain hours).
- Read: a momentum-confirmation play — 15s out the outcome is nearly settled, so
  you buy the near-certain side for a small premium.
- **Risk:** asymmetric. At a 0.92 entry, breakeven win rate is exactly 92% — you
  win ~8¢, lose ~92¢. "98%" has only ~6 pts of margin and these edges decay, so
  it's pennies-in-front-of-a-steamroller: real if the win rate holds, ruinous if
  it slips a few points. Must monitor win rate and cut when it degrades.
- **Testable on our data** (we log odds + spot per second + outcome). Knobs to
  sweep: the 0.06%, the 90–95¢ band, the 15s, the hour filter.
- Feed nuance: signal = Binance, but 5m **resolves on Chainlink** — they track
  closely over 5 min, fine, but note the basis.
- Needs **SOL** added to the collector (Horizon already has an `asset` field).

### B) Smart-money entry heatmap (the more durable idea)
- Pull the trade history of **large/winning wallets** in the 5m markets and map
  **where/when they enter** on a heatmap (e.g. seconds-to-expiry × odds-price or
  distance-from-line, colored by frequency / win-rate).
- This is literally the existing smart-money-tracker thesis applied to 5m
  markets. If some wallets are genuinely informed, following them is a signal
  that decays slower than any fixed threshold.
- Tooling we already have: `get_market_trades(condition_id)` returns per-market
  fills (wallet, side, price, size, ts) + the project's leaderboard/wallet infra.
- **Requires a NEW data stream we are NOT yet capturing: per-window fills.**
  Only available as it happens (+ via the trade API), so worth starting to
  collect early even before committing to use it.
- Risk: survivorship — a wallet with 20 wins may be lucky, not skilled. Need
  enough sample before trusting any wallet.

## Open decisions

- Which entry signal to build first (after research).
- Whether v1 includes stop-loss or buys-and-holds initially.
- Websocket vs. 1s polling for v1 (polling fine to start).
