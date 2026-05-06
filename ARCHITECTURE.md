# Polymarket Smart Money Tracker — Architecture & Audit Guide

> **Purpose of this document:** map the entire system end-to-end so a fresh agent (with zero prior context) can navigate the codebase, understand intent, and audit for weaknesses. Pair with `CLAUDE.md` (project rules), `UI-SPEC.md` (frontend contract), and `session-state.md` (chronological build log).

---

## 1. What this system is

**A personal Polymarket consensus-signal tracker.** Single user, runs on the user's laptop, eventually deploys to Railway. Read-only V1 — never places real trades; helps the user manually decide.

**Core thesis:** ~3% of Polymarket traders are persistently skilled (validated by Gómez-Cram et al. 2026). When several of them concentrate on the same side of the same market, that's a "consensus signal." Surface it; let the user decide whether to follow.

**What the system does, in one paragraph:**
Every 10 minutes it pulls open positions for the union of top-N traders across 3 ranking modes × 7 categories. For each market the tracked pool is in, if ≥5 distinct traders, ≥60% direction skew, and ≥$25k aggregate are all true, a "signal" fires. New signals get a CLOB orderbook snapshot for an executable entry price + book depth. Markets resolve over time → a backtest engine measures whether following these signals would have made money, sliced by any dimension you care about. A paper-trading subsystem lets the user live-test the system with fake money before risking real capital.

**Out of V1 scope:** real-money trading, multi-user, mobile, accounts/auth, on-chain Polygon RPC integration.

---

## 2. Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.14 | User preference; latest |
| DB | Supabase Postgres (eu-west-3) | Free tier, hosted, IPv4 pooler |
| DB driver | asyncpg | Async-native, no SQLAlchemy |
| HTTP client | httpx + tenacity | Async + retries |
| API server | FastAPI 0.136 + pydantic 2.13.3 | Wheels finally available for 3.14 |
| Scheduler | APScheduler with MemoryJobStore | Jobs are code-defined, no need for SQL store |
| Polymarket APIs | gamma-api, data-api, clob | All free, no auth |
| Hosting (V1) | local laptop, eventually Railway | per CLAUDE.md |

**Notable non-choices:** no SQLAlchemy, no pandas/polars (Step 8 used hand-rolled stats to avoid heavy deps), no web3/Polygon RPC (resolution from gamma only), no Resend/email (UI-native notifications instead).

---

## 3. Architecture map (text diagram)

```
                       ┌──────────────────────────────────────────┐
                       │ Polymarket public APIs (free, no auth)   │
                       │  • gamma-api  (events/markets)           │
                       │  • data-api   (positions/trades/lb)      │
                       │  • clob       (orderbook + price hist)   │
                       └──────────────┬───────────────────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────────────────┐
                       │ app/services/polymarket.py               │
                       │ Single typed client. Rate-limited (10/s),│
                       │ tenacity retries. ALL Polymarket calls   │
                       │ go through here (per CLAUDE.md rule).    │
                       └──────────────┬───────────────────────────┘
                                      │
              ┌───────────────────────┼────────────────────────┐
              │                       │                        │
              ▼                       ▼                        ▼
   ┌──────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │ daily snapshot   │    │ 10-min cycle       │    │ weekly batches     │
   │ (02:00 UTC)      │    │ refresh→log→close  │    │ classify, sybil    │
   │ leaderboard×28   │    │ all in one job     │    │ Mon 03:00, 03:15   │
   │ combos           │    │                    │    │                    │
   └────────┬─────────┘    └─────────┬──────────┘    └──────────┬─────────┘
            │                        │                          │
            ▼                        ▼                          ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │ Supabase Postgres (10 tables, see migrations/)                   │
   │                                                                  │
   │  traders | leaderboard_snapshots | events | markets | positions  │
   │  portfolio_value_snapshots | raw_snapshots | signal_log          │
   │  signal_book_snapshots | wallet_classifications | wallet_clusters│
   │  cluster_membership | trader_category_stats | paper_trades       │
   │  slice_lookups | alerts_sent (legacy, unused) | _migrations      │
   └──────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
              ┌────────────────────────────────────────┐
              │ FastAPI app (app/api/)                 │
              │ 17 endpoints — system / traders /      │
              │ signals / markets / backtest /         │
              │ paper_trades                           │
              └────────────────┬───────────────────────┘
                               │ JSON over HTTP (CORS open, single-user)
                               ▼
              ┌────────────────────────────────────────┐
              │ External UI builder (Step 10, TBD)     │
              │ Lovable / v0 / Bolt → React frontend   │
              │ Reads UI-SPEC.md as the design brief   │
              └────────────────────────────────────────┘
```

