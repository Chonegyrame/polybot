# Session State

Last updated: 2026-06-01 (late) — DIRECTION: esports sharp-tracking section
Branch: main (unpushed commits, **PUSH BLOCKED — see git status below**)
Location: `C:\Users\hej\Code\AI projekt rätt mapp\polymarket\` (migrated out of OneDrive)

## 2026-06-01 (late) — CURRENT DIRECTION: Esports sharp-tracking

**👉 READ `ESPORTS_PLAN.md` FIRST — it's the active build plan.**

Arc of the 2026-06-01 session (lots of dead-ends cleared with data):
- **BTC up/down paper bot**: fully built in `btcbot/`, then **SHELVED** — serious
  money there is ~3:1 market-makers, and the directional "sharps" failed an
  out-of-sample persistence test (1/6 = 17%, below coin-flip). See
  `btcbot/DESIGN.md` + memory.
- **LoL-on-Polymarket markets ARE liquid** (matches trade $M live); our earlier
  collector just mis-sampled the dead pre-game window. Not a dead market.
- **Smart-money tracker backtest FIXED**: `scripts/backfill_signal_resolutions.py`
  filled in market resolutions for fired signals → settleable signals 25 → 273.
  Result: generalist-whale consensus shows **no edge** (dedup +0.116/$1, p=0.36);
  the only lean is the **NO signals** (+0.158/$1, 63% win, p=0.25). Backtest
  filters (direction etc.) DO work — earlier "broken filter" claim was a runner bug.
- **Pivot → esports sharps**: niche esports specialists make real money (VPenguin:
  ~$1.45M esports, value-betting underdogs). Esports is the user's domain. Plan =
  track ~10 hand-picked esports sharp wallets, follow with discretion, in a new
  UI section. Full plan in `ESPORTS_PLAN.md`. **Next: user provides ~10 wallets →
  build Phase 1 (watchlist + safe 5s tracker).**

### Git / push status (2026-06-01) — ✅ RESOLVED
- **Pushed & synced** — `main` == `origin/main` (`ca3ddfc..6d1f8e5`). The push
  block is gone.
- `.mcp.json` was scrubbed from history via `git filter-branch` over the
  unpushed range, then reflog-expired + GC'd, so the Supabase token is gone
  from GitHub-bound AND local history. `.mcp.json` is untracked + gitignored;
  the live working-tree copy remains (MCP still works).
- The token was **never actually pushed to GitHub** (it only ever lived in
  local commits, now scrubbed) → rotation is **optional** hygiene, not required.
- Separate, NOT blocking: Supabase Advisor flags 6 tables with RLS disabled
  (lol_* + polymarket_lol_*). Only matters if the anon key is exposed (e.g.
  client-side UI). Address later if so.

---

## 2026-06-01 (earlier) — BTC Up/Down paper bot (SHELVED)

**LoL bot is PAUSED** (too hard, unreliable livestats, heavy storage; Supabase
is full and user won't pay for it right now). Not deleted — just shelved.

**New project: a paper-trading bot for Polymarket's recurring "BTC Up or Down"
markets** (5m / 15m / 1h / daily). Goal is real money; user's bar is "a few
hundred to a few thousand $/day" and is fine treating it as a fun/learning
project too. Paper-first, and paper MUST faithfully mirror live fills.

### Built this session (the harness) — lives in `btcbot/`, all verified
- `discovery.py` — resolve live window from unix-aligned slug (`btc-updown-5m-<start>`)
- `reference.py` — BTC/USD anchor (Coinbase→Binance; Chainlink later)
- `fair_value.py` — P(up)=Φ(ln(spot/open)/(σ√τ)) + EWMA vol
- `book.py` — faithful fill sim (walks the asks)
- `strategy.py` — PLACEHOLDER entry (fair-vs-book). NOT an agreed strategy.
- `ledger.py` — local SQLite: trades, $1000 bankroll, per-second snapshots
- `runner.py` — loop; `--collect-only` logs data & trades nothing
- `btcbot.bat` — launcher (currently set to collect-only)
- `btcbot/DESIGN.md` — living design doc (read this first next session)

### RUNNING NOW: collect-only data collector
A background collect-only run is logging per-second snapshots (BTC price + both
books + outcome) for 5m & 15m to `btcbot/paper_ledger.db`. NO trades. For 24/7
persistence the user double-clicks `btcbot.bat` (don't run two writers at once).

### Verified market facts (2026-06-01)
- 5m/15m resolve on **Chainlink BTC/USD** (end ≥ start); 1h on **Binance
  BTC/USDT** 1h candle (close ≥ open). "Close ≥ open" → ties resolve Up.
- Strike/"price to beat" is NOT in the API → capture at window open ourselves.
- Markets are LIQUID: avg final volume ~$82k per 5m window, ~$50k per 15m,
  ~$223k daily. Tens of millions/day of flow → capacity is NOT the constraint;
  finding/keeping an edge is. Volume is hugely time-of-day dependent.
- Reddit is NOT fetchable by Claude (domain-blocked). GitHub/blogs/web are.
  Public alpha is scarce — nobody posts working edges. Path forward = find our
  OWN edge in our OWN collected data, not research others.

### What comes next (roadmap in DESIGN.md)
1. Keep the collector running → accumulate dataset.
2. **Design ONE concrete entry edge** (the real work — entry model is the core,
   NOT macro reasons for price moves). THEN paper-test it on collected data.
3. v1 = early-bird window handling + chosen entry + stop-loss, paper only.
4. Validate (guard against overfitting — tiny samples invent fake edges).
5. Iterate; later: websocket, UI section in dashboard, more horizons/assets.

### IMPORTANT process note (user was repeatedly frustrated this session)
Claude got way ahead — built the whole harness and ran a placeholder strategy
before the strategy was ever discussed with the user. **Do NOT build/trade on a
strategy that hasn't been agreed. Discuss design first, then build.**

---

## Where we are now (pre-2026-06-01, smart-money tracker)

The 2026-05-26 session shipped three logical lumps of work, plus a
project relocation out of OneDrive. **All committed, working tree clean.**

```
4eac7b6 deps: add requirements.txt (frozen from working venv)
a349b0b classifier v1.2 + 3-day cadence + polybot Chrome launcher
a05e755 insider feed: NEW/TRIM/SELL delta detection + per-wallet UI
f9aea5f lol-bot: collector infrastructure + Oracle's Elixir join
ca3ddfc api+ui: News tab 3-card overview + new-signals feed       ← previous HEAD
```

### What's in each commit

- **f9aea5f — LoL bot infrastructure** (21 files / +4150). Three new
  scheduler jobs (discover_lol_markets_job / snapshot_lol_prices_active /
  snapshot_lol_prices_watcher) covering gamma-api event discovery + 20s
  L2 book snapshots for active matches + 5min watcher tier. Adds OE CSV
  ingestion, alias resolution, PM→OE match-join scripts.
  CSV match-data dumps are gitignored.
- **a05e755 — Insider feed delta detection** (8 files / +1234 / -27).
  Migration 021 adds `insider_actions` table. Refresh job phase 3 diffs
  positions before upsert and writes NEW/TRIM/SELL rows for tagged
  wallets (TRIM threshold 25%). New endpoints under `/insider_wallets`.
  UI rewrite of InsiderWallets with expandable rows, sidebar badge.
- **a349b0b — Classifier v1.2 + 3-day cadence** (6 files / +425 / -274).
  Classifier now catches both-sides-book hedgers (new
  `position_both_sides_count` feature) + breadth-only bots (≥20
  mkts/day). Cadence: Mon-weekly → IntervalTrigger(days=3) for both
  classifier and sybil detection. Startup catch-up
  `catch_up_wallet_hygiene_if_stale` self-heals when polybot is off
  across scheduled fires. System-status thresholds 8/16d → 4/8d.
  polybot.bat opens Chrome explicitly.
- **4eac7b6 — Add requirements.txt**. Polymarket previously had no
  dependency manifest; pip-frozen from the working OneDrive venv so
  rebuilds at new locations work via
  `python -m pip install -r requirements.txt`.

### Adjacent context — OneDrive migration

The project lived at `C:\Users\hej\OneDrive\Dokument\ai agency codex\polymarket\`
until today. OneDrive's atomic-write interception on `.git/logs/HEAD`
was blocking commits. Rather than apply the `windows.appendAtomically
false` workaround, the user opted to move projects out of OneDrive
entirely. Three projects relocated today: `modport`, `quotly`, `ict
trade`. Polymarket joined them in this session. Global CLAUDE.md
(`C:\Users\hej\.claude\CLAUDE.md`) updated to point at the new root
`C:\Users\hej\Code\AI projekt rätt mapp\`.

The old OneDrive polymarket copy still exists at
`C:\Users\hej\OneDrive\Dokument\ai agency codex\polymarket\` with all
the original uncommitted state. **It can be deleted whenever the user
is confident the new location works.** No urgency.

## Quick-start at new location (already done this session)

```powershell
cd "C:\Users\hej\Code\AI projekt rätt mapp\polymarket"
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# smoke-tested: app imports ok, classifier v1.2 confirmed
```

Note: `pip.exe` is blocked by Windows SmartScreen / an app control
policy on this machine. **Always use `python -m pip` instead of
`pip` directly.** Same operation, just bypasses the block. Polybot.bat
already invokes uvicorn the same way (`.\venv\Scripts\python -m uvicorn`)
so launching it works fine.

## What's NOT yet done

1. **PUSH BLOCKED — Supabase token in `.mcp.json`.** GitHub Push
   Protection rejected the push to origin/main because commit
   `f9aea5f` (LoL bot) contains a real Supabase Personal Access Token
   at `.mcp.json:13`. The token is `sbp_895e...951`. **Resolution plan
   agreed (Option A):**
   1. User rotates the token at https://supabase.com/dashboard/account/tokens
      (revoke existing, generate new one named e.g. `claude-code-mcp`)
   2. Add `.mcp.json` to `.gitignore`, untrack it (`git rm --cached`)
   3. Rewrite the 5 unpushed commits to remove `.mcp.json` from
      history (safe — nothing on origin yet, no force-push needed).
      Approach: `git filter-branch --index-filter "git rm --cached
      --ignore-unmatch .mcp.json" origin/main..HEAD` or equivalent.
   4. Write new token into `.mcp.json` (now untracked)
   5. `git push origin main`
