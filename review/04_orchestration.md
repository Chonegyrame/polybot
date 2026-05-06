# DB + Scheduler + API Surface Review

Audit scope: orchestration / glue layer — DB pool + advisory locks, scheduler jobs (transactions/locks/idempotency only — per-step business logic is reviewed elsewhere), FastAPI route surface, config, and migration consistency.

## Critical

### Schema constraint mismatch: `paper_trades.exit_reason` rejects `'manual_close'` value used by API close path … wait — let me re-check
- This was a false alarm; migration 005 widened the CHECK to include `manual_close`, `resolved`, `smart_money_exit`. No constraint mismatch.

(No truly critical findings.)

## High

### `refresh_top_trader_positions` Phase 3 is per-wallet N+1 — each of ~530 wallets does its own pool.acquire() + per-position INSERTs
- **File**: `app/scheduler/jobs.py:293-311` (Phase 3 loop) plus `app/db/crud.py:193-261` (`upsert_positions_for_trader` issues one `conn.execute` per Position in a Python for-loop, then one DELETE)
- **Finding**: The comment in the spec brief flags ~7min for 530 wallets. Source is the orchestration shape, not the business logic:
  1. Phase 3 acquires a pool connection inside the per-wallet loop (`async with pool.acquire() as conn`) — each wallet pays one acquire + release round-trip.
  2. `upsert_positions_for_trader` itself is N+1 within a single trader: it loops over every Position and runs one `INSERT … ON CONFLICT DO UPDATE` per position. With ~530 wallets × ~10–30 positions each, that's 5k–15k separate DB round-trips serialized per wallet.
  3. Phase 3 is also strictly sequential (no concurrency on the persistence side) so the pool boost from 4→12 (A15) is wasted here — only one connection is in use at a time.
- **Impact**: ~7 minutes pinned on serialized per-row INSERT round-trips. With the 10-min cycle and the 9-min warning threshold this is exactly why the warning is said to fire on slow runs — not because anything is wrong, but because the persistence shape is round-trip-bound.
- **Suggested fix**:
  1. Switch `upsert_positions_for_trader` to `executemany` for the position upserts (one round-trip per wallet instead of one per position).
  2. Run a small concurrency pool in Phase 3 (e.g. `asyncio.Semaphore(8)`) so multiple wallets persist in parallel, taking advantage of the 12-connection pool. Each persistence task gets its own short-lived `pool.acquire()`.
  3. Even simpler: aggregate all position rows across all wallets into one big tuple list and do a single bulk `executemany` per cycle, then a separate bulk DELETE for the stale per-wallet rows. This collapses 5k–15k round-trips to ~3.

### `daily_leaderboard_snapshot` holds a single pooled connection for the entire 28-combo run (potentially many minutes)
- **File**: `app/scheduler/jobs.py:140-162`
- **Finding**: One `pool.acquire()` wraps the full 28-combo loop, with HTTP fetches and DB writes interleaved. While the work is running, the pool effectively has a connection held idle during the HTTP round-trips (rate-limited at ~10/s → tens of seconds per combo). Combined with `job_lock("daily_snapshot")` which holds *another* connection (correct usage of session-scope advisory lock — see connection.py:101), the snapshot job alone consumes 2 of 12 pool slots for ≥1 minute, sometimes much longer. Same pattern in `auto_close_resolved_paper_trades` was rewritten to release the connection across HTTP calls (see comment at 1196-1199); the daily snapshot job didn't get the same treatment.
- **Impact**: With max_size=12, this is OK on Railway today. But the explicit decision documented in `auto_close_resolved_paper_trades` ("Holding a pooled DB connection across network round trips can starve the pool under load") applies equally here, and this is the only job that does ≥30 HTTP calls under one held connection.
- **Suggested fix**: Acquire the connection per combo inside `_snapshot_one`, not in the outer loop. Each combo's tx is short — the cost of re-acquiring is trivial vs. holding for minutes.

