"""Vet a shortlist of candidate wallets for true esports PnL, rank by ROI.

Reuses vet_wallet() from the discovery script. Reads wallets from
esports_sharps_candidates.json if present (LoL-active, by exposure); otherwise
falls back to the embedded top-40-board shortlist from the 2026-06-01 sweep.

Ranks by ROI but only among wallets with >= MIN_MARKETS resolved esports
markets (small samples shown separately so a 3-for-3 fluke can't top the board).

Run: python -u -m scripts.vet_candidates [N]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from app.services.polymarket import PolymarketClient
from scripts.find_esports_sharps import vet_wallet

sys.stdout.reconfigure(encoding="utf-8")

MIN_MARKETS = 8  # min resolved esports markets to qualify for the ROI board

# LoL-active wallets from the 2026-06-01 top-40 board (5 pure-CS dropped).
FALLBACK = [
    ("bossoskil1", "0xa5ea13a81d2b7e8e424b182bdc1db08e756bd96a"),
    ("?", "0xa5ef39c3d3e10d0b270233af41cac69796b12966"),
    ("TrevorPlovdivBulgari", "0x13414a77a4be48988851c73dfd824d0168e70853"),
    ("esportGG", "0xf070207d315d47fd07870e464d3ded9151f5ac55"),
    ("0xcF609D32", "0xcf609d3256f0f37f0595e5dc64012fa3a8fea6f5"),
    ("TAIWANNUMBERONE", "0xe015b5a2a299167be835a2fd1e86f09c49e06ffd"),
    ("Zywoo123", "0xc44f432b014f36679e13615f95996eac32bbd49f"),
    ("goo00dluck", "0x17f1e5dee3de2a585ea990fce56dd6ed57f6af18"),
    ("fkgggg2mouzfuria", "0x52ecea7b3159f09db589e4f4ee64872fd0bba6f3"),
    ("0xB10bf118", "0xb10bf118b2a3c1cff0379a4134a82eb6d51e0b04"),
    ("diooson", "0x8fbb3c32496d4bcff8f456c3342f90edaaa09a17"),
    ("aabba", "0xec926e101124b838adedaa511b769197544f6b59"),
    ("SineNooneEI", "0x38337de21ff0bb0a11a40761507d51e318d633d1"),
    ("Antojno66", "0x9770ca178764cdca1271934ad613ad7ab4a10ff5"),
    ("TURURUTURURURU", "0x8c0b024c17831a0dde038547b7e791ae6a0d7aa5"),
    ("Anjun", "0x43372356634781eea88d61bbdd7824cdce958882"),
    ("noMoohyun523", "0x63a51cbb37341837b873bc29d05f482bc2988e33"),
    ("kinogos", "0xc75a9e7cf6d30a184ccf04c98f4bce7fc8c01ab2"),
    ("ColinHe", "0x684299e6ac5595ff7f78f1f1edc97e77fea420d0"),
    ("frankfrankfrank", "0xea2b4224411e723499a803ce3f4758779fb31fc6"),
    ("eyesneverlie", "0xef2684030d14a67c2a30a3e4fc00eed7a99505ba"),
    ("0xF201A19b", "0xf201a19b43471261a3c1ba9247335d55270e527e"),
    ("EVplusrebate", "0x93bc1f104bc72c9141fc41c2acb2265f54a28ca3"),
    ("BRDHD", "0xdd882a70680633a75516f6ed5fce443e2d96dadb"),
    ("Deep7", "0x23073ad0c9dff45353cedb11760570b995663934"),
    ("fuc.your.mother", "0x76cf0286fa25599a491ea4980abee915eece9452"),
    ("0x18bb751d", "0x18bb751dcec69d2f15a683a640e61e22d0d9e5cd"),
    ("Lakersfan111", "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e"),
    ("0x6Bac5865", "0x6bac58654393453854cb29b01892b5fd62096fb7"),
    ("SemyonMarmeladov", "0x37e4728b3c4607fb2b3b205386bb1d1fb1a8c991"),
    ("SimpleTony", "0x52b7fc8a97df5f18974c06a83909f60c81010aa1"),
    ("starbuck02", "0xea7957606f259bcba522a4681494555547a7a9cc"),
    ("AvishaiBass", "0x6df6e2a9ba1e8d7609daada0a83138817f4a8458"),
    ("-Guapo--", "0x87256d0406f876184a66cc158415903bb242eb10"),
    ("ferrariChampions2026", "0xfe787d2da716d60e8acff57fb87eb13cd4d10319"),
]


def _load() -> list[tuple[str, str]]:
    path = "esports_sharps_candidates.json"
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 35
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        lol = [d for d in data if "lol" in (d.get("sectors") or [])]
        return [(d.get("name") or d.get("pseudonym") or "?", d["wallet"]) for d in lol[:n]]
    return FALLBACK[:n]


async def main() -> None:
    wallets = _load()
    print(f"vetting {len(wallets)} LoL-active candidates (recent ≤2500 trades each)…\n")
    sem = asyncio.Semaphore(6)
    name_by = {w.lower(): nm for nm, w in wallets}

    async def _v(w: str):
        async with sem:
            try:
                return await vet_wallet(pm, w)
            except Exception as e:  # noqa: BLE001
                return {"wallet": w, "error": str(e)[:60]}

    async with PolymarketClient() as pm:
        vets = await asyncio.gather(*(_v(w) for _, w in wallets))

    good = [v for v in vets if v.get("es_markets")]
    qual = [v for v in good if v["es_markets"] >= MIN_MARKETS]
    small = [v for v in good if v["es_markets"] < MIN_MARKETS]
    qual.sort(key=lambda v: (v.get("roi") or -9), reverse=True)
    small.sort(key=lambda v: v.get("es_pnl", 0), reverse=True)

    def row(v):
        nm = name_by.get(v["wallet"], "?")[:20]
        roi = f"{v['roi']:+.0%}" if v.get("roi") is not None else "—"
        ent = f"{v['median_entry']:.2f}" if v.get("median_entry") else "—"
        return (f"{nm:20} {v['es_pnl']:>11,.0f} {v['win_rate']*100:>4.0f}% {roi:>7} "
                f"{ent:>5} {v['es_markets']:>5} {str(v.get('last')):>11}  {v['wallet']}")

    hdr = (f"{'name':20} {'es_pnl $':>11} {'win%':>5} {'ROI':>7} {'entry':>5} "
           f"{'mkts':>5} {'last':>11}  wallet")
    print(f"=== RANKED BY ROI (≥{MIN_MARKETS} resolved esports markets) ===")
    print(hdr); print("-" * len(hdr))
    for v in qual:
        print(row(v))
    if small:
        print(f"\n=== smaller sample (<{MIN_MARKETS} markets — ranked by PnL, treat as soft) ===")
        print(hdr); print("-" * len(hdr))
        for v in small:
            print(row(v))
    errs = [v for v in vets if v.get("error") or (not v.get("es_markets"))]
    if errs:
        print(f"\n{len(errs)} wallets: no resolved esports markets in recent history "
              f"or errored (likely pure-CS, exited, or >2500-trade truncation).")


if __name__ == "__main__":
    asyncio.run(main())
