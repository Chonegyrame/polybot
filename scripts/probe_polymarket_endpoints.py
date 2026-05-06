"""Probe live Polymarket API endpoints to verify our code's assumptions.

Run before relying on any data ingestion code. Manually inspect the output
and confirm field names, response shapes, and side semantics match what our
parsing code expects. Findings should drive F12 / F13 fixes.

Usage:
  ./venv/Scripts/python.exe scripts/probe_polymarket_endpoints.py

What this probes (one section per endpoint we use):
  1. data-api /v1/leaderboard          (verify shape + grab a real wallet)
  2. data-api /positions               (verify positions shape)
  3. data-api /trades?user=            (verify wallet-trades shape)
  4. data-api /value                   (F3: verify portfolio value endpoint)
  5. gamma-api /markets                (verify outcomes/clobTokenIds shape)
  6. clob /book                        (F4: verify bid + ask available)
  7. clob /trades?market=              (F2/F12: verify side field semantics)
  8. clob /prices-history              (sanity check)
  9. EDGE: nonsense wallet             (F13: empty list vs error response)
 10. EDGE: nonsense market for trades  (F13: same for clob /trades)

Treat warnings (yellow markers) as items to investigate before relying on
the affected code path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


WARN = "[WARN]"
OK = "[OK]"
INFO = "[INFO]"


def hr(label: str) -> None:
    print(f"\n{'=' * 80}\n  {label}\n{'=' * 80}")


def dump(data: Any, max_chars: int = 2000) -> None:
    s = json.dumps(data, indent=2, default=str)
    if len(s) > max_chars:
        print(s[:max_chars])
        print(f"  ... [truncated; {len(s)} total chars]")
    else:
        print(s)


def show_shape(data: Any) -> None:
    if isinstance(data, list):
        print(f"  Type: list (len={len(data)})")
        if data and isinstance(data[0], dict):
            print(f"  First item keys: {sorted(data[0].keys())}")
    elif isinstance(data, dict):
        print(f"  Type: dict")
        print(f"  Top-level keys: {sorted(data.keys())}")
    else:
        print(f"  Type: {type(data).__name__}, value: {data!r}")


async def get_json(
    client: httpx.AsyncClient, url: str, params: dict | None = None,
) -> Any:
    print(f"\nGET {url}")
    if params:
        print(f"  params: {params}")
    try:
        r = await client.get(url, params=params)
    except httpx.RequestError as e:
        print(f"  {WARN} request failed: {e}")
        return None
    print(f"  status: {r.status_code}")
    print(f"  content-type: {r.headers.get('content-type', 'unknown')}")
    if r.status_code >= 400:
        print(f"  body[:300]: {r.text[:300]}")
        return None
    try:
        data = r.json()
    except json.JSONDecodeError as e:
        print(f"  {WARN} JSON parse failed: {e}")
        print(f"  body[:300]: {r.text[:300]}")
        return None
    return data


async def main() -> None:
    print("Polymarket live API probe")
    print(f"  data-api: {settings.data_api_base}")
    print(f"  gamma-api: {settings.gamma_api_base}")
    print(f"  clob-api: {settings.clob_api_base}")

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ====================================================================
        # 1. LEADERBOARD
        # ====================================================================
        hr("1. data-api /v1/leaderboard  (verify shape + grab a real wallet)")
        url = f"{settings.data_api_base}/v1/leaderboard"
        params = {
            "category": "overall",
            "timePeriod": "all",
            "orderBy": "PNL",
            "limit": 5,
            "offset": 0,
        }
        data = await get_json(client, url, params)
        show_shape(data)

        real_wallet = ""
        if isinstance(data, list) and data:
            print("\n  First entry sample:")
            dump(data[0])
            real_wallet = (
                data[0].get("proxyWallet")
                or data[0].get("address")
                or data[0].get("user")
                or ""
            ).lower()
            print(f"\n  {INFO} Will use wallet: {real_wallet[:20]}...")
        else:
            print(f"  {WARN} Leaderboard returned non-list or empty.")
            return

        # ====================================================================
        # 2. POSITIONS — try several leaderboard wallets until one has open positions
        # ====================================================================
        hr("2. data-api /positions  (iterate top wallets until one has positions)")
        url = f"{settings.data_api_base}/positions"
        real_cid = ""
        real_token = ""
        for entry in data[:10]:
            w = (entry.get("proxyWallet") or "").lower()
            if not w:
                continue
            print(f"\n  trying wallet: {w[:20]}... ({entry.get('userName', '?')})")
            pos_data = await get_json(client, url, {"user": w})
            if isinstance(pos_data, list) and pos_data:
                print(f"  {OK} got {len(pos_data)} positions")
                print("\n  First position sample:")
                dump(pos_data[0])
                real_wallet = w
                real_cid = pos_data[0].get("conditionId") or ""
                real_token = pos_data[0].get("asset") or ""
                print(f"\n  {INFO} Will use wallet: {real_wallet[:20]}...")
                print(f"  {INFO} Will use cid: {real_cid[:20]}...")
                print(f"  {INFO} Will use token: {real_token[:20]}...")
                break
        else:
            print(f"  {WARN} None of the top 10 wallets have open positions. Cannot probe downstream endpoints.")

        # ====================================================================
        # 3. TRADES BY USER
        # ====================================================================
        hr("3. data-api /trades  (by user)")
        url = f"{settings.data_api_base}/trades"
        data = await get_json(client, url, {"user": real_wallet, "limit": 5})
        show_shape(data)
        if isinstance(data, list) and data:
            print("\n  First trade sample:")
            dump(data[0])

        # ====================================================================
        # 4. PORTFOLIO VALUE  (F3)
        # ====================================================================
        hr("4. data-api /value  (F3 needs this for portfolio-fraction denominator)")
        url = f"{settings.data_api_base}/value"
        data = await get_json(client, url, {"user": real_wallet})
        show_shape(data)
        print("\n  Full response:")
        dump(data, max_chars=1500)

        # ====================================================================
        # 5. MARKETS
        # ====================================================================
        hr("5. gamma-api /markets")
        url = f"{settings.gamma_api_base}/markets"
        data = await get_json(client, url, {"limit": 1, "closed": "false"})
        show_shape(data)
        if isinstance(data, list) and data:
            m = data[0]
            print("\n  First market sample (truncated):")
            dump(m, max_chars=1800)
            outcomes_raw = m.get("outcomes")
            tokens_raw = m.get("clobTokenIds")
            print(f"\n  outcomes type: {type(outcomes_raw).__name__}, value: {outcomes_raw!r}")
            print(f"  clobTokenIds type: {type(tokens_raw).__name__}, value: {tokens_raw!r}")
            print(f"  {INFO} Our code expects both as JSON-encoded strings; verify above.")

        # ====================================================================
        # 6. CLOB BOOK  (F4: verify bid + ask both available)
        # ====================================================================
        if real_token:
            hr("6. clob /book  (F4: verify bid + ask both available in same response)")
            url = f"{settings.clob_api_base}/book"
            data = await get_json(client, url, {"token_id": real_token})
            show_shape(data)
            if isinstance(data, dict):
                print("\n  Full response (truncated):")
                dump(data, max_chars=1500)
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                print(f"\n  bids count: {len(bids)}")
                print(f"  asks count: {len(asks)}")
                if bids:
                    print(f"  best bid: {bids[0]}")
                if asks:
                    print(f"  best ask: {asks[0]}")
                if bids and asks:
                    print(f"  {OK} Both bid + ask available — F4 fix can proceed")

        # ====================================================================
        # 7. CLOB TRADES  (F2/F12 — CRITICAL)
        # ====================================================================
        if real_token:
            hr("7. clob /trades  (F2/F12 — verify side field semantics)")
            url = f"{settings.clob_api_base}/trades"

            # --- Try our current param convention: market=token_id ---
            print(f"\n  --- Try A: market=<token_id> (our current code's convention) ---")
            data_a = await get_json(client, url, {"market": real_token, "limit": 10})
            show_shape(data_a)
            if isinstance(data_a, list) and data_a:
                print(f"\n  {OK} Returns a list with {len(data_a)} fills")
                print(f"\n  First 3 fills (look for side / maker_side / taker_side fields):")
                for i, fill in enumerate(data_a[:3]):
                    print(f"\n  --- Fill {i} ---")
                    dump(fill, max_chars=1000)
                # Field-presence audit
                fields_present = set()
                side_values = set()
                maker_side_values = set()
                for f in data_a[:20]:
                    fields_present.update(f.keys())
                    if "side" in f:
                        side_values.add(str(f["side"]))
                    if "maker_side" in f:
                        maker_side_values.add(str(f["maker_side"]))
                    if "maker_order_side" in f:
                        maker_side_values.add(str(f["maker_order_side"]))
                print(f"\n  All fields seen across first 20 fills: {sorted(fields_present)}")
                print(f"  Distinct 'side' values: {sorted(side_values)}")
                print(f"  Distinct 'maker_side'/'maker_order_side' values: {sorted(maker_side_values)}")
                if not side_values and not maker_side_values:
                    print(f"  {WARN} Neither 'side' nor 'maker_side' fields present!")
                    print(f"  {WARN} Our F2 fix has nothing to filter by — every fill returns None and gets excluded.")
                if "maker_address" not in fields_present and "maker" not in fields_present:
                    print(f"  {WARN} No 'maker_address' or 'maker' field — counterparty check has nothing to extract.")
            elif isinstance(data_a, dict):
                print(f"  {WARN} Returns dict, not list. Inner keys: {list(data_a.keys())}")
                inner = data_a.get("data") or data_a.get("trades") or data_a.get("fills")
                if isinstance(inner, list):
                    print(f"  Inner list len: {len(inner)}")
                    if inner:
                        print(f"  Sample first inner item:")
                        dump(inner[0], max_chars=800)
            else:
                print(f"  {WARN} Returned: {data_a!r}")

            # --- Try alternative: market=conditionId ---
            if real_cid:
                print(f"\n  --- Try B: market=<conditionId> (alternative convention) ---")
                data_b = await get_json(client, url, {"market": real_cid, "limit": 5})
                show_shape(data_b)

            # --- Try with no params ---
            print(f"\n  --- Try C: no params (does the endpoint exist at all?) ---")
            data_c = await get_json(client, url)
            show_shape(data_c)

        # ====================================================================
        # 7b. DATA-API /trades?market=  — alternative for B2 if CLOB /trades is gated
        # ====================================================================
        if real_cid or real_token:
            hr("7b. data-api /trades?market=  (no-auth alternative for B2 counterparty)")
            url = f"{settings.data_api_base}/trades"

            for label, params in [
                ("market=<conditionId>", {"market": real_cid, "limit": 10}),
                ("market=<token_id>", {"market": real_token, "limit": 10}),
                ("asset=<token_id>", {"asset": real_token, "limit": 10}),
            ]:
                if not list(params.values())[0]:
                    continue
                print(f"\n  --- Try: {label} ---")
                d = await get_json(client, url, params)
                show_shape(d)
                if isinstance(d, list) and d:
                    print(f"  {OK} returns {len(d)} fills")
                    print(f"  Sample first fill:")
                    dump(d[0], max_chars=600)
                    fields = set()
                    for f in d[:10]:
                        fields.update(f.keys())
                    print(f"  Fields seen: {sorted(fields)}")

        # ====================================================================
        # 8. PRICES HISTORY
        # ====================================================================
        if real_token:
            hr("8. clob /prices-history")
            url = f"{settings.clob_api_base}/prices-history"
            data = await get_json(client, url, {"market": real_token, "interval": "1d"})
            show_shape(data)
            if isinstance(data, dict):
                hist = data.get("history", [])
                print(f"\n  history len: {len(hist)}")
                if hist:
                    print(f"  first point: {hist[0]}")
                    print(f"  last point: {hist[-1]}")
                    print(f"  {INFO} Note open question #2 in session-state: interval=1d returned 1440 points (minutes).")

        # ====================================================================
        # 9. EDGE — nonsense wallet (F13 verification)
        # ====================================================================
        hr("9. EDGE — nonsense wallet (F13: empty list vs error response)")
        url = f"{settings.data_api_base}/positions"
        bogus_wallet = "0x000000000000000000000000000000000000bad0"
        data = await get_json(client, url, {"user": bogus_wallet})
        print(f"\n  Type: {type(data).__name__}")
        if isinstance(data, list):
            print(f"  Length: {len(data)}")
            if len(data) == 0:
                print(f"  {OK} Nonexistent wallet returns []. Our defensive fallback matches reality.")
        elif isinstance(data, dict):
            print(f"  {WARN} Returns dict for nonexistent wallet — our code currently coerces to [].")
            print(f"  This silently masks API errors. F13 fix needs to distinguish.")
            dump(data, max_chars=500)

        # ====================================================================
        # 10. EDGE — nonsense market for /trades (F13 verification)
        # ====================================================================
        hr("10. EDGE — nonsense market for clob /trades (F13)")
        url = f"{settings.clob_api_base}/trades"
        bogus_token = "0x" + "f" * 64
        data = await get_json(client, url, {"market": bogus_token, "limit": 5})
        print(f"\n  Type: {type(data).__name__}")
        if isinstance(data, list):
            print(f"  Length: {len(data)}")
            if len(data) == 0:
                print(f"  {OK} Nonexistent market returns []. Defensive fallback OK.")
        elif isinstance(data, dict):
            print(f"  {WARN} Returns dict — our code coerces to []. F13 should detect this.")
            dump(data, max_chars=500)

        # ====================================================================
        # 11. RATE-LIMIT SANITY — 5 quick calls back-to-back, watch for 429
        # ====================================================================
        hr("11. Rate-limit sanity — 5 rapid /book calls (watch for 429)")
        if real_token:
            url = f"{settings.clob_api_base}/book"
            for i in range(5):
                r = await client.get(url, params={"token_id": real_token})
                print(f"  call {i+1}: status={r.status_code}")
                if r.status_code == 429:
                    ra = r.headers.get("Retry-After", "<not set>")
                    print(f"  {WARN} 429 hit on call {i+1}; Retry-After={ra}")
                    break

    print("\n" + "=" * 80)
    print("  PROBE COMPLETE")
    print("=" * 80)
    print("""
Manual checklist after running:

  [F2/F12] Section 7: confirm 'side' or 'maker_side' field exists with values
           {BUY, SELL}. If not, our counterparty filter excludes everything.
           Confirm 'maker_address' or 'maker' field carries the wallet.

  [F4]     Section 6: confirm both 'bids' and 'asks' arrays are populated and
           accessible in the same response.

  [F3]     Section 4: confirm /value response contains a numeric portfolio
           value (likely 'value' key) we can use as denominator.

  [F13]    Sections 9 + 10: confirm nonexistent inputs return [] (good) and
           NOT a wrapped error object (would mean code silently swallows).

  [#2 open question] Section 8: verify interval semantics for prices-history.
""")


if __name__ == "__main__":
    asyncio.run(main())