The same Python process boots all of: scheduler (APScheduler in-process), HTTP API (FastAPI/uvicorn), and the asyncpg connection pool. Single command: `scripts/run_api.py`.

---

## 4. Build phases (chronological, mapped to code)

### Phase 0 — Spike + skeleton (Day 1)
**Goal:** validate Polymarket APIs, set up the project shell.
- `spike/` — disposable scripts that probed each API endpoint
- `spike/FINDINGS.md` — what each endpoint actually returns (fields, gotchas, double-encoded JSON, etc.)
- `.env.example`, `.gitignore`, `requirements.txt`, `CLAUDE.md`, `UI-SPEC.md`
- `venv/` — local virtualenv (gitignored)

### Phase 1 — API client
**Goal:** typed, rate-limited, retried wrapper over the four Polymarket APIs.
- `app/services/polymarket.py` — `PolymarketClient` async context manager. **All Polymarket calls go through here.**
- `app/services/polymarket_types.py` — dataclasses (no pydantic): `LeaderboardEntry`, `Position`, `Trade`, `Event`, `Market`, `PortfolioValue`, `PricePoint`. Handles Polymarket's quirk of double-JSON-encoding `outcomes` and `clobTokenIds`.
- `app/services/rate_limiter.py` — `TokenBucket` (default 10 req/s)
- `app/config.py` — `Settings` dataclass loaded from `.env`

