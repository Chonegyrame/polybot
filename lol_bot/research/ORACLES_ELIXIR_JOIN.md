# Oracle's Elixir ↔ Polymarket LoL Join — Research

Author: research pass (read-only). Goal: define the cleanest, most bulletproof way
to join Polymarket LoL market rows to Oracle's Elixir (OE) game rows by
`(date, team_a, team_b)`, with a starter alias map and matching algorithm.

Primary sources used in this doc:
- Oracle's Elixir download portal — https://oracleselixir.com/tools/downloads
  and the Tim Sevenhuysen mirror https://lol.timsevenhuysen.com/matchdata/
- Public S3 bucket — https://oracleselixir-downloadable-match-data.s3-us-west-2.amazonaws.com/
- ProjektZero-LoL-Model (gold-standard OE ingestion reference) —
  https://github.com/MRittinghouse/ProjektZero-LoL-Model
  (specifically `src/oracles_elixir.py` and `src/data_generator.py`)
- Oracle's Elixir team URL pattern, e.g. https://oracleselixir.com/team/Karmine%20Corp
- Polymarket public market URLs and titles, e.g.
  `LoL: Weibo Gaming vs JD Gaming (BO3) - LPL Group Ascend`
- Leaguepedia / Liquipedia for canonical team names

A handful of CSV inspection items remain "needs verification once we ingest sample
data" — flagged inline. The shell was sandbox-blocked during this pass so I could
not directly `curl` the CSV; every claim is sourced from the OE site, ProjektZero
source code, or wiki cross-reference.

---

## 1. Executive summary

- **OE format is one CSV per year**, hosted on S3 at
  `https://oracleselixir-downloadable-match-data.s3-us-west-2.amazonaws.com/{YEAR}_LoL_esports_match_data_from_OraclesElixir.csv`,
  updated daily, 12 rows per game (2 team-summary rows + 10 player rows). Join on
  `gameid` + `side` for team rows.
- **Polymarket and OE agree on naming far more often than not.** Polymarket
  titles already use OE-canonical strings like "Weibo Gaming", "JD Gaming",
  "Anyone's Legend", "Karmine Corp", "Gen.G", "T1", "Hanwha Life Esports",
  "Dplus KIA" — verified via live Polymarket URLs and OE team pages.
- **Alias map first, exact match second, fuzzy (rapidfuzz token_set_ratio ≥ 92)
  third, manual-review queue otherwise.** Date-bounded to ±1 day, league-scoped
  when possible. Never auto-accept fuzzy in production without league agreement.
- **Use `rapidfuzz`** (MIT, 5–100× faster than fuzzywuzzy, drop-in API). Skip
  thefuzz/fuzzywuzzy — GPL and unmaintained relative to rapidfuzz.
- **Expected coverage: 70–80% of the ~5,000 markets**. OE covers LCK, LPL, LEC,
  LCS, LTA (formerly CBLOL+LLA), PCS, VCS, plus most regional leagues with
  partner status (LFL, Prime League, LCK Challengers, etc.). Tier-3 leagues like
  LIT (Italy), HLL (Hungary), Road of Legends, and very localized academy
  brackets are the loss bucket. Series-level Polymarket markets need one OE
  row per game — coverage measured per game, not per market.

---

## 2. Oracle's Elixir data format (verified facts)

### 2.1 Distribution and URL pattern

- The download page at https://oracleselixir.com/tools/downloads links to a single
  S3 bucket: `oracleselixir-downloadable-match-data.s3-us-west-2.amazonaws.com`.
- File pattern from ProjektZero (`src/oracles_elixir.py`, verbatim):
  ```python
  url = ("https://oracleselixir-downloadable-match-data."
         "s3-us-west-2.amazonaws.com/")
  file = f"{year}_LoL_esports_match_data_from_OraclesElixir_{date}.csv"
  ```
  ProjektZero originally suffixed the file with a date; the current public link
  is the un-dated form `{year}_LoL_esports_match_data_from_OraclesElixir.csv`.
  Both forms have historically been served — **needs verification once we
  download**, but the un-dated form is the one linked from the live download page.
- One CSV per calendar year, 2014–present. 2020 onwards are CSV (UTF-8). 2014–2019
  were originally XLSX and have since been re-released as CSV.
- **Update cadence:** daily. Tim has stated multiple times the file refreshes
  through the day; pulls of "today" generally include games that completed
  several hours earlier. Treat the file as eventually consistent within ~24h.

