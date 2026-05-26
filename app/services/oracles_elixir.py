"""Oracle's Elixir CSV ingest service.

Streams an annual OE CSV (165 columns, ~80-120k rows, ~30 MB+) into the
lol_pro_matches table. Each game has 12 rows in the CSV: 5 player rows + 1
team summary row per side. We ingest only the 2 team-summary rows per game
(position='team') and stash the dropped columns into raw_blob.

Pairing logic: OE rows for the same game are consecutive in the CSV (the
team summary row follows its 5 player rows). We stream and emit pairs as
soon as both sides are seen.

Idempotent — re-ingesting the same CSV upserts rows with the same primary
key (oe_gameid, side).
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg

log = logging.getLogger(__name__)


# CSV column → DB column mapping for the team-summary row.
# Anything NOT in this dict goes to raw_blob.
CSV_TO_DB: dict[str, str] = {
    "gameid": "oe_gameid",
    "datacompleteness": "data_completeness",
    "league": "league",
    "year": "year",
    "split": "split",
    "playoffs": "playoffs",
    "date": "game_date",
    "game": "game_in_series",
    "patch": "patch",
    "side": "side",
    "teamname": "team_name",
    "teamid": "team_id",
    "firstPick": "first_pick",
    "pick1": "pick1", "pick2": "pick2", "pick3": "pick3", "pick4": "pick4", "pick5": "pick5",
    "ban1": "ban1", "ban2": "ban2", "ban3": "ban3", "ban4": "ban4", "ban5": "ban5",
    "gamelength": "game_length_s",
    "result": "result",
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "team kpm": "team_kpm",
    "ckpm": "ckpm",
    "firstblood": "first_blood",
    "firstdragon": "first_dragon",
    "firstherald": "first_herald",
    "firstbaron": "first_baron",
    "firsttower": "first_tower",
    "firstmidtower": "first_mid_tower",
    "firsttothreetowers": "first_to_three_towers",
    "dragons": "dragons",
    "opp_dragons": "opp_dragons",
    "infernals": "infernals",
    "mountains": "mountains",
    "clouds": "clouds",
    "oceans": "oceans",
    "chemtechs": "chemtechs",
    "hextechs": "hextechs",
    "elders": "elders",
    "heralds": "heralds",
    "void_grubs": "void_grubs",
    "barons": "barons",
    "atakhans": "atakhans",
    "towers": "towers",
    "opp_towers": "opp_towers",
    "turretplates": "turret_plates",
    "inhibitors": "inhibitors",
    "opp_inhibitors": "opp_inhibitors",
    "damagetochampions": "damage_to_champs",
    "dpm": "dpm",
    "damageshare": "damage_share",
    "damagetotowers": "damage_to_towers",
    "totalgold": "total_gold",
    "earnedgold": "earned_gold",
    "goldspent": "gold_spent",
    "earned gpm": "earned_gpm",
    "gspd": "gspd",
    "gpr": "gpr",
    "goldat10": "gold_at_10",
    "xpat10": "xp_at_10",
    "csat10": "cs_at_10",
    "golddiffat10": "golddiff_at_10",
    "xpdiffat10": "xpdiff_at_10",
    "csdiffat10": "csdiff_at_10",
    "killsat10": "kills_at_10",
    "deathsat10": "deaths_at_10",
    "assistsat10": "assists_at_10",
    "goldat15": "gold_at_15",
    "xpat15": "xp_at_15",
    "csat15": "cs_at_15",
    "golddiffat15": "golddiff_at_15",
    "xpdiffat15": "xpdiff_at_15",
    "csdiffat15": "csdiff_at_15",
    "killsat15": "kills_at_15",
    "deathsat15": "deaths_at_15",
    "assistsat15": "assists_at_15",
    "goldat20": "gold_at_20",
    "xpat20": "xp_at_20",
    "csat20": "cs_at_20",
    "golddiffat20": "golddiff_at_20",
    "xpdiffat20": "xpdiff_at_20",
    "csdiffat20": "csdiff_at_20",
    "killsat20": "kills_at_20",
    "deathsat20": "deaths_at_20",
    "assistsat20": "assists_at_20",
    "goldat25": "gold_at_25",
    "xpat25": "xp_at_25",
    "csat25": "cs_at_25",
    "golddiffat25": "golddiff_at_25",
    "xpdiffat25": "xpdiff_at_25",
    "csdiffat25": "csdiff_at_25",
    "killsat25": "kills_at_25",
    "deathsat25": "deaths_at_25",
    "assistsat25": "assists_at_25",
}

# Bool-coerce columns (CSV uses "1"/"0"/"" — empty is NULL).
BOOL_FIELDS: set[str] = {
    "playoffs", "first_pick", "first_blood", "first_dragon", "first_herald",
    "first_baron", "first_tower", "first_mid_tower", "first_to_three_towers",
}
# Int-coerce columns.
INT_FIELDS: set[str] = {
    "year", "game_in_series", "game_length_s",
    "kills", "deaths", "assists",
    "dragons", "opp_dragons", "infernals", "mountains", "clouds", "oceans",
    "chemtechs", "hextechs", "elders", "heralds", "void_grubs", "barons",
    "atakhans", "towers", "opp_towers", "turret_plates", "inhibitors",
    "opp_inhibitors", "damage_to_champs", "damage_to_towers",
    "total_gold", "earned_gold", "gold_spent",
    "gold_at_10", "xp_at_10", "cs_at_10", "golddiff_at_10", "xpdiff_at_10",
    "csdiff_at_10", "kills_at_10", "deaths_at_10", "assists_at_10",
    "gold_at_15", "xp_at_15", "cs_at_15", "golddiff_at_15", "xpdiff_at_15",
    "csdiff_at_15", "kills_at_15", "deaths_at_15", "assists_at_15",
    "gold_at_20", "xp_at_20", "cs_at_20", "golddiff_at_20", "xpdiff_at_20",
    "csdiff_at_20", "kills_at_20", "deaths_at_20", "assists_at_20",
    "gold_at_25", "xp_at_25", "cs_at_25", "golddiff_at_25", "xpdiff_at_25",
    "csdiff_at_25", "kills_at_25", "deaths_at_25", "assists_at_25",
}
# Result is required + tinyint (0 or 1).
REQUIRED_INT_FIELDS: set[str] = {"result"}
# Numeric (float) columns.
FLOAT_FIELDS: set[str] = {
    "team_kpm", "ckpm", "dpm", "damage_share", "earned_gpm", "gspd", "gpr",
}


@dataclass
class IngestResult:
    csv_path: str
    rows_seen: int
    games_seen: int
    rows_inserted: int
    rows_skipped: int
    duration_seconds: float


def _to_bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if s == "1" or s.lower() in ("true", "t", "yes"):
        return True
    if s == "0" or s.lower() in ("false", "f", "no"):
        return False
    return None


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_oe_date(v: str | None) -> datetime | None:
    """OE dates look like '2026-05-14 16:43:00'. Treat as UTC."""
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _row_hash(raw: dict[str, str]) -> str:
    """Stable hash of the raw row dict (so re-ingestion can detect changes)."""
    blob = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _coerce_team_row(raw: dict[str, str], ingested_year: int) -> dict[str, Any]:
    """Map a single OE team-summary row to our DB column shape.

    Drops player-row noise. Returns a dict ready for INSERT params, MINUS
    the opp_* fields (which come from the paired-side row).
    """
    out: dict[str, Any] = {}
    raw_blob: dict[str, Any] = {}
    for csv_col, csv_val in raw.items():
        db_col = CSV_TO_DB.get(csv_col)
        if db_col is None:
            # Keep dropped columns in raw_blob (string-typed; downstream
            # can re-parse). Drop empty strings to keep blob compact.
            if csv_val not in (None, ""):
                raw_blob[csv_col] = csv_val
            continue
        # Coerce per type
        if db_col in BOOL_FIELDS:
            out[db_col] = _to_bool(csv_val)
        elif db_col in INT_FIELDS or db_col in REQUIRED_INT_FIELDS:
            out[db_col] = _to_int(csv_val)
        elif db_col in FLOAT_FIELDS:
            out[db_col] = _to_float(csv_val)
        elif db_col == "game_date":
            out[db_col] = _parse_oe_date(csv_val)
        else:
            # Plain text columns
            out[db_col] = csv_val if csv_val != "" else None

    out["raw_blob"] = json.dumps(raw_blob) if raw_blob else None
    out["raw_row_hash"] = _row_hash(raw)
    out["ingested_year"] = ingested_year
    return out


# ---------------------------------------------------------------------------
# UPSERT SQL
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO lol_pro_matches (
    oe_gameid, side, game_date, patch, league, year, split, playoffs, game_in_series,
    team_name, team_id, opp_team_name, opp_team_id, first_pick,
    pick1, pick2, pick3, pick4, pick5,
    ban1, ban2, ban3, ban4, ban5,
    result, game_length_s, kills, deaths, assists, team_kpm, ckpm,
    first_blood, first_dragon, first_herald, first_baron,
    first_tower, first_mid_tower, first_to_three_towers,
    dragons, opp_dragons, infernals, mountains, clouds, oceans, chemtechs, hextechs,
    elders, heralds, void_grubs, barons, atakhans,
    towers, opp_towers, turret_plates, inhibitors, opp_inhibitors,
    damage_to_champs, dpm, damage_share, damage_to_towers,
    total_gold, earned_gold, gold_spent, earned_gpm, gspd, gpr,
    gold_at_10, xp_at_10, cs_at_10, golddiff_at_10, xpdiff_at_10, csdiff_at_10,
    kills_at_10, deaths_at_10, assists_at_10,
    gold_at_15, xp_at_15, cs_at_15, golddiff_at_15, xpdiff_at_15, csdiff_at_15,
    kills_at_15, deaths_at_15, assists_at_15,
    gold_at_20, xp_at_20, cs_at_20, golddiff_at_20, xpdiff_at_20, csdiff_at_20,
    kills_at_20, deaths_at_20, assists_at_20,
    gold_at_25, xp_at_25, cs_at_25, golddiff_at_25, xpdiff_at_25, csdiff_at_25,
    kills_at_25, deaths_at_25, assists_at_25,
    data_completeness, raw_row_hash, raw_blob, ingested_year
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    $10, $11, $12, $13, $14,
    $15, $16, $17, $18, $19,
    $20, $21, $22, $23, $24,
    $25, $26, $27, $28, $29, $30, $31,
    $32, $33, $34, $35,
    $36, $37, $38,
    $39, $40, $41, $42, $43, $44, $45, $46,
    $47, $48, $49, $50, $51,
    $52, $53, $54, $55, $56,
    $57, $58, $59, $60,
    $61, $62, $63, $64, $65, $66,
    $67, $68, $69, $70, $71, $72, $73, $74, $75,
    $76, $77, $78, $79, $80, $81, $82, $83, $84,
    $85, $86, $87, $88, $89, $90, $91, $92, $93,
    $94, $95, $96, $97, $98, $99, $100, $101, $102,
    $103, $104, $105::jsonb, $106
)
ON CONFLICT (oe_gameid, side) DO UPDATE SET
    game_date = EXCLUDED.game_date,
    patch = EXCLUDED.patch,
    league = EXCLUDED.league,
    year = EXCLUDED.year,
    split = EXCLUDED.split,
    playoffs = EXCLUDED.playoffs,
    game_in_series = EXCLUDED.game_in_series,
    team_name = EXCLUDED.team_name,
    team_id = EXCLUDED.team_id,
    opp_team_name = EXCLUDED.opp_team_name,
    opp_team_id = EXCLUDED.opp_team_id,
    first_pick = EXCLUDED.first_pick,
    pick1 = EXCLUDED.pick1, pick2 = EXCLUDED.pick2, pick3 = EXCLUDED.pick3,
    pick4 = EXCLUDED.pick4, pick5 = EXCLUDED.pick5,
    ban1 = EXCLUDED.ban1, ban2 = EXCLUDED.ban2, ban3 = EXCLUDED.ban3,
    ban4 = EXCLUDED.ban4, ban5 = EXCLUDED.ban5,
    result = EXCLUDED.result, game_length_s = EXCLUDED.game_length_s,
    kills = EXCLUDED.kills, deaths = EXCLUDED.deaths, assists = EXCLUDED.assists,
    team_kpm = EXCLUDED.team_kpm, ckpm = EXCLUDED.ckpm,
    first_blood = EXCLUDED.first_blood, first_dragon = EXCLUDED.first_dragon,
    first_herald = EXCLUDED.first_herald, first_baron = EXCLUDED.first_baron,
    first_tower = EXCLUDED.first_tower, first_mid_tower = EXCLUDED.first_mid_tower,
    first_to_three_towers = EXCLUDED.first_to_three_towers,
    dragons = EXCLUDED.dragons, opp_dragons = EXCLUDED.opp_dragons,
    infernals = EXCLUDED.infernals, mountains = EXCLUDED.mountains,
    clouds = EXCLUDED.clouds, oceans = EXCLUDED.oceans,
    chemtechs = EXCLUDED.chemtechs, hextechs = EXCLUDED.hextechs,
    elders = EXCLUDED.elders, heralds = EXCLUDED.heralds,
    void_grubs = EXCLUDED.void_grubs, barons = EXCLUDED.barons,
    atakhans = EXCLUDED.atakhans, towers = EXCLUDED.towers,
    opp_towers = EXCLUDED.opp_towers, turret_plates = EXCLUDED.turret_plates,
    inhibitors = EXCLUDED.inhibitors, opp_inhibitors = EXCLUDED.opp_inhibitors,
    damage_to_champs = EXCLUDED.damage_to_champs, dpm = EXCLUDED.dpm,
    damage_share = EXCLUDED.damage_share, damage_to_towers = EXCLUDED.damage_to_towers,
    total_gold = EXCLUDED.total_gold, earned_gold = EXCLUDED.earned_gold,
    gold_spent = EXCLUDED.gold_spent, earned_gpm = EXCLUDED.earned_gpm,
    gspd = EXCLUDED.gspd, gpr = EXCLUDED.gpr,
    gold_at_10 = EXCLUDED.gold_at_10, xp_at_10 = EXCLUDED.xp_at_10,
    cs_at_10 = EXCLUDED.cs_at_10, golddiff_at_10 = EXCLUDED.golddiff_at_10,
    xpdiff_at_10 = EXCLUDED.xpdiff_at_10, csdiff_at_10 = EXCLUDED.csdiff_at_10,
    kills_at_10 = EXCLUDED.kills_at_10, deaths_at_10 = EXCLUDED.deaths_at_10,
    assists_at_10 = EXCLUDED.assists_at_10,
    gold_at_15 = EXCLUDED.gold_at_15, xp_at_15 = EXCLUDED.xp_at_15,
    cs_at_15 = EXCLUDED.cs_at_15, golddiff_at_15 = EXCLUDED.golddiff_at_15,
    xpdiff_at_15 = EXCLUDED.xpdiff_at_15, csdiff_at_15 = EXCLUDED.csdiff_at_15,
    kills_at_15 = EXCLUDED.kills_at_15, deaths_at_15 = EXCLUDED.deaths_at_15,
    assists_at_15 = EXCLUDED.assists_at_15,
    gold_at_20 = EXCLUDED.gold_at_20, xp_at_20 = EXCLUDED.xp_at_20,
    cs_at_20 = EXCLUDED.cs_at_20, golddiff_at_20 = EXCLUDED.golddiff_at_20,
    xpdiff_at_20 = EXCLUDED.xpdiff_at_20, csdiff_at_20 = EXCLUDED.csdiff_at_20,
    kills_at_20 = EXCLUDED.kills_at_20, deaths_at_20 = EXCLUDED.deaths_at_20,
    assists_at_20 = EXCLUDED.assists_at_20,
    gold_at_25 = EXCLUDED.gold_at_25, xp_at_25 = EXCLUDED.xp_at_25,
    cs_at_25 = EXCLUDED.cs_at_25, golddiff_at_25 = EXCLUDED.golddiff_at_25,
    xpdiff_at_25 = EXCLUDED.xpdiff_at_25, csdiff_at_25 = EXCLUDED.csdiff_at_25,
    kills_at_25 = EXCLUDED.kills_at_25, deaths_at_25 = EXCLUDED.deaths_at_25,
    assists_at_25 = EXCLUDED.assists_at_25,
    data_completeness = EXCLUDED.data_completeness,
    raw_row_hash = EXCLUDED.raw_row_hash,
    raw_blob = EXCLUDED.raw_blob,
    ingested_year = EXCLUDED.ingested_year,
    ingested_at = NOW()
"""

