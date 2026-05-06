# Ingestion + API Client Review

Scope: `app/services/polymarket.py`, `polymarket_types.py`, `rate_limiter.py`, `market_sync.py`, `orderbook.py`, and the position-refresh / leaderboard-snapshot / JIT-discovery / B4 price-snapshot portions of `app/scheduler/jobs.py`. Signal logic, exit logic, paper-trade settlement, and backtest math are out of scope and noted only when they reveal an ingestion bug.

---

## Critical (would silently corrupt the edge or cause data loss)

### 1. Counterparty diagnostic conflates "maker" with "seller"
- **File**: `app/services/counterparty.py:38-71`, `app/services/polymarket.py:346-371`
- **Finding**: `_extract_maker_addresses` pulls every fill's maker address and treats them all as sellers of the YES (or NO) token we're about to buy. CLOB makers are simply the side that **posted the resting limit order** — they may have been buyers OR sellers. A bid maker filled by an aggressive seller is a buyer. The B2 brief says "maker = seller" (locked decision in session-state), but that's only true for taker-buy fills (taker BUY hits ask, maker is the seller). For taker-SELL fills the relationship inverts. The fill record carries a `side` field (TAKER side) that we must consult; we currently ignore it.
- **Impact**: `counterparty_warning` will fire on smart-money wallets who were actually BUYING the same direction we're being told they oppose. The warning becomes worse than useless: it inverts a tailwind into a headwind. Every user decision the warning influences is degraded.
- **Suggested fix**: Filter fills to ones where the taker side is BUY before extracting makers (those are the genuine "smart money sells into our buy"). Alternatively, use the `side` of each fill plus `maker`/`taker` addresses to flag any tracked-pool wallet that was the seller (regardless of maker/taker role). Validate the field name on a real `/trades` response before locking the logic — the spike findings never validated this endpoint shape.

### 2. `get_clob_trades` endpoint and parameter not validated by the spike
- **File**: `app/services/polymarket.py:346-371`, `spike/FINDINGS.md:99-104`
- **Finding**: The spike validated `/book` and `/prices-history` on `clob.polymarket.com`, but `/trades` is **not** in the validated endpoints list. The implementation guesses parameter name (`market={token_id}`) and response shape (list, or `{data: [...]}`, or `{trades: [...]}`). On Polymarket's CLOB, the trades feed is documented to take `market={condition_id}` (not token_id) and to require auth for some routes; the public endpoint is `/data/trades` with different params. If the call returns 404 or an error-wrapped JSON, our defensive parsing silently returns `[]` → counterparty check defaults to FALSE for every signal, with no log noise distinguishing "looked but found nothing" from "endpoint broken."
- **Impact**: B2 may have been silently no-op since shipping. The audit trail says "B2 counterparty pool = 530 wallets, 0 warnings fired" — that's indistinguishable from a broken fetch. Every fresh signal pays an HTTP round-trip per fire for a check that may never succeed.
- **Suggested fix**: Add a one-shot validation script that hits `/trades` for a known active market and dumps the raw JSON. Compare to the Polymarket CLOB OpenAPI spec. Either confirm `market=token_id` works, or correct to `market=condition_id`. Add an explicit "endpoint returned non-list, non-data-wrapped shape" warning so silent breakage is visible.