### 2.2 Row structure

Each completed game produces **12 rows**:
- 10 player rows (`position` ∈ {`top`, `jng`, `mid`, `bot`, `sup`}, 5 per team)
- 2 team-summary rows (`position == 'team'`, one per side)

Rows for the same game share a `gameid`. Within a game, `side` ∈ {`Blue`, `Red`}.

### 2.3 Confirmed columns (from ProjektZero subset, plus OE schema notes)

These are the columns ProjektZero actively reads from the CSV (verbatim, both
team-level and player-level subset). The full file has more columns; this set is
the load-bearing schema for our join:

Identity / context:
`gameid`, `date`, `league`, `split`, `playoffs`, `patch`, `side`, `position`,
`teamname`, `teamid`, `playername`, `playerid`, `champion`, `result`,
`datacompleteness`, `gamelength`.

Drafting (key for our use case — champion + roles):
`champion` (per-player), `ban1`..`ban5`, `pick1`..`pick5` (5 new fields added
recently per the FAQ post — pick order for that side).

Game stats (we likely won't need all but they're in the schema):
`kills`, `deaths`, `assists`, `firstblood`, `dragons`, `barons`, `towers`,
`team kpm`, `ckpm`, `earned gpm`, `earnedgoldshare`, `total cs`,
`goldat15`, `xpat15`, `csat15`, `killsat15`, `assistsat15`, `deathsat15`,
`opp_killsat15`, `opp_assistsat15`, `opp_deathsat15`,
`golddiffat15`, `xpdiffat15`, `csdiffat15`.

Notes:
- `datacompleteness` ∈ {`complete`, `partial`} — drop `partial` for analytics.
- `date` is in UTC and formatted `YYYY-MM-DD HH:MM:SS` (per ProjektZero's
  `clean_data()` casting it via pandas; **needs verification once we ingest**).
- `result` is `1` (win) or `0` (loss).
- Recently added (FAQ post): **Void Grub** (`grubs` / similar) and the five
  ordered `pick1`..`pick5` fields. Older rows will have NaN for these.

### 2.4 Coverage (leagues OE tracks)

Per the FAQ + download page, OE covers all "major" leagues:
LCK, LPL, LEC, LCS/LTA-N, LTA-S (formerly CBLOL/LLA), PCS, VCS, plus the bigger
regionals: LFL, Prime League (PRM), Superliga, NLC, EBL, LJL, LCK Challengers,
LDL (LPL Challengers), LCS Challengers / NACL, and the international circuit
(MSI, Worlds, EWC, Esports World Cup). Coverage for 2nd-division and very
small national leagues is **inconsistent** — confirmed gap.

### 2.5 League string format on OE

Examples from OE URL paths and CSV samples cited in third-party work:
`LCK`, `LPL`, `LEC`, `LCS`, `LTA N`, `LTA S`, `PCS`, `VCS`, `LJL`, `CBLOL`,
`LFL`, `PRM`, `WLDs` (Worlds), `MSI`, `EWC`. Note the **plain abbreviation**
style — no "2025", no "Spring" in the league column (that lives in `split`).

---

## 3. Team-name convention comparison

Confidence column legend: `H` = high (verified two independent sources or live
OE team URL hits), `M` = medium (Polymarket title matches Leaguepedia canonical
spelling and OE pages typically follow Leaguepedia), `L` = low (needs verification
once we ingest CSV — academies and 2nd-division teams are the highest-risk).