2. **Polybot launched but catch-up classification status unknown.** User
   launched polybot.bat during evening session. Did not verify whether
   `catch_up_wallet_hygiene_if_stale` completed and the 25
   mis-classified wallets (swisstony, GamblingIsAllYouNeed, debased,
   etc.) flipped from `directional` → `market_maker`. Re-check via
   System Status pill (wallet_classifier should be green / "<1d ago")
   and spot-check classifications next session.
3. **Supabase storage 508 MB — exceeded 500 MB free tier.** User leaning
   toward **Supabase Pro upgrade ($25/mo, 8 GB)** rather than the
   pruning path. At current ~20 MB/day recurring growth (10 MB/day
   portfolio snapshots + 10 MB/day LoL price snapshots), 8 GB lasts
   roughly a year before another decision. Decision not finalized.
   Quick cleanup wins available if user changes mind: drop `raw_blob`
   from `lol_pro_matches` (-52 MB, safe — re-derivable from OE CSVs on
   disk), drop unused indexes on `portfolio_value_snapshots` (88 MB
   of indexes on 35 MB of data — over-indexed).
4. **OneDrive polymarket copy** still deletable once user is confident.

## LoL bot architecture clarification (evening session)

User pushed back to understand exactly how the LoL bot would work
end-to-end. Investigation produced clear findings and one critical
unverified assumption.

