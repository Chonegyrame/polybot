"""Deep-dive one wallet's LoL performance (reusable for sharps-list building).

Pulls the wallet's full trade history, fetches resolutions for every market it
touched, reconstructs per-market net PnL (buys - sells + resolution payout),
and isolates LoL markets (incl. game-handicap markets whose team names match
ones seen in 'LoL:' titles). Reports win rate, ROI, entry odds, and best/worst.

PnL is approximate (net-position settled at resolution; sells credited at trade
price). Run: python -m scripts.wallet_lol_deepdive [wallet_address]
"""

from __future__ import annotations

import asyncio
import re
import statistics
import sys
from collections import defaultdict

from app.services.polymarket import PolymarketClient

DEFAULT_WALLET = "0xfbf3d501e88815464642d0e913f15379c3eeb218"  # VPenguin


def _notional(t) -> float:
    return t.usdc_size if t.usdc_size else t.price * t.size


async def main() -> None:
    wallet = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WALLET
    async with PolymarketClient() as pm:
        trades = []
        async for t in pm.iter_trades(wallet, page_size=500):
            trades.append(t)
            if len(trades) >= 8000:
                break
        print(f"wallet {wallet[:14]} — pulled {len(trades)} trades")
        if not trades:
            return

        # Build LoL team set from explicit 'LoL:' titles, to also catch the
        # 'Game Handicap: TeamA (-1.5) vs TeamB' markets that omit 'LoL'.
        lol_teams: set[str] = set()
        for t in trades:
            tl = t.title or ""
            if tl.lower().startswith("lol:"):
                m = re.search(r"lol:\s*(.+?)\s+vs\s+(.+?)\s*(?:-|\(|$)", tl, re.I)
                if m:
                    lol_teams.add(m.group(1).strip().lower())
                    lol_teams.add(m.group(2).strip().lower())

        def is_lol(title: str | None) -> bool:
            tl = (title or "").lower()
            if tl.startswith("lol:") or "league of legends" in tl:
                return True
            if "handicap" in tl or "game" in tl:
                return any(tm in tl for tm in lol_teams if len(tm) > 2)
            return False

        # Resolutions for every market touched.
        cids = list({t.condition_id for t in trades if t.condition_id})
        markets = await pm.get_markets_by_condition_ids(cids, closed=True)
        winner_token: dict[str, str] = {}
        title_by: dict[str, str | None] = {}
        for m in markets:
            title_by[m.condition_id] = m.question
            if (len(m.outcome_prices) == len(m.clob_token_ids) == 2
                    and max(m.outcome_prices) > 0.99):
                winner_token[m.condition_id] = m.clob_token_ids[
                    m.outcome_prices.index(max(m.outcome_prices))]

        # Per-market PnL reconstruction.
        by_cid: dict[str, list] = defaultdict(list)
        for t in trades:
            by_cid[t.condition_id].append(t)

        results = []
        for cid, ts_ in by_cid.items():
            title = title_by.get(cid) or ts_[0].title
            net: dict[str, float] = defaultdict(float)
            cash = 0.0
            staked = 0.0
            entry_px = []
            for t in ts_:
                amt = _notional(t)
                if t.side == "BUY":
                    net[t.asset] += t.size; cash -= amt
                    staked += amt; entry_px.append(t.price)
                elif t.side == "SELL":
                    net[t.asset] -= t.size; cash += amt
            wt = winner_token.get(cid)
            payout = max(net.get(wt, 0.0), 0.0) if wt else 0.0
            results.append({
                "title": title, "pnl": cash + payout, "resolved": cid in winner_token,
                "lol": is_lol(title), "staked": staked,
                "entry": (sum(entry_px) / len(entry_px)) if entry_px else None,
            })

        def summ(rs, label):
            rs = [r for r in rs if r["resolved"]]
            if not rs:
                print(f"{label}: no resolved markets"); return
            pnl = sum(r["pnl"] for r in rs)
            wins = sum(1 for r in rs if r["pnl"] > 0)
            staked = sum(r["staked"] for r in rs)
            roi = f"{pnl / staked:+.1%}" if staked else "n/a"
            print(f"{label}: {len(rs)} markets | net PnL ${pnl:,.0f} | "
                  f"win rate {wins / len(rs):.0%} | staked ${staked:,.0f} | ROI {roi}")

        print()
        summ(results, "ALL resolved")
        summ([r for r in results if r["lol"]], "LoL resolved")
        lol = [r for r in results if r["lol"] and r["resolved"]]
        ent = [r["entry"] for r in lol if r["entry"]]
        if ent:
            print(f"LoL entry odds: median {statistics.median(ent):.2f} "
                  f"(range {min(ent):.2f}-{max(ent):.2f})")
        lol.sort(key=lambda r: -r["pnl"])
        print("\nTop LoL markets by PnL:")
        for r in lol[:6]:
            print(f"   +${r['pnl']:>9,.0f}  {str(r['title'])[:55]}")
        print("Worst LoL markets:")
        for r in lol[-4:]:
            print(f"   ${r['pnl']:>10,.0f}  {str(r['title'])[:55]}")


if __name__ == "__main__":
    asyncio.run(main())
