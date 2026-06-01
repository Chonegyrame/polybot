"""Build a candidate '5m BTC sharps list' with an OUT-OF-SAMPLE persistence test.

Scan N hours of resolved 5m BTC windows, split into an EARLY half (in-sample)
and a LATE half (out-of-sample) at the midpoint. For each serious-money wallet
(>=$400 bets), reconstruct per-window PnL and classify directional vs MM.

A 'sharp' = a DIRECTIONAL wallet that is active AND profitable in BOTH halves.
Luck rarely survives an independent out-of-sample period; persistence is the
real skill signal. The headline number is the persistence rate: of the wallets
that looked good in-sample, how many stayed good out-of-sample?

Honest limits: capped (>4k-fill) windows are excluded from PnL (incomplete);
PnL is approximate (in-window fills, flat start assumption); halves are short.

Run: python -m btcbot.research.sharps_list [lookback_hours]
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import defaultdict

from app.services.polymarket import PolymarketClient
from btcbot.discovery import HORIZONS
from btcbot.research.profile_wallets import MIN_NOTIONAL, fetch_window_fills

H = HORIZONS["5m"]


def _agg() -> dict:
    return {"windows": 0, "pnl": 0.0, "wins": 0, "settled": 0,
            "up": 0, "down": 0, "notional": 0.0, "big": 0,
            "tte_sum": 0.0, "tte_n": 0}


def _both_ratio(a: dict) -> float:
    t = a["up"] + a["down"]
    return min(a["up"], a["down"]) / t if t else 0.0


def settle_window(fills: list, ts: int, end: int, winner: str) -> dict:
    """Per-wallet PnL + behavior for one window (wallets with >=1 big bet)."""
    pw: dict = defaultdict(lambda: {
        "net": {"up": 0.0, "down": 0.0}, "cash": 0.0, "big": 0,
        "up": 0, "down": 0, "notional": 0.0, "tte_sum": 0.0, "tte_n": 0})
    for f in fills:
        try:
            tsf = float(f.get("timestamp", 0) or 0)
            px = float(f.get("price")); sz = float(f.get("size"))
        except (TypeError, ValueError):
            continue
        if not (ts <= tsf <= end):
            continue
        w = f.get("proxyWallet")
        if not w:
            continue
        oc = str(f.get("outcome", "")).strip().lower()
        side = f.get("side"); notl = px * sz
        d = pw[w]
        if notl >= MIN_NOTIONAL:
            d["big"] += 1; d["notional"] += notl
            d["tte_sum"] += (end - tsf); d["tte_n"] += 1
            if oc == "up": d["up"] += 1
            elif oc == "down": d["down"] += 1
        if side == "BUY":
            if oc in d["net"]: d["net"][oc] += sz; d["cash"] -= notl
        elif side == "SELL":
            if oc in d["net"]: d["net"][oc] -= sz; d["cash"] += notl
    out: dict = {}
    for w, d in pw.items():
        if d["big"] == 0:
            continue
        payout = max(d["net"].get(winner, 0.0), 0.0)
        out[w] = {"pnl": d["cash"] + payout, "big": d["big"], "up": d["up"],
                  "down": d["down"], "notional": d["notional"],
                  "tte_sum": d["tte_sum"], "tte_n": d["tte_n"]}
    return out


async def main() -> None:
    lookback = float(sys.argv[1]) if len(sys.argv) > 1 else 72.0
    nwin = int(lookback * 3600 // 300)
    early: dict = defaultdict(_agg)
    late: dict = defaultdict(_agg)
    scanned = capped = 0

    async with PolymarketClient() as pm:
        now = int(time.time()); base = (now // 300) * 300
        midpoint = base - (nwin // 2) * 300  # older than this = in-sample (early)
        for i in range(2, nwin + 2):
            ts = base - i * 300
            ev = await pm.get_event_by_slug(H.slug_for_start(ts))
            if not ev or not ev.markets:
                continue
            m = ev.markets[0]
            if not m.closed or len(m.outcome_prices) != 2 or max(m.outcome_prices) < 0.99:
                continue
            winner = m.outcomes[m.outcome_prices.index(max(m.outcome_prices))].strip().lower()
            fills, was_capped = await fetch_window_fills(pm, m.condition_id)
            scanned += 1
            if was_capped:
                capped += 1
                continue  # incomplete fills -> unreliable PnL
            bucket = early if ts < midpoint else late
            for w, r in settle_window(fills, ts, ts + 300, winner).items():
                a = bucket[w]
                a["windows"] += 1; a["settled"] += 1; a["pnl"] += r["pnl"]
                if r["pnl"] > 0: a["wins"] += 1
                a["up"] += r["up"]; a["down"] += r["down"]
                a["notional"] += r["notional"]; a["big"] += r["big"]
                a["tte_sum"] += r["tte_sum"]; a["tte_n"] += r["tte_n"]

    MINW = 4
    # in-sample candidates: directional, active, profitable
    is_candidates = [w for w, a in early.items()
                     if a["windows"] >= MINW and _both_ratio(a) <= 0.30 and a["pnl"] > 0]
    # held to out-of-sample
    sharps = [w for w in is_candidates
              if w in late and late[w]["windows"] >= MINW
              and _both_ratio(late[w]) <= 0.30 and late[w]["pnl"] > 0]
    # control: how many in-sample candidates even SHOWED UP enough OOS?
    appeared_oos = [w for w in is_candidates if w in late and late[w]["windows"] >= MINW]

    print(f"\nscanned {scanned} resolved 5m windows ({lookback:.0f}h); "
          f"{capped} capped/excluded from PnL")
    print(f"in-sample serious wallets active >= {MINW}w: "
          f"{sum(1 for a in early.values() if a['windows'] >= MINW)}")
    print(f"in-sample directional + profitable + active: {len(is_candidates)}")
    print(f"  ...of those, active enough OOS to judge: {len(appeared_oos)}")
    print(f"  ...STILL directional + profitable OOS (SHARPS): {len(sharps)}")
    if appeared_oos:
        print(f"  persistence rate: {len(sharps)}/{len(appeared_oos)} "
              f"= {len(sharps)/len(appeared_oos):.0%} "
              f"(coin-flip baseline ~50%; >>50% = real skill)")
    print("\n=== CANDIDATE SHARPS (persisted in + out of sample) ===")
    for w in sorted(sharps, key=lambda x: -(early[x]["pnl"] + late[x]["pnl"])):
        e, l = early[w], late[w]
        ew = e["wins"] / e["settled"] if e["settled"] else 0
        lw = l["wins"] / l["settled"] if l["settled"] else 0
        tte = (e["tte_sum"] + l["tte_sum"]) / max(e["tte_n"] + l["tte_n"], 1)
        print(f"  {w} | IS {e['windows']}w wr={ew:.0%} ${e['pnl']:+.0f} | "
              f"OOS {l['windows']}w wr={lw:.0%} ${l['pnl']:+.0f} | entry~{tte:.0f}s")
    if not sharps:
        print("  (none persisted — consistent with the directional 'winners' being luck)")


if __name__ == "__main__":
    asyncio.run(main())
