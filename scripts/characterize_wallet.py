"""Characterize a wallet's trading style: directional value-bettor vs maker/hedger.

Pulls recent trades (≤2500, the /trades?user offset cap) and reports the signals
that actually distinguish a maker from a directional bettor:
  - how often it BOUGHT both outcome tokens of the same market (maker/hedger tell)
  - sell/buy ratio (heavy two-way churn leans maker/scalper)
  - entry-price distribution + deep-underdog share (value-hunter tell)
  - trade size

Run: python -u -m scripts.characterize_wallet <wallet>
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from collections import defaultdict

from app.services.polymarket import PolymarketClient

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT = "0x93bc1f104bc72c9141fc41c2acb2265f54a28ca3"  # EVplusrebate


async def main() -> None:
    wallet = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    async with PolymarketClient() as pm:
        trades = []
        async for t in pm.iter_trades(wallet, page_size=500):
            trades.append(t)
            if len(trades) >= 2500:
                break

    print(f"wallet {wallet}  —  {len(trades)} recent trades")
    if not trades:
        return
    buys = [t for t in trades if t.side == "BUY"]
    sells = [t for t in trades if t.side == "SELL"]
    print(f"BUY {len(buys)}   SELL {len(sells)}   "
          f"sell/buy ratio {len(sells) / max(len(buys), 1):.2f}")

    bought_tokens: dict[str, set] = defaultdict(set)
    for t in trades:
        if t.condition_id and t.side == "BUY":
            bought_tokens[t.condition_id].add(t.asset)
    both = sum(1 for toks in bought_tokens.values() if len(toks) > 1)
    n_mkts = len(bought_tokens)
    print(f"\ndistinct markets bought in: {n_mkts}")
    print(f"markets where they BOUGHT BOTH outcomes (maker/hedger tell): "
          f"{both}  ({both / max(n_mkts, 1) * 100:.1f}%)")

    px = sorted(t.price for t in buys if t.price)
    if px:
        def q(p):  # crude percentile
            return px[min(int(p * len(px)), len(px) - 1)]
        cheap = sum(1 for p in px if p <= 0.20)
        print(f"\nBUY entry price:  min {px[0]:.2f}  p10 {q(.1):.2f}  "
              f"median {statistics.median(px):.2f}  p90 {q(.9):.2f}  max {px[-1]:.2f}")
        print(f"  deep-underdog BUYs (<=0.20): {cheap / len(px) * 100:.0f}%  "
              f"→ high share = value/longshot hunter, explains big ROI")

    sizes = [t.usdc_size or (t.price * t.size) for t in trades]
    print(f"\ntrade notional: median ${statistics.median(sizes):,.0f}  "
          f"max ${max(sizes):,.0f}")

    print("\nread: both-outcome% high (≳25%) + sell/buy near 1 → maker/hedger. "
          "both-outcome% low + one-sided → directional (followable).")


if __name__ == "__main__":
    asyncio.run(main())
