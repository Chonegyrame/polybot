"""Spike: validate clob.polymarket.com read endpoints.

Tests:
- /markets — same data as gamma but with order book metadata
- /book?token_id= — order book for a specific outcome token
- /price?token_id=&side= — current best price
- /prices-history?market= — historical price series (needed for price-drift labels)
"""

import json
from pathlib import Path

import httpx

BASE = "https://clob.polymarket.com"
OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)


def dump(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, default=str)[:50000])
    print(f"  -> dumped {name}.json")


def main() -> None:
    with httpx.Client(timeout=30.0) as c:
        print("== GET /markets?next_cursor= (clob market list shape) ==")
        r = c.get(f"{BASE}/markets")
        print(f"  status={r.status_code}")
        token_id = None
        condition_id = None
        if r.status_code == 200:
            data = r.json()
            print(f"  type={type(data).__name__}")
            if isinstance(data, dict):
                print(f"  top-level keys={sorted(data.keys())}")
                items = data.get("data") or []
                print(f"  data list len={len(items)}")
                if items:
                    first = items[0]
                    print(f"  first market keys={sorted(first.keys())}")
                    condition_id = first.get("condition_id")
                    tokens = first.get("tokens") or []
                    if tokens:
                        token_id = tokens[0].get("token_id")
                        print(f"  first.tokens[0]={tokens[0]}")
                    print(f"  first.condition_id={condition_id}")
                    print(f"  first.minimum_order_size={first.get('minimum_order_size')}")
                    dump("clob_markets", data)

        if token_id:
            print(f"\n== GET /book?token_id={token_id} ==")
            r = c.get(f"{BASE}/book", params={"token_id": token_id})
            print(f"  status={r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"  keys={sorted(data.keys()) if isinstance(data, dict) else type(data).__name__}")
                dump("clob_book", data)

            print(f"\n== GET /price?token_id={token_id}&side=BUY ==")
            r = c.get(f"{BASE}/price", params={"token_id": token_id, "side": "BUY"})
            print(f"  status={r.status_code} body={r.text[:200]}")

            print(f"\n== GET /prices-history?market={token_id}&interval=1d ==")
            r = c.get(
                f"{BASE}/prices-history",
                params={"market": token_id, "interval": "1d"},
            )
            print(f"  status={r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    print(f"  keys={sorted(data.keys())}")
                    h = data.get("history") or []
                    print(f"  history len={len(h)}")
                    if h:
                        print(f"  first point={h[0]}")
                dump("clob_prices_history", data)


if __name__ == "__main__":
    main()