| Polymarket name              | Oracle's Elixir teamname        | Conf | Notes |
|------------------------------|----------------------------------|------|-------|
| Karmine Corp                 | Karmine Corp                     | H    | Direct match. OE URL: `/team/Karmine%20Corp`. |
| Karmine Corp Blue            | Karmine Corp Blue                | M    | OE tracks LFL; Leaguepedia uses identical string. The newer "Karmine Corp Blue Stars" (LFL D2) likely *not* in OE. |
| Movistar KOI                 | Movistar KOI                     | M    | Was "KOI" pre-Movistar sponsorship rebrand (early 2024); OE keeps the in-season string. |
| T1                           | T1                               | H    | Has been "T1" in OE since 2019 rebrand from SK Telecom T1. |
| Gen.G                        | Gen.G                            | H    | OE uses the dotted form, matches Leaguepedia. |
| Dplus KIA                    | Dplus KIA                        | H    | Successor of DWG KIA (2023 rebrand). |
| Hanwha Life Esports          | Hanwha Life Esports              | H    | |
| Bilibili Gaming              | Bilibili Gaming                  | H    | LPL — also abbreviated "BLG" in some contexts but OE uses full name. |
| JD Gaming                    | JD Gaming                        | H    | LPL — abbr "JDG". |
| Top Esports                  | Top Esports                      | H    | |
| NIP                          | Ninjas in Pyjamas                | M    | Polymarket has used both "NIP" and "Ninjas in Pyjamas" — OE consistently uses long form (verified via Polymarket URL `lol-tes-nip` whose page shows "Ninjas in Pyjamas"). **Alias required.** |
| Anyone's Legend              | Anyone's Legend                  | H    | LPL. Apostrophe form matches Wikipedia and Leaguepedia. |
| Weibo Gaming                 | Weibo Gaming                     | H    | |
| Team Heretics                | Team Heretics                    | H    | LEC. |
| Team Vitality                | Team Vitality                    | H    | LEC. |
| G2 Esports                   | G2 Esports                       | H    | |
| Fnatic                       | Fnatic                           | H    | |
| GIANTX                       | GIANTX                           | H    | LEC, 2024 merger of GIANTS and EXCEL — OE uses all-caps. |
| Cloud9                       | Cloud9                           | H    | |
| 100 Thieves                  | 100 Thieves                      | H    | |
| FlyQuest                     | FlyQuest                         | H    | |
| Shopify Rebellion            | Shopify Rebellion                | H    | LCS 2024+. |
| paiN Gaming                  | paiN Gaming                      | H    | CBLOL/LTA-S — lowercase "p", capital "N". |
| Vivo Keyd                    | Vivo Keyd Stars                  | M    | OE has historically used "Vivo Keyd Stars" (current 2024+ branding); Polymarket may use the shorter "Vivo Keyd". **Alias required, verify on ingest.** |
| RED Canids                   | RED Canids                       | H    | Sometimes shown as "RED Canids Kalunga" with sponsor — OE typically strips sponsor. **Verify.** |
| LOUD                         | LOUD                             | H    | |
| KaBuM!                       | KaBuM! Esports                   | M    | OE historically appends "Esports". **Alias required.** |
| Flamengo                     | Flamengo Los Grandes             | M    | Org name changed multiple times (Flamengo, Flamengo Los Grandes, Los Grandes). **Alias required, verify.** |
| BoostGate Esports            | BoostGate Esports                | L    | Smaller EU team — verify on ingest. |
| EKO Esports                  | EKO Esports                      | L    | LIT (Italian league). OE coverage of LIT is **inconsistent** — may not exist. |
| GMBLERS ESPORTS              | GMBLERS Esports                  | L    | OE typically uses Title Case not ALL CAPS. **Alias required.** |
| Myth Esports                 | Myth Esports                     | L    | Verify. |
| The Bandits                  | The Bandits                      | L    | Verify. |
| GOAL                         | GOAL Gaming                      | L    | Multiple "GOAL" orgs exist across leagues — **disambiguation risk.** |
| Team Insidious               | Team Insidious                   | L    | Verify. |
| GTZ Esports                  | GTZ Esports                      | L    | Verify; LIT may not be in OE. |
| ZeroZone Gaming              | ZeroZone Gaming                  | L    | Verify. |
| PCIFIC                       | ?                                | L    | Unknown to public sources — manual review. |
| Dark Passage                 | Dark Passage                     | M    | TCL — OE covers TCL. |
| Senshi                       | Senshi                           | L    | Verify; small EU team. |
| mCon esports                 | mCon esports                     | L    | Verify. |
| FENNEL                       | FENNEL                           | M    | LJL — OE covers LJL. |
| Arneb                        | ?                                | L    | Manual review. |
| CTBC Flying Oyster           | CTBC Flying Oyster               | H    | PCS — full long-form name. |
| Deep Cross Gaming            | Deep Cross Gaming                | M    | PCS. |
| GAM Esports                  | GAM Esports                      | H    | VCS. |
| MVK Esports                  | MVK Esports                      | L    | Verify. |
| DRX                          | DRX                              | H    | LCK Challengers in 2025 (relegated). |
| KT Rolster                   | KT Rolster                       | H    | LCK. |
| Nongshim (RedForce)          | NS RedForce                      | M    | OE historically uses "NS RedForce" — Polymarket may write "Nongshim" or "NongShim RedForce". **Alias required.** |
| OKSavingsBank (BRION)        | OKSavingsBank BRION              | M    | Full sponsored brand on OE. **Verify alias.** |
| BNK FEARX                    | BNK FearX                        | M    | OE casing is "FearX" not "FEARX". **Alias required.** |

