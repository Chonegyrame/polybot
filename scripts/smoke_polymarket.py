"""Smoke test for app.services.polymarket — exercises every public method.

Run from project root:
    ./venv/Scripts/python.exe scripts/smoke_polymarket.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.polymarket import PolymarketClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def hr(label: str) -> None:
    print(f"\n{'=' * 60}\n {label}\n{'=' * 60}")


async def main() -> None:
    async with PolymarketClient() as pm:
        hr("Leaderboard: PNL / all-time / overall (top 100, paginated)")
        lb = await pm.get_leaderboard(order_by="PNL", time_period="all", depth=100)
        print(f"  total entries: {len(lb)}  (max rank: {lb[-1].rank if lb else '?'})")
        for e in lb[:5] + lb[-3:]:
            badge = " ✓" if e.verified_badge else ""
            print(
                f"  #{e.rank:<4}  {(e.user_name or '<anon>'):<28}{badge}"
                f"  pnl=${e.pnl:>14,.0f}  vol=${e.vol:>14,.0f}  {e.proxy_wallet}"
            )

        if not lb:
            print("  no leaderboard entries — aborting downstream tests")
            return

        hr("Leaderboard: VOL / month / sports (top 5)")
        lb_sports = await pm.get_leaderboard(
            order_by="VOL", time_period="month", category="sports", depth=5
        )
        for e in lb_sports:
            print(
                f"  #{e.rank:<3}  {(e.user_name or '<anon>'):<25}"
                f"  vol=${e.vol:>14,.0f}  pnl=${e.pnl:>+12,.0f}"
            )

        # Pick the top trader who has a non-zero portfolio value (i.e. still active)
        hr("Find a currently-active top trader")
        active_wallet = None
        active_entry = None
        for entry in lb[:30]:
            val = await pm.get_portfolio_value(entry.proxy_wallet)
            if val and val.value > 1.0:
                active_wallet = entry.proxy_wallet
                active_entry = entry
                print(f"  picked: {entry.user_name} (${val.value:,.0f} portfolio)")
                break
            else:
                print(f"  skipping {entry.user_name}: portfolio=${(val.value if val else 0):,.2f}")
        if not active_wallet:
            print("  no active wallet found in top 30 — using #1 anyway")
            active_wallet = lb[0].proxy_wallet
            active_entry = lb[0]

        hr(f"Positions for {active_entry.user_name} ({active_wallet})")
        positions = await pm.get_positions(active_wallet, limit=10)
        print(f"  open positions: {len(positions)}")
        for p in positions[:5]:
            print(
                f"  - {p.title[:50] if p.title else p.condition_id[:10]:<50}"
                f"  {p.outcome:<6}  size={p.size:>10,.2f}"
                f"  cur_value=${p.current_value or 0:>10,.2f}"
                f"  pnl=${p.cash_pnl or 0:>+10,.2f}"
            )

        hr(f"Recent trades for {active_entry.user_name}")
        trades = await pm.get_trades(active_wallet, limit=5)
        print(f"  trades returned: {len(trades)}")
        for t in trades[:5]:
            ts = t.timestamp.strftime("%Y-%m-%d %H:%M") if t.timestamp else "?"
            print(
                f"  - {ts}  {t.side:<4}  size={t.size:>10,.2f}  price={t.price:.3f}"
                f"  {(t.title or t.condition_id[:10])[:50]}"
            )

        hr("iter_trades sanity (page through up to 1000 trades)")
        count = 0
        async for _ in pm.iter_trades(active_wallet, page_size=500):
            count += 1
            if count >= 1000:
                break
        print(f"  iterated trades: {count}")

        hr("Events with categories (first 5)")
        events = await pm.get_events(limit=5, closed=False)
        print(f"  events returned: {len(events)}")
        for ev in events:
            mc = len(ev.markets)
            print(f"  - [{ev.category or '?':<12}] {ev.title or '<no title>':<60}  ({mc} markets)")

        hr("Single market parse (verify clobTokenIds + outcomePrices double-decoded)")
        markets = await pm.get_markets(limit=1, closed=False)
        if markets:
            m = markets[0]
            print(f"  question: {m.question}")
            print(f"  conditionId: {m.condition_id}")
            print(f"  outcomes: {m.outcomes}")
            print(f"  outcomePrices (parsed): {m.outcome_prices}")
            print(f"  clobTokenIds count: {len(m.clob_token_ids)}")
            print(f"  best bid/ask: {m.best_bid} / {m.best_ask}")

        hr("Prices history for first market's YES token")
        if markets and markets[0].clob_token_ids:
            from app.services.polymarket_types import pair_yes_no_tokens
            yes_token, _ = pair_yes_no_tokens(
                markets[0].outcomes, markets[0].clob_token_ids
            )
            # Fall back to first token if not a clean binary (script is dev-only).
            yes_token = yes_token or markets[0].clob_token_ids[0]
            history = await pm.get_prices_history(yes_token, interval="1d")
            print(f"  history points: {len(history)}")
            if history:
                first, last = history[0], history[-1]
                print(f"  first: {first.timestamp} @ {first.price}")
                print(f"  last:  {last.timestamp} @ {last.price}")

    print("\nALL SMOKE TESTS PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
