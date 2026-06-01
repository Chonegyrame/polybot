"""Discover + vet candidate esports sharp wallets by mining the esports markets.

The Polymarket leaderboard is useless for this: on the 1st of a month the
weekly/monthly windows are near-empty, the all-time board ranks GENERALISTS by
raw dollars (so a LoL/CS specialist mid-pack overall never surfaces), and there's
no native esports category. So we mine the esports markets directly.

Two stages, both in this script:

  STAGE A — discover. Sweep recent + open LoL and CS markets and, for each, pull
  the top holders via data-api `/holders?market=<cid>` (one light request per
  market — no deep pagination, so NONE of the concurrent-408 grief the
  /trades?market tape causes). Tally each wallet's total share exposure and how
  many distinct esports markets it's a top holder in. Holders is a CURRENT
  snapshot (misses exited traders) — it's a candidate signal, not the verdict.

  STAGE B — vet. For the top-K candidates, reconstruct TRUE esports PnL from the
  light, reliable /trades?user history + market resolutions (same math as
  scripts/wallet_lol_deepdive.py). This separates real value-bettors
  (VPenguin-style: ~53% win at ~0.40 entry) from market-makers and coin-flippers.

All Polymarket calls route through PolymarketClient (shared rate limiter), per
project rule.

Run:
  python -m scripts.find_esports_sharps                      # both sectors, 45d, vet top 12
  python -m scripts.find_esports_sharps --sector lol --days 90 --vet 20
  python -m scripts.find_esports_sharps --no-vet             # discovery only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.polymarket import PolymarketClient
from app.services.polymarket_types import Event

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 stdout guard

# Tag slugs confirmed live against gamma (2026-06-01). LoL has one canonical
# slug; CS is fragmented across four, so we sweep all and dedup events by id.
SECTOR_TAGS = {
    "lol": ["league-of-legends"],
    "cs": ["counter-strike", "counter-strike-2", "cs2", "csgo"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Stage A — discovery aggregation
# --------------------------------------------------------------------------


@dataclass
class WalletAgg:
    wallet: str
    name: str | None = None
    pseudonym: str | None = None
    shares: float = 0.0            # total share exposure (≤ $1/share payout)
    markets: set[str] = field(default_factory=set)
    both_sides: set[str] = field(default_factory=set)  # MM tell: holds YES+NO same mkt
    sectors: set[str] = field(default_factory=set)


async def _collect_events(
    pm: PolymarketClient, tag: str, cutoff: datetime,
) -> list[Event]:
    """All open events for `tag` + closed events resolved on/after `cutoff`."""
    out: list[Event] = []
    offset = 0
    while True:
        page = await pm.get_events(limit=100, offset=offset, closed=False, tag_slug=tag)
        if not page:
            break
        out.extend(page)
        if len(page) < 100:
            break
        offset += 100

    offset = 0
    done = False
    while not done:
        page = await pm.get_events(
            limit=100, offset=offset, closed=True, tag_slug=tag,
            order="closedTime", ascending=False,
        )
        if not page:
            break
        for ev in page:
            ct = _parse_dt(ev.raw.get("closedTime")) or _parse_dt(ev.end_date)
            if ct is not None and ct < cutoff:
                done = True
                break
            out.append(ev)
        if len(page) < 100:
            break
        offset += 100
    return out


async def discover(
    pm: PolymarketClient, sectors: list[str], cutoff: datetime,
    min_vol: float, holders_n: int, concurrency: int, max_markets: int | None,
) -> tuple[dict[str, WalletAgg], int]:
    # gather unique events, tracking which sector(s) each belongs to
    events: dict[str, tuple[Event, set[str]]] = {}
    for sector in sectors:
        for tag in SECTOR_TAGS[sector]:
            for ev in await _collect_events(pm, tag, cutoff):
                cur = events.get(ev.id)
                if cur is None:
                    events[ev.id] = (ev, {sector})
                else:
                    cur[1].add(sector)
    print(f"  {len(events)} unique esports events")

    markets: dict[str, set[str]] = {}  # cid -> sectors
    for ev, evs_sectors in events.values():
        for m in ev.markets:
            if m.condition_id and (m.volume_num or 0.0) >= min_vol:
                markets.setdefault(m.condition_id, set()).update(evs_sectors)
    cids = list(markets)
    if max_markets:
        cids = cids[:max_markets]
    print(f"  {len(cids)} markets above ${min_vol:,.0f} vol → pulling /holders "
          f"(concurrency {concurrency})")

    sem = asyncio.Semaphore(concurrency)

    async def _h(cid: str) -> tuple[str, list]:
        async with sem:
            return cid, await pm.get_market_holders(cid, limit=holders_n)

    results = await asyncio.gather(*(_h(c) for c in cids))

    agg: dict[str, WalletAgg] = {}
    empty = 0
    for cid, tokens in results:
        if not tokens:
            empty += 1
            continue
        sectors_for_cid = markets[cid]
        seen_side: dict[str, set] = defaultdict(set)  # wallet -> outcome indices
        for tok in tokens:
            for h in tok.get("holders", []):
                w = str(h.get("proxyWallet") or "").lower()
                if not w:
                    continue
                a = agg.get(w)
                if a is None:
                    a = agg[w] = WalletAgg(wallet=w)
                a.shares += float(h.get("amount") or 0.0)
                a.markets.add(cid)
                a.sectors |= sectors_for_cid
                a.name = a.name or h.get("name")
                a.pseudonym = a.pseudonym or h.get("pseudonym")
                seen_side[w].add(h.get("outcomeIndex"))
        for w, sides in seen_side.items():
            if len(sides) > 1:
                agg[w].both_sides.add(cid)
    return agg, empty


# --------------------------------------------------------------------------
# Stage B — per-wallet PnL vetting (same math as wallet_lol_deepdive)
# --------------------------------------------------------------------------

_ESPORTS_PREFIXES = ("lol:", "cs2:", "csgo:", "cs:go", "counter-strike", "valorant:", "dota")


def _notional_trade(t) -> float:
    return t.usdc_size if t.usdc_size else t.price * t.size


async def vet_wallet(pm: PolymarketClient, wallet: str, max_trades: int = 2500) -> dict:
    # data-api hard-caps /trades?user at offset 3000 ("max historical activity
    # offset of 3000 exceeded" → terminal 400). page_size 500 means offsets
    # 0,500,…,2500 are the last safe page; 2500 trades keeps us clear. So for
    # very active wallets this is their most-recent ~2500 trades (recent form),
    # not full lifetime — fine for a sharp/MM/coin-flip read.
    trades = []
    async for t in pm.iter_trades(wallet, page_size=500):
        trades.append(t)
        if len(trades) >= max_trades:
            break
    if not trades:
        return {"wallet": wallet, "trades": 0}

    # esports team set from explicit titles, to also catch handicap markets
    teams: set[str] = set()
    for t in trades:
        tl = (t.title or "").lower()
        if tl.startswith(("lol:", "cs2:", "csgo:")):
            m = re.search(r":\s*(.+?)\s+vs\s+(.+?)\s*(?:-|\(|$)", tl)
            if m:
                teams.add(m.group(1).strip())
                teams.add(m.group(2).strip())

    def is_esports(title: str | None) -> bool:
        tl = (title or "").lower()
        if any(p in tl for p in _ESPORTS_PREFIXES) or "league of legends" in tl:
            return True
        if "handicap" in tl or "game" in tl or "map" in tl:
            return any(tm in tl for tm in teams if len(tm) > 2)
        return False

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

    by_cid: dict[str, list] = defaultdict(list)
    for t in trades:
        by_cid[t.condition_id].append(t)

    es = []  # esports resolved markets
    for cid, ts_ in by_cid.items():
        title = title_by.get(cid) or ts_[0].title
        if not is_esports(title):
            continue
        net: dict[str, float] = defaultdict(float)
        cash = staked = 0.0
        entry = []
        for t in ts_:
            amt = _notional_trade(t)
            if t.side == "BUY":
                net[t.asset] += t.size; cash -= amt; staked += amt; entry.append(t.price)
            elif t.side == "SELL":
                net[t.asset] -= t.size; cash += amt
        wt = winner_token.get(cid)
        if wt is None:
            continue  # unresolved — skip for win-rate honesty
        payout = max(net.get(wt, 0.0), 0.0)
        es.append({"pnl": cash + payout, "staked": staked,
                   "entry": (sum(entry) / len(entry)) if entry else None})

    last_ts = max((t.timestamp for t in trades if t.timestamp), default=None)
    if not es:
        return {"wallet": wallet, "trades": len(trades), "es_markets": 0,
                "last": last_ts.date().isoformat() if last_ts else None}
    pnl = sum(r["pnl"] for r in es)
    staked = sum(r["staked"] for r in es)
    wins = sum(1 for r in es if r["pnl"] > 0)
    ent = [r["entry"] for r in es if r["entry"]]
    return {
        "wallet": wallet, "trades": len(trades), "es_markets": len(es),
        "es_pnl": pnl, "win_rate": wins / len(es), "staked": staked,
        "roi": (pnl / staked) if staked else None,
        "median_entry": statistics.median(ent) if ent else None,
        "last": last_ts.date().isoformat() if last_ts else None,
    }


# --------------------------------------------------------------------------


async def main() -> None:
    ap = argparse.ArgumentParser(description="Discover + vet candidate esports sharp wallets.")
    ap.add_argument("--sector", choices=["lol", "cs", "both"], default="both")
    ap.add_argument("--days", type=int, default=45, help="lookback for resolved markets")
    ap.add_argument("--min-vol", type=float, default=4000.0,
                    help="skip markets below this total volume")
    ap.add_argument("--holders", type=int, default=20, help="top holders per outcome to pull")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--max-markets", type=int, default=None)
    ap.add_argument("--top", type=int, default=40, help="discovery rows to print")
    ap.add_argument("--vet", type=int, default=12, help="vet this many top candidates")
    ap.add_argument("--no-vet", action="store_true")
    ap.add_argument("--out", default="esports_sharps_candidates.json")
    args = ap.parse_args()

    sectors = ["lol", "cs"] if args.sector == "both" else [args.sector]
    cutoff = _now() - timedelta(days=args.days)
    print(f"sweeping sectors={sectors} | resolved since {cutoff.date()} "
          f"| vol floor ${args.min_vol:,.0f}")

    async with PolymarketClient() as pm:
        agg, empty = await discover(
            pm, sectors, cutoff, args.min_vol, args.holders,
            args.concurrency, args.max_markets,
        )
        if empty:
            print(f"  note: {empty} markets returned no holders")

        # exclude obvious market-makers: top holder on BOTH sides in many markets
        ranked = sorted(agg.values(), key=lambda a: a.shares, reverse=True)
        print(f"\nTop {args.top} candidates by share exposure "
              f"({len(agg)} wallets seen). 'both' = #markets holding YES+NO (MM tell).\n")
        hdr = (f"{'#':>3} {'name':20} {'shares':>12} {'mkts':>5} {'both':>5} "
               f"{'sec':>6}  wallet")
        print(hdr); print("-" * len(hdr))
        for i, a in enumerate(ranked[: args.top], 1):
            nm = (a.name or a.pseudonym or "?")[:20]
            sec = "+".join(sorted(a.sectors))
            print(f"{i:>3} {nm:20} {a.shares:>12,.0f} {len(a.markets):>5} "
                  f"{len(a.both_sides):>5} {sec:>6}  {a.wallet}")

        payload = [
            {"wallet": a.wallet, "name": a.name, "pseudonym": a.pseudonym,
             "shares": round(a.shares, 2), "markets": len(a.markets),
             "both_sides_markets": len(a.both_sides), "sectors": sorted(a.sectors)}
            for a in ranked
        ]

        # ---- Stage B: vet top candidates ----
        if not args.no_vet and ranked:
            # skip wallets that look like MMs (both-sided in >=3 markets) when
            # picking who to vet, but still vet a generous pool
            pool = [a for a in ranked if len(a.both_sides) < 3][: args.vet]
            print(f"\nvetting {len(pool)} candidates (true esports PnL from "
                  f"/trades?user)…\n")
            vets = await asyncio.gather(
                *(vet_wallet(pm, a.wallet) for a in pool), return_exceptions=True
            )
            vets = [v for v in vets if isinstance(v, dict) and v.get("es_markets")]
            vets.sort(key=lambda v: v.get("es_pnl", 0), reverse=True)
            byw = {a.wallet: a for a in ranked}
            hdr2 = (f"{'name':20} {'es_pnl $':>12} {'win%':>5} {'ROI':>7} "
                    f"{'entry':>5} {'mkts':>5} {'last':>11}  wallet")
            print(hdr2); print("-" * len(hdr2))
            for v in vets:
                a = byw[v["wallet"]]
                nm = (a.name or a.pseudonym or "?")[:20]
                roi = f"{v['roi']:+.0%}" if v.get("roi") is not None else "—"
                ent = f"{v['median_entry']:.2f}" if v.get("median_entry") else "—"
                print(f"{nm:20} {v['es_pnl']:>12,.0f} {v['win_rate']*100:>4.0f}% "
                      f"{roi:>7} {ent:>5} {v['es_markets']:>5} {str(v['last']):>11}  "
                      f"{v['wallet']}")
            # fold vetting into the dumped payload
            vmap = {v["wallet"]: v for v in vets}
            for p in payload:
                if p["wallet"] in vmap:
                    p["vet"] = vmap[p["wallet"]]

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nfull ranking ({len(payload)} wallets) → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