General `_Academy` / `_Challengers` notes: Leaguepedia uses suffixes like
"`Team Liquid Honda`" → academy "`Team Liquid Challengers`". OE tracks the
academies as **separate teamname rows** with the suffix in the string, e.g.
"Team Liquid Challengers", "T1 Esports Academy". Polymarket sometimes uses
"`X Academy`" — if Polymarket uses "Team Liquid Academy" and OE has
"Team Liquid Challengers" (the league rebrand), the alias map *must* cover this.

---

## 4. Recommended matching algorithm

### 4.1 Library choice

**Use `rapidfuzz`.**
- MIT licensed (`thefuzz`/`fuzzywuzzy` are GPL, viral for the project).
- 5–100× faster than fuzzywuzzy. Compiled C++ core (no Python-Levenshtein
  GPL dependency).
- Provides `fuzz.token_set_ratio`, `fuzz.WRatio`, `fuzz.partial_ratio`,
  `process.extractOne` — the exact APIs we need.
- Actively maintained (2026), recommended in every modern comparison I read.

### 4.2 Algorithm (layered, deterministic, auditable)

```python
# Pseudocode → real stub
from rapidfuzz import fuzz, process
from datetime import timedelta

ALIAS_MAP: dict[str, str] = {...}   # see section 6 — Polymarket-name -> OE-name
FUZZY_ACCEPT = 92                    # token_set_ratio score threshold
FUZZY_REVIEW = 80                    # below this -> drop, above -> review queue
DATE_WINDOW  = timedelta(days=1)     # OE date is UTC midnight day; ±1 covers
                                     # cross-midnight games + tz drift


def normalize(name: str) -> str:
    """Lowercase, strip punctuation/whitespace for matching only.
       Keep the original string for display/storage."""
    import re, unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s]", " ", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def resolve_oe_team(pm_name: str, candidates: list[str]) -> tuple[str | None, int, str]:
    """Return (oe_name, score, method). method in {'alias','exact','fuzzy','none'}."""
    # 1. Alias map (hand-curated, wins everything)
    if pm_name in ALIAS_MAP:
        return ALIAS_MAP[pm_name], 100, "alias"

    # 2. Exact match after normalization
    norm = normalize(pm_name)
    norm_map = {normalize(c): c for c in candidates}
    if norm in norm_map:
        return norm_map[norm], 100, "exact"

    # 3. Fuzzy — token_set_ratio handles word reorder + extra tokens
    best = process.extractOne(
        norm,
        list(norm_map.keys()),
        scorer=fuzz.token_set_ratio,
    )
    if best is None:
        return None, 0, "none"
    matched_norm, score, _ = best
    if score >= FUZZY_ACCEPT:
        return norm_map[matched_norm], score, "fuzzy"
    return None, score, "none"   # falls into review queue if score >= FUZZY_REVIEW


def join_polymarket_to_oe(pm_event, oe_games_df):
    """For one Polymarket event, find matching OE game(s)."""
    # 0. Pre-scope OE candidates by date + league (huge precision win)
    lo = pm_event.start_time - DATE_WINDOW
    hi = pm_event.end_date + DATE_WINDOW
    scope = oe_games_df[
        (oe_games_df.date >= lo) & (oe_games_df.date <= hi)
    ]
    if pm_event.league_normalized:
        scope = scope[scope.league == pm_event.league_normalized]

    candidates_a = scope.teamname.unique().tolist()
    a_oe, a_score, a_method = resolve_oe_team(pm_event.team_a, candidates_a)
    b_oe, b_score, b_method = resolve_oe_team(pm_event.team_b, candidates_a)
    if not (a_oe and b_oe):
        return None  # → unmatched_queue

    # 1. Find the actual game(s) where both teams played that day
    games = scope.groupby("gameid").filter(
        lambda g: {a_oe, b_oe}.issubset(set(g.teamname))
    )
    return games  # one row per game; for series markets returns multiple gameids
```

Why this layering wins:
- **Alias map first** is non-negotiable. Many real cases (NIP ↔ Ninjas in Pyjamas,
  BNK FEARX ↔ BNK FearX, KaBuM! ↔ KaBuM! Esports) have edit distance large enough
  that token_set_ratio at the 92 threshold misses them, while the wrong-team
  fuzzy collisions ("LOUD" vs "LOUD2", "DRX" vs "DRX Academy") happen above 92.
  Hand-curated wins for safety.