### `compute_trader_category_stats` swallows API failures into a shared list mutated from concurrent tasks
- **File**: `app/scheduler/jobs.py:1115-1138` (`failures` is a closure-captured list, mutated inside `fetch_one` which runs under `asyncio.gather`)
- **Finding**: `failures.append(...)` and `trades_by_wallet[wallet] = ...` happen inside concurrent tasks. CPython's GIL makes individual `list.append` / `dict.__setitem__` thread-safe, but reads-then-writes are not atomic across awaits — this is fine *here* because each task only writes its own entries, but the pattern is fragile and there's no test pinning it.
- **Impact**: Subtle, but the bigger issue is that `await asyncio.gather(*[fetch_one(w) for w in wallets])` with ~530 wallets means 530 concurrent `fetch_one` coroutines all contending for the semaphore. The async runtime handles this, but every gather'd coroutine is alive until they all complete — memory grows linearly with wallet count. Also there's no `return_exceptions=True`, so a single un-caught exception inside fetch_one (e.g. NameError introduced later) blows up the entire batch.
- **Suggested fix**: Either (a) collect (wallet, trades_or_None, exc_or_None) tuples returned from each task and consolidate in the outer loop, mirroring `_fetch_one_wallet` in `refresh_top_trader_positions`, or (b) add `return_exceptions=True` to gather and tolerate per-task failures. The shared-mutation pattern works but isn't the codebase's convention elsewhere.

### Routes contain DB queries directly — `traders/{wallet}`, `markets/{condition_id}`, `paper_trades/`, `signals/active`, `system/status`, `backtest/half_life`
- **File**:
  - `app/api/routes/traders.py:60-130` (4 inline `conn.fetch[row]` queries)
  - `app/api/routes/markets.py:21-122` (4 inline queries)
  - `app/api/routes/paper_trades.py:70-77, 152-159` (2 inline queries inside route)
  - `app/api/routes/signals.py:58-79` (inline query for liquidity/exits enrichment)
  - `app/api/routes/system.py:91-114` (3 inline queries)
  - `app/api/routes/backtest.py:330-351` (inline /half_life query)