### Phase 2 — Daily leaderboard snapshot
**Goal:** point-in-time history from day 1 (so walk-forward backtests aren't survivorship-biased).
- `app/scheduler/jobs.py` → `daily_leaderboard_snapshot()` — 28 combos (7 categories × 2 time_periods × 2 order_bys)
- `app/scheduler/jobs.py` → `catch_up_snapshot_if_stale()` — runs on startup if last snapshot >24h old
- `scripts/run_snapshot.py`

### Phase 3 — DB schema + migrations
- `migrations/001_initial_schema.sql` — 9 tables + `_migrations`. **Read this first** to understand the data model.
- `migrations/002_backtest_schema.sql` — Phase 8a additions: 12 new columns on `signal_log`, 7 new tables.
- `app/db/connection.py` — asyncpg pool factory (Session Pooler for IPv4)
- `app/db/crud.py` — **only place SQL is written** outside `services/*.py` aggregation queries
- `scripts/apply_migrations.py` — idempotent migration runner using `_migrations` tracking table

### Phase 4 — Trader ranker
**Goal:** turn leaderboard snapshots into top-N pools by ranking mode.
- `app/services/trader_ranker.py` — `rank_traders(mode, category, top_n)` returns `list[RankedTrader]`
- Three modes (Phase 8 Y added the third):
  - **`absolute`** — top-N by lifetime PnL within category
  - **`hybrid`** — rank-average of (PnL rank, ROI rank) with `vol >= $5k` floor
  - **`specialist`** — per-category ROI ranking, `vol >= $20k` + positive PnL + active in last month
- All three exclude wallets classified as `market_maker`/`arbitrage`/`likely_sybil` via shared SQL fragment `_EXCLUDE_CONTAMINATED_SQL`

### Phase 5 — Position refresh + JIT market discovery
**Goal:** know what each tracked trader currently holds, on every 10-min tick.
- `app/scheduler/jobs.py` → `refresh_top_trader_positions()` — 3-phase pipeline:
  1. Fetch positions concurrently (semaphore-bounded, rate-limited inside client)
  2. Discover unknown markets via batched gamma calls
  3. Persist positions per wallet, drop stale ones, snapshot portfolio total
- `app/services/market_sync.py` — `discover_and_persist_markets()` (JIT discovery only — original bulk sync `sync_active_markets()` is kept but unused; 4,359 markets in DB vs 50k+ if we'd kept everything)
- `_derive_category()` maps gamma tag slugs to our 7 leaderboard categories

### Phase 6 — Signal detection + logging
**Goal:** detect consensus signals every cycle and record them durably for backtest.
- `app/services/signal_detector.py` — `detect_signals(conn, mode, category, top_n)` returns `list[Signal]`
  - Floors: `MIN_TRADER_COUNT=5`, `MIN_AGGREGATE_USDC=25_000`, `MIN_NET_DIRECTION_SKEW=0.60`
  - SQL aggregates positions by (market, direction); critically uses `COUNT(DISTINCT identity)` where `identity = COALESCE(cluster_id, proxy_wallet)` — this is the cluster-deduplication for sybils
- `app/scheduler/jobs.py` → `log_signals()` — runs `detect_signals` across all (mode × category) at top_n=50, upserts each firing signal into `signal_log`. **Triggers a CLOB book snapshot on every fresh insert** (Phase 8a wiring).
- Per-signal upsert preserves `first_fired_at` and seeds both `first_*` and `peak_*` from the first observation; subsequent updates only `MAX()` the peaks.

### Phase 7 — Scheduler glue
- `app/scheduler/runner.py` — APScheduler with MemoryJobStore. Three triggers:
  - Every 10 min: `refresh_positions_then_log_signals` (composed: positions → signals → auto-close)
  - Daily 02:00 UTC: `daily_leaderboard_snapshot`
  - Weekly Mon 03:00 UTC: `classify_tracked_wallets`
  - Weekly Mon 03:15 UTC: `detect_sybil_clusters_in_pool`
- `lifespan_scheduler()` async context manager → integrates into FastAPI's lifespan hook
- `scripts/run_scheduler.py` — standalone runner (used pre-Step-9; now superseded by `run_api.py` which boots both)
- **Decision:** chose `MemoryJobStore` over `SQLAlchemyJobStore` to avoid `sqlalchemy + psycopg2-binary` deps. Jobs are code-defined; missed runs handled by explicit `catch_up_snapshot_if_stale()` on startup.

### Phase 8 — Honest backtest pipeline
This is the biggest phase. Two research passes preceded the build (see `session-state.md` decisions log).

#### 8a — Schema + CLOB capture
- `migrations/002_backtest_schema.sql` — added `first_*` snapshot fields, executable entry-pricing fields (`signal_entry_offer`, `signal_entry_mid`, `signal_entry_spread_bps`, `liquidity_at_signal_usdc`, `liquidity_tier`), `cluster_id`, `market_type`, `resolution_disputed`. Plus 7 new tables.
- `app/services/orderbook.py` — pure book-metrics computation (`BookMetrics`, `compute_book_metrics`). $5k/$25k tier thresholds.
- `app/services/polymarket.py` → `get_orderbook(token_id)` (CLOB `/book` endpoint).
- `app/db/crud.py` → `persist_book_snapshot_and_pricing` writes the snapshot row + UPDATEs entry-pricing on `signal_log`.
- Hooked into `log_signals` → CLOB capture happens outside the upsert transaction (avoid holding DB tx during network call).
- **Critical fix this phase:** `signal_entry_offer` (current ask, what you'd actually pay) replaced `first_top_trader_entry_price` (smart money's cost basis, unreachable) as the canonical price for backtest math. The two diverge dramatically — see live test in session-state.md (entry 0.03 vs 0.65).

#### 8b — Backtest engine
- `app/services/backtest_engine.py` — pure-Python, zero new heavy deps:
  - `BacktestFilters` and `BacktestResult` dataclasses
  - Hand-rolled **Wilson 95% CI** (closed-form, ~5 lines)
  - Hand-rolled **cluster bootstrap CI** (resamples whole clusters with replacement, 5000 iters)
  - Per-signal P&L formula: `(payoff / effective_entry) - 1 - fee_rate` where `effective_entry = entry_price + slippage`, slippage = `min(0.10, k * sqrt(size / liquidity))`, `k = 0.02` (placeholder; see weakness section)
  - `summarize(filters)` and `slice_by(dimension, filters)`
  - Slice dimensions: mode, category, direction, market_category, liquidity_tier, skew_bucket, trader_count_bucket, aggregate_bucket, entry_price_bucket, **gap_bucket** (this is the core insight — `signal_entry_offer / first_top_trader_entry_price - 1`)
  - n_eff = number of distinct clusters represented; `underpowered = n_eff < 30`
- `scripts/run_backtest.py` — CLI runner with filters/slicing
- **Per-category taker fees** in `TAKER_FEES` dict — placeholder values, see weakness section

#### 8c — Wallet classification + sybil detection
- `app/services/wallet_classifier.py` — rule-based v1:
  - Features: `two_sided_ratio` (BUY/SELL same asset within 1h pairs), `cross_leg_arb_ratio` (trades on YES and NO of same market within 5min), `median_trade_size_usdc`, `distinct_markets_per_day`, `buy_share`
  - Thresholds: `arb_ratio > 0.30` → arbitrage; `two_sided_ratio > 0.40` → market_maker; else directional. Below 5 trades → unknown.
- `app/services/sybil_detector.py` — time-correlation only:
  - 60-second buckets keyed by (condition_id, asset, time)
  - For each pair of wallets, `co_entry_rate = co_buckets / min(buckets_a, buckets_b)`
  - Edges (pairs with rate ≥ 0.30) connected via union-find → clusters
- `app/scheduler/jobs.py` → `classify_tracked_wallets` and `detect_sybil_clusters_in_pool` (weekly batches)
- `app/services/trader_ranker.py` — exclusion SQL filters out classified MM/arb/likely_sybil from all three modes
- `app/services/signal_detector.py` — `COUNT(DISTINCT identity)` where `identity = COALESCE(cluster_id::text, proxy_wallet)` deduplicates sybils in trader_count
- `scripts/run_classifier.py`, `scripts/run_sybil_detection.py`

#### 8 Y — Specialist mode (third ranking method)
- `app/services/trader_ranker.py` → `_rank_specialist()` SQL: per-category ROI ranking using only **existing leaderboard snapshots** (no nightly batch, no extra API calls). Floors: `category_volume >= $20k`, `category_pnl > 0`, present in latest `time_period='month'` snapshot.
- Wired into `LOG_SIGNALS_MODES` and `POSITION_REFRESH_MODES` tuples.
- `trader_category_stats` table from migration 002 was conceptually for a heavier nightly batch; **currently unused** — the simplification path uses leaderboard snapshots directly. Keep table; could feed a future v2 specialist refinement.

#### 8 paper-trade auto-close
- `app/scheduler/jobs.py` → `auto_close_resolved_paper_trades()`:
  1. Re-fetch markets behind open paper trades from gamma (closed markets stop showing in JIT discovery, so they need a targeted refresh — `_refresh_open_paper_trade_markets()`)
  2. Find open trades on now-resolved markets
  3. Settle each: `realized_pnl = (size/entry_price) × payoff − size − entry_fee − entry_slippage` where `payoff ∈ {1.0 winner, 0.0 loser, 0.5 oracle 50_50}`
- Wired into `refresh_positions_then_log_signals` as step 3 of the 10-min compound job.
- `scripts/run_auto_close.py`
- Smoke-test math validated: $1000 YES @ 0.95, resolved YES → +$50.13 (= 1052.63 shares × 1.0 − 1000 − 0 − 2.50).

### Phase 9 — FastAPI HTTP layer
**Goal:** expose everything to the external UI builder via JSON over HTTP.
- `app/api/main.py` — FastAPI app + `lifespan` boots scheduler + permissive CORS
- `app/api/deps.py` — per-request asyncpg connection dependency
- `app/api/routes/system.py` — `/system/status` (drives dashboard health dot)
- `app/api/routes/traders.py` — `/traders/top`, `/traders/{wallet}` (drill-down)
- `app/api/routes/signals.py` — `/signals/active` (live recompute), `/signals/new?since=` (badge query)
- `app/api/routes/markets.py` — `/markets/{condition_id}` (enriched single-market)
- `app/api/routes/backtest.py` — `/backtest/summary`, `/backtest/slice`
- `app/api/routes/paper_trades.py` — full CRUD + manual close
- `scripts/run_api.py` — single command boots both API + scheduler
- Auto-docs at `http://localhost:8000/docs`

---

## 5. Critical concepts an auditor must understand

These are the non-obvious ideas. If something looks "wrong," it might be one of these.

### 5.1 The two prices: smart-money cost basis vs reachable entry
- `first_top_trader_entry_price` = smart money's average buy price (informational only — not executable)
- `signal_entry_offer` = current CLOB ask at signal-fire time (what you'd actually pay)
- Backtest P&L MUST use `signal_entry_offer`. Using cost basis would compute fantasy P&L. This was the single biggest correctness fix in Phase 8a.

### 5.2 Gap to smart money
`gap = (signal_entry_offer - first_top_trader_entry_price) / first_top_trader_entry_price`
- Big positive gap → price has converged toward smart money's view → less edge for new entrants
- Small/negative gap → entry near where smart money entered → edge still on the table
- This is a backtest slicing dimension. The user's intuition was that big-gap signals win MORE often (true) but earn LESS per win (also true). Mean P&L per $ catches both effects; win rate alone misleads.

### 5.3 first_* vs peak_* fields on signal_log
- `first_*` = frozen at first observation. Use these in backtest filters.
- `peak_*` = max across signal lifetime. Forward-looking → using them in filters introduces look-ahead bias.
- Pre-fix rows (the 11 from before migration 002) have `first_* = peak_*` because both were seeded from the same first observation. Marked `signal_entry_source = 'unavailable'`; excluded by default in backtest.

### 5.4 cluster_id has TWO unrelated meanings
- On `signal_log.cluster_id` = parent gamma `event_id`. Used for cluster-bootstrap CI (correlated signals from same parent event don't double-count).
- On `cluster_membership.cluster_id` = sybil cluster UUID. Used for trader_count deduplication.
- Same column name, different concept. **Easy source of confusion when reading queries.** The signal_detector SQL dereferences `cluster_membership` (sybil), not `signal_log.cluster_id`.

### 5.5 Top-N pool size vs in-market trader count
- `top_n` = how many top traders we monitor (UI slider 20–100, default 50)
- `trader_count` = how many of those top-N are present in a specific market
- Eligibility floor of "≥5 traders" is on the in-market count, not on top_n.
- Three top_n=50 lookups feed: signal detection, log_signals (canonical), backtest comparisons.

### 5.6 Mode tuple layering
- `POSITION_REFRESH_MODES` = ("absolute", "hybrid", "specialist") — defines whose positions we fetch
- `LOG_SIGNALS_MODES` = same tuple — defines what (mode, category) combos get logged
- `SNAPSHOT_CATEGORIES` = 7 category slugs
- Multiplying these gives 21 combos per cycle (3 × 7).

### 5.7 Wallet classification is stale-tolerant
- `wallet_classifications` is recomputed weekly. Wallets without a classification (NULL) pass through filters — "innocent until proven guilty."
- A wallet that becomes a market maker mid-week is detected next Monday, not immediately. Acceptable for personal-use cadence.

### 5.8 Sybil detection v1 is precision-biased
- 30% co-entry threshold is high — produces few false positives, may miss real sybils.
- 0 clusters in current pool is plausible (some real sybils may already be filtered by classifier as MM/arb).
- See weaknesses §8 for tuning guidance.

### 5.9 Resolution path simplification
- Originally planned: UMA Optimistic Oracle on Polygon (canonical) + Gamma fallback.
- Shipped: Gamma `outcomePrices → [1.0, 0.0]` inference only.
- Tradeoff: lose dispute auto-detection (~2% of markets), can't distinguish manipulated resolutions, no `web3` dep.
- `resolution_disputed` column exists in schema but is never set automatically. Explicit V1 trade-off; revisit if disputes start mattering.

### 5.10 Paper trades use the same cost model as the backtest
This is intentional. If paper P&L diverged from backtest P&L, the user couldn't trust either. Both use:
- Entry price = current CLOB ask at click time
- Slippage = √(size / liquidity_5c) × k, capped 10c
- Fee = per-category taker fee
- Auto-close on resolution: payoff in {1.0, 0.0, 0.5} − size − fees − slippage

---

## 6. Data flow end-to-end (one full cycle)

```
T-0  Scheduler tick (every 10 min)
     │
     ├─► refresh_top_trader_positions()
     │   • _gather_tracked_wallets() — union of (3 modes × 7 categories × top-N)
     │     pulls leaderboard_snapshots → applies _EXCLUDE_CONTAMINATED_SQL filter
     │   • Fetch /positions for each wallet (concurrency=12, rate-limited 10/s)
     │   • Phase 2: discover_and_persist_markets() for unknown condition_ids
     │   • Phase 3: upsert positions per wallet, snapshot portfolio_value
     │
     ├─► log_signals(top_n=50)
     │   • For each (mode × category) of 21 combos:
     │     ‣ rank_traders() → top-50 wallets
     │     ‣ detect_signals() — SQL aggregates positions by (market, direction)
     │       with COUNT(DISTINCT identity) for sybil dedup
     │     ‣ Apply 5/60%/$25k floors
     │     ‣ Upsert each firing signal into signal_log
     │     ‣ For fresh inserts: get_orderbook() → compute_book_metrics()
     │       → persist_book_snapshot_and_pricing() (writes signal_book_snapshots
     │         row + UPDATE signal_log entry-price columns)
     │
     └─► auto_close_resolved_paper_trades()
         • _refresh_open_paper_trade_markets() — re-fetch gamma for any market
           behind an open paper trade, update resolved_outcome
         • Find open trades where market.resolved_outcome IS NOT NULL
         • Settle each → status='closed_resolved', exit_price, realized_pnl_usdc

T+...  (other triggers fire on their own cadence)
       Daily 02:00 UTC: daily_leaderboard_snapshot — 28 combos written to leaderboard_snapshots
       Weekly Mon 03:00 UTC: classify_tracked_wallets — refresh wallet_classifications
       Weekly Mon 03:15 UTC: detect_sybil_clusters_in_pool — refresh wallet_clusters

User-triggered (via FastAPI):
       GET /signals/active     → detect_signals() live (NOT from log)
       GET /backtest/summary   → reads signal_log + markets + bootstrap CI
       POST /paper_trades      → CLOB book snapshot + insert paper_trades
       POST /paper_trades/{id}/close → manual close at current bid
```

---

## 7. File map (where to look for what)

```
polymarket/
├── ARCHITECTURE.md         ← this file
├── CLAUDE.md               ← project rules (locked decisions)
├── UI-SPEC.md              ← frontend contract
├── session-state.md        ← chronological build log + decisions
│
├── migrations/             ← run via scripts/apply_migrations.py
│   ├── 001_initial_schema.sql
│   └── 002_backtest_schema.sql
│
├── app/
│   ├── config.py           ← env-driven Settings dataclass
│   ├── api/                ← FastAPI HTTP layer (Phase 9)
│   │   ├── main.py
│   │   ├── deps.py
│   │   └── routes/         ← system, traders, signals, markets, backtest, paper_trades
│   ├── db/
│   │   ├── connection.py   ← asyncpg pool factory
│   │   └── crud.py         ← ALL SQL writes; some reads
│   ├── scheduler/
│   │   ├── jobs.py         ← every scheduled function lives here
│   │   └── runner.py       ← APScheduler config, lifespan_scheduler()
│   └── services/
│       ├── polymarket.py             ← API client (only place that talks to Polymarket)
│       ├── polymarket_types.py       ← dataclasses for typed responses
│       ├── rate_limiter.py           ← TokenBucket
│       ├── market_sync.py            ← JIT market discovery
│       ├── trader_ranker.py          ← 3 ranking modes
│       ├── signal_detector.py        ← consensus signal aggregation
│       ├── orderbook.py              ← BookMetrics computation (pure)
│       ├── wallet_classifier.py      ← MM/arb/directional rules
│       ├── sybil_detector.py         ← time-correlation clustering
│       └── backtest_engine.py        ← Wilson CI + cluster bootstrap + slicing
│
├── scripts/                 ← manual runners; one per job
│   ├── apply_migrations.py
│   ├── run_snapshot.py
│   ├── run_position_refresh.py
│   ├── run_log_signals.py
│   ├── run_classifier.py
│   ├── run_sybil_detection.py
│   ├── run_backtest.py
│   ├── run_auto_close.py
│   ├── run_scheduler.py     ← scheduler-only (no API)
│   ├── run_api.py           ← API + scheduler (production entry)
│   └── smoke_*.py           ← legacy spike validators
│
├── spike/                   ← Phase 0 disposable scripts + FINDINGS.md
└── venv/                    ← gitignored Python 3.14 venv
```

---

## 8. Known weaknesses / things to audit FIRST

These are the items most likely to harbor bugs or need verification. Listed roughly in order of "stuff I'd check first if I were the auditor."

### 8.1 Hardcoded / placeholder values that need verification
- **`TAKER_FEES` in `app/services/backtest_engine.py`** — per-category fee schedule is an educated guess. Verify against `polymarket.com/learn/fees` for March 2026 schedule. Currently: politics 0%, sports/crypto 1.8%, others 1.2%.
- **`SLIPPAGE_K = 0.02` in `app/services/backtest_engine.py`** — square-root impact coefficient is a placeholder. Should be calibrated against real fills (we have none yet).
- **Wallet classifier thresholds** in `app/services/wallet_classifier.py` (`MM_TWO_SIDED_RATIO_THRESHOLD = 0.40`, `ARB_CROSS_LEG_RATIO_THRESHOLD = 0.30`) — educated guesses. Recommendation in original research: pull a Dune query distribution of these features and re-derive empirically.
- **`SYBIL_CO_ENTRY_THRESHOLD = 0.30`** in `app/services/sybil_detector.py` — produced 0 detections. May be too strict.

### 8.2 Schema / data integrity
- **Pre-fix `signal_log` rows (11 rows)** have `signal_entry_source='unavailable'` and `first_*` backfilled from `peak_*`. Excluded by default in backtest. Verify the exclusion logic in `_fetch_signals` (`COALESCE(s.signal_entry_source, '') != 'unavailable'`).
- **`cluster_id` ambiguity**: `signal_log.cluster_id` = parent event id; `cluster_membership.cluster_id` = sybil UUID. Same column name, different domains. Confirm queries use the right one.
- **`trader_category_stats` table** is migrated but never written. No code path populates it. Either remove, or build the v2 specialist refinement that uses it.
- **`alerts_sent` table** from migration 001 is a leftover from the dropped Resend email integration. Never populated. Could be dropped.
- **`raw_snapshots` table** has `insert_raw_snapshot` helper but is currently unused — was a debug stage during early phases. Either remove or wire in.

### 8.3 Concurrency / transaction concerns
- `log_signals` separates upsert (in transaction) from book capture (outside transaction) intentionally — confirms the network call doesn't hold a tx. Race window: signal_log row exists momentarily without entry-price fields. Backtest handles this gracefully (NULL → excluded), but worth re-verifying.
- `auto_close_resolved_paper_trades` does the gamma fetch INSIDE the asyncpg connection context (`async with pool.acquire() as conn:` wraps both the refresh AND the candidate fetch + close). For larger paper-trade volumes this could hold a connection too long. Currently fine at our scale (likely <10 trades).
- `_capture_book_for_signal` swallows all exceptions to a warning log. Network failure marks the row `signal_entry_source='unavailable'`. Confirm we don't retry — a permanently-archived market would otherwise loop.

### 8.4 Math correctness to spot-check
- **P&L formula in `compute_pnl_per_dollar`** — verify the ordering: `(payoff / effective_entry) - 1 - fee_rate`. This computes per-dollar return. For YES@0.40 winning: `1.0/0.40 - 1 = +1.5` (150%) — correct.
- **Wilson CI** — closed-form, verified against published values (70/100 → [0.604, 0.781]).
- **Cluster bootstrap** — hand-rolled. Verify it actually resamples whole clusters, not individual rows. Look for `rng.choice(keys)` followed by `extend(by_cluster[k])`.
- **Auto-close shares calculation** uses `entry_price` not `effective_entry`. Mild inconsistency: slippage is double-counted (once in shares received, once again as a fixed cost). For large trades this is non-trivial. See `auto_close_resolved_paper_trades` lines computing `shares = size / entry_price` then subtracting `slip` separately.

### 8.5 Time / locale
- Daily snapshot scheduled at 02:00 UTC. APScheduler's UTC. Confirm `datetime.now(timezone.utc)` is used everywhere (not naive `datetime.now()`). Risk: silent timezone drift if a missed run, then catch-up on a different day.
- `last_seen_at` and `first_fired_at` use `NOW()` (Postgres) — DB server time. Supabase region: eu-west-3. Confirm timestamps are UTC-comparable to Python timestamps.

### 8.6 Resilience gaps
- No retry policy on the `auto_close_resolved_paper_trades` market refresh. If gamma is down for the cycle, settlement is delayed until next cycle. Acceptable.
- `MemoryJobStore` means: laptop reboot loses scheduler state. Catch-up handled for snapshots only; in-flight runs are lost. Acceptable for personal use.
- No circuit breaker on Polymarket API failures. Tenacity retries 4 times with exponential backoff; after that, the cycle log records a failure and continues. Confirm rate limiter doesn't get into bad state on retries.

### 8.7 Auth / safety
- FastAPI CORS is `allow_origins=["*"]`. Single-user local-only assumption. **Must be tightened before any deploy.**
- No auth at all. Anyone with network access to port 8000 can query, paper-trade, etc.
- `get_orderbook` and other endpoints take user-supplied IDs (condition_id, token_id) without validation beyond existence checks. Watch for SQL injection — asyncpg uses parameterized queries everywhere, so should be safe, but worth scanning for any string interpolation in SQL.

### 8.8 Code-organization smells
- Most `services/*.py` modules import from `app.db.crud`. Some queries are inline in services (e.g. `signal_detector._aggregate_positions` builds SQL directly). CLAUDE.md says all DB writes go through crud; reads are looser. Verify no service writes outside crud.
- `app/scheduler/jobs.py` is ~700 lines and growing. Split candidates: `position_refresh.py`, `signal_logging.py`, `paper_trade_jobs.py`. Not urgent.
- `app/api/routes/paper_trades.py` has business logic (`_estimate_costs`) that should arguably live in `services/`. Acceptable for v1.
- Several scripts duplicate boilerplate (`Path` insertion, `logging.basicConfig`). Could share via a `scripts/_common.py`.

### 8.9 Things that were intentionally simplified (not bugs)
- **No UMA oracle integration.** Gamma resolution_outcome is canonical for our purposes.
- **No funding-source sybil detection.** Time-correlation only.
- **No nightly trade-history batch.** Specialist mode uses leaderboard snapshots.
- **MemoryJobStore** instead of SQLAlchemyJobStore.
- **Paper trades use "innocent until classified" wallet pool** — wallets not yet classified are kept. Intentional rollout shape.

---

## 9. Things that look wrong but aren't (save the auditor time)

- `pydantic` was dropped Day 1 (no Python 3.14 wheels) and re-added Day 2 in Phase 9 (wheels became available). Shows in `requirements.txt`.
- `apscheduler` requirements include `tzdata` — needed because Windows lacks system tz info.
- `MemoryJobStore` not SQLAlchemyJobStore — explicit decision logged in session-state.md.
- 0 sybil clusters detected — could be true negative (45 contaminated wallets already removed by classifier).
- `resolution_disputed` always FALSE — never set automatically (gamma path doesn't expose disputes).
- 11 pre-fix signal_log rows with `signal_entry_source='unavailable'` — backfilled from `peak_*`, marked excluded.
- `trader_category_stats` table unused — remnant of original Specialist mode design before simplification.
- `alerts_sent` table unused — remnant of dropped email integration.
- `signal_log.signal_entry_offer = NULL` for old rows means backtest excludes them by default — feature, not bug.
- Same market appears in multiple signal_log rows (different mode/category combos). Expected — each (mode, category, top_n, market, direction) is its own signal.
- `_rank_specialist` returns empty for category=overall when no traders qualify — overall + specialist is a niche combo.
- Polymarket API calls show 429s occasionally — tenacity retries handle them invisibly. Look in logs for repeated identical URLs.

---

## 10. How to run the system & inspect data

### Running

```bash
# Apply migrations (idempotent)
./venv/Scripts/python.exe scripts/apply_migrations.py

# Boot full system (API + scheduler in one process)
./venv/Scripts/python.exe scripts/run_api.py
# → http://localhost:8000/docs (interactive API explorer)

# Or run individual jobs manually:
./venv/Scripts/python.exe scripts/run_snapshot.py
./venv/Scripts/python.exe scripts/run_position_refresh.py
./venv/Scripts/python.exe scripts/run_log_signals.py
./venv/Scripts/python.exe scripts/run_classifier.py
./venv/Scripts/python.exe scripts/run_sybil_detection.py
./venv/Scripts/python.exe scripts/run_backtest.py --slice gap_bucket
./venv/Scripts/python.exe scripts/run_auto_close.py
```

### Inspecting live data

The DB is a Supabase Postgres at `aws-1-eu-west-3.pooler.supabase.com:5432`. Connection string is in `.env` (gitignored).

Quick inline queries:

```bash
./venv/Scripts/python.exe -c "
import asyncio
from app.db.connection import init_pool, close_pool
async def main():
    pool = await init_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT mode, COUNT(*) FROM signal_log GROUP BY mode ORDER BY 2 DESC')
        for r in rows: print(dict(r))
    await close_pool()
asyncio.run(main())
"
```

Useful tables to spot-check:
- `signal_log` — all fired signals
- `paper_trades` — user's portfolio
- `wallet_classifications` — distribution of MM/arb/directional
- `wallet_clusters` + `cluster_membership` — detected sybils
- `signal_book_snapshots` — raw L2 books at signal-fire time

---

## 11. Current data state (snapshot at end-of-build session)

- **traders:** ~1,500 distinct wallets
- **leaderboard_snapshots:** ~2,800 rows (1 day of all 28 combos)
- **markets:** 4,359 (only those touched by tracked traders)
- **events:** 1,320 (1,038 with derived category)
- **positions:** ~9,800 open positions across ~530 active wallets
- **signal_log:** 17 rows across 3 modes × 7 categories at top_n=50
- **wallet_classifications:** 530 wallets classified (474 directional / 42 arbitrage / 11 unknown / 3 market_maker)
- **wallet_clusters / cluster_membership:** 0 clusters with v1 threshold
- **paper_trades:** 0 (test trade was closed and cleaned up)
- **signal_book_snapshots:** at least 7 (one for each fresh signal that fired post-Phase-8a)

No markets have resolved yet during the system's operational window, so backtest is currently `n_resolved=0` across all slices. Will fill in over coming days as sports/politics markets settle.

---

## 12. Roadmap (what's next)

- **Step 10 — Frontend.** Generate a React UI in Lovable / v0 / Bolt against the FastAPI. Two routes: `/dashboard`, `/paper-trades`. UI-SPEC.md is the design brief.
- **Step 11+ — Deploy.** Move from laptop to Railway. Tighten CORS, add basic auth, secrets management.
- **Optional polish (not blocking V1):**
  - Verify per-category fee schedule against polymarket.com/learn/fees
  - Calibrate `SLIPPAGE_K` empirically once paper-trade fills accumulate
  - Tune sybil threshold lower if real cases surface
  - Drop / repurpose `trader_category_stats`, `alerts_sent`, `raw_snapshots` if confirmed unused
  - Migrate to UMA oracle for resolution if dispute frequency starts mattering
