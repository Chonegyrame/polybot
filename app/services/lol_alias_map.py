"""Polymarket-name -> Oracle's Elixir canonical-name alias map.

Seed values for the lol_team_aliases table. The matcher reads from the DB
table at runtime (lol_team_aliases is the source of truth), but this file
is the curated seed and the place to add new known aliases.

Confidence levels:
  high   - verified against both Polymarket market title and OE team page
  medium - inferred from naming conventions, plausible but not directly verified
  low    - guess; should land in the manual-review queue on first encounter

Sources informing this map:
  - lol_bot/research/ORACLES_ELIXIR_JOIN.md (Section 6)
  - direct inspection of polymarket_lol_market_meta team_a/team_b values
  - direct inspection of the 2026 OE CSV teamname column
"""

from __future__ import annotations

# Polymarket-style team name (verbatim from market_title parser) -> OE 'teamname'.
# All names stored verbatim. The matcher normalizes both sides at lookup time
# (lowercase + NFKD + punctuation strip) so casing doesn't affect lookups.
POLYMARKET_TO_OE: dict[str, tuple[str, str]] = {
    # ---- Identity mappings (same name on both sides) -------------------
    # Listed explicitly so the alias path always hits FIRST — skips fuzzy
    # matching entirely for known clean teams. Reduces ambiguity in metrics.
    "Karmine Corp":            ("Karmine Corp", "high"),
    "Movistar KOI":            ("Movistar KOI", "high"),
    "T1":                      ("T1", "high"),
    "Gen.G":                   ("Gen.G", "high"),
    "Dplus KIA":               ("Dplus KIA", "high"),
    "Hanwha Life Esports":     ("Hanwha Life Esports", "high"),
    "Bilibili Gaming":         ("Bilibili Gaming", "high"),
    "JD Gaming":               ("JD Gaming", "high"),
    "Top Esports":             ("Top Esports", "high"),
    "Anyone's Legend":         ("Anyone's Legend", "high"),
    "Weibo Gaming":            ("Weibo Gaming", "high"),
    "Team Heretics":           ("Team Heretics", "high"),
    "Team Vitality":           ("Team Vitality", "high"),
    "G2 Esports":              ("G2 Esports", "high"),
    "Fnatic":                  ("Fnatic", "high"),
    "GIANTX":                  ("GIANTX", "high"),
    "Cloud9":                  ("Cloud9", "high"),
    "100 Thieves":             ("100 Thieves", "high"),
    "FlyQuest":                ("FlyQuest", "high"),
    "Shopify Rebellion":       ("Shopify Rebellion", "high"),
    "paiN Gaming":             ("paiN Gaming", "high"),
    "LOUD":                    ("LOUD", "high"),
    "DRX":                     ("DRX", "high"),
    "Kiwoom DRX":              ("DRX", "high"),  # sponsor variant
    "KT Rolster":              ("KT Rolster", "high"),
    "CTBC Flying Oyster":      ("CTBC Flying Oyster", "high"),
    "GAM Esports":             ("GAM Esports", "high"),
    "EDward Gaming":           ("EDward Gaming", "high"),
    "Invictus Gaming":         ("Invictus Gaming", "high"),
    "LNG Esports":             ("LNG Esports", "high"),
    "LGD Gaming":              ("LGD Gaming", "high"),
    "Team Liquid":             ("Team Liquid", "high"),
    "Dignitas":                ("Dignitas", "high"),
    "MAD Lions":               ("MAD Lions KOI", "high"),
    "MAD Lions KOI":           ("MAD Lions KOI", "high"),

    # ---- Real renames / casing fixes -----------------------------------
    "NIP":                     ("Ninjas in Pyjamas", "high"),
    "Ninjas in Pyjamas":       ("Ninjas in Pyjamas", "high"),
    "KaBuM!":                  ("KaBuM! Esports", "high"),
    "KaBuM! Esports":          ("KaBuM! Esports", "high"),
    "BNK FEARX":               ("BNK FearX", "high"),  # OE uses lowercase 'r'
    "BNK FearX":               ("BNK FearX", "high"),
    "Nongshim":                ("NS RedForce", "high"),
    "Nongshim RedForce":       ("NS RedForce", "high"),
    "NongShim RedForce":       ("NS RedForce", "high"),
    "NS RedForce":             ("NS RedForce", "high"),
    "OKSavingsBank":           ("OKSavingsBank BRION", "high"),
    "OKSavingsBank BRION":     ("OKSavingsBank BRION", "high"),
    "OKBRO":                   ("OKSavingsBank BRION", "medium"),
    "GMBLERS ESPORTS":         ("GMBLERS Esports", "medium"),  # may not exist in OE
    "GMBLERS Esports":         ("GMBLERS Esports", "medium"),

    # ---- Sponsor variants (Brazilian/regional teams) -------------------
    "Vivo Keyd":               ("Vivo Keyd Stars", "medium"),
    "Vivo Keyd Stars":         ("Vivo Keyd Stars", "high"),
    "RED Canids":              ("RED Canids", "high"),
    "RED Canids Kalunga":      ("RED Canids", "medium"),
    "Flamengo":                ("Flamengo Los Grandes", "medium"),
    "Flamengo Los Grandes":    ("Flamengo Los Grandes", "high"),
    "Los Grandes":             ("Flamengo Los Grandes", "medium"),
    "RED Academy":             ("RED Canids Academy", "low"),

    # ---- Academy / 2nd team variants -----------------------------------
    "Karmine Corp Blue":       ("Karmine Corp Blue", "medium"),

    # ---- Confirmed-by-CSV teams I should add as identity ---------------
    "Hanwha Life Esports Challengers": ("Hanwha Life Esports Challengers", "medium"),
    "Nongshim Esports Academy":         ("NS Esports Academy", "low"),
    "Dplus KIA Academy":                ("Dplus KIA Challengers", "low"),

    # ---- Worlds/MSI/EWC international identity-mappings ---------------
    "Natus Vincere":           ("Natus Vincere", "high"),
    "Solary":                  ("Solary", "high"),
    "BDS":                     ("Team BDS", "medium"),
    "SK Gaming":               ("SK Gaming", "high"),
    "Shifters":                ("Shifters", "medium"),
}