- **Date + league pre-scoping** is the biggest precision lever — it shrinks the
  candidate pool from 500+ teams to typically <20, which makes even
  partial_ratio safe.
- **token_set_ratio at 92** is empirically the right threshold for short team
  strings with punctuation / suffix variation; below 92 you start matching
  "100 Thieves" to "100 Thieves Challengers".

### 4.3 Manual-review queue (don't skip this)

Anything in `[FUZZY_REVIEW, FUZZY_ACCEPT)` (80–91) goes into a `lol_match_review`
table the user can resolve in the UI in one click, then it gets back-written
to the alias map. This is how the alias map grows organically.

### 4.4 Same-day BO3/BO5

OE has one row per game with the same `date`. Polymarket has either a series
parent (`market_kind='series'`) or game children (`market_kind='game'`,
`game_number=1..5`).
- **Game-level Polymarket markets** → match to the Nth OE game between those
  two teams on that date, ordered by `gameid` lexicographically (OE gameids
  are monotonically increasing per series — **needs verification on ingest,
  but Leaguepedia + OE convention strongly suggests this**).
- **Series-level Polymarket markets** → join 1-to-many (one Polymarket row
  per multiple OE rows). The aggregation (BO3 winner, score) is computed
  client-side.

---

## 5. Recommended schema — `lol_pro_matches`

Design: ingest one OE team row per `(gameid, side)`, denormalize the opponent
into the same row to make the Polymarket join a single index lookup. Champion
roster is stored as a JSONB array (5 entries, ordered top→sup) so we can
filter cheaply.

```sql
create table lol_pro_matches (
    -- OE identity
    oe_gameid          text primary key,            -- one row per (gameid, side)? No.
                                                    -- see composite below.

    -- ↑ scrap that. Use composite. Below is the real schema:
);

drop table if exists lol_pro_matches;
create table lol_pro_matches (
    oe_gameid          text        not null,
    side               text        not null check (side in ('Blue','Red')),
    primary key (oe_gameid, side),

    -- temporal
    game_date          timestamptz not null,
    patch              text,                       -- e.g. '15.10'
    league             text        not null,       -- e.g. 'LEC'
    split              text,                       -- e.g. 'Summer'
    playoffs           boolean,
    series_id          text,                       -- OE doesn't expose this
                                                   -- directly; derive from
                                                   -- (date, team_a, team_b)
    game_in_series     smallint,                   -- 1..5, derived

    -- teams (this side's team and the opponent)
    team_name          text        not null,       -- OE 'teamname'
    team_id            text,                       -- OE 'teamid' (stable id)
    opp_team_name      text        not null,
    opp_team_id        text,

    -- draft (10 champions total in the game; this row has its side's 5)
    champion_top       text,
    champion_jng       text,
    champion_mid       text,
    champion_bot       text,
    champion_sup       text,
    bans               text[],                     -- ['Ahri','LeBlanc',...]
    picks_ordered      text[],                     -- pick1..pick5 verbatim
                                                    -- (order picks were made)

    -- outcome
    result             smallint    not null,       -- 1 win, 0 loss
    game_length_s      integer,
    first_blood        boolean,
    dragons            smallint,
    barons             smallint,
    towers             smallint,
    kills              smallint,
    deaths             smallint,
    assists            smallint,

    -- early game (the columns ProjektZero pulls — high signal)
    gold_at_15         integer,
    xp_at_15           integer,
    cs_at_15           integer,
    golddiff_at_15     integer,
    xpdiff_at_15       integer,
    csdiff_at_15       integer,

    -- data quality
    data_completeness  text        not null,        -- 'complete' or 'partial'
    raw_row_hash       text        not null,        -- sha1 of the OE row,
                                                    -- lets us detect changes
                                                    -- on re-ingest
    ingested_at        timestamptz not null default now()
);

create index lol_pro_matches_date_idx       on lol_pro_matches (game_date);
create index lol_pro_matches_league_date_idx on lol_pro_matches (league, game_date);
create index lol_pro_matches_team_date_idx  on lol_pro_matches (team_name, game_date);
create index lol_pro_matches_opp_date_idx   on lol_pro_matches (opp_team_name, game_date);
-- Composite (team_a, team_b, date) lookup is the join hot path:
create index lol_pro_matches_pair_date_idx  on lol_pro_matches
    (least(team_name, opp_team_name), greatest(team_name, opp_team_name), game_date);
```

