"""Audit every alias in lol_team_aliases: confirm the oe_team_name actually
exists in lol_pro_matches. For ones that don't, try to find the closest
real OE name and update the alias.

Reason: seed aliases were written from memory / Polymarket-side conventions,
but OE has its own canonical names which sometimes differ in casing
("Dplus KIA" vs "Dplus Kia") or sponsor-form ("NS RedForce" vs "Nongshim
RedForce"). When an alias points to a non-existent OE name, the join's
team-lookup fails silently — markets fall into no_oe_data even though they
should have matched.
"""

from __future__ import annotations

import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

from app.db.connection import init_pool
from rapidfuzz import fuzz, process


async def main() -> None:
    pool = await init_pool(min_size=1, max_size=2)

    print("Auditing lol_team_aliases against actual OE team names...")
    async with pool.acquire() as conn:
        # Every alias and whether its OE name exists
        rows = await conn.fetch(
            """
            SELECT a.polymarket_name, a.oe_team_name,
                   EXISTS(SELECT 1 FROM lol_pro_matches m WHERE m.team_name = a.oe_team_name) AS exists_in_oe
            FROM lol_team_aliases a
            ORDER BY a.polymarket_name
            """
        )
        oe_teams_rows = await conn.fetch(
            "SELECT DISTINCT team_name FROM lol_pro_matches WHERE team_name IS NOT NULL"
        )
    oe_teams = [r["team_name"] for r in oe_teams_rows]
    print(f"  total aliases: {len(rows)}")
    print(f"  distinct OE team names: {len(oe_teams)}")
    missing = [r for r in rows if not r["exists_in_oe"]]
    print(f"  aliases pointing to non-existent OE name: {len(missing)}")

    if not missing:
        print("Nothing to fix.")
        return

    print("\nFixing pointing-to-nothing aliases via fuzzy match against OE team list:")
    updates: list[tuple[str, str, str, int]] = []
    drops: list[tuple[str, str]] = []
    for r in missing:
        pm = r["polymarket_name"]
        bad_oe = r["oe_team_name"]
        # Best fuzzy candidate among real OE teams
        match = process.extractOne(
            bad_oe, oe_teams, scorer=fuzz.token_set_ratio, score_cutoff=85,
        )
        if match:
            new_oe, score, _ = match
            updates.append((pm, bad_oe, new_oe, int(score)))
        else:
            drops.append((pm, bad_oe))

    if updates:
        print(f"\n[update] {len(updates)} aliases will be re-pointed:")
        for pm, bad, new, score in updates:
            print(f"  {pm!r:>32s}: {bad!r:<32s} -> {new!r:<32s}  (fuzzy {score})")
        async with pool.acquire() as conn:
            async with conn.transaction():
                for pm, _bad, new_oe, score in updates:
                    await conn.execute(
                        """
                        UPDATE lol_team_aliases
                        SET oe_team_name = $2,
                            notes = COALESCE(notes, '') || ' [auto-fixed casing/rebrand: fuzzy ' || $3 || ']',
                            confidence = CASE WHEN confidence = 'high' THEN 'medium' ELSE confidence END
                        WHERE polymarket_name = $1
                        """,
                        pm, new_oe, str(score),
                    )

    if drops:
        print(f"\n[review] {len(drops)} aliases point to OE names that simply don't exist (drop or leave as-is):")
        for pm, bad in drops:
            print(f"  {pm!r:>32s} -> {bad!r}  (no fuzzy match >= 85)")
        # We DON'T delete — leave them in case the user has reason to keep them.
        # They just won't resolve until manually fixed.

    print("\nDone. Re-run scripts/join_pm_to_oe.py to apply the corrections.")


if __name__ == "__main__":
    asyncio.run(main())