# Teams Polymarket lists that Oracle's Elixir very likely does NOT track.
# These come from tier-3 leagues (LIT, HLL, Road of Legends, sub-tier
# academies). Adding them here means the matcher skips fuzzy matching
# rather than wasting cycles and clogging the review queue.
#
# HEURISTIC, NOT A HARD BLOCK: if a team gets promoted into an OE-covered
# league, the matcher should re-evaluate. (Implementation-wise: the matcher
# checks OE_UNTRACKED_HINT as a default-skip, but if Layer 3 fuzzy matching
# finds a close OE match anyway, we trust that and update the alias.)
OE_UNTRACKED_HINT: set[str] = {
    # confirmed in OE inspection — DO NOT add here. Keeping comment for clarity.
    # Confirmed: LIT, HLL, ROL teams are in OE based on 2026 CSV inspection.
    # so the "tier-3 untracked" assumption is wrong for these specific leagues.
    # Leaving this set empty by default. We'll populate it as we discover
    # genuinely untracked teams during the join run.
}


# Polymarket's title-parsed league string -> OE league code.
# OE uses short codes like 'LCK', 'LPL', 'LEC', etc. — see top-leagues
# query against 2026 CSV.
PM_LEAGUE_TO_OE: dict[str, str] = {
    # Top-tier major leagues
    "LCK":                     "LCK",
    "LPL":                     "LPL",
    "LEC":                     "LEC",
    "LCS":                     "LCS",
    "LCP":                     "LCP",
    "LJL":                     "LJL",
    "PCS":                     "PCS",
    "VCS":                     "VCS",
    "LTA North":               "LTA N",
    "LTA South":               "LTA S",
    # 2025+ merger: CBLOL + LLA -> LTA S
    "CBLOL":                   "LTA S",
    "LLA":                     "LTA S",
    "CBLOL Regular Season":    "LTA S",
    "CBLOL Playoffs":          "LTA S",

    # Regional / academy
    "LFL":                     "LFL",
    "Prime League":            "PRM",
    "PRM":                     "PRM",
    "Superliga":               "SL",
    "LCK Challengers":         "LCKC",
    "LCK Challengers League":  "LCKC",
    "LCK Challengers League Rounds 1-2": "LCKC",
    "NACL":                    "NACL",
    "North American Challengers League": "NACL",
    "North American Challengers League Regular Season": "NACL",
    "Arabian League":          "AL",
    "Arabian League Group Stage": "AL",
    "Esports Manager":         "EM",
    "Hitpoint Liga":           "LIT",  # OE confirmed
    "LIT Playoffs":            "LIT",
    "LIT":                     "LIT",
    "Hungarian Liga":          "HLL",
    "HLL":                     "HLL",
    "HLL Regular Season":      "HLL",
    "HLL Playoffs":            "HLL",
    "Road Of Legends":         "ROL",
    "Road Of Legends Regular Season": "ROL",
    "TCL":                     "TCL",
    "TCL Regular Season":      "TCL",
    "EBL":                     "EBL",
    "RL":                      "RL",
    "HW":                      "HW",
    "LAS":                     "LAS",
    "LPLOL":                   "LPLOL",
    "LPLOL Regular Season":    "LPLOL",
    "LES":                     "LES",
    "LES Regular Season":      "LES",

    # International tournaments
    "Worlds":                  "WLDs",
    "MSI":                     "MSI",
    "Esports World Cup":       "EWC",
    "EWC":                     "EWC",
    "Esports World Cup EMEA Qualifier Playoffs": "EWC",
    "Esports World Cup Asia-Pacific Qualifier Playoffs": "EWC",
    "Esports World Cup Korea Qualifier Playoffs": "EWC",

    # LPL stages
    "LPL Group Ascend":        "LPL",
    "LPL Group Nirvana":       "LPL",
    "LPL Playoffs":            "LPL",

    # LCK stages (various sub-tournaments)
    "LCK Rounds 1-2":          "LCK",
    "LCK Cup":                 "LCK",
    "LCK Cup Group Stage":     "LCK",
    "LCK 2026 Season Winner":  "LCK",
    "LCK Playoffs":            "LCK",

    # LEC stages
    "LEC Spring":              "LEC",
    "LEC Summer":              "LEC",
    "LEC Regular Season":      "LEC",
    "LEC Playoffs":            "LEC",
    "LEC 2026 Spring":         "LEC",
}