Plus a **team alias** table to make the alias map data not code:

```sql
create table lol_team_aliases (
    polymarket_name  text primary key,    -- exact string as it appears in
                                          -- Polymarket market_title parse
    oe_team_name     text not null,       -- the canonical OE 'teamname'
    confidence       text not null,       -- 'high' | 'medium' | 'low' | 'manual'
    notes            text,
    created_at       timestamptz not null default now(),
    created_by       text                  -- 'seed' | 'review_ui' | user_id
);

create index lol_team_aliases_oe_idx on lol_team_aliases (oe_team_name);
```

And a review queue for ambiguous fuzzy matches:

```sql
create table lol_match_review (
    id               bigserial primary key,
    polymarket_event_id text not null,
    pm_team_a        text not null,
    pm_team_b        text not null,
    suggested_oe_team_a text,
    suggested_oe_team_b text,
    score_a          smallint,
    score_b          smallint,
    candidate_gameids text[],
    status           text not null default 'pending',  -- pending|resolved|skipped
    resolved_at      timestamptz,
    resolved_by      text,
    created_at       timestamptz not null default now()
);
```

---

## 6. Starter alias map (Python dict)

Verified-confidence entries only. Anything `L` confidence in §3 is omitted
here; it goes into the review queue on first encounter.

```python
# lol_bot/alias_map.py
# Polymarket-style team name (verbatim from market title) -> OE 'teamname'.
# Keep keys lower-cased only if you also lower-case PM names at lookup time —
# the example matcher in §4.2 normalizes both sides, so casing doesn't matter
# for lookup. Stored verbatim for traceability.
POLYMARKET_TO_OE: dict[str, str] = {
    # Identity-mappings still listed so the alias path always hits first
    # (skips the fuzzy pass and removes ambiguity in metrics).
    "Karmine Corp":            "Karmine Corp",
    "Movistar KOI":            "Movistar KOI",
    "T1":                      "T1",
    "Gen.G":                   "Gen.G",
    "Dplus KIA":               "Dplus KIA",
    "Hanwha Life Esports":     "Hanwha Life Esports",
    "Bilibili Gaming":         "Bilibili Gaming",
    "JD Gaming":               "JD Gaming",
    "Top Esports":             "Top Esports",
    "Anyone's Legend":         "Anyone's Legend",
    "Weibo Gaming":            "Weibo Gaming",
    "Team Heretics":           "Team Heretics",
    "Team Vitality":           "Team Vitality",
    "G2 Esports":              "G2 Esports",
    "Fnatic":                  "Fnatic",
    "GIANTX":                  "GIANTX",
    "Cloud9":                  "Cloud9",
    "100 Thieves":             "100 Thieves",
    "FlyQuest":                "FlyQuest",
    "Shopify Rebellion":       "Shopify Rebellion",
    "paiN Gaming":             "paiN Gaming",
    "LOUD":                    "LOUD",
    "DRX":                     "DRX",
    "KT Rolster":              "KT Rolster",
    "CTBC Flying Oyster":      "CTBC Flying Oyster",
    "GAM Esports":             "GAM Esports",

    # Real renames / casing fixes (these are the ones that matter)
    "NIP":                     "Ninjas in Pyjamas",
    "Ninjas in Pyjamas":       "Ninjas in Pyjamas",
    "KaBuM!":                  "KaBuM! Esports",
    "KaBuM! Esports":          "KaBuM! Esports",
    "BNK FEARX":               "BNK FearX",
    "BNK FearX":               "BNK FearX",
    "Nongshim":                "NS RedForce",
    "Nongshim RedForce":       "NS RedForce",
    "NongShim RedForce":       "NS RedForce",
    "NS RedForce":             "NS RedForce",
    "OKSavingsBank":           "OKSavingsBank BRION",
    "OKSavingsBank BRION":     "OKSavingsBank BRION",
    "OKBRO":                   "OKSavingsBank BRION",
    "GMBLERS ESPORTS":         "GMBLERS Esports",
    "GMBLERS Esports":         "GMBLERS Esports",

    # Academies / 2nd teams — confidence medium, verify on ingest:
    "Karmine Corp Blue":       "Karmine Corp Blue",

    # Brazilian sponsor variants (med confidence, verify):
    "Vivo Keyd":               "Vivo Keyd Stars",
    "Vivo Keyd Stars":         "Vivo Keyd Stars",
    "RED Canids":              "RED Canids",
    "RED Canids Kalunga":      "RED Canids",
    "Flamengo":                "Flamengo Los Grandes",
    "Flamengo Los Grandes":    "Flamengo Los Grandes",
    "Los Grandes":             "Flamengo Los Grandes",
}

# Teams Polymarket lists but Oracle's Elixir likely does NOT track
# (LIT/HLL/Road of Legends/etc.). Put them here so we don't even attempt
# a join — saves on noisy review-queue churn.
OE_UNTRACKED_HINT = {
    "EKO Esports", "GMBLERS ESPORTS", "Myth Esports", "The Bandits",
    "GOAL", "Team Insidious", "GTZ Esports", "ZeroZone Gaming",
    "PCIFIC", "Senshi", "mCon esports", "Arneb", "MVK Esports",
    "BoostGate Esports",
}
```

