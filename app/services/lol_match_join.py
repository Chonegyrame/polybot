"""Polymarket-market ↔ Oracle's Elixir-game team-name matcher and joiner.

Four-layer matching algorithm:

  Layer 1 — Alias map (high-confidence): look up the Polymarket-verbatim team
            name in the lol_team_aliases table. Exact-string key, returns the
            OE canonical name. Fastest, most certain. Most matches resolve here.

  Layer 2 — Exact match after normalization: lowercase + NFKD + strip
            punctuation/whitespace on both sides. Catches casing-only and
            diacritic-only differences. E.g. "Karmine corp" -> "Karmine Corp".

  Layer 3 — Fuzzy match via rapidfuzz token_set_ratio against the
            league+date scoped OE team-name pool. Auto-accept ≥ 92.
            80-91 → manual review queue.

  Layer 4 — Drop / no match.

Constraints applied at every layer:
  - Date bound: ±1 day around the Polymarket event's start_time.
  - League scoping: when we have a confident league mapping, only consider
    OE teams that played in that league within the date window.

Series-vs-game expansion: Polymarket has both series markets (BO winner) and
per-game-winner child markets. OE has one row per (gameid, side); for a
BO3 series, that's 1-3 game-rows per team-pair (5 for BO5). We attach the
SAME OE game(s) to all related Polymarket markets (series + per-game).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import asyncpg
from rapidfuzz import fuzz, process

from app.services.lol_alias_map import (
    POLYMARKET_TO_OE,
    OE_UNTRACKED_HINT,
    normalize_league,
    normalize_team_name,
)

log = logging.getLogger(__name__)

# Fuzzy auto-accept threshold. Below this we land in manual review.
FUZZY_AUTO_ACCEPT_THRESHOLD = 92
FUZZY_REVIEW_FLOOR = 80


@dataclass(frozen=True)
class MatchResolution:
    """Outcome of one team-name resolution."""
    pm_name: str
    oe_name: str | None
    layer: str           # 'alias', 'normalize', 'fuzzy', 'untracked_hint', 'unresolved'
    score: int | None    # rapidfuzz score for fuzzy hits, else None


@dataclass(frozen=True)
class JoinResult:
    """Outcome of one Polymarket market's full join attempt."""
    pm_condition_id: str
    pm_event_id: str
    pm_team_a: str
    pm_team_b: str
    pm_league: str | None
    pm_start_time: datetime | None
    market_kind: str         # 'series' | 'game'
    game_number: int | None
    status: str              # 'matched' | 'review_queued' | 'no_oe_data' | 'no_team_match'
    oe_team_a_name: str | None
    oe_team_b_name: str | None
    matched_gameids: tuple[str, ...]
    layer_a: str
    layer_b: str
    note: str | None


# ---------------------------------------------------------------------------
# Alias-table lookup (caches the lol_team_aliases table content per-call)
# ---------------------------------------------------------------------------


async def _load_alias_table(conn: asyncpg.Connection) -> dict[str, str]:
    """Read every row in lol_team_aliases into a dict for in-process lookups."""
    rows = await conn.fetch(
        "SELECT polymarket_name, oe_team_name FROM lol_team_aliases"
    )
    return {r["polymarket_name"]: r["oe_team_name"] for r in rows}


