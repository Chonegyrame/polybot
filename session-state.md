# Session State

Last updated: 2026-05-26 (classifier v1.2 + 3-day cadence + OneDrive migration)
Branch: main (uncommitted — 3-commit split mid-execution, see "Resume work" below)

## What was done this session (2026-05-26)

### 1. Polybot launch fixes
- Diagnosed "polybot won't open the UI" → root cause was a stale earlier
  polybot instance holding port 8000 hostage. After killing it, the bat
  works.
- `polybot.bat` now opens the dashboard in Chrome explicitly instead of
  the system default browser (was opening in Edge).

### 2. Wallet classifier — three fixes shipped

**Fix A — Cadence + startup catch-up.**
The "Wallet classifier" status pill in System Status was showing 18 days
old. Root cause: the job ran on `CronTrigger(mon, 03:00 UTC)` with a 24h
misfire grace, so any week the laptop was off across that 24h window the
run got permanently dropped. Three consecutive Mondays missed.

Changes:
- `app/scheduler/runner.py` — switched classifier from `CronTrigger(mon)`
  to `IntervalTrigger(days=3)`; renamed job id to `triweekly_classify`.
  Same change applied to `detect_sybil_clusters_in_pool` (paired job,
  renamed `triweekly_sybil`).
- `app/scheduler/jobs.py` — new `catch_up_wallet_hygiene_if_stale()`
  modeled on `catch_up_snapshot_if_stale`. Runs classifier + sybil
  sequentially when `latest_classification_at` is older than 3 days.
  Freshness signal: `MAX(wallet_classifications.classified_at)` —
  sybil writes via the same `upsert_wallet_classification` path so
  this proxy is reliable for the pair.
- `app/scheduler/runner.py` — background `_startup_wallet_hygiene_catchup()`
  task in `lifespan_scheduler`, non-blocking so polybot startup stays fast.
- `app/api/routes/system.py` — `CLASSIFIER_GREEN_MAX_DAYS` 8→4,
  `CLASSIFIER_AMBER_MAX_DAYS` 16→8 to match the new cadence.

**Fix B — Classifier v1.2: position-state both-sides detection.**
PROGRAM-LIVE-NEED-TO-FIX.md documented a known bug: wallets running
simultaneous YES + NO positions on the same condition_id were being
tagged `directional`. Confirmed at scale via Supabase query — 25 wallets
were affected. Most egregious:
- **swisstony** — 65 markets with both YES + NO held
- **GamblingIsAllYouNeed** — 4 markets, **$3.3M** in both-sides exposure
- **debased** — 11 markets, $735k
- **anonymous 0x4e25** — 22 markets

Root cause: trade-history features can't catch this pattern. A wallet
that buys YES Monday and NO Wednesday on the same market generates two
separate BUY trades on different assets at different times — no SELL
pair, no co-temporal 5-min bucket overlap. But position state shows it
plainly.

Changes in `app/services/wallet_classifier.py` (bumped to `v1.2`):
- `compute_features(trades, positions=None)` — new optional positions
  param, computes `position_both_sides_count` and `distinct_conditions_held`.
- `_compute_position_features()` helper extracts both-sides count (with
  $50 min-leg dust filter).
- `classify()` — two new rules between arb and legacy MM rule:
  - `position_both_sides_count >= 3` → market_maker (conf 0.85)
  - `distinct_markets_per_day >= 20` → market_maker (conf 0.80)
- New thresholds: `BOTH_SIDES_MM_COUNT_THRESHOLD=3`,
  `BOTH_SIDES_MIN_LEG_USD=50.0`, `BREADTH_ONLY_MM_MARKETS_PER_DAY=20.0`.

Changes in `app/scheduler/jobs.py`:
- `classify_one` now fetches positions via `crud.list_positions_for_wallet`
  and passes them to `compute_features`.
- Wrapped the whole function in try/except (was only the trades fetch
  before) so per-wallet failures don't crash the entire classify job.

**Smoke-tested:** synthetic swisstony features → market_maker ✓.
Breadth-only bot (135 mkts/day) → market_maker ✓. Clean directional →
directional ✓. Borderline 2-both-sides → still directional ✓. Legacy MM
rule + arb rule still fire as before ✓. `scripts/smoke_phase_a2.py`
classifier tests still pass ✓.

### 3. Major infrastructure change — OneDrive migration