def normalize_team_name(name: str | None) -> str:
    """Lowercase + strip whitespace/punctuation so different conventions
    line up. Use for exact-match comparison (Layer 2 of the matcher).

    Keeps internal letters but drops spaces, dots, exclamation, etc.
    "Gen.G" -> "geng", "KaBuM!" -> "kabum", "Cloud9" -> "cloud9".
    """
    if not name:
        return ""
    import unicodedata
    # NFKD: separate base characters from accents/diacritics
    decomposed = unicodedata.normalize("NFKD", name)
    # Drop everything that isn't an alphanumeric ASCII char
    return "".join(c.lower() for c in decomposed if c.isalnum())


def normalize_league(name: str | None) -> str | None:
    """Map a Polymarket league string to the OE league code.

    Tries exact match, then partial-prefix match. Returns None if neither
    works — caller should treat unknown league as 'no league constraint'
    rather than fail the match.
    """
    if not name:
        return None
    s = name.strip()
    if s in PM_LEAGUE_TO_OE:
        return PM_LEAGUE_TO_OE[s]
    # Partial-prefix: "LCK Rounds 1-2" should hit "LCK"
    for prefix in sorted(PM_LEAGUE_TO_OE.keys(), key=len, reverse=True):
        if s.startswith(prefix):
            return PM_LEAGUE_TO_OE[prefix]
    return None
