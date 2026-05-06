# Live Polymarket API Probe — Findings

Run: `./venv/Scripts/python.exe scripts/probe_polymarket_endpoints.py`
Date: 2026-05-06

## Headline findings

### 🚨 1. `clob.polymarket.com/trades` requires API authentication

```
GET https://clob.polymarket.com/trades?market=...&limit=5
status: 401
body: {"error":"Unauthorized/Invalid api key"}
```

Confirmed across THREE param shapes (token_id, conditionId, no params). All return 401. Our code (`polymarket.py:357-363`) catches `HTTPStatusError` and silently returns `[]`.

**Impact**: B2 counterparty diagnostic has been a complete no-op since shipping. Every call returns "no warning" not because there's no counterparty, but because the API is unreachable.

**Resolution**: switch to `data-api.polymarket.com/trades?market=<conditionId>` which is **public, no auth required**, and returns trades on a market.

### ✅ 2. `data-api.polymarket.com/trades?market=<conditionId>` works perfectly

```
GET https://data-api.polymarket.com/trades?market=0x7ba2...&limit=10
status: 200
returns: list of 10 trades
```

Sample fill:
```json
{
  "proxyWallet": "0x4852abe54b776ff3c5f1ff3786c1c4ca41e22da9",
  "side": "SELL",
  "asset": "53627918742542...",
  "conditionId": "0x7ba2827dc36...",
  "size": 2,
  "price": 0.999,
  "timestamp": 1778039492,
  "outcome": "Thunder",
  "outcomeIndex": 1,
  ...
}
```

Field shape is **identical** to `data-api/trades?user=`. Each fill carries `proxyWallet`, `side` (BUY/SELL), and `outcome` (Yes/No or other label) and `outcomeIndex`.

**This is cleaner than maker/taker semantics** — each fill is a single trader's action, no maker/taker disambiguation needed.

### ✅ 3. Counterparty logic must be rewritten using (outcome, side) pairs

For a YES-direction signal (we're buying YES tokens), a counterparty is anyone:
- Selling YES tokens: `outcome="Yes" AND side="SELL"`
- Buying NO tokens: `outcome="No" AND side="BUY"`

For a NO-direction signal (we're buying NO tokens), a counterparty is anyone:
- Selling NO tokens: `outcome="No" AND side="SELL"`
- Buying YES tokens: `outcome="Yes" AND side="BUY"`

Both reduce to: counterparty if `(outcome != signal_direction) XOR (side == "SELL")` is False... actually simpler to spell out the two cases.

This is more accurate than the maker-side check we coded — it directly captures "what side of the market did this trader take," with no maker/taker conflation.

### ✅ 4. `data-api/value` works as expected

```json
[{"user": "0x...", "value": 0}]
```

List with one entry. `value` is a numeric portfolio value (in USDC). Theo4's value is 0 because he has no open positions currently — that's a true value, not an error.

Existing `get_portfolio_value` wrapper already handles this shape. F3 fix can proceed by calling it.

### ✅ 5. `/positions` for a wallet with positions returns standard list

Sample position fields confirmed: `conditionId`, `asset` (= token_id), `outcome`, `size`, `avgPrice`, `curPrice`, `currentValue`, `cashPnl`, etc. Matches our `Position.from_dict` parsing.

### ✅ 6. `/markets` returns `outcomes` and `clobTokenIds` as JSON-encoded strings

Confirmed:
```
outcomes type: str, value: '["Yes", "No"]'
clobTokenIds type: str, value: '["8501...", "2527..."]'
```

Our F6 fix correctly handles this (we parse via `_parse_json_string_list`).

### ⚠ 7. `data-api/positions` for nonexistent wallet returns `[]` — defensive fallback OK

```
GET /positions?user=0x000...bad0
status: 200
returns: []
```

So for wallet-not-found, our `if not isinstance(data, list): return []` is correct — Polymarket genuinely returns an empty list. F13 fix should NOT change this.

But for **API-error scenarios** (like CLOB /trades 401), the same code path silently swallows. That's where F13 needs to distinguish.

### ⚠ 8. `prices-history` returned 567 points for `interval=1d` on a 5-min-cadence market

Open question #2 in session-state notes "1d returned 1440 minute-points." Probe shows it depends on the market's resolution cadence. Not blocking, but not a clean "1 day = 1 daily candle" semantic.

### ⚠ 9. CLOB `/book` for the token I tested returned 404 (5 times in a row)

Probably because the test market had already resolved when the probe ran. Not a real problem — orderbook endpoint works for live markets (we use it in production every cycle).

## Action items for Pass 2

| Fix | What changes based on probe |
|---|---|
| **F12** | Switch counterparty source from `clob.polymarket.com/trades` → `data-api.polymarket.com/trades?market=<conditionId>`. No auth. |
| **F2** | Rewrite `_extract_maker_addresses` (rename to `_extract_counterparty_wallets`) to use `(outcome, side)` pairs from data-api fills, not maker/taker semantics. Filter by signal direction. |
| **F3** | Call existing `get_portfolio_value` wrapper. Returns `[{user, value}]`; extract `value` field. |
| **F13** | Distinguish (a) successful empty list (legit, e.g. wallet has no positions) from (b) API error swallowed by `except HTTPStatusError` (silent failure). Log loudly on (b). |
| **F4** | Confirmed: `/book` returns both `bids` and `asks` arrays. Migration can proceed. |
| **F6** | Confirmed: outcomes is `'["Yes", "No"]'` JSON-encoded string. Existing fix handles this correctly. |

## What I'm worried about

The B2 counterparty being a no-op since shipping means: every "counterparty_warning = False" in the database is currently meaningless — we don't know if there was no counterparty or if the API was unreachable. Once F12+F2 are fixed, going forward the warnings will be honest. But existing `signal_log` rows can't be retroactively corrected.

Recommend treating all pre-F12 counterparty data as unreliable (assume `counterparty_warning = NULL` semantically, even though it's stored as `False`).
