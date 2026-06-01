"""Seed the esports_sharps watchlist.

Wallets + recent-form vetted stats from the 2026-06-01 discovery sweep
(scripts/find_esports_sharps.py → scripts/vet_candidates.py), plus VPenguin
(user-flagged). `follow=1` = mirror-worthy; `follow=0` = watch-only (net-negative
recent form, or a maker whose edge isn't copyable). Stats are RECENT-FORM
(≤2500 most-recent trades) and reconstructed — directional, not audited.

Re-runnable: upsert refreshes stats without disturbing added_at or logged actions.

Run: python -m esports.watchlist
"""

from __future__ import annotations

import sys

from esports import db

sys.stdout.reconfigure(encoding="utf-8")

# (name, wallet, sectors, pnl, win_rate, roi, median_entry, markets, follow, note)
SEED = [
    ("ColinHe", "0x684299e6ac5595ff7f78f1f1edc97e77fea420d0", "lol", 1596210, 0.59, 1.21, 0.47, 741, 1, "LoL-only top value-bettor"),
    ("bossoskil1", "0xa5ea13a81d2b7e8e424b182bdc1db08e756bd96a", "lol,cs", 5310020, 0.54, 0.35, 0.48, 818, 1, "biggest absolute PnL"),
    ("Zywoo123", "0xc44f432b014f36679e13615f95996eac32bbd49f", "lol,cs", 1241835, 0.55, 0.44, 0.52, 625, 1, None),
    ("esportGG", "0xf070207d315d47fd07870e464d3ded9151f5ac55", "lol,cs", 756608, 0.49, 0.37, 0.48, 477, 1, None),
    ("0xB10bf118", "0xb10bf118b2a3c1cff0379a4134a82eb6d51e0b04", "lol,cs", 557510, 0.46, 1.21, 0.41, 502, 1, None),
    ("fkgggg2mouzfuria", "0x52ecea7b3159f09db589e4f4ee64872fd0bba6f3", "lol,cs", 445215, 0.57, 0.04, 0.55, 941, 1, None),
    ("SemyonMarmeladov", "0x37e4728b3c4607fb2b3b205386bb1d1fb1a8c991", "lol,cs", 366506, 0.57, 0.55, 0.47, 37, 1, "smaller sample"),
    ("0x18bb751d", "0x18bb751dcec69d2f15a683a640e61e22d0d9e5cd", "lol,cs", 421067, 0.30, 0.50, 0.24, 522, 1, "longshot style, low win%"),
    ("Anjun", "0x43372356634781eea88d61bbdd7824cdce958882", "lol,cs", 218612, 0.67, 0.16, 0.49, 359, 1, "highest win-rate at scale"),
    ("TURURUTURURURU", "0x8c0b024c17831a0dde038547b7e791ae6a0d7aa5", "lol,cs", 211406, 0.52, 0.09, 0.42, 887, 1, None),
    ("Deep7", "0x23073ad0c9dff45353cedb11760570b995663934", "lol,cs", 177546, 0.53, 0.07, 0.49, 593, 1, None),
    ("SineNooneEI", "0x38337de21ff0bb0a11a40761507d51e318d633d1", "lol,cs", 143231, 0.52, 0.02, 0.50, 891, 1, None),
    ("0xF201A19b", "0xf201a19b43471261a3c1ba9247335d55270e527e", "lol", 128296, 0.53, 0.10, 0.49, 194, 1, None),
    ("eyesneverlie", "0xef2684030d14a67c2a30a3e4fc00eed7a99505ba", "lol,cs", 93115, 0.38, 0.11, 0.36, 677, 1, None),
    ("frankfrankfrank", "0xea2b4224411e723499a803ce3f4758779fb31fc6", "lol,cs", 87591, 0.60, 0.25, 0.50, 42, 1, "smaller sample"),
    ("0xcF609D32", "0xcf609d3256f0f37f0595e5dc64012fa3a8fea6f5", "lol,cs", 86056, 0.57, 0.03, 0.58, 848, 1, None),
    ("TrevorPlovdivBulgari", "0x13414a77a4be48988851c73dfd824d0168e70853", "lol,cs", 72974, 0.46, 0.03, 0.45, 558, 1, "very high frequency"),
    ("AvishaiBass", "0x6df6e2a9ba1e8d7609daada0a83138817f4a8458", "lol,cs", 60605, 0.45, 0.30, 0.28, 434, 1, None),
    ("0x6Bac5865", "0x6bac58654393453854cb29b01892b5fd62096fb7", "lol", 55308, 0.39, 0.15, 0.35, 156, 1, None),
    ("-Guapo--", "0x87256d0406f876184a66cc158415903bb242eb10", "lol,cs", 53769, 0.38, 0.26, 0.38, 709, 1, None),
    ("SimpleTony", "0x52b7fc8a97df5f18974c06a83909f60c81010aa1", "lol,cs", 52267, 0.30, 0.18, 0.28, 1171, 1, "very high frequency"),
    ("kinogos", "0xc75a9e7cf6d30a184ccf04c98f4bce7fc8c01ab2", "lol", 39959, 0.49, 0.65, 0.12, 662, 1, "deep-underdog hunter"),
    ("fuc.your.mother", "0x76cf0286fa25599a491ea4980abee915eece9452", "lol,cs", 34542, 0.49, 0.06, 0.37, 101, 1, None),
    ("Antojno66", "0x9770ca178764cdca1271934ad613ad7ab4a10ff5", "lol,cs", 31875, 0.51, 0.01, 0.51, 547, 1, None),
    ("goo00dluck", "0x17f1e5dee3de2a585ea990fce56dd6ed57f6af18", "lol,cs", 26208, 0.58, 1.61, 0.06, 84, 1, "deep-underdog hunter"),
    ("aabba", "0xec926e101124b838adedaa511b769197544f6b59", "lol,cs", 11197, 0.33, 0.95, 0.18, 64, 1, "longshot style"),
    ("EVplusrebate", "0x93bc1f104bc72c9141fc41c2acb2265f54a28ca3", "lol,cs", 877257, 0.61, 2.06, 0.37, 1320, 1, "high-freq directional value/underdog hunter (verified NOT a maker: 4.7% both-sided, 13% buys <=0.20)"),
    # ---- watch-only (follow=0): net-negative recent form ----
    ("starbuck02", "0xea7957606f259bcba522a4681494555547a7a9cc", "lol,cs", -163934, 0.61, -0.09, 0.63, 240, 0, "net negative recent"),
    ("Lakersfan111", "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e", "lol,cs", -119139, 0.48, -0.11, 0.49, 284, 0, "net negative recent"),
    ("BRDHD", "0xdd882a70680633a75516f6ed5fce443e2d96dadb", "lol,cs", -205670, 0.54, -0.12, 0.54, 586, 0, "net negative recent"),
    ("noMoohyun523", "0x63a51cbb37341837b873bc29d05f482bc2988e33", "lol,cs", -522989, 0.39, -0.13, 0.34, 44, 0, "net negative recent"),
    ("diooson", "0x8fbb3c32496d4bcff8f456c3342f90edaaa09a17", "lol,cs", -201685, 0.52, -0.20, 0.51, 190, 0, "net negative recent"),
    ("TAIWANNUMBERONE", "0xe015b5a2a299167be835a2fd1e86f09c49e06ffd", "lol", -246108, 0.39, -0.22, 0.40, 236, 0, "net negative recent"),
    # ---- user-flagged (stats from ESPORTS_PLAN.md, lifetime not recent) ----
    ("VPenguin", "0xfbf3d501e88815464642d0e913f15379c3eeb218", "lol", 1450000, 0.53, None, 0.40, None, 1, "user-flagged; ~$1.45M esports lifetime @0.40 median"),
]


def seed() -> None:
    conn = db.connect()
    for (name, wallet, sectors, pnl, win, roi, entry, mkts, follow, note) in SEED:
        db.upsert_sharp(
            conn, wallet=wallet, name=name, pseudonym=None, sectors=sectors,
            vet_pnl=pnl, vet_win_rate=win, vet_roi=roi, vet_median_entry=entry,
            vet_markets=mkts, note=note, follow=follow, active=1,
        )
    rows = db.active_wallets(conn)
    foll = sum(1 for r in rows if r["follow"])
    print(f"seeded {len(rows)} wallets ({foll} follow, {len(rows) - foll} watch-only) "
          f"→ {db.DEFAULT_DB}")
    conn.close()


if __name__ == "__main__":
    seed()
