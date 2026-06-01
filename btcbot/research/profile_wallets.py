"""Discover & profile SERIOUS-MONEY wallets on 5m BTC up/down markets.

Phase-1 analysis (first pass). Method:
  - Scan resolved 5m BTC windows over a lookback (default 24h).
  - Pull in-window fills; keep only bets >= MIN_NOTIONAL ($400). A wallet
    qualifies for a window if it placed >=1 big bet there.
  - For qualifying wallets, reconstruct that window's PnL from ALL their
    in-window fills, settled on the real outcome.
  - Aggregate per wallet across windows: # windows, both-sides % (the
    market-maker tell), avg entry timing, win rate, total PnL & notional.

Honest limits (printed at runtime):
  - Per-market fill feed caps at ~4000 (offset<=3000). Windows that hit the
    cap have incomplete fills, so they are EXCLUDED from PnL (counted/reported).
  - PnL is approximate: in-window fills only, assumes flat starting position.
  - This is NOT the skill-vs-luck test — that's phase 2 (out-of-sample
    persistence on the shortlist this produces).

Run:  python -m btcbot.research.profile_wallets [lookback_hours]
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import defaultdict

from app.services.polymarket import PolymarketClient
from btcbot.discovery import HORIZONS

MIN_NOTIONAL = 400.0
H = HORIZONS["5m"]


async def fetch_window_fills(pm: PolymarketClient, cid: str):
    """Return (fills, capped). capped=True means >4000 fills exist and we only
    have the most-recent 4000 (earliest entries missing)."""
    out: list = []
    for off in (0, 1000, 2000, 3000):
        page = await pm.get_market_trades(cid, limit=1000, offset=off)
        if not page:
            return out, False
        out.extend(page)
        if len(page) < 1000:
            return out, False
    return out, True  # got the full 4000 -> more may exist


async def main() -> None:
    lookback_h = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
    n_windows = int(lookback_h * 3600 // 300)

    # per-wallet lifetime aggregates
    W = lambda: {  # noqa: E731
        "windows": set(), "big_bets": 0, "buy": 0, "sell": 0,
        "up_bets": 0, "down_bets": 0, "notional": 0.0, "pnl": 0.0,
        "wins": 0, "settled_windows": 0, "tte_sum": 0.0, "tte_n": 0,
    }
    wallet: dict[str, dict] = defaultdict(W)

    scanned = capped_windows = 0
    async with PolymarketClient() as pm:
        now = int(time.time())
        base = (now // 300) * 300
        for i in range(2, n_windows + 2):
            ts = base - i * 300
            ev = await pm.get_event_by_slug(H.slug_for_start(ts))
            if not ev or not ev.markets:
                continue
            m = ev.markets[0]
            if not m.closed or len(m.outcome_prices) != 2:
                continue
            if max(m.outcome_prices) < 0.99:
                continue  # not cleanly resolved
            winner = m.outcomes[m.outcome_prices.index(max(m.outcome_prices))].strip().lower()
            fills, capped = await fetch_window_fills(pm, m.condition_id)
            scanned += 1
            if capped:
                capped_windows += 1
            end = ts + 300

            # accumulate ALL in-window fills per wallet for this window
            pw: dict[str, dict] = defaultdict(lambda: {
                "net": {"up": 0.0, "down": 0.0}, "cash": 0.0, "big": 0,
                "buy": 0, "sell": 0, "up": 0, "down": 0, "notional": 0.0,
                "tte_sum": 0.0, "tte_n": 0,
            })
            for f in fills:
                try:
                    tsf = float(f.get("timestamp", 0) or 0)
                    px = float(f.get("price"))
                    sz = float(f.get("size"))
                except (TypeError, ValueError):
                    continue
                if not (ts <= tsf <= end):
                    continue  # in-window only
                w = f.get("proxyWallet")
                if not w:
                    continue
                oc = str(f.get("outcome", "")).strip().lower()
                side = f.get("side")
                notional = px * sz
                d = pw[w]
                if notional >= MIN_NOTIONAL:
                    d["big"] += 1
                    d["notional"] += notional
                    d["tte_sum"] += (end - tsf); d["tte_n"] += 1
                    if oc == "up": d["up"] += 1
                    elif oc == "down": d["down"] += 1
                if side == "BUY":
                    d["buy"] += 1
                    if oc in d["net"]: d["net"][oc] += sz; d["cash"] -= notional
                elif side == "SELL":
                    d["sell"] += 1
                    if oc in d["net"]: d["net"][oc] -= sz; d["cash"] += notional

            # settle qualifying wallets (>=1 big bet); skip capped windows for PnL
            for w, d in pw.items():
                if d["big"] == 0:
                    continue
                agg = wallet[w]
                agg["windows"].add(ts)
                agg["big_bets"] += d["big"]; agg["notional"] += d["notional"]
                agg["buy"] += d["buy"]; agg["sell"] += d["sell"]
                agg["up_bets"] += d["up"]; agg["down_bets"] += d["down"]
                agg["tte_sum"] += d["tte_sum"]; agg["tte_n"] += d["tte_n"]
                if capped:
                    continue  # incomplete fills -> don't trust PnL here
                payout = max(d["net"].get(winner, 0.0), 0.0)
                pnl = d["cash"] + payout
                agg["pnl"] += pnl; agg["settled_windows"] += 1
                if pnl > 0: agg["wins"] += 1

    # ---- report ----
    print(f"\nLookback: {lookback_h:.0f}h | scanned {scanned} resolved 5m windows "
          f"| {capped_windows} hit the 4k cap (excluded from PnL)")
    print(f"Wallets with >=1 big bet (>=${MIN_NOTIONAL:.0f}): {len(wallet)}")

    for minw in (3, 5, 10):
        n = sum(1 for a in wallet.values() if len(a["windows"]) >= minw)
        print(f"  active in >= {minw} windows: {n}")

    MINW = 5
    active = [(w, a) for w, a in wallet.items()
              if len(a["windows"]) >= MINW and a["settled_windows"] >= 3]
    active.sort(key=lambda x: -x[1]["pnl"])

    def fmt(w, a):
        sw = a["settled_windows"]; wr = a["wins"] / sw if sw else 0
        tb = a["up_bets"] + a["down_bets"]
        both = min(a["up_bets"], a["down_bets"]) / tb if tb else 0
        tte = a["tte_sum"] / a["tte_n"] if a["tte_n"] else 0
        kind = "MM?" if both > 0.30 else "dir"
        return (f"{w[:12]} {kind:>3} wnd={len(a['windows']):>3} bets={a['big_bets']:>4} "
                f"wr={wr:>4.0%} pnl=${a['pnl']:>8.0f} notl=${a['notional']:>9.0f} "
                f"both={both:>3.0%} entry~{tte:>3.0f}s")

    print(f"\n=== TOP 20 by PnL (active >= {MINW} windows, >=3 settled) ===")
    for w, a in active[:20]:
        print("  " + fmt(w, a))
    print("\n=== BOTTOM 8 by PnL ===")
    for w, a in active[-8:]:
        print("  " + fmt(w, a))

    # structural split
    dir_w = [a for _, a in active if (a["up_bets"] + a["down_bets"]) and
             min(a["up_bets"], a["down_bets"]) / (a["up_bets"] + a["down_bets"]) <= 0.30]
    mm_w = [a for _, a in active if a not in dir_w]
    print(f"\nStructural split of {len(active)} active wallets: "
          f"~{len(dir_w)} directional, ~{len(mm_w)} MM-like (both-sides>30%)")
    if dir_w:
        prof = sum(1 for a in dir_w if a["pnl"] > 0)
        print(f"  directional wallets profitable (in-sample): {prof}/{len(dir_w)}")


if __name__ == "__main__":
    asyncio.run(main())
