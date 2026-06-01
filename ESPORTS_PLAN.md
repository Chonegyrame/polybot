# Esports Sharp-Tracking Section — Build Plan

Status: **planned, not started.** This is the active direction as of 2026-06-01.
Read this + `session-state.md` first when resuming.

## Why this (the short version of a long session)

Tested and **rejected** with data: LoL-on-Polymarket *as a 5-min-BTC-style bot*
(markets fine, but our edge isn't there), naive BTC 5-min bots (mostly
market-makers; directional "sharps" failed an out-of-sample persistence test,
1/6 = 17%, below coin-flip), and "clever structure" ideas (stop-loss / both-sides
— zero EV on efficiently-priced markets).

What we **found**: niche **esports specialists make real, durable money** on
Polymarket. Example wallet **VPenguin** (`0xfbf3d501e88815464642d0e913f15379c3eeb218`):
~$10.2M lifetime net, 62% win rate; **~$1.45M from esports specifically**, 53%
win rate at a **median entry of 0.40** — i.e. value-betting underpriced sides,
sizing up on conviction (median $550, max $393k), actively trimming/exiting.
Long track record (since Sept 2024), human-paced (~5.5min median gap) → skill,
not luck, not an HFT bot.

Esports is also the **user's own domain** (he bets LoL manually and feels an
edge). So the plan is to **track the esports sharps and follow with discretion**
— augment the user's judgment, not replace it.

## Key decisions / facts

- **Forward-test, don't backtest.** The historical follow-backtest was abandoned:
  the data-api 408s (transient timeouts, NOT size-based — confirmed) on bulk
  trade-history queries, and time-filter params are ignored, so reconstructing
  post-entry price paths from history is unreliable. Instead, paper-follow live
  going forward — it captures the *true* price you'd pay (real spread) and IS
  the feature we want anyway.
- **Lag is a coin-flip, not pure cost.** LoL odds swing wildly mid-game, so
  entering 30–60s after a sharp can be better OR worse. Only live forward-testing
  settles whether following pays.
- **Polling is safe.** data-api shrugged off ~60 req/s in testing. 10 wallets @
  5s = ~2 req/s of the LIGHT `/trades?user` query. MUST route through the
  existing `PolymarketClient` shared rate-limiter so it can't stampede the API
  or starve the existing polybot jobs. Loop must be crash-resilient. NEVER put
  heavy `/trades?market` history queries in the hot loop.
- **Storage: local SQLite, NOT Supabase.** ~1–5 MB/month for actions+PnL; ~30
  MB/month even with per-entry price-path capture. Disk has years of headroom.
- **Reuse existing infra** (insider_wallets/insider_actions pattern, the
  consensus-signal engine, the now-fixed backtest engine, the UI shell). Don't
  rebuild; extend. Keep the generalist tracker; just add an esports section.
- **Leaderboard note:** esports has no native leaderboard category — esports
  bettors surface under the **sports** category. User will hand-pick the ~10
  wallets.

## Build phases

### Phase 1 — Watchlist + live tracker (the core)
- `esports_sharps` watchlist table (~10 hand-picked wallets).
- `esports_sharp_actions` table: one row per detected entry/exit — wallet,
  condition_id, outcome/asset, side (BUY/SELL), their price, size, traded_at,
  detected_at, and the **live price at detection** (so we record what we'd pay).
- A crash-resilient async loop (~5s) polling each wallet's recent trades via the
  shared `PolymarketClient`, detecting NEW esports actions, logging them.
  (Detect "esports" by event tag / title patterns incl. game-handicap markets.)

### Phase 2 — Per-wallet PnL tracking
- Reconstruct each wallet's esports equity curve (reuse
  `scripts/wallet_lol_deepdive.py` logic: their trades + market resolutions).
- A periodic refresh so the curves stay current; weight signals by recent form.

### Phase 3 — UI "Esports" navbar section
- New navbar section, visually separate, housing: live entry/exit feed,
  per-wallet PnL graphs, and (Phase 4) the backtest panel.
- Bridge: UI reads the bot's local SQLite (small read endpoint or direct).

### Phase 4 — Esports follow-backtest / forward-test panel
- Parameters: entry lag, market filter (series vs game-winner vs handicap, odds
  band, league, wallet), sizing (fixed/proportional), exit rule (hold to
  resolution / exit at +X / exit when they exit).
- Fed by the live-captured prices accumulating from Phase 1 (the honest,
  spread-inclusive data) rather than flaky historical reconstruction.

## Reusable pieces already built this session
- `scripts/wallet_lol_deepdive.py` — per-wallet esports PnL/win-rate/entry-odds.
- `scripts/follow_backtest.py` — follow-at-lag logic (tape-based; flaky on big
  markets — superseded by live capture, but the PnL math is reusable).
- `scripts/backfill_signal_resolutions.py` + `crud.set_market_resolution` /
  `list_unresolved_signal_condition_ids` — fixed the backtest's blind spot.
- `app/services/polymarket.py`: `get_event_by_slug`, `get_market_trades(offset=)`.

## First action on resume
Get the ~10 wallet addresses from the user, then build Phase 1 (watchlist +
safe tracker) after reading how `insider_wallets`/scheduler/UI are wired.