async def seed_alias_table(conn: asyncpg.Connection) -> int:
    """Seed lol_team_aliases from the in-code POLYMARKET_TO_OE dict.

    Idempotent — uses ON CONFLICT DO NOTHING so existing manual edits are
    preserved. Returns the number of rows attempted to insert (some may have
    been skipped because already present).
    """
    inserted = 0
    for pm_name, (oe_name, confidence) in POLYMARKET_TO_OE.items():
        await conn.execute(
            """
            INSERT INTO lol_team_aliases (polymarket_name, oe_team_name, confidence, created_by)
            VALUES ($1, $2, $3, 'seed')
            ON CONFLICT (polymarket_name) DO NOTHING
            """,
            pm_name, oe_name, confidence,
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# OE candidate pool (scoped to a date window + optional league)
# ---------------------------------------------------------------------------


async def _candidate_oe_teams(
    conn: asyncpg.Connection,
    *,
    start_time: datetime | None,
    oe_league_code: str | None,
    date_window_hours: int = 72,
) -> tuple[set[str], list[asyncpg.Record]]:
    """Pull the distinct OE team names that played in the date window.

    Returns (set of team names, list of game records). Game records are kept
    for later resolution after we've identified the matching team names.
    """
    if start_time is None:
        # No date bound = full table. Risky but workable. We'd rarely fire
        # this in practice (Polymarket markets always have a start_time).
        if oe_league_code:
            rows = await conn.fetch(
                "SELECT DISTINCT team_name FROM lol_pro_matches WHERE league = $1",
                oe_league_code,
            )
        else:
            rows = await conn.fetch("SELECT DISTINCT team_name FROM lol_pro_matches")
        return ({r["team_name"] for r in rows}, [])

    # Date-scoped (±N hours around start_time). Compute the window endpoints
    # in Python rather than inline-INTERVAL math so asyncpg's type inference
    # stays unambiguous (the inline math hits 'operator does not exist:
    # timestamp with time zone >= interval').
    anchor = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
    delta = timedelta(hours=date_window_hours)
    lower = anchor - delta
    upper = anchor + delta
    if oe_league_code:
        game_rows = await conn.fetch(
            """
            SELECT oe_gameid, side, team_name, opp_team_name, game_date, league, game_in_series
            FROM lol_pro_matches
            WHERE game_date BETWEEN $1 AND $2
              AND league = $3
            """,
            lower, upper, oe_league_code,
        )
    else:
        game_rows = await conn.fetch(
            """
            SELECT oe_gameid, side, team_name, opp_team_name, game_date, league, game_in_series
            FROM lol_pro_matches
            WHERE game_date BETWEEN $1 AND $2
            """,
            lower, upper,
        )
    teams = set()
    for r in game_rows:
        teams.add(r["team_name"])
        teams.add(r["opp_team_name"])
    return (teams, list(game_rows))


# ---------------------------------------------------------------------------
# Team-name resolution (one PM name -> one OE name)
# ---------------------------------------------------------------------------


def _resolve_team(
    pm_name: str,
    alias_map: dict[str, str],
    candidate_oe_names: set[str],
) -> MatchResolution:
    """Resolve a single Polymarket team name through the 4-layer algorithm."""
    if not pm_name:
        return MatchResolution(pm_name=pm_name, oe_name=None, layer="unresolved", score=None)

    # Layer 1: alias map
    if pm_name in alias_map:
        return MatchResolution(
            pm_name=pm_name, oe_name=alias_map[pm_name], layer="alias", score=None,
        )

    # Untracked-hint short-circuit (don't even attempt fuzzy)
    if pm_name in OE_UNTRACKED_HINT:
        return MatchResolution(
            pm_name=pm_name, oe_name=None, layer="untracked_hint", score=None,
        )

    # Layer 2: exact match after normalization
    norm_pm = normalize_team_name(pm_name)
    if norm_pm:
        for candidate in candidate_oe_names:
            if normalize_team_name(candidate) == norm_pm:
                return MatchResolution(
                    pm_name=pm_name, oe_name=candidate, layer="normalize", score=100,
                )

    # Layer 3: fuzzy match (rapidfuzz token_set_ratio)
    if candidate_oe_names:
        match = process.extractOne(
            pm_name,
            list(candidate_oe_names),
            scorer=fuzz.token_set_ratio,
            score_cutoff=FUZZY_REVIEW_FLOOR,
        )
        if match is not None:
            best_name, score, _idx = match
            if score >= FUZZY_AUTO_ACCEPT_THRESHOLD:
                return MatchResolution(
                    pm_name=pm_name, oe_name=best_name, layer="fuzzy", score=int(score),
                )
            # 80-91: review queue (caller handles)
            return MatchResolution(
                pm_name=pm_name, oe_name=best_name, layer="fuzzy_review", score=int(score),
            )

    return MatchResolution(pm_name=pm_name, oe_name=None, layer="unresolved", score=None)


# ---------------------------------------------------------------------------
# Full-market join
# ---------------------------------------------------------------------------


async def join_one_market(
    conn: asyncpg.Connection,
    *,
    pm_condition_id: str,
    pm_event_id: str,
    pm_team_a: str,
    pm_team_b: str,
    pm_league_str: str | None,
    pm_start_time: datetime | None,
    market_kind: str,
    game_number: int | None,
    alias_map: dict[str, str] | None = None,
) -> JoinResult:
    """Resolve both team names and find the corresponding OE game(s).

    Returns a JoinResult describing what happened. Doesn't persist anything —
    caller decides whether to insert review-queue rows or just log results.
    """
    if alias_map is None:
        alias_map = await _load_alias_table(conn)

    oe_league_code = normalize_league(pm_league_str)
    candidate_teams, game_rows = await _candidate_oe_teams(
        conn, start_time=pm_start_time, oe_league_code=oe_league_code,
    )

    res_a = _resolve_team(pm_team_a, alias_map, candidate_teams)
    res_b = _resolve_team(pm_team_b, alias_map, candidate_teams)

    # If either side couldn't be resolved at all, report no match
    if res_a.oe_name is None and res_a.layer == "untracked_hint":
        return JoinResult(
            pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
            pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
            pm_start_time=pm_start_time, market_kind=market_kind,
            game_number=game_number, status="no_oe_data",
            oe_team_a_name=None, oe_team_b_name=None,
            matched_gameids=(), layer_a=res_a.layer, layer_b=res_b.layer,
            note="team_a in OE_UNTRACKED_HINT",
        )
    if res_b.oe_name is None and res_b.layer == "untracked_hint":
        return JoinResult(
            pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
            pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
            pm_start_time=pm_start_time, market_kind=market_kind,
            game_number=game_number, status="no_oe_data",
            oe_team_a_name=None, oe_team_b_name=None,
            matched_gameids=(), layer_a=res_a.layer, layer_b=res_b.layer,
            note="team_b in OE_UNTRACKED_HINT",
        )

    # Review-queue case: either side is in fuzzy_review
    if res_a.layer == "fuzzy_review" or res_b.layer == "fuzzy_review":
        return JoinResult(
            pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
            pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
            pm_start_time=pm_start_time, market_kind=market_kind,
            game_number=game_number, status="review_queued",
            oe_team_a_name=res_a.oe_name, oe_team_b_name=res_b.oe_name,
            matched_gameids=(),
            layer_a=res_a.layer, layer_b=res_b.layer,
            note=f"scores: a={res_a.score} b={res_b.score}",
        )

    # Either side unresolved (no match at any layer)
    if res_a.oe_name is None or res_b.oe_name is None:
        return JoinResult(
            pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
            pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
            pm_start_time=pm_start_time, market_kind=market_kind,
            game_number=game_number, status="no_team_match",
            oe_team_a_name=res_a.oe_name, oe_team_b_name=res_b.oe_name,
            matched_gameids=(),
            layer_a=res_a.layer, layer_b=res_b.layer,
            note=None,
        )

    # Both sides resolved — find OE games where both team names appear
    oe_a, oe_b = res_a.oe_name, res_b.oe_name
    matched_gameids: list[str] = []
    seen = set()
    for r in game_rows:
        # Each OE game has 2 rows (one per side). team_name + opp_team_name
        # collectively cover both teams in the game.
        if {r["team_name"], r["opp_team_name"]} == {oe_a, oe_b}:
            if r["oe_gameid"] not in seen:
                seen.add(r["oe_gameid"])
                matched_gameids.append(r["oe_gameid"])

    # For per-game markets, pick the corresponding gameid by series-order
    # (sort by game_in_series ascending, take index = game_number-1).
    if market_kind == "game" and game_number and matched_gameids:
        # Re-sort matched_gameids by game_in_series
        game_info = {r["oe_gameid"]: r["game_in_series"] for r in game_rows
                     if r["oe_gameid"] in set(matched_gameids)}
        matched_gameids.sort(key=lambda g: (game_info.get(g) or 99, g))
        if game_number - 1 < len(matched_gameids):
            matched_gameids = [matched_gameids[game_number - 1]]
        else:
            # Series didn't reach that game (e.g., BO3 ended 2-0, no Game 3)
            matched_gameids = []

    if not matched_gameids:
        return JoinResult(
            pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
            pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
            pm_start_time=pm_start_time, market_kind=market_kind,
            game_number=game_number, status="no_oe_data",
            oe_team_a_name=oe_a, oe_team_b_name=oe_b,
            matched_gameids=(),
            layer_a=res_a.layer, layer_b=res_b.layer,
            note=f"teams resolved but no OE game in window for {oe_a} vs {oe_b}",
        )

    return JoinResult(
        pm_condition_id=pm_condition_id, pm_event_id=pm_event_id,
        pm_team_a=pm_team_a, pm_team_b=pm_team_b, pm_league=pm_league_str,
        pm_start_time=pm_start_time, market_kind=market_kind,
        game_number=game_number, status="matched",
        oe_team_a_name=oe_a, oe_team_b_name=oe_b,
        matched_gameids=tuple(matched_gameids),
        layer_a=res_a.layer, layer_b=res_b.layer,
        note=None,
    )