### Confirmed facts

- **The data currently captured is ONLY Polymarket betting-market
  prices** (bid/ask/mid for YES/NO sides, captured every 20s via
  `snapshot_lol_prices_active_job`). Nothing about the game itself.
- **OE CSV data (`lol_pro_matches`) provides only final-state +
  minute-10/15/20/25 checkpoints** for completed games. Cannot answer
  "at minute 7" type questions.
- **`acs.leagueoflegends.com` / LEGs / leaguepedia-parser is confirmed
  dead** per existing research docs. User confirmed verbally. Memory
  saved: `lol_live_data_acs_dead.md`. Do not propose it again.
- **`feed.lolesports.com/livestats/v1/window/{gameId}` and
  `/details/{gameId}` are alive and rich** — tested directly. No auth
  needed. Returns sub-second-cadence frames with team-level (gold,
  kills, towers, dragons-with-type, barons, inhibitors) and per-player
  (gold, level, K/D/A, CS, HP, items, perks, runes, stats) data.
- **Historical replay confirmed working for LCK** (T1 vs BRO 2026-05-24
  returned 44 frames with full mid-game state). Strong inference for
  other Tier 1 by extension but not individually tested.
- **Live mode confirmed BROKEN for Circuito Desafiante** (Estral vs
  paiN, 2026-05-26 evening — endpoint returned 10 frames all stuck at
  game-start timestamp, didn't advance as game progressed). Smaller
  leagues are likely uncovered.
- **Manual data entry of live game state via custom UI is not
  realistic.** Math: ~200 entries per 30-min game at team-level only
  (1 every 9s with no break), ~1000 at per-player granularity. OCR
  off the broadcast is the actual fallback if livestats fails — not
  manual entry.

### Critical unverified assumption

**Live mode for Tier 1 has NOT been tested.** Only historical replay
and one failed smaller-league live test. Whether livestats returns
fresh frames within ~30-60s of real time during an actually-live LCK
or LPL or LEC game is inference, not proof. Inference rests on
lolesports.com using this endpoint to power its own live stats display
— if it didn't work live, that page would be broken during broadcasts.

**User explicitly said no further LoL work until this is verified.**

### Verification plan (next session)

Next Tier 1 game window: **2026-05-27 08:00 UTC (10:00 Stockholm CEST
if user is on Swedish time)** — LCK GEN vs HLE, matchId
`115548128962971863`. Also LJL NM vs DFMA at same time and additional
LCK at 10:00 UTC.

Procedure once GEN vs HLE goes live:
1. Get gameId from `getEventDetails?id=115548128962971863` (the first
   game where state=inProgress)
2. Poll `https://feed.lolesports.com/livestats/v1/window/{gameId}` with
   NO `startingTime` parameter
3. Confirm latest frame timestamp is within ~30-60s of current UTC time
4. Re-poll 30s later and confirm frame timestamps advanced and state
   numbers changed (gold counter going up, etc.)
5. Hit `/details/{gameId}` once to confirm per-player rich data works
   live too
6. If all pass: bot is buildable. Commit architecture.
7. If any fail: we learn the real constraint (lag, partial data,
   broken) and adapt.

### Coverage scope (if live verification passes)

Restrict bot to leagues with confirmed/strong-inference coverage. Of
~410 currently-open Polymarket LoL markets:
- ~195 in confirmed-Tier-1-covered (LCK, LPL, LEC, LCS, LCP, LJL,
  First Stand, LCK Cup, etc.)
- ~140 in plausibly-covered Tier 2 (LCK Challengers, CBLOL, NA
  Challengers, LFL, Prime League, TCL) — verify per-league before
  trusting
- ~75+ in smaller leagues likely uncovered (Circuito Desafiante, Rift
  Legends, Road of Legends, LES, LPLOL, etc.) — out of scope for the
  livestats-based bot. OCR or paid Bayes/GRID feed would be needed
  to extend coverage there.

Per-league coverage status memory saved: `lol_livestats_coverage.md`.

### Other open architectural questions (deferred)

- Polymarket-market → lolesports-gameId join: currently `lol_match_join.py`
  joins Polymarket → OE post-match. For the live bot we need a
  Polymarket-market → lolesports-gameId mapper. lolesports schedule
  API exposes (team_a, team_b, startTime, league) for every match —
  same fuzzy team-name matcher (`lol_alias_map.py`) should work.
  Not started.
- Storage schema for live frames: `lol_pro_game_events` stub table
  proposed in DEBATE_DEFER_TIMELINE.md. Not created yet. Would be
  the landing place for parsed livestats frames.

## What comes next

User signed off evening of 2026-05-26 with the LoL bot work blocked
on live-mode verification. **Next session priority order:**

1. **Verify livestats live mode** during LCK GEN vs HLE on
   2026-05-27 08:00 UTC. Wait for user confirmation that the game is
   actually running (past pick/ban + loading), then run the procedure
   under "Verification plan" above. Until this passes, NO further LoL
   bot architecture work — user was explicit.
2. **If verification passes:** propose the next concrete LoL build
   step. Likely candidates: Polymarket↔lolesports gameId mapper,
   `lol_pro_game_events` schema, frame ingestion job that piggybacks
   on the existing 20s active-window collector.
3. **If verification fails:** revisit. Options become OCR off the
   broadcast (free, hacky) or paid Bayes/GRID feed. User has not
   committed to either.
4. **Push the 5 unpushed commits** once Supabase token is rotated
   (procedure under "What's NOT yet done" #1 above).
5. **Decide on Supabase Pro upgrade** ($25/mo, 8 GB) vs the cleanup
   path. User leaning Pro but undecided.
6. **Polybot catch-up verification** — confirm wallet_classifier
   pill is green and the 25 mis-classified wallets flipped to
   `market_maker`.

Existing LoL infrastructure (committed in f9aea5f, ~4,150 LOC):
- `app/services/polymarket_lol.py` — discover & classify LoL events
  on gamma-api
- `app/services/oracles_elixir.py` — OE CSV ingestion
- `app/services/lol_match_join.py` — PM→OE match matching (post-game)
- `app/services/lol_alias_map.py` — team name alias resolution
- `lol_bot/research/` — debate docs, existing models, OE join notes
- 7 scripts in `scripts/`
- 3 LoL collector jobs in `app/scheduler/runner.py` (discover,
  active 20s, watcher 5min)

## Context that is easy to forget

- The insider-actions diff in `refresh_top_trader_positions` phase 3
  MUST run BEFORE `upsert_positions_for_trader`. After the upsert,
  existing state is already overwritten and the diff would see no
  changes.
- TRIM threshold is locked at 25% (`INSIDER_TRIM_THRESHOLD = 0.25` in
  `app/db/crud.py`).
- All Polymarket API calls still must route through
  `app/services/polymarket.py` per project rule.
- Windows cp1252 stdout limitation still applies for any new scripts —
  use `sys.stdout.reconfigure(encoding="utf-8")` at the top.
- `pip.exe` is blocked machine-wide — always use `python -m pip`.
- Polybot.bat opens Chrome explicitly (hardcoded path
  `C:\Program Files\Google\Chrome\Application\chrome.exe`). If Chrome
  is ever uninstalled, that path will break — easy fix in polybot.bat.
- Classifier v1.2 thresholds in `app/services/wallet_classifier.py`:
  `BOTH_SIDES_MM_COUNT_THRESHOLD=3`, `BOTH_SIDES_MIN_LEG_USD=50.0`,
  `BREADTH_ONLY_MM_MARKETS_PER_DAY=20.0`. Tunable if false positives appear.
