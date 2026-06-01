"""Optimistic tape-based FOLLOW-backtest for one wallet's esports entries.

For each esports market the wallet entered (their first BUY), reconstruct the
price at +10s/+30s/+60s/+180s from the market's trade tape, and compute the PnL
you'd have made FOLLOWING at that lag, settled on the real resolution. Compares
to the wallet's OWN entry (zero lag).

OPTIMISTIC by design: uses executed (tape) prices and ignores the bid/ask
spread + fees, so true following would do somewhat worse. This is a GATE — if
following doesn't profit even here, stop. Coverage is reported per lag (the
per-market 3000-fill cap can hide early post-entry trades on huge markets).

Run: python -m scripts.follow_backtest [wallet_address]
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import defaultdict

from app.services.polymarket import PolymarketClient

LAGS = [10, 30, 60, 180]
DEFAULT_WALLET = "0xfbf3d501e88815464642d0e913f15379c3eeb218"  # VPenguin


async def fetch_post_entry(pm: PolymarketClient, cid: str, entry_ts: float) -> list:
    """Trades from newest backward, stopping once we've reached entry_ts.

    We only need trades at/after the entry to price the follow-lags, and a sharp
    enters DURING the live game (near the market's end = newest trades), so this
    is usually 1-2 pages. Returns recs [(ts, asset, price)]. Offset capped 3000.
    """
    recs: list = []
    for off in range(0, 3001, 500):
        pg = await pm.get_market_trades(cid, limit=500, offset=off)
        if not pg:
            break
        oldest = 1e18
        for f in pg:
            try:
                fts = float(f.get("timestamp", 0) or 0)
                recs.append((fts, f.get("asset"), float(f.get("price"))))
                oldest = min(oldest, fts)
            except (TypeError, ValueError):
                continue
        if len(pg) < 500 or oldest <= entry_ts:
            break  # covered the whole post-entry window
    return recs


def price_at(recs: list, their_asset: str, target: float) -> float | None:
    """Last price on their asset at/before target; else complement-derived."""
    last_own = last_comp = None
    for fts, a, px in recs:
        if fts > target:
            break
        if a == their_asset:
            last_own = px
        elif a:
            last_comp = 1.0 - px
    return last_own if last_own is not None else last_comp


async def main() -> None:
    wallet = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WALLET
    async with PolymarketClient() as pm:
        trades = []
        async for t in pm.iter_trades(wallet, page_size=500):
            trades.append(t)
            if len(trades) >= 8000:
                break
        print(f"wallet {wallet[:14]} — {len(trades)} trades")

        teams: set[str] = set()
        for t in trades:
            tl = t.title or ""
            if tl.lower().startswith("lol:"):
                m = re.search(r"lol:\s*(.+?)\s+vs\s+(.+?)\s*(?:-|\(|$)", tl, re.I)
                if m:
                    teams.add(m.group(1).strip().lower())
                    teams.add(m.group(2).strip().lower())

        def is_es(title: str | None) -> bool:
            tl = (title or "").lower()
            if tl.startswith("lol:") or "league of legends" in tl or tl.startswith("dota"):
                return True
            if "handicap" in tl or "game" in tl:
                return any(x in tl for x in teams if len(x) > 2)
            return False

        by_cid: dict[str, list] = defaultdict(list)
        for t in trades:
            if t.condition_id:
                by_cid[t.condition_id].append(t)
        es_cids = [c for c, ts_ in by_cid.items() if is_es(ts_[0].title)]

        markets = await pm.get_markets_by_condition_ids(es_cids, closed=True)
        winner: dict[str, str] = {}
        for m in markets:
            if (len(m.outcome_prices) == len(m.clob_token_ids) == 2
                    and max(m.outcome_prices) > 0.99):
                winner[m.condition_id] = m.clob_token_ids[
                    m.outcome_prices.index(max(m.outcome_prices))]

        agg = {L: {"pnl": 0.0, "cost": 0.0, "wins": 0, "n": 0, "uncov": 0} for L in LAGS}
        own = {"pnl": 0.0, "cost": 0.0, "wins": 0, "n": 0}

        for cid in es_cids:
            if cid not in winner:
                continue
            ts_ = sorted([t for t in by_cid[cid] if t.timestamp], key=lambda t: t.timestamp)
            buys = [t for t in ts_ if t.side == "BUY"]
            if not buys:
                continue
            entry = buys[0]
            their_asset = entry.asset
            won = their_asset == winner[cid]
            payoff = 1.0 if won else 0.0
            own["n"] += 1; own["cost"] += entry.price
            own["pnl"] += payoff - entry.price
            if payoff - entry.price > 0:
                own["wins"] += 1

            ets = entry.timestamp.timestamp()
            recs = await fetch_post_entry(pm, cid, ets)
            recs.sort()
            for L in LAGS:
                fp = price_at(recs, their_asset, ets + L)
                if fp is None or not (0.0 < fp < 1.0):
                    agg[L]["uncov"] += 1
                    continue
                agg[L]["n"] += 1; agg[L]["cost"] += fp
                agg[L]["pnl"] += payoff - fp
                if payoff - fp > 0:
                    agg[L]["wins"] += 1

        print(f"esports markets with resolution: {sum(1 for c in es_cids if c in winner)}")
        print("(OPTIMISTIC — executed prices, no spread/fees)\n")
        if own["n"]:
            print(f"THEIR OWN entry (0 lag): n={own['n']} "
                  f"winrate={own['wins']/own['n']:.0%} avg_price={own['cost']/own['n']:.2f} "
                  f"ROI/$1={own['pnl']/own['cost']:+.1%}")
        for L in LAGS:
            a = agg[L]
            if a["n"]:
                print(f"FOLLOW +{L:>3}s: n={a['n']} (uncov {a['uncov']}) "
                      f"winrate={a['wins']/a['n']:.0%} avg_price={a['cost']/a['n']:.2f} "
                      f"ROI/$1={a['pnl']/a['cost']:+.1%}")


if __name__ == "__main__":
    asyncio.run(main())
