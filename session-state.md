# Session State

Last updated: 2026-05-09, evening
Branch: main (uncommitted — see "Uncommitted changes")

## What was done this session

Eight items closed from PROGRAM-LIVE-NEED-TO-FIX.md (Phases A-E),
all working from the doc's open backlog.

### Phase A — quick wins

- **Doc cleanup.** Moved #11 Load flicker from Open to Completed
  (already shipped in commit 6cc5677, doc just hadn't been updated).
- **Sort-by-Freshness fallback chain.** [ui/dashboard.jsx](ui/dashboard.jsx)
  `fresh` comparator now cascades `first_fired_at → last_seen_at →
  first_top_trader_first_seen_at` so candidate signals (top_n != 50,
  where `first_fired_at` is null) get a meaningful sort key.
- **Effectively-resolved markets filter.** [app/services/signal_detector.py](app/services/signal_detector.py)
  `pool_positions` CTE now drops markets where `end_date < NOW() - 7
  days` OR `cur_price` is outside `[0.02, 0.92]`. Catches the Hungary
  Magyar PM case (price 0.999, end_date a month past, formal
  `closed=false`). Lost signals so filtered out show up under News tab
  Card B with "Effectively resolved".
- **Startup catch-up for position refresh.** [app/scheduler/runner.py](app/scheduler/runner.py)
  `lifespan_scheduler` and `run_forever` now spawn a non-blocking
  `_startup_position_refresh` task immediately after `scheduler.start()`,
  so positions don't stay stale up to 10 min after a restart.
  `max_instances=1` on the scheduled job prevents racing the next tick.

### Phase B — medium backend changes

- **Specialist 30d-leaderboard drop (defensive).** [app/services/trader_ranker.py](app/services/trader_ranker.py)
  `_rank_specialist`: the `active_recently` (monthly leaderboard
  presence) filter is now bypassed when `trader_category_stats` is
  seeded AND fresh, falling back to the existing F9 60-day
  `last_trade_at` gate as the sole recency check. Defensively keeps
  the old filter active when stats are unseeded so Specialist never
  runs without ANY recency check. Pool size roughly doubles per
  category once nightly stats has run.
- **Heal job for stale `signal_entry_source = 'unavailable'`.** New
  `heal_unavailable_signal_books` in [app/scheduler/jobs.py](app/scheduler/jobs.py),
  wired in runner.py on a 30-min cadence. Re-fetches the CLOB book for
  any signal_log row stuck on `unavailable`; if reachable, writes the
  real entry_offer / liquidity_tier / spread fields and flips source
  to `clob_l2`. Healed rows leave the candidate pool naturally.
- **Insider Wallets edit-in-place.** New `PATCH /insider_wallets/{wallet}`
  endpoint with a true UPDATE (no COALESCE). New `update_insider_wallet`
  crud helper. UI gets an "edit" button per row that opens a styled
  modal pre-filled with current label/notes. New `apiPatch` helper in
  shared.jsx.

### Phase C — News tab (large)

- **Backend.** New `GET /signals/lost?hours=72` in
  [app/api/routes/signals.py](app/api/routes/signals.py) returns
  signals that fired in the last 72h but stopped firing on every
  (mode, category, top_n) combo. Each row carries a "why" label
  (Market resolved / Effectively resolved / Smart money exited /
  Trimmed below floor / No longer firing) computed server-side.
  Open paper trades on a (cid, direction) flagged via
  `open_paper_trade_id`. New `list_lost_signals` crud helper joins
  signal_log → markets → signal_exits → paper_trades in one pass.
- **Frontend.** New file [ui/news.jsx](ui/news.jsx) with two cards
  ("What's happening" recent activity feed + "Lost signals" rolled-off
  view). Sidebar gets a "News" nav item with unread badge. App-level
  `useNewsBadge` hook owns one shared poll for both Sidebar + NewsPage
  (no double-fetch). Polling = 60s. Dismissal is client-side only via
  localStorage (`news_dismissed_lost_ids`); auto-purge happens
  naturally via the `?hours=72` default once 72h elapse. Unread badge
  reads `news_last_seen_at` localStorage; cleared on page open AND
  re-cleared on every poll while still on the page.

### Phase D — Event-grouping V1

- **Backend.** No change needed — `event_id` was already on the Signal
  dataclass and exposed via asdict() in `/signals/active`.
- **Frontend.** [ui/dashboard.jsx](ui/dashboard.jsx) now builds a
  `renderItems` array that walks the sorted `filtered` signals and
  emits an `EventHeader` row immediately before 2+ child signals that
  share an event_id. Singletons render unchanged. EventHeader shows
  child count, deduped smart-money headcount (union of
  `contributing_wallets`), aggregate sum, and a primary-thesis chip
  when one direction holds 70%+ of the aggregate. Mixed-direction
  groups carry a one-line caveat that aggregate-sum can overstate
  net capital.

### Phase E — Live sports timer (Tier 2)

- **Backend.** New [app/services/sports_meta.py](app/services/sports_meta.py)
  with ESPN scoreboard provider — fuzzy-matches a market question
  ("Will Cagliari win on 2026-05-09?") against the day's fixture list
  across 10 soccer leagues + NBA/NFL/MLB/NHL. New
  `GET /markets/{condition_id}/live_status` endpoint in
  [app/api/routes/markets.py](app/api/routes/markets.py) returns
  `{state, kickoff_at, home_team, away_team, home_score, away_score,
  current_minute, ...}`. Caches: 24h for fixture mapping per
  condition_id (negative results too), 60s for live status per
  fixture_id. All in-process module dicts — no Redis.
- **Frontend.** New `LiveSportsChip` component on each sports
  SignalCard. States: `KICKOFF in Xh Ym` (pre), `LIVE · 43'` or
  `LIVE · 1-1` (in-game), `HT · 1-1`, `FT · 2-1`. Polls every 60s
  via IntersectionObserver — pauses when card scrolls off-screen,
  re-fetches on re-entry. Silently omits when ESPN returns 404 (no
  fixture matched), per the doc's accepted partial-coverage trade-off.

## Current state

- All Phase A-E changes are unstaged and untested at runtime.
- Polybot has not been restarted — backend changes (signal_detector,
  trader_ranker, scheduler heal job, /signals/lost, /markets/live_status,
  PATCH /insider_wallets) are not yet active until the user restarts.
- Frontend changes load fresh on next browser refresh.
- Items still in the open backlog (PROGRAM-LIVE-NEED-TO-FIX.md):
  - Wallet classifier mis-tagging high-frequency bots (discuss-only)
  - Windowed ROI for Specialist (deferred — needs trades-table backfill)
  - Whale-fill detector (deferred)
  - Event grouping V1.1 (TraderModal match-book section, deferred)
  - Backtest event-level signal attribution V2 (deferred)

## Uncommitted changes

Everything from this session — no commits made. Suggested commit
breakdown when ready (5 logical chunks):

1. **Phase A quick wins** — dashboard.jsx (sort), signal_detector.py
   (effectively-resolved filter), runner.py (startup catch-up),
   PROGRAM-LIVE-NEED-TO-FIX.md (load-flicker → Completed).
2. **Phase B medium backend** — trader_ranker.py (Specialist drop),
   jobs.py + runner.py (heal job), insider.py + crud.py + testing.jsx +
   shared.jsx (PATCH endpoint + edit UI).
3. **Phase C News tab** — signals.py + crud.py (`/signals/lost`),
   news.jsx (new file), index.html (script tag), shared.jsx (sidebar
   News item), app.jsx (route + useNewsBadge wiring).
4. **Phase D Event grouping V1** — dashboard.jsx (renderItems +
   EventHeader).
5. **Phase E Sports timer** — sports_meta.py (new file), markets.py
   (route), dashboard.jsx (LiveSportsChip).
6. (Optional 6th) **Doc updates** — PROGRAM-LIVE-NEED-TO-FIX.md and
   session-state.md final pass.

## What comes next

1. **Restart polybot, smoke-test each surface.**
   - News tab: visit `/ui/`, click "News" sidebar — both cards render,
     unread badge clears.
   - Lost signals: confirm the Cagliari NO trade or any other rolled-off
     signal appears under Card B with the right "why" label.
   - Sports chip: open dashboard with sports signals visible — chips
     should appear within ~1s of card mount for matchable fixtures.
   - Event grouping: any 2+ children of one event_id should render
     under one header. Cagliari + Udinese (if still live) is the
     obvious test case.
   - Insider edit: open Insider Wallets, edit a row, confirm changes
     persist via PATCH.
2. **Trigger trader_category_stats seed manually** if you want
   Specialist mode to immediately benefit from the wider 60-day
   recency check (otherwise the old monthly-leaderboard filter
   stays active until the nightly job runs).
3. **Verify the Cagliari paper trade** auto-resolved or still open
   (this was a pre-existing carry-over — manually closing was blocked
   because the book had no live bids).
4. **Pick next from deferred:** Whale-fill detector is the highest-
   value next feature (uniquely catches what current detection misses).
   Windowed ROI is biggest data lift. Event-grouping V1.1 is the
   smallest but value depends on V1 being in use first.

## Open questions

- Whether to commit each phase as its own commit or bundle them. The
  uncommitted changes section above suggests 5-6 commits; user can
  decide.
- Whether the Tier-2 sports timer chip's coverage feels right after a
  day of use. Acceptable to widen the league list in sports_meta.py
  if obvious gaps show up.

## Context that is easy to forget

- `useApi(path, mock, opts?)` — the third argument `{ pollMs }` enables
  auto-refresh. Pass `pollMs: 60_000` for poll-every-60s. Without it
  the hook fetches once per path change and never again.
- `useApi` initial state is `data: null, source: 'pending'` — mock is
  ONLY used as offline fallback when fetch errors. Components that
  expect data on first paint must guard with
  `if (res.loading && res.data == null) return <Loading/>;`. Critical:
  this loading guard MUST be placed AFTER all hook calls in the
  component (rules-of-hooks). TraderModal had a bug from violating this.
- `apiPatch(path, body)` — new helper in shared.jsx for PATCH requests.
  Same shape as apiPost. Surfaces FastAPI's `detail` field on errors.
- `event_id` is on every Signal — backend exposes via asdict(Signal).
  Use it for client-side event-grouping (V1) or future event-level
  features.
- `sports_meta.lookup_live_status_for_market` is best-effort — never
  raises out, returns None on any failure. The `/markets/{cid}/live_status`
  route returns 404 on None; the LiveSportsChip silently omits on 404.
- `useNewsBadge` is the single source of truth for News tab data.
  Both Sidebar (badge) and NewsPage (full data) read from one shared
  poll. App-level lifecycle ensures one timer regardless of route.
- News dismissals are client-side only (localStorage
  `news_dismissed_lost_ids`). They do NOT hide a row from the backend
  query — `/signals/lost` returns everything in the 72h window; UI
  hides dismissed locally. After 72h pass, the row auto-purges from
  the response naturally.
- The new `_startup_position_refresh` task fires asynchronously when
  the FastAPI app starts. Don't await it from lifespan — it can take
  30-60s and would block the API from accepting requests. The
  `max_instances=1` setting on the scheduled job prevents the next
  10-min tick from racing the startup run.
- The heal-unavailable scheduler job runs every 30 min and only acts
  when the book has materialized; it does NOT downgrade existing
  `clob_l2` rows. Safe to run forever.
- `exit_bid_price` displayed in TRIM/EXIT banners can show absurd
  values (e.g. $0.01) when the orderbook was thin/transitioning at
  detection time. Doc-flagged for a defensive sanity check (`<0.10`
  when `cur_price` is mid → store NULL, hide in UI). Not yet
  implemented.
- The `exit_bid_price` field is captured at trim/exit detection time
  and represents the standing bid you'd hypothetically receive if you
  sold immediately — it does NOT represent what smart money actually
  got out at.
- ConfirmDialog component is exposed via `window.ConfirmDialog`,
  mirrors `window.confirm()` API but styled. Used by PaperPortfolio
  close flow. When adding new destructive actions, pass `tone="danger"`
  to use `.btn.danger` styling (red).
- API errors now surface FastAPI's `detail` field via the
  `_apiErrorMessage` helper in shared.jsx. When writing new backend
  routes, raise `HTTPException(status, detail="...")` with a
  user-friendly detail and the UI will show it directly.
- Polymarket multi-outcome events (3-way moneylines, etc) are sets of
  binary markets sharing an `event_id`. YES + NO of any one child
  sums to ~$1; siblings of the same event do NOT sum to $1 (each is
  its own binary). The new EventHeader's "combined aggregate" is a
  raw sum across children — when YES + NO mix across siblings (NO on
  child A + YES on child B) the sum can overstate net capital. The
  header carries that caveat in its footer line for mixed-direction
  groups.
