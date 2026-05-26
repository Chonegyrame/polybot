# Session State

Last updated: 2026-05-26 (post-migration, commits landed, ready for LoL work)
Branch: main (clean working tree)
Location: `C:\Users\hej\Code\AI projekt rätt mapp\polymarket\` (migrated out of OneDrive)

## Where we are now

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

1. **Polybot has not been restarted at the new location.** Double-click
   `polybot.bat` to launch it. On startup, the background
   `catch_up_wallet_hygiene_if_stale` task will fire (since last
   classification is from 2026-05-08, ~18 days old > 3 day threshold).
   It runs classifier + sybil sequentially (~2 min total). The
   wallet_classifier pill in System Status should flip from red to
   green once done.
2. **The 25 wallets we identified as mis-classified should reclassify**
   to `market_maker` once the catch-up cycle runs. Earlier diagnostic
   in this session confirmed: swisstony (65 markets), GamblingIsAllYouNeed
   (4 markets / $3.3M), debased (11 markets / $735k), etc. all running
   both-sides books while tagged `directional`. See
   `PROGRAM-LIVE-NEED-TO-FIX.md:241-316` for the full topic note.
3. **OneDrive polymarket copy can be deleted** once the user has
   verified the new location works.

## What comes next — LoL bot work

The user's stated goal for the rest of the day was to dig deeper into
the LoL bot. From earlier session notes, **Step 1 was champion
archetype tagging** — building a lolalytics + U.GG + Mobalytics scrape
pipeline to bootstrap per-champion behavioral scores (early vs late
game, scaling, snowball potential, etc), then surfacing for user
review.

Existing LoL infrastructure (now committed in f9aea5f):
- `app/services/polymarket_lol.py` (514 lines) — discover & classify
  LoL events on gamma-api
- `app/services/oracles_elixir.py` (499 lines) — OE CSV ingestion
- `app/services/lol_match_join.py` (365 lines) — PM→OE match matching
- `app/services/lol_alias_map.py` (242 lines) — team name alias
  resolution
- `lol_bot/research/` — DEBATE_DEFER_TIMELINE.md,
  DEBATE_INCLUDE_TIMELINE.md, EXISTING_MODELS.md, ORACLES_ELIXIR_JOIN.md
- 7 scripts in `scripts/` (ingest_oracles_elixir, join_pm_to_oe,
  backfill_lol_history, smoke_lol_collector, etc.)
- 3 LoL collector jobs in `app/scheduler/runner.py`

**Start the next session by asking the user which direction to take:**
champion archetype tagging (Step 1 as planned), reviewing what's already
built, querying Supabase to see accumulated LoL data, or a specific
question they have in mind.

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
