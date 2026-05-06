# API Spike Findings — Step 0

**Run date:** 2026-05-04
**Goal:** Validate Polymarket public endpoints before locking architecture.

---

## Summary

All endpoints needed for V1 are confirmed working. Three architectural decisions are now data-driven (not assumptions) and one prior assumption was wrong.

---

## 1. Leaderboard endpoint — CRACKED (revised)

**The proper endpoint** (discovered 2026-05-04 via DevTools inspection of polymarket.com/leaderboard):

```
GET https://data-api.polymarket.com/v1/leaderboard
  ?timePeriod = day | week | month | all
  &orderBy    = VOL | PNL          (uppercase only — PROFIT, profit, volume → 400)
  &limit      = int (silently capped at 50)
  &offset     = int (verified to depth 2000+)
  &category   = overall | politics | sports | crypto | culture | tech | finance
```

**Response shape per row:**
```
{ rank, proxyWallet, userName, xUsername, verifiedBadge, vol, pnl, profileImage }
```

Every row carries BOTH `vol` and `pnl` regardless of how the leaderboard is sorted — so a single call produces the data needed for either ranking mode. `rank` arrives as a string; we coerce to int.

**Pagination:** `limit` capped at 50 per call but `offset` paginates as deep as you want. To get top 100, fetch offset=0 and offset=50 (2 calls). Verified depth to rank 2001.

**Confirmed categories** (7): `overall`, `politics`, `sports`, `crypto`, `culture`, `tech`, `finance`. Other category labels visible in the UI navbar (`geopolitics`, `esports`, `economy`, `iran`, etc.) return 400 on this endpoint — likely those are tag-based event filters, not leaderboard categories.

**Top-3 all-time by PNL (sanity check):**
1. Theo4 — `0x56687bf447db6ffa42ffe2204a05edaa20f55839` — $22.05M
2. Fredi9999 — `0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf` — $16.6M
3. kch123 — `0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee` — $12.1M

### Legacy / deprecated

`lb-api.polymarket.com/{profit,volume}` (window={1d|7d|30d|all}) — older/legacy endpoint, hard-capped at 50 entries, no pagination, no category filter, no per-entry rank/pnl/vol. **Do not use.** We dropped `LB_API_BASE` from config.

**Architectural impact:** unblocks the top-100 slider entirely. We can paginate to any depth, filter by 7 categories, and sort by VOL or PNL with one consistent endpoint shape.

---

## 2. Proxy wallet vs EOA gotcha — confirmed real, but easy to handle

The journalism-cited Theo addresses I initially tried (`0x55c66f43...` and `0x9d84ce03...`) returned **empty arrays** from `data-api`. The leaderboard's authoritative proxy wallet for "Theo4" is `0x56687bf447db6ffa42ffe2204a05edaa20f55839`. With that, `/positions`, `/value`, `/activity`, `/trades` all return real data.

**Architectural impact:** we always source proxy addresses from `lb-api`'s `proxyWallet` field, never from external sources. Skip the EOA→proxy resolution layer entirely; lb-api already gives proxies.

---

## 3. Categories live on EVENTS, not markets

`gamma-api.polymarket.com/markets` returns `category=null` and `tags=null` on every market sampled (100 active markets surveyed, zero categories). The category lives on `/events`:

```json
{ "title": "NBA: Will the Mavericks beat...", "category": "Sports", ... }
```

Each event contains a `markets` array of its child markets.

**Architectural impact (changes the plan):** to categorize a market, we must:
1. Either fetch via `/events` (then iterate `event.markets`) and tag each market with the event's category
2. Or maintain an event_id → category mapping and join when we ingest market positions

This is a small but real change. We were going to have a `category` column on `markets`; now it's a derived field via `event_id`. Easy fix.

---

## 4. Confirmed endpoints + shapes