### 3. Portfolio value is reconstructed from positions instead of using `/value`
- **File**: `app/scheduler/jobs.py:303-309`
- **Finding**: `refresh_top_trader_positions` computes `portfolio_total = sum((p.current_value or 0.0) for p in valid)` and writes that as the wallet's portfolio value. The dedicated `data-api/value` endpoint (already wrapped as `pm.get_portfolio_value`) is **never called** in this path. Polymarket's `/value` returns the wallet's full equity including uninvested USDC cash, redeemed-but-unclaimed proceeds, and resolved-unredeemed positions. Summing only currently-open `current_value` underestimates total portfolio.
- **Impact**: `avg_portfolio_fraction = position_value / portfolio_total` uses a denominator that is systematically too small → the metric overstates how committed each wallet is to a position. This is one of the two headline signal metrics surfaced in the UI (per CLAUDE.md "Two metrics surfaced separately: trader headcount + average portfolio fraction allocated"). A trader with $10k in one position + $90k cash looks 100% committed in our data; in reality they're 10%. Signal eligibility (≥X% portfolio fraction filters) and the UI ranking are biased toward whales who happen to be fully-deployed at the moment.
- **Suggested fix**: Call `pm.get_portfolio_value(wallet)` once per wallet (it's a single-row endpoint, cheap), use the returned `value` as the denominator. Fall back to `sum(current_value)` only if `/value` fails. If `/value` rate cost is a concern, fold it into the same per-wallet semaphore as positions.

### 4. B4 price snapshots compare ask (entry) to bid (snapshot) — guaranteed drift artifact
- **File**: `app/scheduler/jobs.py:1437-1445`, `app/services/orderbook.py:100-105`, `app/services/half_life.py:74-89`
- **Finding**: `signal_entry_offer` is captured at fire time as the **best ASK** (we cross to buy). `record_signal_price_snapshots` captures the **best BID** at +30/+60/+120 min. Half-life logic compares them as if they were the same price series. Any market with a non-zero spread will show snapshot < fire even if the mid hasn't moved, so the convergence rate ("did price move toward smart-money entry?") is biased by the spread itself. On thin markets where spread is 5–10 cents, this swamps any real signal.
- **Impact**: Half-life numbers will systematically suggest "price reverts toward smart-money cost basis after fire" purely because we're sampling the lower side of the book. Once UI surfaces these numbers (`/backtest/half_life`), the user makes worse latency / hold-period decisions. This also poisons B10 (latency simulation) — the simulated "delayed entry price" is a bid, not the ask the user would actually pay.
- **Suggested fix**: Snapshot the **best ask** at +30/+60/+120, matching the entry price's side of the book. Or snapshot both bid and ask and store mid as the canonical comparison point. Document the choice; pin it to a smoke test that asserts entry_price and snapshot_price are sampled from the same side.

### 5. Counterparty pool is built from 7 categories but ignores ranking modes
- **File**: `app/scheduler/jobs.py:476-484`, vs. session-state.md decision "union of ALL 21 mode×category top-N pools"
- **Finding**: `gather_union_top_n_wallets(conn, top_n=top_n, categories=SNAPSHOT_CATEGORIES)` is called once per cycle for the counterparty check. The `RankingMode` axis (absolute / hybrid / specialist) is missing from the call signature. The locked B2 decision says the pool should be all 21 lens combinations (3 modes × 7 categories). If the function only enumerates by category at default mode, the pool may exclude wallets that only surface under specialist or hybrid lenses (small-bankroll sharps the user explicitly tracks).
- **Impact**: Counterparty warnings miss the very wallets the system is most distinctively tuned for (specialist mode is the differentiator vs. raw absolute leaderboards). False negatives on the warning weaken the user's "is consensus actually unanimous?" check.
- **Suggested fix**: Verify `gather_union_top_n_wallets` enumerates across all 3 modes (likely: pass `modes=POSITION_REFRESH_MODES` explicitly, or read directly from the `traders` table for any wallet that appeared in any recent `leaderboard_snapshots` row regardless of lens — that's the most robust "union of 21 pools" definition).

---

## High

### 6. Silent empty-on-error in every list-returning client method
- **File**: `app/services/polymarket.py:133-136, 169-171, 181-183, 228-230, 243-245`
- **Finding**: Every method that expects a list response uses the same idiom: `if not isinstance(data, list): return []`. The Polymarket APIs return 200 + JSON-wrapped error objects in some failure modes (e.g. when a worker is over quota, or when the leaderboard endpoint hits an internal sort error). Treating "non-list response" as "empty result" silently truncates pagination and aggregation everywhere. `get_leaderboard` will end its paging loop one page early; `get_positions` will report a wallet has no positions when the API was actually broken; `get_markets_by_condition_ids` will silently lose batches of cids.
- **Impact**: Compounds across the system. A blip on `data-api` during a position refresh leads to traders being marked as "zero positions" → their consensus weight evaporates → real signals fail to fire → the operator sees nothing wrong because the cycle "succeeded." Particularly insidious during the 10-min cycle where the next cycle re-fetches and "fixes" things, leaving no audit trail of the missed signal.
- **Suggested fix**: Distinguish three return states: (a) successful list, (b) successful but unexpected shape (log + raise so retry kicks in), (c) successful empty list (the only legitimate `[]`). At minimum, when shape is unexpected, log the first 200 chars of the body at WARNING and increment a counter; do not silently coerce to `[]`.

### 7. `get_leaderboard` paginates without race-tolerant cursor — risk of dups + skips
- **File**: `app/services/polymarket.py:138-162`
- **Finding**: Pagination is `offset += 50` until `len(page) < 50`. The leaderboard re-orders continuously (especially `timePeriod=day`/`week`), so between page 1 and page 2 a wallet can move from rank 50 to rank 51 (skipped) or rank 49 to rank 51 (duplicated). For `timePeriod=all` this is rare in practice. There's also no defensive de-dup before truncating to `out[:depth]`.
- **Impact**: Snapshot dedup at the (snapshot_date, category, time_period, order_by, proxy_wallet) UNIQUE key absorbs duplicates silently. Skipped wallets at the rank-50 boundary mean some legitimately-top-ranked traders never enter the tracked pool and their positions are invisible to the signal detector. Most likely effect: 1–3 wallets per (cat × period × order_by × day) silently missing.
- **Suggested fix**: Sort each page by `rank` and de-dup before returning. Optionally fetch one extra row of overlap (e.g., offset += 49) and reconcile, similar to how cursored APIs handle this.

### 8. `_filter_known_markets` silently drops positions with only an aggregate count
- **File**: `app/scheduler/jobs.py:299-318`, `app/scheduler/jobs.py:356-373`
- **Finding**: Positions whose `condition_id` is unknown after JIT discovery are silently filtered out. The aggregate `positions_dropped_unknown_market` is logged once per cycle ("dropped X positions ..."). Per-wallet, per-cid information is not preserved anywhere — there's no `dropped_positions` audit table. Session-state notes "26,435 dropped positions confirmed benign (resolved markets)" as a historical fact, but the system has no way to *verify* that going forward; we just trust the count.
- **Impact**: A real bug in `discover_and_persist_markets` (e.g., gamma rate-limits and we silently fall back to an empty fetch) would manifest as "more positions dropped" — but the operator has no per-cid trace to investigate. Worse, signal eligibility silently undercounts trader_count for any market that's missing because of this filter.
- **Suggested fix**: When a position is dropped for unknown-market, write a row to a small `dropped_positions_audit` table (proxy_wallet, condition_id, dropped_at). Auto-purge after 7 days. Even just exposing the cid set in the cycle log would help triage.

### 9. JIT discovery drops markets whose embedded event we couldn't refetch
- **File**: `app/services/market_sync.py:285-324`
- **Finding**: `discover_and_persist_markets` builds `event_ids` from `m.raw["events"][0]["id"]`. It then calls `pm.get_events_by_ids(sorted(event_ids))` and sets `events_by_id`. For each fetched market, the event is linked **only if the event_id was successfully refetched**. If gamma's `/events?id=...` silently drops one of the requested event ids (which it can — it's a list endpoint with a default limit of 20 unless we override, which we do, so this is mitigated; but partial responses are possible), the corresponding markets get persisted with `event_id=None`. Those markets then have no category and only ever appear in "Overall."
- **Impact**: Category-filtered signal lenses (politics, sports, crypto, etc.) silently miss markets whose parent event refetch glitched. The user clicks "Politics" and sees a smaller set than they should.
- **Suggested fix**: When an expected event_id doesn't come back from `get_events_by_ids`, log a WARNING with the missing ids. Optionally: synthesize an event row from the embedded event payload (`m.raw["events"][0]`) — it has slug/title/closed even if it lacks `tags`. The category will be NULL but the FK link survives, and a future market_sync pass can backfill tags.

### 10. Tenacity retries 4xx terminal errors
- **File**: `app/services/polymarket.py:83-105`
- **Finding**: `retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError))` matches every HTTPStatusError, including 400 (bad params), 401, 403, 404. The code DOES log `"client error %d on %s"` for any 4xx and then raises — but tenacity catches the raise and retries 3 more times. Each retry burns a rate-limit token and ~0.5–8s of backoff for a request that will never succeed.
- **Impact**: A single 400 (e.g., bad orderBy capitalization) becomes 4× a 400 → 4× rate-limit consumption. With ~530 wallet refreshes per cycle, even a 1% bad-request rate adds noticeable rate-limit pressure. More importantly the warning logs become noisy and operators stop reading them.
- **Suggested fix**: Branch retry behavior: 429 + 5xx → retry; 4xx other than 429 → raise immediately, do not retry. Use `retry_if_exception(lambda e: isinstance(e, TransportError) or (isinstance(e, HTTPStatusError) and e.response.status_code in (429, *range(500,600))))`.

### 11. Retry ignores `Retry-After` header on 429
- **File**: `app/services/polymarket.py:83-105`
- **Finding**: `wait_exponential(multiplier=0.5, min=0.5, max=8)` waits up to 8s between attempts regardless of what the server tells us. Polymarket's CLOB and gamma do return `Retry-After` on rate-limit responses; we ignore it. Our default token-bucket rate is also conservatively pre-set, so this is unlikely to trigger today, but burst events (CLOB during a major news catalyst) could hit it.
- **Impact**: Retry storm if the server is asking for a longer cooldown than 8s. Increases the chance of cascading 429s.
- **Suggested fix**: Custom wait callable that reads `Retry-After` from `e.response.headers` when present; falls back to exponential otherwise.

### 12. YES/NO token assignment from `clob_token_ids[0]/[1]` is unverified
- **File**: `app/services/market_sync.py:104-106, 321-322`, `app/services/polymarket_types.py:217`
- **Finding**: We always treat `clob_token_ids[0]` as YES and `clob_token_ids[1]` as NO. The spike findings mention `outcomes` and `outcomePrices` but never validate that the order of `clobTokenIds` is parallel to `outcomes`. Polymarket's gamma response carries `outcomes: ["Yes", "No"]` (or sometimes "No" first?) and `clobTokenIds` in some order — we just guess.
- **Impact**: If a market ships with `outcomes: ["No", "Yes"]` (it can — sports markets sometimes order by team name or by negation), we look up the wrong token's orderbook. `signal_entry_offer` becomes the price of the WRONG side. Every downstream calculation (paper trade entry, P&L, B1 exit bid, B4 snapshot) is on the wrong token. This is a silent, market-specific corruption.
- **Suggested fix**: Pair `clob_token_ids` with `outcomes`. Find the index where `outcomes[i].lower() == "yes"` and use `clob_token_ids[i]` as the YES token. If `outcomes` is empty or has no "yes", flag the market as non-binary and skip. Add a smoke test on a real fetched market that asserts `outcomes` and `clob_token_ids` are paired correctly.

### 13. Counterparty pool depth (top_n=50) ≠ position-refresh depth (top_n=100)
- **File**: `app/scheduler/jobs.py:185, 477`
- **Finding**: `POSITION_REFRESH_TOP_N = 100` (depth tracked per category). `LOG_SIGNALS_TOP_N = 50` is used both for signal detection AND for the counterparty pool. Wallets ranked 51–100 ARE being position-refreshed but are NOT in the counterparty tracked-pool union.
- **Impact**: A signal can fire whose contributing wallets are ranked 1–50, but the counterparty check fails to flag a wallet ranked 60 selling on the other side — even though that wallet is in our tracked universe. Inconsistent definition of "smart money."
- **Suggested fix**: Use `POSITION_REFRESH_TOP_N` (or whatever the broadest tracked depth is) as the pool depth for the counterparty check. The check is meant to be inclusive — fewer false negatives outweigh the cost of one slightly larger SQL.

---

## Medium

### 14. Token bucket is shared across hosts, but per `PolymarketClient` instance
- **File**: `app/services/polymarket.py:55-58`, `app/services/rate_limiter.py:9-37`
- **Finding**: Each `PolymarketClient()` instantiation creates a fresh `TokenBucket`. The composed cycle (`refresh_positions_then_log_signals`) creates separate `PolymarketClient` instances per phase via `async with PolymarketClient() as pm:`. Within a phase the bucket is shared across all concurrent fetches (correct). Across phases each client gets its own bucket. If two phases run concurrently (they don't today; the cycle is sequential under `job_lock`), they'd burst above the limit. Also: a single bucket spans all three host families (data-api, gamma-api, clob-api), which is conservative — fine, just worth noting.
- **Impact**: Today: low risk (sequential phases). Future risk if anyone parallelizes phases or runs `scripts/run_*` concurrently with the scheduler — they would each get their own bucket and double the effective rate.
- **Suggested fix**: Module-level `TokenBucket` keyed by host (or a single global bucket, since all three hosts share infrastructure). Document the intent.

### 15. `iter_trades` / `iter_events` pagination has no sanity bound
- **File**: `app/services/polymarket.py:185-198, 303-331`
- **Finding**: `iter_trades` paginates until an empty page. There is no `max_pages` safety net (unlike `iter_events`). For a wallet with mis-implemented offset logic on Polymarket's side, or one with > 50k trades (Theo4-class), this could iterate indefinitely.
- **Impact**: Low-probability runaway. Worst-case ~minutes of API consumption before any human notices.
- **Suggested fix**: Add a `max_pages` parameter to `iter_trades` mirroring `iter_events`. Default to something like 50 pages (25k trades) which is more than enough for V1.

### 16. `get_prices_history` interval bug (open question #2 from session-state)
- **File**: `app/services/polymarket.py:335-344`
- **Finding**: Spike findings note `interval=1d` returns 1440 minute-points, not daily candles. The function defaults to `interval="1d"` and we haven't validated `1h`/`1m`/`max`. Today not called in the active code paths I reviewed, but it's a public method that future work will hit.
- **Impact**: Latent footgun. If B4 or any drift-label feature uses this, output is wrong-granularity.
- **Suggested fix**: Validate the interval taxonomy against the live API once. Document in docstring. Consider renaming the default to `"max"` (most useful for resolved/half-life work) or removing the default to force a conscious choice.

### 17. `_infer_resolved_outcome` returns None for non-standard binary labels
- **File**: `app/services/market_sync.py:191-200`
- **Finding**: The function only matches outcome labels exactly equal to `"yes"` or `"no"` (lowercased). Markets with labels like `"Yes (5+ goals)"`, `"Yes - Trump"`, `"Trump wins"` resolve at $1.00 but the function returns None (treated as "not resolved" by callers). Result: `resolved_outcome` stays NULL forever for these markets.
- **Impact**: Backtest silently excludes any market with a non-standard binary outcome label. The exclusion is biased — politics and sports are over-represented in custom-label markets — so the backtest's category-level edge estimate is biased toward markets with vanilla "Yes"/"No" labels.
- **Suggested fix**: When neither outcome matches yes/no, infer from price: `outcome[i] resolved at 1.0 → resolved_outcome = "WIN_<i>"` for non-binary, or treat the binary-but-custom-label case via "find the side with price ~ 1.0 and map to YES if that side was originally the long side." Practically: if the market has exactly two outcomes and one is at 1.0, use the position's `outcome` field at signal-fire time to determine YES/NO mapping. At minimum, log a warning so the operator sees how many markets are silently NULL'd.

### 18. `_capture_book_for_signal` writes "unavailable" on missing token but not on book-fetch failure
- **File**: `app/scheduler/jobs.py:400-443`
- **Finding**: When `token_id` is missing (multi-outcome / weird market), we explicitly write an `available=False` BookMetrics. When the book fetch raises, we catch + log + set `book = None`, and `compute_book_metrics(None, ...)` correctly returns `available=False`. BUT — when the API returns 200 with an empty `bids:[]/asks:[]` (resolved markets that haven't been delisted yet, e.g.), `compute_book_metrics` returns `available=False` correctly. The `signal_entry_offer` is therefore NULL and backtest excludes the row. No alert anywhere distinguishes "thin book on real market" from "fetch failed." Operationally undiagnosable.
- **Impact**: Signals fire on real markets but get marked "unavailable" and never enter the backtest universe. The backtest sample size shrinks invisibly.
- **Suggested fix**: Persist the failure reason as a column (e.g., `book_capture_status` ∈ `ok`, `unknown_token`, `fetch_failed`, `empty_book`). Easy to add and pays for itself the first time the operator wonders why their signal count seems low.

### 19. `outcome_prices` parsing skips non-numeric without flagging the market
- **File**: `app/services/polymarket_types.py:219-220`
- **Finding**: `prices = [float(p) for p in prices_raw if p not in (None, "")]`. If `outcomePrices = ["1.0", "garbage"]`, the result is `[1.0]` of length 1 — but `outcomes` length is still 2. The downstream `_infer_resolved_outcome` checks `len(outcomes) != len(outcome_prices)` and returns "VOID" — OK. But silently corrupting a market with one ~legit price + one bad string into a single-element list is a footgun. Better to fail loud or filter both arrays in lockstep.
- **Impact**: Edge case, low probability today. Worth tightening for robustness.
- **Suggested fix**: Either keep arrays in lockstep (`prices = [float(p) if numeric else None for p in prices_raw]`) or log a warning when filtering shrinks the array.

### 20. `discover_and_persist_markets` shrinks input set by skipping cids without an embedded event
- **File**: `app/services/market_sync.py:285-294`
- **Finding**: The loop only adds events to `event_ids` when `m.raw.get("events")` is non-empty. Markets without an embedded event get persisted with `event_id=None`. That's tolerable, but we never re-enrich them later — there's no nightly job that picks NULL-event markets and tries to discover their parent event.
- **Impact**: Markets that didn't have an embedded event at first-discovery time are stuck without a category permanently. Same downstream effect as #9.
- **Suggested fix**: Add a nightly batch (or fold into `sync_active_markets`) that selects `markets WHERE event_id IS NULL AND closed = FALSE` and calls a different gamma endpoint (`/markets/{id}` with `include=events`) or queries `/events?slug=...` to backfill.

### 21. `fetched = fetched + fetched_closed` does not de-dup
- **File**: `app/services/market_sync.py:275`
- **Finding**: After active and closed sweeps, the two lists are concatenated. If gamma returns the same cid in both responses (transition state — market just closed during the call), we'd write the market twice in the loop below. The `crud.upsert_market` is idempotent so it's not corrupting, but it does waste DB time and mask diagnostic logs (`written_markets` over-counts).
- **Impact**: Low; cosmetic.
- **Suggested fix**: De-dup by `condition_id` before iterating: `seen = set(); fetched = [m for m in fetched + fetched_closed if not (m.condition_id in seen or seen.add(m.condition_id))]`.

### 22. Position refresh uses asyncio.create_task without bounded concurrency at task creation
- **File**: `app/scheduler/jobs.py:269`
- **Finding**: `tasks = [asyncio.create_task(fetch_one(w)) for w in wallets]` creates ALL tasks immediately for ~530–1000 wallets. Each task `await sem` inside fetch_one — so only 12 run at once, but 988 are sitting in pending state. Memory: small (a coroutine each). Real issue: no `cancel()` on shutdown. If the scheduler stops mid-cycle (Ctrl-C, SIGTERM during deploy), these tasks leak until process exit. Less critical on Railway's container model but ugly.
- **Impact**: Mostly cosmetic; minor memory + ungraceful shutdown.
- **Suggested fix**: Use a bounded `asyncio.gather(*[asyncio.wait_for(...)])` with a shared sem, or a worker-pool pattern. Or wrap the cycle in a try/finally that cancels remaining tasks.

### 23. `daily_leaderboard_snapshot` holds a single DB connection through the entire 28-combo run
- **File**: `app/scheduler/jobs.py:140-161`
- **Finding**: The whole 28-combo loop runs inside `async with pool.acquire() as conn:`. Each combo is a sequential `pm.get_leaderboard(...)` (paged 50→100 = 2 calls) + a transaction. With max_size=12, this hogs one connection for ~3-5 minutes. Other concurrent callers have 11 to share, fine in practice — but if a combo's API hangs, the connection sits idle in pool.
- **Impact**: Minor connection-pool pressure. Operationally fine.
- **Suggested fix**: Re-acquire per combo. Cheap, isolates failures, and lets pool reclaim faster.

### 24. `r.json()` not wrapped in try/except
- **File**: `app/services/polymarket.py:105`
- **Finding**: A 200 response with non-JSON body (rare; server misconfig) raises `json.JSONDecodeError`, which is NOT in the tenacity retry types — so it bubbles up immediately. For per-wallet calls this gets caught by `_fetch_one_wallet`'s broad `except Exception` and the wallet is marked failed; for `get_leaderboard` it kills the snapshot for that combo.
- **Impact**: Low probability; failure mode is "loud," not silent. So it's actually fine — just inconsistent with how other shape errors are handled.
- **Suggested fix**: Either catch + retry on JSONDecodeError, or document that JSON-parse failure is treated as a hard error.

---

## Low / Nits

### 25. USDC values stored as `float`, not `Decimal`
- **File**: `polymarket_types.py` (size, usdc_size, value, pnl, vol throughout)
- **Finding**: Float roundoff is small at $25k thresholds, but accumulating sums (`portfolio_total = sum(...)`, `aggregate_usdc`) drift. Postgres NUMERIC(14,2) absorbs the rounding on write. CLAUDE.md doesn't mandate Decimal; it's a personal V1 tool and the math is signal-tier, not accounting-tier. Worth flagging only.

### 26. `iter_events` has no inter-page yield throttling
- **File**: `polymarket.py:303-331`
- **Finding**: Pages run back-to-back; only the rate limiter spaces actual requests. Fine.

### 27. `LeaderboardEntry.from_dict` swallows non-int rank as 0
- **File**: `polymarket_types.py:71-74`
- **Finding**: `rank=0` for malformed input. Sorting by rank later will put malformed rows at the top. Low impact (Polymarket's API is consistent), but consider `rank=10**9` to push them to the bottom, or reject the row.

### 28. `LeaderboardEntry.from_dict` lower-cases proxy_wallet but `Position.from_dict` does the same — good. Trade.from_dict also does. But the `Trade` raw dict still has the un-lowered `proxyWallet`, so anywhere we read `t.raw["proxyWallet"]` (search for usages) we'd hit a casing mismatch.
- **File**: `polymarket_types.py:159, 117, 77`
- **Suggested fix**: Lowercase once at the boundary (already done on the dataclass field). Avoid reaching into `raw` for wallet addresses anywhere else.

### 29. `get_orderbook` returns the raw dict — callers pass it to `compute_book_metrics`. The hash field `raw_response_hash` is computed from `json.dumps(book or {}, sort_keys=True, default=str)`. Two structurally-equivalent books with different float string representations could hash differently.
- **File**: `orderbook.py:70-71`
- **Finding**: Cosmetic; the hash is for "did the book change since last snapshot?" not for security. Float representation jitter could spuriously suggest a change. Low priority.

### 30. `_parse_iso` returns None on parse failure but caller (e.g. cutoff check) treats None as "no cutoff" → full sync. Mostly fine but the silent fallback could mask a malformed `updatedAt` from gamma.
- **File**: `market_sync.py:77-84, 399-407`
- **Suggested fix**: Log a WARNING on parse failure rather than silently degrading.

### 31. `concurrency` parameter on `refresh_top_trader_positions` is `12`, but the DB pool max is also `12` — phase 3's per-wallet `pool.acquire()` runs sequentially, so OK. If anyone parallelizes phase 3 to match phase 1 concurrency, they'd starve the pool. Worth a comment.

### 32. `get_clob_trades` catches only HTTPStatusError — not TransportError
- **File**: `polymarket.py:357-363`
- **Finding**: A network blip raises `httpx.TransportError` which propagates. Caller (`check_and_persist_counterparty_warning`) does its own broad `except Exception` (line 90), so it's caught — but inconsistent with `get_orderbook` which catches HTTPStatusError too. Make them consistent.

### 33. `Event.from_dict` reads `markets_raw = d.get("markets") or []`. Fine. But the embedded markets don't carry the parent event's `tags` / `category` — and for some downstream paths we pass these embedded markets straight to upsert without re-fetching the event. Already mitigated in `discover_and_persist_markets` by the explicit events refetch. Worth a docstring note on `Event.markets` that they're "thin" for category purposes.

---

## Empty sections

None — every priority bucket has content.

## Things that genuinely look good

- Rate limit acquired INSIDE the retry attempt (`polymarket.py:96`) — explicitly addresses the A6 fix and is the right call.
- The two-pass JIT discovery (active then closed=true) in `discover_and_persist_markets` is solid; the unrecovered-cids warning is the right level.
- Time handling is consistently `datetime.now(timezone.utc)` and aware — no naive-vs-aware bugs spotted.
- Idempotency on `daily_leaderboard_snapshot` looks correct (UNIQUE on the snapshot key + DO NOTHING).
- The catch-up logic for missed snapshot days (`catch_up_snapshot_if_stale`) honestly logs the unrecoverable-gap warning rather than pretending to backfill.
- `_filter_known_markets` exists at all (defensive FK guard) — that's a sign someone has been bitten by JIT-discovery races before.