The project root was moved out of OneDrive because OneDrive's atomic-write
interception on `.git/logs/HEAD` was blocking `git commit`. New location:

- **Old:** `C:\Users\hej\OneDrive\Dokument\ai agency codex\polymarket\`
- **New:** `C:\Users\hej\Code\ai agency codex\polymarket\`

The `windows.appendAtomically false` git workaround was deliberately NOT
applied per user preference — they wanted a clean break from OneDrive.

Global CLAUDE.md (`C:\Users\hej\.claude\CLAUDE.md`) was updated to
reference the new path. The gusta↔hej junction is still relevant for
legacy hardcoded paths inside per-project files (venvs, .env, etc.) —
that cleanup is deferred.

Polymarket's own venv was already rebuilt under hej on this PC, so it
has no stale gusta references.

### 4. Diagnostics + analysis tools used

- `mcp__supabase__execute_sql` to confirm the 25 mis-classified wallets
- `scripts/smoke_phase_a2.py` for classifier regression check
- Direct synthetic-features unit testing of `classify()` via PowerShell
  python heredocs

## Current state

- All session-2026-05-26 edits are present in the working tree.
- Three commits planned but not yet executed (see "Resume work" below).
- jobs.py and runner.py are at their FINAL state (i.e., contain all
  session changes mixed together).
- Backup copies of the FINAL state at:
  - `.commit-staging/jobs.final.py`
  - `.commit-staging/runner.final.py`
  These are safe to delete after the 3-commit split lands.
- Insider-action delta logging (from the 2026-05-18 session) is in code
  but the running scheduler has not been restarted to activate v1.2 +
  cadence + insider-actions. Restart polybot after the commits to
  activate everything; the startup catch-up will then run a fresh
  classification pass and the system status pill will flip to green.
- `.gitignore` now excludes `*_LoL_esports_match_data_from_OraclesElixir.csv`
  to stop the 300k-line CSV dumps from showing up in `git status`.

## Resume work — finish the 3-commit split

**Goal:** turn the current uncommitted lump into 3 clean commits:

1. **lol-bot: collector infrastructure + Oracle's Elixir join**
   (carryover from prior sessions, was uncommitted on main)
2. **insider feed: NEW/TRIM/SELL delta detection + UI**
   (the 2026-05-18 session's work)
3. **classifier v1.2 + 3-day cadence + polybot Chrome launcher**
   (this session — 2026-05-26)

The previous session attempted this split but hit OneDrive's
commit-blocking issue. Now that we're outside OneDrive, commits will
work. To execute:

### Execution plan

For each commit, the technique is:
- Restore the mixed files (`app/scheduler/jobs.py`, `app/scheduler/runner.py`)
  to a base state via `git restore --source HEAD --worktree`
- Edit the files to add ONLY that commit's hunks (use the diffs from
  the previous session's analysis as the spec)
- Stage the relevant files + the rebuilt mixed files
- Commit
- Repeat for next commit, rebuilding the mixed files on top

**Helpful reference:** `.commit-staging/jobs.final.py` and
`.commit-staging/runner.final.py` are byte-identical to the desired
final state of those files after all 3 commits are done. Use them as
the "ground truth" target.

### Hunks attribution (per the previous session's diff analysis)

**`app/scheduler/jobs.py`:**
- Line 31 imports (`polymarket_lol`): **LoL** commit
- Lines 342-410 (`refresh_top_trader_positions` phase 3 insider hook):
  **Insider** commit
- Lines 813-862 (`classify_one` refactor + positions fetch): **Classifier** commit
- End-of-file additions:
  - `catch_up_wallet_hygiene_if_stale` function (~40 lines): **Classifier** commit
  - LoL collector job section (`LolDiscoveryResult`, `LolSnapshotResult`,
    `discover_lol_markets_job`, `_snapshot_lol_market_list`,
    `snapshot_lol_prices_active_job`, `snapshot_lol_prices_watcher_job`):
    **LoL** commit

**`app/scheduler/runner.py`:**
- LoL job imports (`discover_lol_markets_job`,
  `snapshot_lol_prices_active_job`, `snapshot_lol_prices_watcher_job`): **LoL**
- LoL job registrations (the three `scheduler.add_job` blocks at end of
  `build_scheduler`): **LoL**
- `catch_up_wallet_hygiene_if_stale` import: **Classifier**
- Classifier/sybil CronTrigger → IntervalTrigger(days=3) + id renames:
  **Classifier**
- `_startup_wallet_hygiene_catchup` task creation + function definition: **Classifier**

**Pure files (whole file goes in one commit):**
- LoL commit: `.mcp.json`, `app/services/lol_alias_map.py`,
  `app/services/lol_match_join.py`, `app/services/oracles_elixir.py`,
  `app/services/polymarket_lol.py`, `lol_bot/`, `scripts/*_lol_*.py`,
  `scripts/audit_alias_oe_existence.py`,
  `scripts/auto_resolve_lol_aliases.py`,
  `scripts/backfill_lol_history.py`, `scripts/backfill_lol_start_times.py`,
  `scripts/ingest_oracles_elixir.py`, `scripts/join_pm_to_oe.py`,
  `scripts/smoke_lol_collector.py`, `app/services/market_sync.py`
  (start_time hook), `app/services/polymarket.py` (tag_slug param),
  `app/services/polymarket_types.py` (Event.start_time field)
- Insider commit: `migrations/021_insider_actions.sql`,
  `app/api/routes/insider.py`, `app/db/crud.py` (insider-actions block),
  `ui/app.jsx`, `ui/shared.jsx`, `ui/testing.jsx`,
  `scripts/inspect_paper_trades.py` (general inspection tool, fits here)
- Classifier commit: `app/services/wallet_classifier.py`,
  `app/api/routes/system.py`, `polybot.bat`

**`session-state.md`:** include in the classifier commit (final commit)
so the final state captures everything that happened.

**`.gitignore`:** include in the LoL commit (it's gitignoring LoL CSV files).

### Git identity

Previous commits on this branch used `Chonegyrame <Gustav.wallin123@gmail.com>`.
Use `git -c user.email=Gustav.wallin123@gmail.com -c user.name=Chonegyrame commit ...`
inline rather than updating the persistent config (the user's global
CLAUDE.md has a "never update git config" rule).

### Sanity checks before committing

1. `git status` should match expectations after each stage
2. `python -c "from app.scheduler.runner import build_scheduler; print('ok')"`
   after each commit's file edits to confirm imports still work
3. After all 3 commits: `git status` should be clean (only `.commit-staging/`
   remaining as untracked, ready for deletion)

## What comes next AFTER the 3-commit split

1. **Delete `.commit-staging/`** — no longer needed once commits land.
2. **Restart polybot** to activate the v1.2 classifier + 3-day cadence
   + insider-actions logging + Chrome launcher.
3. **Verify the classifier fix worked**: ~2 min after restart, re-run
   the Supabase diagnostic SQL from earlier in this session to confirm
   the 25 affected wallets now correctly tag as `market_maker`. The
   diagnostic query lives in this session's transcript — search for
   "WITH both_sides AS".
4. **Delete the old OneDrive copy** (`C:\Users\hej\OneDrive\Dokument\ai agency codex\polymarket\`)
   once everything in the new location is verified working. OneDrive's
   recycle bin keeps a 30-day backup so this is reversible.
5. **LoL bot work** — user's stated goal for the rest of today. Step 1
   per the prior session's notes was champion archetype tagging via
   lolalytics + U.GG + Mobalytics scrape. Pick up from there.

## Context that is easy to forget

- The insider-actions diff in `refresh_top_trader_positions` phase 3
  MUST run BEFORE `upsert_positions_for_trader`. After the upsert,
  existing state is already overwritten and the diff would see no changes.
- TRIM threshold is locked at 25% (`INSIDER_TRIM_THRESHOLD = 0.25` in
  `app/db/crud.py`).
- The `useApi` hook in `ui/shared.jsx` does not expose a manual refetch.
  The insider badge clears with up to a 30-second lag.
- `GET /insider_wallets/{w}/positions` returns 404 if the wallet isn't
  in `insider_wallets` — by design.
- All Polymarket API calls still must route through `app/services/polymarket.py`
  per project rule.
- Windows cp1252 stdout limitation still applies for any new scripts —
  use `sys.stdout.reconfigure(encoding="utf-8")` at the top.
- The OneDrive folder under `C:\Users\hej\OneDrive\Dokument\ai agency codex\`
  may still exist after the move depending on whether the user deleted
  it. Don't confuse old + new — always work from `C:\Users\hej\Code\ai agency codex\polymarket\`.
- The `windows.appendAtomically` git config workaround was intentionally
  NOT applied. It's a no-op outside OneDrive anyway.