# The column ordering for the upsert positional args, matching $1..$106.
_UPSERT_COLS: tuple[str, ...] = (
    "oe_gameid", "side", "game_date", "patch", "league", "year", "split", "playoffs", "game_in_series",
    "team_name", "team_id", "opp_team_name", "opp_team_id", "first_pick",
    "pick1", "pick2", "pick3", "pick4", "pick5",
    "ban1", "ban2", "ban3", "ban4", "ban5",
    "result", "game_length_s", "kills", "deaths", "assists", "team_kpm", "ckpm",
    "first_blood", "first_dragon", "first_herald", "first_baron",
    "first_tower", "first_mid_tower", "first_to_three_towers",
    "dragons", "opp_dragons", "infernals", "mountains", "clouds", "oceans", "chemtechs", "hextechs",
    "elders", "heralds", "void_grubs", "barons", "atakhans",
    "towers", "opp_towers", "turret_plates", "inhibitors", "opp_inhibitors",
    "damage_to_champs", "dpm", "damage_share", "damage_to_towers",
    "total_gold", "earned_gold", "gold_spent", "earned_gpm", "gspd", "gpr",
    "gold_at_10", "xp_at_10", "cs_at_10", "golddiff_at_10", "xpdiff_at_10", "csdiff_at_10",
    "kills_at_10", "deaths_at_10", "assists_at_10",
    "gold_at_15", "xp_at_15", "cs_at_15", "golddiff_at_15", "xpdiff_at_15", "csdiff_at_15",
    "kills_at_15", "deaths_at_15", "assists_at_15",
    "gold_at_20", "xp_at_20", "cs_at_20", "golddiff_at_20", "xpdiff_at_20", "csdiff_at_20",
    "kills_at_20", "deaths_at_20", "assists_at_20",
    "gold_at_25", "xp_at_25", "cs_at_25", "golddiff_at_25", "xpdiff_at_25", "csdiff_at_25",
    "kills_at_25", "deaths_at_25", "assists_at_25",
    "data_completeness", "raw_row_hash", "raw_blob", "ingested_year",
)


