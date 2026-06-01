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

### Phase 1 — Watchlist + live tracker (the core) ✅ BUILT 2026-06-01
Lives in `esports/` (local SQLite `esports/esports.db`, NOT Supabase). Launcher
`esports.bat`. All three pieces done and smoke-tested:
- `esports_sharps` watchlist table — **34 wallets** seeded (`esports/watchlist.py`):
  33 discovered+vetted (2026-06-01 sweep) + VPenguin. Each carries vetted
  recent-form stats (pnl/win/roi/median-entry/markets) and a `follow` flag
  (27 follow, 7 watch-only = 6 net-negative + EVplusrebate the maker).
- `esports_sharp_actions` table — wallet, condition_id, asset, outcome, side,
  their_price, size, traded_at, detected_at, **live_bid/live_ask at detection**.
  `tracker_cursor` table = per-wallet high-water ts for crash-safe resume.
- `esports/tracker.py` — crash-resilient async loop (default 8s), polls each
  wallet's recent `/trades?user` via shared `PolymarketClient`, baselines on
  first sight (no history flood), logs new esports actions + fetches live book
  only when a new action fires. Esports detected by title keywords (LoL/CS/
  Valorant/Dota); **known v1 gap: game-handicap/map markets that omit the game
  name aren't matched** (no per-wallet team set in the hot loop).

**How discovery worked (reusable):** `scripts/find_esports_sharps.py` mines the
esports markets directly (leaderboard is useless on month-start + can't filter
by sector). Sweeps LoL+CS markets, pulls top holders via data-api
`/holders?market=` (one light call each — NO 408 storm, unlike the trade tape),
ranks wallets by exposure. Then `scripts/vet_candidates.py` reconstructs true
recent-form esports PnL per wallet. **data-api `/trades?user` hard-caps at
offset 3000** — vetting uses the most-recent ≤2500 trades.

### Phase 2 — Per-wallet PnL tracking ✅ v1 BUILT 2026-06-02
- `esports/analysis.py` `wallet_equity_curve()` reconstructs recent-form
  cumulative esports PnL (≤2500 trades + resolutions, ordered by resolution
  date) with a 5-min in-process cache. Verified: ColinHe = 741 markets,
  $1.596M, 58.6% win — matches the vetting exactly.
- `GET /esports/wallet/{wallet}` returns watchlist meta + curve + that wallet's
  logged actions.
- UI: clicking a sharp (feed name or watchlist row) opens a **modal overlay**
  (user's choice) with an SVG **sparkline** equity curve + recent-form stats +
  logged-actions list. Verified rendering in preview (modal opens, curve draws).
- TODO later: weight live signals by recent form; the curve is recompute-on-
  click (cached 5 min), not a stored/periodically-refreshed series yet.

### Phase 3 — UI "Esports" navbar section ✅ v1 BUILT 2026-06-01
- Navbar entry "Esports" (between News and Testing) → `ui/esports.jsx`
  (`EsportsPage`): Live feed tab (recent entries/exits: time, sharp, side,
  market, their price vs OUR ask, slippage, size — All/Follow-only toggle) +
  Watchlist tab (name, games, follow/watch, vetted PnL/win/ROI/entry, #logged).
- Bridge: `app/api/routes/esports.py` reads the local SQLite **read-only**
  (`mode=ro`, never contends with the tracker's writes): `/esports/summary`,
  `/esports/sharps`, `/esports/actions`. Mounted in `app/api/main.py`.
  Endpoints verified in-process; UI tab render-verified via preview.
- **To see it live:** run `polybot.bat` (serves UI + API at `/ui/`) AND
  `esports.bat` (the tracker that fills the DB). Tab at `/ui/` → Esports.
- Phase 2 still pending: per-wallet PnL graphs / refresh (the watchlist shows
  the static vetted stats for now, not a live-updating equity curve).

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

## First action on resume (updated 2026-06-01 — Phase 1 + 3 + handicap fix done)
Built & verified: Phase 1 tracker, Phase 3 UI section, and tag-based detection.

**Tag-based detection (handicap fix) — DONE.** `esports/markets.py`
`refresh_esports_markets()` sweeps OPEN + recent-closed LoL/CS events and writes
every sub-market's (condition_id, game, market_type) into `esports_markets`
(10,643 markets / 770 events on first sweep — incl. 704 handicaps, 5,646 totals,
1,987 winners). The tracker now detects esports by MEMBERSHIP in that table
(refreshed every 15 min on startup + in-loop), with a title-keyword fallback
for brand-new winner markets. So handicap/total/prop markets — which the old
title check dropped — are now captured + classified. Actions carry game +
market_type; the UI feed has Game (LoL/CS) + Type (Winner/Handicap/Total/Prop)
filters and chips.

Next:
1. **Phase 2 — wallet-detail modal + equity sparkline** (user chose MODAL
   overlay). Click a sharp (feed or watchlist) → modal showing recent-form
   cumulative-PnL sparkline (reconstructed from ≤2500 trades) + their logged
   actions. Value of the curve: shows recency + skill-vs-one-lucky-hit, which
   the scalar stats can't. Needs a `/esports/wallet/{w}/curve` endpoint.
2. **Let the tracker accumulate data** — run `esports.bat`. ONE writer at a time.
3. **Phase 4 — forward-test/backtest panel** over accumulated actions.
Two wallets the user found independently still to confirm/add (VPenguin is in).