### `gamma-api.polymarket.com`
- `GET /markets?limit=&offset=&closed=` ✅ pagination via offset works
- `GET /events?limit=&offset=` ✅ pagination via offset works, includes `category`, `tags`, and child `markets`
- Markets carry: `id`, `slug`, `conditionId`, `clobTokenIds` (string-encoded JSON array of two token IDs — YES and NO), `outcomes`, `outcomePrices` (string-encoded), `volume`, `volumeNum`, `liquidity`, `endDate`, `closed`, `bestBid`, `bestAsk`, `lastTradePrice`
- `clobTokenIds` and `outcomePrices` are JSON-encoded strings inside the JSON — must `json.loads` them

### `data-api.polymarket.com`
- `GET /positions?user={proxy}&limit=` ✅ returns current open positions (empty if user has none)
- `GET /value?user={proxy}` ✅ returns `[{ user, value }]` — total portfolio USDC value
- `GET /activity?user={proxy}&type=TRADE&limit=&offset=` ✅ raw activity feed
- `GET /trades?user={proxy}&limit=` ✅ **richer than `/activity`** — includes `title` (market question) and `slug` directly
- All accept `limit`/`offset` for pagination
- Empty response for invalid/inactive wallets is `[]` (200 OK), not 404 — must handle as "no data" not error

**Recommended endpoint priority for V1:**
- `/trades` for trade history (better metadata)
- `/positions` for current state
- `/value` for total portfolio value (denominator for portfolio-fraction signal metric)
- Skip `/activity` — `/trades` superset

### `clob.polymarket.com`
- `GET /markets` — returns `{ data: [...], next_cursor, count, limit }` with cursor-based pagination (different from gamma!)
- `GET /book?token_id=` — 404s on resolved markets, works on active
- `GET /price?token_id=&side=BUY|SELL` — current price
- `GET /prices-history?market={token_id}&interval=1d` — returns `{ history: [...] }`, empty for resolved markets

---

## 5. Things that surprised me

- `lb-api.polymarket.com/?` returns `{"data":"OK"}` — health check endpoint
- The CLOB `/markets` returns 1000 in one call — significantly larger page than gamma's default
- gamma's `endDateIso` field exists separately from `endDate`
- Resolved markets return `outcomePrices=["0", "0"]` (not the actual resolution price like `["1", "0"]` for YES wins). Resolution status must be checked via the `closed` flag or `umaResolutionStatuses` instead.

---

## 6. Open items (not blockers, but flag-worthy)

- **Rate limiting** — none observed during the spike (~50 calls in 30 seconds, all 200/400). No documented limit. Stick with the planned 10 req/s ceiling.
- **Leaderboard depth** — need to confirm how deep the leaderboard goes. We need top 100; if `lb-api` only returns top 50, we'd need a fallback. Sample dump showed 500+ entries, so likely fine.
- **`window=all` consistency** — the all-time leaderboard is dominated by 2024 election traders. By the time we add per-category, we'll naturally re-rank within sub-domains and this concern fades.
- **Categories taxonomy** — saw `Sports` confirmed. Need to enumerate the full set by sampling more events (will do during ranker build).

---

## Plan adjustments based on findings

| Original plan | Updated plan |
|---|---|
| Markets carry category directly | Categories derived from parent event |
| Resolve EOA → proxy via on-chain factory | Skip — leaderboard returns proxies natively |
| Use `/activity` for trade history | Use `/trades` (richer, has title/slug) |
| Leaderboard endpoint TBD | `data-api.polymarket.com/v1/leaderboard` (paginates to any depth, 7 categories, VOL or PNL sort) |
| Top-N capped at 50 (lb-api limit) | Top-N up to 100+ via offset pagination |

No rewrite needed — these are localized changes to `polymarket.py` and `db/models.py`.

---

## Verdict

**Step 0 complete. No blockers. Architecture is safe to build on top of these endpoints.** Proceed to Step 1 (`polymarket.py` API wrapper + rate limiter).
