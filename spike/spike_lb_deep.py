"""Deeper probe of lb-api.polymarket.com — service exists but rejected our first params.

Try various param combinations to find the right shape.
"""

import json
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)


def probe(c: httpx.Client, url: str, params: dict) -> None:
    label = f"{url}?{httpx.QueryParams(params)}"
    print(f"\n-- {label}")
    try:
        r = c.get(url, params=params)
    except httpx.HTTPError as e:
        print(f"   net err: {e}")
        return
    print(f"   status={r.status_code} body={r.text[:250]}")
    if r.status_code == 200:
        try:
            data = r.json()
            slug = url.split("//")[1].replace("/", "_").replace(".", "_")
            param_slug = "_".join(f"{k}-{v}" for k, v in params.items())[:60]
            (OUT / f"lb_{slug}_{param_slug}.json").write_text(
                json.dumps(data, indent=2, default=str)[:50000]
            )
            print(f"   -> dumped")
        except Exception:
            pass


def main() -> None:
    with httpx.Client(timeout=15.0, follow_redirects=True) as c:
        # lb-api.polymarket.com/profit — try different param shapes
        url = "https://lb-api.polymarket.com/profit"
        for params in [
            {"window": "1d"},
            {"window": "7d"},
            {"window": "30d"},
            {"window": "all"},
            {"window": "day"},
            {"window": "week"},
            {"window": "month"},
            {"interval": "month"},
            {"period": "month"},
            {"window": "month", "limit": 10},
            {"window": "month", "address": "0x0"},
            {},
        ]:
            probe(c, url, params)

        # Try /volume too
        url = "https://lb-api.polymarket.com/volume"
        for params in [
            {"window": "month", "limit": 10},
            {"interval": "month"},
        ]:
            probe(c, url, params)

        # Try root and OPTIONS-equivalent
        for path in ["/", "/leaderboard", "/users", "/traders", "/api"]:
            probe(c, f"https://lb-api.polymarket.com{path}", {})


if __name__ == "__main__":
    main()
