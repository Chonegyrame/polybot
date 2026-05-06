"""Spike: validate data-api.polymarket.com endpoint shapes.

Tests:
- /positions?user= — current open positions per wallet
- /activity?user=&type=TRADE — historical trades
- /value?user= — total portfolio value
- /trades?user= — alternative trade feed
- Proxy/EOA gotcha probe: known proxy address vs an EOA returns nothing
- Pagination via limit/offset

We use the publicly-known "Theo" 2024 election whale wallet as a real test target,
plus a clearly-wrong EOA to confirm the proxy/EOA failure mode.
"""

import json
from pathlib import Path

import httpx

BASE = "https://data-api.polymarket.com"
OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)

# Publicly-named wallets from journalism (WSJ/FT 2024 election coverage on "Theo").
# These are the proxy addresses commonly cited; if they 404 or return empty, we'll
# document that and pivot to a different known-active wallet.
KNOWN_WALLETS = [
    "0x55c66f43b88ba8557f4ac9a32cab542ad7d2b39d",  # one of the Theo cluster (verify)
    "0x9d84ce0306f8531c365c2880616d9285dc5b3c40",  # another Theo cluster (verify)
]
INVALID_EOA = "0x0000000000000000000000000000000000000001"


def dump(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, default=str)[:50000])
    print(f"  -> dumped {name}.json")


def probe(c: httpx.Client, path: str, params: dict, label: str) -> dict | list | None:
    print(f"\n== GET {path}  params={params} ==")
    r = c.get(f"{BASE}{path}", params=params)
    print(f"  status={r.status_code} content-type={r.headers.get('content-type')}")
    if r.status_code != 200:
        print(f"  body (truncated): {r.text[:300]}")
        return None
    try:
        data = r.json()
    except Exception as e:
        print(f"  json parse error: {e}")
        return None
    if isinstance(data, list):
        print(f"  list len={len(data)}")
        if data:
            print(f"  first keys={sorted(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__}")
        dump(label, data)
    else:
        print(f"  type={type(data).__name__}")
        if isinstance(data, dict):
            print(f"  keys={sorted(data.keys())}")
        dump(label, data)
    return data


def main() -> None:
    with httpx.Client(timeout=30.0) as c:
        for i, w in enumerate(KNOWN_WALLETS):
            probe(c, "/positions", {"user": w, "limit": 5}, f"positions_known_{i}")
            probe(c, "/value", {"user": w}, f"value_known_{i}")
            probe(c, "/activity", {"user": w, "type": "TRADE", "limit": 5}, f"activity_known_{i}")
            probe(c, "/trades", {"user": w, "limit": 5}, f"trades_known_{i}")

        print("\n== Proxy/EOA gotcha probe (invalid/empty wallet) ==")
        probe(c, "/positions", {"user": INVALID_EOA}, "positions_invalid")

        print("\n== Pagination probe ==")
        if KNOWN_WALLETS:
            w = KNOWN_WALLETS[0]
            r1 = c.get(f"{BASE}/activity", params={"user": w, "type": "TRADE", "limit": 3, "offset": 0})
            r2 = c.get(f"{BASE}/activity", params={"user": w, "type": "TRADE", "limit": 3, "offset": 3})
            if r1.status_code == 200 and r2.status_code == 200:
                d1 = r1.json() or []
                d2 = r2.json() or []
                if isinstance(d1, list) and isinstance(d2, list):
                    print(f"  page1 len={len(d1)} page2 len={len(d2)}")
                    if d1 and d2 and isinstance(d1[0], dict):
                        h1 = [e.get("transactionHash") or e.get("hash") or e.get("id") for e in d1]
                        h2 = [e.get("transactionHash") or e.get("hash") or e.get("id") for e in d2]
                        print(f"  page1 ids={h1}")
                        print(f"  page2 ids={h2}")


if __name__ == "__main__":
    main()