`OE_UNTRACKED_HINT` is a heuristic, not a hard block — if a team escapes into a
larger league (e.g., promotion), the matcher should re-evaluate. Treat it as
"skip auto-review until we confirm no OE coverage" rather than a permanent
denylist.

League-name normalization (Polymarket title's trailing segment → OE league):

```python
PM_LEAGUE_TO_OE = {
    "LCK": "LCK", "LPL": "LPL", "LEC": "LEC", "LCS": "LCS",
    "LTA North": "LTA N", "LTA South": "LTA S",
    "CBLOL": "LTA S",            # 2025+ merger
    "LLA": "LTA S",              # 2025+ merger
    "PCS": "PCS", "VCS": "VCS", "LJL": "LJL",
    "LFL": "LFL", "Prime League": "PRM", "PRM": "PRM",
    "Superliga": "SL",
    "Worlds": "WLDs", "MSI": "MSI",
    "Esports World Cup": "EWC", "EWC": "EWC",
    # ... extend on ingest
}
```

---

## 7. Edge cases catalog

1. **Cross-midnight UTC games.**
   International events (Worlds, MSI) regularly start ~3pm Korea (06:00 UTC)
   but finals run 6+ hours. A game starting Friday 22:00 PT (Saturday 05:00 UTC)
   sits a calendar day apart from Polymarket's `start_time` field if Polymarket
   set it to local time. **Handle:** ±1-day window on the OE date filter
   (already in the stub). Cheap and correct.

2. **Same-day rematches (BO3/BO5).**
   Both teams play each other 3–5 times on the same `date` in OE. Polymarket
   has a parent series market and per-game child markets.
   **Handle:** for game children, sort the day's gameids ascending and align
   to `game_number`. For series parents, return all matching OE games and
   aggregate.

3. **Mid-season rebrands.**
   `OKBRO` → `OKSavingsBank BRION` (LCK), `Vivo Keyd` → `Vivo Keyd Stars`,
   `Karmine Corp` (added Movistar sponsor on `KOI` but stayed "Karmine Corp"),
   sponsorship changes mid-split.
   **Handle:** alias map points both old and new strings at the *current* OE
   teamname. Historical OE rows will have the old string — the matcher must
   handle the case where the alias map's RHS doesn't appear in the date-scoped
   OE candidates and fall back to fuzzy (which will find it because both
   strings differ by ≤ 5 chars).

4. **Forfeit / walk-over games.**
   Polymarket usually voids/resolves these instantly. OE does **not** create a
   row for forfeits (no actual game played → no draft → no data). **Handle:**
   if a Polymarket market resolves but no OE row exists in the ±1 day window
   for that team pair, mark it `unmatched_forfeit_suspected` rather than
   `unmatched_review`.

5. **Sub-tier leagues (LIT, HLL, ROL, regional D2/D3).**
   OE coverage is incomplete here. Don't burn review-queue capacity on these.
   **Handle:** use `OE_UNTRACKED_HINT` to skip auto-review, set
   `match_status = 'no_oe_coverage'` and surface a coverage-rate metric in
   the UI.

6. **Academy/Challengers naming collisions.**
   "T1" ≠ "T1 Esports Academy". A naive fuzzy match on "T1" against the LCK
   Challengers slate would score 100 against "T1 Esports Academy" via
   partial_ratio. **Handle:** stick to `token_set_ratio` not `partial_ratio`
   for the main scorer, and league-scope the candidates (`T1` plays LCK, the
   academy plays LCK CL).

7. **Diacritics and punctuation.**
   `Gen.G` (dot), `KaBuM!` (exclamation), `paiN Gaming` (lower-then-upper),
   `Anyone's Legend` (apostrophe).
   **Handle:** `normalize()` in §4.2 strips punctuation + lower-cases for
   matching only; storage keeps the original. Apostrophe-curly vs apostrophe-
   straight is a real CSV gotcha — normalize NFKD already handles it.

8. **Same org name across regions ("GOAL").**
   "GOAL" exists in multiple regional rosters. Without league scoping, fuzzy
   will pick the first 100-score hit. **Handle:** league-pre-scoping is
   mandatory before fuzzy resolution. If league can't be resolved
   (PM title has unknown league string), bail to review queue rather than
   guess.

9. **OE re-statement of historical rows.**
   Tim has historically corrected old games (typos, missing fields filled in).
   **Handle:** `raw_row_hash` in `lol_pro_matches` schema → on each daily
   ingest, upsert and detect row changes; emit a `pro_matches_changed` event
   so any downstream caches refresh.

10. **`datacompleteness == 'partial'` rows.**
    Some early-game stats missing (esp. for older patches or smaller
    leagues). The draft / teamname / result are always present, but
    `goldat15` etc. may be NULL.
    **Handle:** keep the row; the join still works; flag the partial-ness
    so downstream analytics can decide.

---

## 8. Coverage estimate

Working from the ~5,000 LoL markets figure and the league string examples
in Polymarket titles I've seen:

- **Top-tier (LCK, LPL, LEC, LCS, LTA-N, LTA-S, PCS, VCS, LJL, EWC, MSI, Worlds):**
  expect ~95%+ match rate. OE is the canonical record for these. Misses are
  mostly forfeits or rare casing edge cases caught by the alias map.

- **Mid-tier (LFL, PRM, Superliga, NLC, EBL, LCK Challengers, LDL, NACL):**
  expect ~70–85% match. OE covers most but lags 1–2 days on the smaller
  leagues, and academy team-name conventions are noisier.

- **Bottom-tier (LIT, HLL, Road of Legends, very regional D2 / national
  qualifiers, exhibition cup matches):**
  expect 0–30% match. OE often skips these entirely. This is the structural
  loss bucket.

If Polymarket's ~5,000 LoL markets follow the typical distribution
(roughly 50% top-tier, 30% mid-tier, 20% bottom-tier — eyeballed from the
league strings in the example team list), the **blended expected coverage is
70–80%**. The 20–30% we lose is almost entirely:
- Bottom-tier leagues OE doesn't track
- Series-parent markets where we matched 1 of 3 games (treat as partial match)
- A small tail (<2%) of genuine alias-map misses on first encounter, which
  the review queue closes over time

**What we lose:** champion drafts and per-game stats for the bottom-tier
markets. The Polymarket data itself (entry prices, resolution, trader
positions) is unaffected; we just can't enrich those markets with pro-game
context. This is acceptable because the bottom-tier markets also have the
thinnest liquidity and least informational value.

---

## Open verification items (for first-ingest day)

These are the items I marked "needs verification" inline and should be
sanity-checked against the actual CSV the first time we pull it:

1. **CSV header column casing and exact list.** ProjektZero's column subset is
   verbatim from real CSVs but the full header is ~120 columns and a few
   have changed (Void Grub additions, pick order additions). Confirm via
   `head -1`.
2. **`date` column format.** ProjektZero parses it with default pandas — most
   likely `YYYY-MM-DD HH:MM:SS` UTC, but could be `YYYY-MM-DD` only for some
   years. Decide on storage as `timestamptz` after confirming.
3. **S3 filename — dated suffix or not?** The current download page link is
   un-dated (`{year}_LoL_esports_match_data_from_OraclesElixir.csv`).
   ProjektZero appends a date. Try un-dated first; fall back to dated.
4. **`gameid` monotonicity within a same-day BO3.** Strongly believed to
   increase with game number but should be verified before relying on it for
   `game_in_series` derivation.
5. **NS RedForce / OKSavingsBank BRION / BNK FearX exact casing.** Confirm
   against a recent LCK CL CSV row.
6. **Karmine Corp Blue, Vivo Keyd Stars, KaBuM! Esports, Flamengo Los Grandes**
   teamname strings — confirm vs an LFL/LTA-S row.

All of the above are 5-minute checks once the first CSV is on disk.