async def _flush_batch(
    conn: asyncpg.Connection, batch: list[dict[str, Any]],
) -> int:
    """Upsert a batch of rows. Returns inserted/updated count."""
    if not batch:
        return 0
    args_list = [
        tuple(row.get(col) for col in _UPSERT_COLS)
        for row in batch
    ]
    await conn.executemany(_UPSERT_SQL, args_list)
    return len(batch)


async def ingest_oracles_elixir_csv(
    conn: asyncpg.Connection,
    csv_path: str | Path,
    *,
    batch_size: int = 500,
) -> IngestResult:
    """Stream an OE annual CSV and upsert team-summary rows into lol_pro_matches.

    Pairs each game's Blue + Red team rows so opp_* fields are populated.
    """
    started = datetime.now(timezone.utc)
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(p)

    # Infer the source year from the filename (e.g. "2025_LoL_..." -> 2025).
    try:
        ingested_year = int(p.name.split("_", 1)[0])
    except (ValueError, IndexError):
        ingested_year = 0

    rows_seen = 0
    games_seen = 0
    rows_inserted = 0
    rows_skipped = 0
    batch: list[dict[str, Any]] = []

    # Per-gameid in-flight buffer (1 row max in normal CSV ordering, but
    # we treat it as a dict for robustness against ordering quirks).
    pending: dict[str, dict[str, Any]] = {}

    with p.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for raw in reader:
            rows_seen += 1
            if raw.get("position") != "team":
                continue  # skip the 10 player rows per game
            try:
                team_row = _coerce_team_row(raw, ingested_year)
            except Exception as e:  # noqa: BLE001
                log.warning("coerce failed at row %d: %r", rows_seen, e)
                rows_skipped += 1
                continue

            gameid = team_row.get("oe_gameid")
            if not gameid or team_row.get("result") is None:
                rows_skipped += 1
                continue

            # Pair with the other side
            other = pending.pop(gameid, None)
            if other is None:
                pending[gameid] = team_row
                continue

            # We have both sides. Fill opp_* fields on each, queue both for upsert.
            games_seen += 1
            a, b = team_row, other
            a["opp_team_name"] = b.get("team_name")
            a["opp_team_id"] = b.get("team_id")
            b["opp_team_name"] = a.get("team_name")
            b["opp_team_id"] = a.get("team_id")
            batch.append(a)
            batch.append(b)

            if len(batch) >= batch_size:
                rows_inserted += await _flush_batch(conn, batch)
                batch = []

        # Final flush
        if batch:
            rows_inserted += await _flush_batch(conn, batch)

    # Anything left in pending is unpaired (CSV missing the other side) — log
    if pending:
        log.warning(
            "ingest: %d games had only one side in the CSV (orphaned team rows). "
            "Sample gameids: %s",
            len(pending), list(pending.keys())[:5],
        )
        rows_skipped += len(pending)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "OE ingest: csv=%s rows_seen=%d games=%d inserted=%d skipped=%d in %.1fs",
        p.name, rows_seen, games_seen, rows_inserted, rows_skipped, duration,
    )
    return IngestResult(
        csv_path=str(p),
        rows_seen=rows_seen,
        games_seen=games_seen,
        rows_inserted=rows_inserted,
        rows_skipped=rows_skipped,
        duration_seconds=duration,
    )
