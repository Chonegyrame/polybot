"""Spike: discover the leaderboard endpoint.

The leaderboard is what we'll use to seed our top-trader universe.
Path is not officially documented; previous training-data guesses include:
  - data-api.polymarket.com/leaderboard
  - data-api.polymarket.com/leaderboards
  - lb-api.polymarket.com/...
  - polymarket.com/api/leaderboard

This script probes likely candidates so we can lock down the right one.
"""

import json
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)

CANDIDATES = [
    ("https://data-api.polymarket.com/leaderboard", {"window": "month", "type": "profit", "limit": 5}),
    ("https://data-api.polymarket.com/leaderboards", {"window": "month", "type": "profit", "limit": 5}),
    ("https://lb-api.polymarket.com/profit", {"window": "month", "limit": 5}),
    ("https://lb-api.polymarket.com/leaderboard", {"window": "month", "limit": 5}),
    ("https://polymarket.com/api/leaderboard", {"window": "month", "type": "profit", "limit": 5}),
    ("https://gamma-api.polymarket.com/leaderboard", {"window": "month", "limit": 5}),
    # Alternate naming conventions
    ("https://data-api.polymarket.com/rankings", {"limit": 5}),
    ("https://data-api.polymarket.com/top-traders", {"limit": 5}),
]


def main() -> None:
    with httpx.Client(timeout=20.0, follow_redirects=True) as c:
        for url, params in CANDIDATES:
            print(f"\n== {url}  params={params} ==")
            try:
                r = c.get(url, params=params)
            except httpx.HTTPError as e:
                print(f"  network error: {type(e).__name__}: {e}")
                continue
            print(f"  status={r.status_code} content-type={r.headers.get('content-type')}")
            if r.status_code == 200 and "json" in (r.headers.get("content-type") or ""):
                try:
                    data = r.json()
                except Exception:
                    print(f"  body (first 200): {r.text[:200]}")
                    continue
                if isinstance(data, list):
                    print(f"  list len={len(data)}")
                    if data and isinstance(data[0], dict):
                        print(f"  first keys={sorted(data[0].keys())}")
                elif isinstance(data, dict):
                    print(f"  keys={sorted(data.keys())}")
                slug = url.split("//")[1].replace("/", "_").replace(".", "_")
                (OUT / f"lb_{slug}.json").write_text(json.dumps(data, indent=2, default=str)[:50000])
                print(f"  -> dumped lb_{slug}.json")
            else:
                print(f"  body (first 200): {r.text[:200]}")


if __name__ == "__main__":
    main()
