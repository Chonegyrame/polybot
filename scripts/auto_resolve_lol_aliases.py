"""Auto-discover team-name aliases by date-correlating unmatched Polymarket
markets against Oracle's Elixir games.

Strategy:
  1. Find all distinct unresolved Polymarket team names (NOT in alias map,
     NOT covered by exact-normalize or fuzzy).
  2. For each name, look up the Polymarket events that use it. Extract
     the start_time of each.
  3. For each event date, fetch the OE games played within +/-1 day.
     Collect the set of teams that played on those dates.
  4. For each unresolved name, score every candidate OE team by:
       - Initials match (PM='JDG', OE='JD Gaming' -> initials 'JDG' match)
       - Substring containment (PM='Fnatic TQ', OE='Fnatic')
       - Co-occurrence consistency across multiple events
  5. Auto-apply aliases where the best candidate scores >= AUTO_THRESHOLD
     AND beats the second-best by at least MARGIN.
  6. Print ambiguous cases for human review.

This is conservative: we only auto-add aliases when the match is unambiguous.
Risk of bad auto-aliases is small because:
  - We require the OE team to have played on the same date
  - We require an initials/substring match score >= threshold
  - We require a margin over the second-best candidate
  - Anything ambiguous goes to the manual review list

Usage:
    PYTHONPATH=. ./venv/Scripts/python.exe scripts/auto_resolve_lol_aliases.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import unicodedata
from collections import defaultdict, Counter
from datetime import timedelta

# Windows stdout defaults to cp1252; switch to UTF-8 so we can print team
# names with diacritics (e.g. Polish 'ą', Korean Hangul) without UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

from app.db.connection import init_pool

AUTO_THRESHOLD = 70      # min score to consider a candidate viable
MARGIN_REQUIRED = 15     # winning candidate must beat #2 by this much


def _initials(name: str) -> str:
    """Extract uppercase initials from a team name.
    'JD Gaming' -> 'JDG', 'EDward Gaming' -> 'EG', 'BNK FearX' -> 'BFX'.
    Strip diacritics, drop common stopwords ('Esports', 'Gaming',
    'eSports' typically also captured as 'E' and 'G').
    """
    if not name:
        return ""
    # Strip diacritics (NFKD), keep only ASCII
    decomposed = unicodedata.normalize("NFKD", name)
    clean = "".join(c for c in decomposed if c.isascii())
    # Drop punctuation
    clean = re.sub(r"[^A-Za-z0-9 ]", " ", clean)
    tokens = clean.split()
    stopwords = {"esports", "esport", "gaming", "game", "team", "club", "the", "of"}
    keep = [t for t in tokens if t.lower() not in stopwords]
    if not keep:
        keep = tokens
    out_chars: list[str] = []
    for tok in keep:
        if not tok:
            continue
        out_chars.append(tok[0].upper())
        # For all-caps tokens (like 'JD', 'NS', 'KT'), include all letters
        if len(tok) > 1 and tok.isupper() and tok.isalpha():
            out_chars.extend(list(tok[1:].upper()))
    return "".join(out_chars)


def _score_candidate(pm_name: str, oe_name: str) -> int:
    """Score 0-100 how likely the PM short-name maps to an OE team.

    Heuristics in priority order:
      1. PM is the OE team's initials exactly (or prefix of initials) -> high
      2. PM normalized substring of OE name -> medium
      3. PM and OE share initial letter -> low
    """
    pm = pm_name.strip()
    oe = oe_name.strip()
    if not pm or not oe:
        return 0

    pm_upper = pm.upper().replace(".", "").replace(" ", "")
    oe_initials = _initials(oe)

    if pm_upper == oe_initials:
        return 100  # perfect initials match
    if oe_initials.startswith(pm_upper) and len(pm_upper) >= 2:
        return 90   # PM is prefix of OE initials (e.g. PM='EG' -> OE='EDward Gaming' = 'EG')
    if pm_upper in oe_initials:
        return 75   # PM substring of OE initials
    # Substring containment of normalized names
    pm_norm = pm.lower().replace(" ", "").replace(".", "")
    oe_norm = oe.lower().replace(" ", "").replace(".", "")
    if len(pm_norm) >= 3 and pm_norm in oe_norm:
        return 70
    if len(pm_norm) >= 3 and oe_norm.startswith(pm_norm):
        return 65
    # First-letter agreement
    if pm_upper and oe_initials and pm_upper[0] == oe_initials[0]:
        return 30
    return 0


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    pool = await init_pool(min_size=1, max_size=2)

    print("=" * 70)
    print("LoL alias auto-resolution")
    print("=" * 70)

    # Step 1: every distinct Polymarket team-name not already aliased
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH all_pm_names AS (
              SELECT DISTINCT team_a AS pm_name FROM polymarket_lol_market_meta WHERE team_a IS NOT NULL
              UNION
              SELECT DISTINCT team_b FROM polymarket_lol_market_meta WHERE team_b IS NOT NULL
            )
            SELECT pm_name
            FROM all_pm_names
            WHERE pm_name NOT IN (SELECT polymarket_name FROM lol_team_aliases)
            """
        )
    unresolved = [r["pm_name"] for r in rows]
    print(f"\n[step1] {len(unresolved)} distinct unresolved Polymarket team names")

    # Step 2: for each unresolved name, find its event dates
    async with pool.acquire() as conn:
        date_rows = await conn.fetch(
            """
            SELECT mm.team_a, mm.team_b, e.start_time, e.end_date
            FROM polymarket_lol_market_meta mm
            LEFT JOIN events e ON e.id = mm.event_id
            WHERE COALESCE(e.start_time, e.end_date) IS NOT NULL
            """
        )
    name_to_dates: dict[str, list] = defaultdict(list)
    for r in date_rows:
        anchor = r["start_time"] or r["end_date"]
        if anchor is None:
            continue
        if r["team_a"]:
            name_to_dates[r["team_a"]].append(anchor)
        if r["team_b"]:
            name_to_dates[r["team_b"]].append(anchor)

    # Step 3: for each unresolved name with date context, find the OE
    # candidate pool from those dates and score every (PM, OE) pair.
    candidates_per_name: dict[str, Counter] = defaultdict(Counter)

    for pm_name in unresolved:
        dates = name_to_dates.get(pm_name, [])
        if not dates:
            continue
        # Sample up to 8 dates to keep load reasonable
        for anchor in dates[:8]:
            lower = anchor - timedelta(days=1)
            upper = anchor + timedelta(days=1)
            async with pool.acquire() as conn:
                oe_teams = await conn.fetch(
                    """
                    SELECT DISTINCT team_name
                    FROM lol_pro_matches
                    WHERE game_date BETWEEN $1 AND $2
                    """,
                    lower, upper,
                )
            for r in oe_teams:
                oe_name = r["team_name"]
                if oe_name is None:
                    continue
                score = _score_candidate(pm_name, oe_name)
                if score > 0:
                    # Accumulate the BEST score this pair achieved across all dates
                    candidates_per_name[pm_name][oe_name] = max(
                        candidates_per_name[pm_name][oe_name], score
                    )

    print(f"[step3] scored candidates for {len(candidates_per_name)} of {len(unresolved)} names")

    # Step 4: pick winners
    auto_aliases: dict[str, tuple[str, int, int]] = {}   # pm -> (oe, score, margin)
    ambiguous: list[tuple[str, list[tuple[str, int]]]] = []
    no_candidates: list[str] = []

    for pm_name, cand in candidates_per_name.items():
        sorted_cands = cand.most_common(5)
        if not sorted_cands:
            no_candidates.append(pm_name)
            continue
        best_name, best_score = sorted_cands[0]
        second_score = sorted_cands[1][1] if len(sorted_cands) > 1 else 0
        margin = best_score - second_score
        if best_score >= AUTO_THRESHOLD and margin >= MARGIN_REQUIRED:
            auto_aliases[pm_name] = (best_name, best_score, margin)
        else:
            ambiguous.append((pm_name, sorted_cands))

    for pm_name in unresolved:
        if pm_name not in candidates_per_name:
            no_candidates.append(pm_name)

    print(f"\n[step4] auto-resolved unambiguously: {len(auto_aliases)}")
    print(f"[step4] ambiguous (need human review): {len(ambiguous)}")
    print(f"[step4] no OE candidates on event dates: {len(set(no_candidates))}")

    # Step 5: apply auto aliases
    if auto_aliases:
        print("\n[step5] auto-applying these aliases:")
        for pm_name, (oe_name, score, margin) in sorted(auto_aliases.items()):
            print(f"  {pm_name!r:>32s} -> {oe_name!r:<32s}  score={score} margin={margin}")
        async with pool.acquire() as conn:
            async with conn.transaction():
                for pm_name, (oe_name, score, margin) in auto_aliases.items():
                    confidence = "high" if score >= 90 and margin >= 25 else "medium"
                    await conn.execute(
                        """
                        INSERT INTO lol_team_aliases (polymarket_name, oe_team_name, confidence, notes, created_by)
                        VALUES ($1, $2, $3, $4, 'auto_resolve')
                        ON CONFLICT (polymarket_name) DO NOTHING
                        """,
                        pm_name, oe_name, confidence,
                        f"auto-resolved: score={score} margin={margin}",
                    )

    # Step 6: print ambiguous list for manual review
    if ambiguous:
        print("\n[step6] AMBIGUOUS — needs manual decision. Top 30 by candidate count:")
        # Sort by best candidate score descending so the "almost-auto" ones come first
        ambiguous.sort(key=lambda x: -x[1][0][1] if x[1] else 0)
        for pm_name, cands in ambiguous[:30]:
            cands_str = ", ".join(f"{n!r}({s})" for n, s in cands[:3])
            print(f"  {pm_name!r:>32s}  candidates: {cands_str}")

    if no_candidates:
        print(f"\n[step6] {len(set(no_candidates))} unresolved names have NO OE candidate on their event dates")
        print("        (likely tier-3 leagues OE doesn't cover). Sample 20:")
        for n in sorted(set(no_candidates))[:20]:
            print(f"  {n!r}")

    print("\nDone. Re-run scripts/join_pm_to_oe.py to apply the new aliases.")


if __name__ == "__main__":
    asyncio.run(main())