- **Finding**: Direct violation of project rule "All DB access goes through `db/crud.py`". The pattern is consistent across the route layer; the codebase has effectively normalized inline SQL in routes despite the rule. CRUD-routed paths do exist (paper_trades writes, watchlist, insider, signal_log writes from scheduler) — it's the read-side enrichment queries that escape.
- **Impact**: Two concrete consequences beyond the rule violation:
  1. Same SQL appears in multiple places (e.g. the liquidity-tier + exits join in signals.py is replicated structure of crud's signal_log helpers but not abstracted). Drift risk when the schema evolves.
  2. Tests can't easily mock the DB layer — they have to spin up a Postgres for any route test.
- **Suggested fix**: Extract each of these into named functions in `crud.py` (e.g. `crud.get_trader_profile_with_positions(...)`, `crud.get_market_with_aggregates(...)`, `crud.get_signal_enrichment(...)`, `crud.get_system_health_components(...)`, `crud.fetch_half_life_rows(...)`). Routes call those and shape the response. This is the pattern signals.py already follows for `crud.list_recent_signal_exits` and `crud.count_new_signals_since`.

### `paper_trades.list_trades` whitelist drops `closed_exit` status — UI cannot filter to smart-money-exit closes
- **File**: `app/api/routes/paper_trades.py:120` — accepts only `("open", "closed_resolved", "closed_manual")`
- **Finding**: Migration 005 added `'closed_exit'` to the schema CHECK constraint (and `crud.close_paper_trade_smart_money_exit` writes that value). The route validation never got updated, so a UI request `?status=closed_exit` returns 400 even though such rows exist in the DB.
- **Impact**: Functional gap — the smart-money-exit feature ships writes but the route to filter for those trades rejects the value. Dashboard "closed via smart-money exit" tab is unreachable via this endpoint.
- **Suggested fix**: Extend the whitelist to `("open", "closed_resolved", "closed_manual", "closed_exit")`. Add a smoke test asserting all four values pass validation.

## Medium

### `_filter_known_markets` adds another N+1 — one SELECT per wallet inside Phase 3 loop
- **File**: `app/scheduler/jobs.py:298-299, 356-373`
- **Finding**: For every wallet in Phase 3, we do `_filter_known_markets(conn, positions)` which `SELECT`s the FK-known cids. Same query shape with different cids per wallet — easy to batch. With Phase 2 already discovering+persisting any new markets, the residual misses are rare but the query still runs unconditionally per wallet.
- **Impact**: 530 extra SELECTs per cycle. Cheap individually, but rolls up into the cycle duration.
- **Suggested fix**: Either (a) compute the unknown-cids set once after Phase 2 by re-querying `markets WHERE condition_id = ANY($1)` against `all_cids`, then pass the known-set into the loop as a Python set lookup; or (b) drop the safety net entirely now that JIT discovery runs in Phase 2 — let the FK violation surface in logs if anything slips through.

### `gather_union_top_n_wallets` runs twice every 10-min cycle (once in `refresh_top_trader_positions`, once in `log_signals`, once in `detect_and_persist_exits`)
- **File**: `app/scheduler/jobs.py:212` (refresh), `:477-479` (log_signals counterparty pool), `:999-1001` (detect_and_persist_exits)
- **Finding**: The same expensive union query is recomputed three times within a single advisory-locked cycle. Wallets don't change between these phases (no new daily snapshot has landed mid-cycle). All three call sites share a single `top_n` value (50/100 — different in two of them, see next finding).
- **Impact**: Wasted query time and confusing reasoning about "tracked pool" — it's slightly different shapes between callers.
- **Suggested fix**: Compute once in `refresh_positions_then_log_signals` (the composer), pass through to each step as an argument. Bonus: pin the `top_n` choice so all three phases see the same pool.

### `refresh_top_trader_positions` uses top_n=100, but `log_signals` counterparty uses top_n=50, and `detect_and_persist_exits` uses top_n=100
- **File**: `app/scheduler/jobs.py:185 vs 384 vs 999-1001`
- **Finding**: Three different "tracked pool" definitions inside one cycle. Position refresh tracks the union at depth 100 (covers UI's 100-slider max). Counterparty checks against the union at depth 50 (the canonical signal-firing pool). Exit detector uses 100. The semantics may be intentional, but they're not documented and will be confusing during a future audit of "who counts as smart money."
- **Impact**: Conceptual debt + possible bugs at the boundary (e.g. a wallet ranked 51–100 is tracked, fires exits, but isn't checked for counterparty).
- **Suggested fix**: Add a top-of-file constant block making the three depths explicit and explaining why each chose the value it did.

### Per-row insert loop in `mark_wallets_likely_sybil`
- **File**: `app/db/crud.py:914-948`
- **Finding**: For each wallet in a sybil cluster, `upsert_wallet_classification` is awaited individually. Cluster sizes are small (handful) so this is tolerable, but it's an inconsistency with the bulk path (`upsert_trader_category_stats_bulk`).
- **Impact**: Low, only weekly cost.
- **Suggested fix**: Add a `bulk_upsert_wallet_classifications` that uses `executemany`, then call from both the classifier loop (`classify_tracked_wallets`) and `mark_wallets_likely_sybil`.

### `slice_lookups` session window has cross-day session leakage
- **File**: `app/db/crud.py:681-709`, `app/api/routes/backtest.py:208-220, 285-300`
- **Finding**: The 4-hour rolling cutoff means that if the user opened the UI in the morning and ran 5 backtests, then opens it again at noon, those morning queries still count toward the Bonferroni N for the noon session if <4 hours apart. There's no concept of a logical session boundary. As a personal tool used by one person, the impact is small (the user knows whether they're "starting fresh"), but the multiplicity_warning will quietly inflate.
- **Impact**: Slightly conservative corrections — false alarms not false negatives. Honest direction.
- **Suggested fix**: Optional: add a `session_id` query param the UI can rotate (e.g. on app open). When present, scope the cutoff to that session_id. Or: shorten the window to 1 hour — covers a typical analysis sitting without leaking from morning to afternoon.

### Status endpoint `signals_health` makes overall amber on quiet days
- **File**: `app/api/routes/system.py:109-119`
- **Finding**: The `_worst` aggregation includes `signals_health`, which goes amber whenever there have been zero signals in 48h. Polymarket genuinely has quiet stretches; the comment "Quiet days are normal so amber, not red" suggests this is intentional, but the practical effect is that the dashboard pill spends meaningful chunks of time amber for non-actionable reasons. The user comes in, sees amber, opens the tooltip — and it just says "no signals fired in 48h" which doesn't need user attention.
- **Impact**: Alert fatigue on the only health surface the UI has.
- **Suggested fix**: Either (a) drop signals_health from the overall composite — keep it as an informational component only (not in `_worst`), or (b) make it green if there have been any signals in the last 7 days, amber only if nothing in 7d (catches "the cycle stopped firing" without flagging quiet weekends).

### `record_signal_price_snapshots` keeps one DB connection acquired across all CLOB book-fetch HTTP calls
- **File**: `app/scheduler/jobs.py:1402-1459`
- **Finding**: `async with pool.acquire() as conn` wraps the entire candidate loop, and each candidate's `pm.get_orderbook(token_id)` HTTP call (rate-limited to 10/s, often slower) happens inside. With even 30 candidates this can pin one connection for several seconds. `min_size=1, max_size=2` is intentionally narrow here, but the comment in `auto_close_resolved_paper_trades` about not holding connections across HTTP applies here too.
- **Impact**: Same starvation pattern flagged elsewhere; pool budget tight.
- **Suggested fix**: Read candidates first, drop the conn, run the HTTP loop, then re-acquire briefly per insert (or batch all inserts at the end with `executemany`).

### Potentially incorrect `paper_trade` close path under concurrent updates — silent on success/failure
- **File**: `app/db/crud.py:583-602, 712-732, 629-648`
- **Finding**: All three close paths use `result.endswith(" 1")` to detect a single-row update. If the row is concurrently transitioned by another path (e.g. resolved auto-close races a smart_money_exit auto-close), the second one returns False and the call sites in `jobs.py` happily count zero closures and move on without a log. Diagnostic gap when both paths legitimately fire on the same trade.
- **Impact**: Silent loss of accounting events under rare concurrent scenarios. The `refresh_cycle` advisory lock prevents same-process races, but a manually triggered script + the scheduler at the same time could collide.
- **Suggested fix**: When the close path returns False, fetch the row's current status and log "paper trade #{id} already closed via {status} — skipping" so the operator can reconstruct what happened.

### `_apply_paper_trade_market_refresh` has no transaction
- **File**: `app/scheduler/jobs.py:861-883`
- **Finding**: Loops `UPDATE markets … WHERE condition_id = $1` per fetched market with no enclosing transaction. If the loop crashes halfway, some markets will have been refreshed and others not. Subsequent `auto_close_resolved_paper_trades` would only settle the ones that did get refreshed.
- **Impact**: Modest — partial state recoverable on next cycle. Not a correctness bug, just a sharper fail-then-retry surface.
- **Suggested fix**: Wrap the loop in `async with conn.transaction()` so it's atomic, or rewrite as a single `executemany`.

### Snapshot gap detection prints a warning but doesn't expose it via /system/status
- **File**: `app/scheduler/jobs.py:1500-1507`, `app/api/routes/system.py:84-88`
- **Finding**: `catch_up_snapshot_if_stale` warns on >1 day gap, but `/system/status` only checks days-since-snapshot which goes amber/red on staleness. There's no "we have unrecoverable historical gaps" surface — the warning lives only in stdout. After a long laptop-off period, the operator has no UI visibility that backtests for those days are blind.
- **Impact**: Hidden caveat for backtest accuracy.
- **Suggested fix**: Persist a row in some `snapshot_gaps` table (or a key in a generic system_state KV) when a gap is detected, surface it as a `gaps` field on /system/status.

## Low / Nits

### `from typing import Any` missing in `jobs.py` despite annotation use
- **File**: `app/scheduler/jobs.py:941` (`trade: dict[str, Any]`); only `Iterable` is imported from typing (line 17)
- **Finding**: With `from __future__ import annotations` at the top, annotations are stringified and never evaluated at runtime, so this doesn't crash. But `inspect.get_type_hints(_settle_paper_trade_at_exit)` would NameError. Latent landmine for any tooling that introspects.
- **Suggested fix**: Add `Any` to the typing import.

### `connection.py` defaults `max_size=12` in code but the same value is hard-coded into every job
- **File**: `app/db/connection.py:24`, `app/scheduler/jobs.py:122, 248, 461, 678, 763, 1487`
- **Finding**: Every job calls `init_pool(min_size=1, max_size=12)` but the function is idempotent — only the first call's args are honored. Later calls' args are silently ignored. Confusing because someone might think they can tune it per-job.
- **Suggested fix**: Either drop the explicit args in jobs (the defaults already match) or extract a `DEFAULT_POOL_MAX = 12` constant in `connection.py` and reference it from both sides. Currently this is two parallel sources of truth.

### `auto_close_resolved_paper_trades` overrides `max_size=2`, last-call-wins in init_pool means it's a no-op
- **File**: `app/scheduler/jobs.py:1204` (`init_pool(min_size=1, max_size=2)`)
- **Finding**: This is the *intent* per the docstring ("avoid pool starvation"). But `init_pool` is idempotent — if any earlier job opened the pool with max_size=12, this call returns the existing pool unchanged. The 2-cap never takes effect. The only way it would is if `auto_close` was the very first job to run after import, before any other job called init_pool. In practice it isn't.
- **Impact**: Code reads as if it implements a precaution that's actually a no-op.
- **Suggested fix**: Remove the misleading args; the pool sizing decision belongs at startup, not per-call.

### `delete_positions_for_dropped_wallets` zero-arg branch silently no-ops, but no metric distinguishes "empty list" from "nothing to delete"
- **File**: `app/db/crud.py:175-178`
- **Finding**: Defensive against accidental whole-table delete. But if the upstream computation broke and produced an empty list (e.g. a transient DB issue in `gather_union_top_n_wallets`), we'd silently skip cleanup forever with no observable alert. The Phase 4 caller does an `if wallets:` guard too, so this branch is reached only via direct callers (none in tree).
- **Suggested fix**: Either log a warning when called with an empty list, or remove the branch and let the caller's guard be the only safety net. Belt-and-suspenders is fine but should be observable.

### `CLAUDE.md` still references `raw_snapshots` even though migration 004 dropped the table
- **File**: `CLAUDE.md:21`
- **Finding**: Project rule says "Raw API responses staged to `raw_snapshots` before processing" — the table no longer exists per migration 004. New contributors / future-you reading CLAUDE.md will be confused.
- **Suggested fix**: Edit the rule to reflect current state: either drop the line or replace with the actual approach (parsed dataclasses).

### `markets.py` `/{condition_id}` route accepts arbitrary string with no shape check
- **File**: `app/api/routes/markets.py:15-21`
- **Finding**: `condition_id` is a 0x-prefixed 66-char hex on Polymarket. Route accepts any string, queries DB, returns 404 on miss. No SQL injection risk (parametrized), but cheap front-line validation would reduce DB hits from random scrapers.
- **Suggested fix**: Add a `regex` constraint on the path param via `Path(..., regex="^0x[0-9a-fA-F]{64}$")`.

### Status endpoint `tracked_wallets` reads from `positions`, not the actual top-N union
- **File**: `app/api/routes/system.py:103-107`
- **Finding**: `SELECT COUNT(DISTINCT proxy_wallet) FROM positions` measures wallets that have at least one open position. That's a proxy for "wallets we can see," but the actual tracked pool comes from `gather_union_top_n_wallets` (~530 expected). If 50 wallets have closed all their positions but are still in top-N, this count will be 480 not 530. The health threshold `< 1` is so loose this won't fire spurious red, but the displayed number is misleading.
- **Suggested fix**: Either rename the field to `wallets_with_open_positions` to match what's measured, or change the query to count from the leaderboard union.

### Backtest `/summary` and `/slice` write to `slice_lookups` even when called for non-interactive purposes (e.g. tests, scripts)
- **File**: `app/api/routes/backtest.py:212-217, 290-298`
- **Finding**: Every API hit logs into the audit trail used for multiple-testing corrections. There's no way to say "I'm calling this from a smoke test, don't pollute my Bonferroni N." The 4-hour window will swallow it eventually but during a smoke run the user could end up with a multiplicity_warning that has nothing to do with their analysis session.
- **Suggested fix**: Optional `log_to_session=true` query param (defaults true for back-compat); smoke scripts pass `false`.

### `_worst` overall-health aggregator double-counts when `_worst("amber", "amber")` returns "amber" — harmless, just verbose
- **File**: `app/api/routes/system.py:46-47`
- **Finding**: Just a styling note: `max(colors, key=...)` works but a comment `_HEALTH_RANK` with explicit ordering would help future readers see why `red > amber > green`.
- **Suggested fix**: Comment the rank table with rationale; or add an enum.

### `connection.py:_lock_id` returns int4-range but `pg_try_advisory_lock` accepts bigint or int4 — unused range slack
- **File**: `app/db/connection.py:65-74`
- **Finding**: Comment says "pg_advisory_lock takes int8 (or two int4s). Using zlib.crc32 keeps the id stable." Actually `pg_try_advisory_lock(bigint)` and `pg_try_advisory_lock(int, int)` are both valid; the chosen single-int approach is fine. Just noting that the int4 range constraint is self-imposed; could be `crc32` directly cast to bigint without the subtract trick.
- **Suggested fix**: None needed; current code is correct. Optional simplification: `await conn.fetchval("SELECT pg_try_advisory_lock($1::bigint)", zlib.crc32(...))`.

### `signal_log.resolution_outcome` migration 002 added `'PENDING'` to allowed values but there's no code path that writes it
- **File**: `migrations/002_backtest_schema.sql:64-67`; no writer in `crud.py` or `jobs.py`
- **Finding**: Constraint allows `('YES','NO','50_50','VOID','PENDING')`, but only YES/NO are ever written by `auto_close_resolved_paper_trades` and `resolve_signal_log` paths. PENDING/VOID dead constants.
- **Suggested fix**: Either delete unused values from the CHECK or wire them up in `_payoff_for_resolution`. Currently the code at jobs.py:835-841 only handles YES/NO/50_50, returning None for VOID/PENDING — silently skipping them. Document this somewhere reachable.

---

## Sections with no findings

- **SQL injection risk** — every route uses parameterized queries; the only string-interpolation is the dollar-arg position counter in `backtest.py:348` (`f" AND e.category = ${len(args)}"`) which interpolates a number, not user input. Safe.
- **Idempotency of catch-up snapshot, position upsert retries, paper-trade auto-close after restart** — `latest_snapshot_date` check handles catch-up; `ON CONFLICT DO NOTHING` and UNIQUE keys make signal_log / signal_exits / paper_trade transitions safe to re-run; `status='open'` filters in close paths make those naturally idempotent.
- **Time math** — all `datetime.now(timezone.utc)` and `NOW()` (which returns `now()` at TZ of session, but rows are TIMESTAMPTZ which is unambiguous). No mixed naive/aware comparisons spotted.
- **Config secrets** — `config.py` reads from env, doesn't print; no leakage in scheduler or routes.
- **9-min cycle warning usefulness** — given the High finding above on the N+1 in Phase 3, this warning is currently load-bearing as the only signal that the cycle is bumping the cadence. After the fix it should fire rarely.
