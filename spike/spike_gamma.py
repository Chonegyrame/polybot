"""Spike: validate gamma-api.polymarket.com endpoint shapes.

Tests:
- GET /markets — pagination, filtering by active/closed, response shape
- GET /events — same
- Field presence: conditionId, clobTokenIds, outcomes, outcomePrices, volume, liquidity, endDate
- What the category taxonomy actually looks like
"""

import json
from pathlib import Path

import httpx

BASE = "https://gamma-api.polymarket.com"
OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)


def dump(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, default=str)[:50000])
    print(f"  -> dumped {name}.json")


def main() -> None:
    with httpx.Client(timeout=30.0) as c:
        print("== GET /markets?limit=5&closed=false (active markets) ==")
        r = c.get(f"{BASE}/markets", params={"limit": 5, "closed": "false"})
        print(f"  status={r.status_code} content-type={r.headers.get('content-type')}")
        if r.status_code == 200:
            data = r.json()
            print(f"  type={type(data).__name__} len={len(data) if hasattr(data, '__len__') else 'n/a'}")
            if isinstance(data, list) and data:
                first = data[0]
                print(f"  keys on first market: {sorted(first.keys())}")
                print(f"  first.id={first.get('id')} slug={first.get('slug')}")
                print(f"  first.conditionId={first.get('conditionId')}")
                print(f"  first.clobTokenIds={first.get('clobTokenIds')}")
                print(f"  first.outcomes={first.get('outcomes')}")
                print(f"  first.outcomePrices={first.get('outcomePrices')}")
                print(f"  first.category={first.get('category')}")
                print(f"  first.tags={first.get('tags')}")
                dump("gamma_markets_active", data)

        print("\n== GET /markets?limit=5&closed=true (resolved markets) ==")
        r = c.get(f"{BASE}/markets", params={"limit": 5, "closed": "true"})
        print(f"  status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                first = data[0]
                print(f"  first.resolved={first.get('resolved')}")
                print(f"  first.umaResolutionStatuses={first.get('umaResolutionStatuses')}")
                print(f"  first.outcomePrices (resolved)={first.get('outcomePrices')}")
                dump("gamma_markets_closed", data)

        print("\n== GET /events?limit=3 ==")
        r = c.get(f"{BASE}/events", params={"limit": 3})
        print(f"  status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                first = data[0]
                print(f"  keys on first event: {sorted(first.keys())}")
                print(f"  first.title={first.get('title')}")
                print(f"  first.category={first.get('category')}")
                print(f"  first.tags (sample)={first.get('tags')}")
                dump("gamma_events", data)

        print("\n== Pagination probe: /markets?limit=2&offset=0 vs offset=2 ==")
        r1 = c.get(f"{BASE}/markets", params={"limit": 2, "offset": 0})
        r2 = c.get(f"{BASE}/markets", params={"limit": 2, "offset": 2})
        if r1.status_code == 200 and r2.status_code == 200:
            ids1 = [m.get("id") for m in r1.json()]
            ids2 = [m.get("id") for m in r2.json()]
            print(f"  page1 ids={ids1}")
            print(f"  page2 ids={ids2}")
            print(f"  pagination works={ids1 != ids2 and not set(ids1) & set(ids2)}")

        print("\n== Category survey (unique categories in first 100 markets) ==")
        r = c.get(f"{BASE}/markets", params={"limit": 100, "closed": "false"})
        if r.status_code == 200:
            data = r.json()
            cats = sorted({m.get("category") for m in data if m.get("category")})
            print(f"  unique categories seen: {cats}")
            tag_sample = []
            for m in data[:10]:
                if m.get("tags"):
                    tag_sample.append(m["tags"])
            print(f"  tags sample (first 10 with tags): {tag_sample[:3]}")


if __name__ == "__main__":
    main()
