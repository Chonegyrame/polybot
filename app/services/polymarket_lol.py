"""LoL collector service.

Identifies LoL pro-match markets on Polymarket, classifies them by kind
(series vs per-game) and game-number, and captures price snapshots at
20-second cadence while matches are in the active window.

The classification layer (polymarket_lol_market_meta) is what makes this
collector cheap: we only snapshot markets we've classified, not every
LoL-tagged thing on Polymarket (which includes derivative markets like
Game Handicap and Games Total O/U that V1 explicitly skips per the design
decision in session-state).

Design notes:
  - We classify only "series" and "game" markets per V1 scope. Series =
    the BO match winner. Game = "Game N Winner" child markets. Everything
    else (handicaps, totals, futures like "LCK 2026 Season Winner") is
    not classified and therefore not snapshotted by this collector.
  - The discovery job sweeps gamma-api with tag_slug="league-of-legends"
    AND tag_slug="lol" since both tags appear in the live catalog.
  - The snapshot job filters to markets where the event's end_date falls
    in the [now - 3h, now + 4h] window (the "active match window"). Outside
    that window we don't snapshot — discovery picks up new markets on its
    own cadence, and post-match resolution is handled by the existing
    market sync via discover_and_persist_markets.

ALL Polymarket API calls go through app.services.polymarket as the project
rule requires.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import asyncpg

from app.db import crud
from app.services.polymarket import PolymarketClient
from app.services.polymarket_types import Event, Market, pair_yes_no_tokens
from app.services.market_sync import _derive_category, _infer_resolved_outcome, _parse_iso

log = logging.getLogger(__name__)

# Both tag-slugs appear in the live gamma catalog for LoL events.
# Confirmed via SELECT against the events table (2026-05): 341 events tagged
# "league-of-legends" + 34 tagged "lol".
LOL_TAG_SLUGS: tuple[str, ...] = ("league-of-legends", "lol")

# Parse match-style event titles:
#   "LoL: T1 vs Gen.G (BO3) - LCK Rounds 1-2"
#   "LoL: Karmine Corp vs Movistar KOI (BO5) - Esports World Cup EMEA Qualifier Playoffs"
#   "LoL: Team A vs Team B - LCS Spring"   (no BO format)
# Captures team_a, team_b, optional bo_format, league string.
_MATCH_TITLE_RE = re.compile(
    r"^LoL:\s*(?P<team_a>.+?)\s+vs\s+(?P<team_b>.+?)"
    r"(?:\s*\(BO(?P<bo>\d+)\))?"
    r"(?:\s*-\s*(?P<league>.+?))?$",
    re.IGNORECASE,
)

# Match a market question that looks like a per-game-winner child market:
#   "LoL: LNG Esports vs LGD Gaming - Game 1 Winner"
_GAME_WINNER_RE = re.compile(
    r"\bGame\s+(?P<num>[1-5])\s+Winner\b",
    re.IGNORECASE,
)

# Patterns that mark a market as a derivative we intentionally SKIP.
_DERIVATIVE_PATTERNS = (
    re.compile(r"\bGame\s+Handicap\b", re.IGNORECASE),
    re.compile(r"\bGames?\s+Total\b", re.IGNORECASE),
    re.compile(r"\bO/U\b", re.IGNORECASE),
    re.compile(r"\bMap\s+Handicap\b", re.IGNORECASE),
    # Per-game prop markets (penta-kill, baron, total kills odd/even, etc.).
    # V1 captures match + per-game outcomes only, no props.
    re.compile(r"^Game\s+[1-5]:\s+", re.IGNORECASE),
)


def _pair_tokens_for_lol(
    outcomes: list[str], clob_token_ids: list[str],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Pair the two CLOB tokens for a LoL market.

    Unlike pair_yes_no_tokens, this works for markets whose outcomes are
    team names (e.g. ["Dplus KIA", "T1"]) rather than ["Yes", "No"]. For
    our snapshot purposes the labels are immaterial — we just need both
    sides of the book.

    Returns (token_a, token_b, label_a, label_b) where 'a' is outcomes[0]
    and 'b' is outcomes[1]. Returns all None on malformed input.
    """
    if len(outcomes) != 2 or len(clob_token_ids) != 2:
        return (None, None, None, None)
    label_a = outcomes[0] if isinstance(outcomes[0], str) else None
    label_b = outcomes[1] if isinstance(outcomes[1], str) else None
    return (clob_token_ids[0], clob_token_ids[1], label_a, label_b)


@dataclass(frozen=True)
class ParsedEventTitle:
    team_a: str | None
    team_b: str | None
    bo_format: int | None
    league: str | None


def parse_event_title(title: str | None) -> ParsedEventTitle:
    """Extract teams / BO format / league from a match-style event title.

    Returns a ParsedEventTitle with None fields if the title isn't a match-style
    title (e.g. "LCK 2026 Season Winner" — tournament-wide, not a single match).
    Callers use the None-team_a guard to detect "not a per-match event."
    """
    if not title:
        return ParsedEventTitle(None, None, None, None)
    m = _MATCH_TITLE_RE.match(title.strip())
    if not m:
        return ParsedEventTitle(None, None, None, None)
    bo_raw = m.group("bo")
    bo: int | None
    try:
        bo = int(bo_raw) if bo_raw else None
    except (TypeError, ValueError):
        bo = None
    if bo is not None and bo not in (1, 3, 5):
        bo = None
    league = (m.group("league") or "").strip() or None
    return ParsedEventTitle(
        team_a=(m.group("team_a") or "").strip() or None,
        team_b=(m.group("team_b") or "").strip() or None,
        bo_format=bo,
        league=league,
    )


@dataclass(frozen=True)
class MarketClassification:
    market_kind: str  # 'series' or 'game'
    game_number: int | None


def classify_market(
    market: Market, parsed_event: ParsedEventTitle, event_title: str | None,
) -> MarketClassification | None:
    """Decide whether a market is a series winner, per-game winner, or
    derivative we skip.

    Returns None if the market should NOT be classified (derivative, or we
    couldn't make sense of the question).

    Resolution logic:
      1. If the question matches a derivative pattern (Handicap, O/U, Totals),
         return None.
      2. If the question contains "Game N Winner", classify as game with
         that game_number.
      3. If the question equals or strongly resembles the event title, classify
         as series.
      4. Otherwise, conservatively return None so we don't accidentally include
         markets we don't yet understand (e.g. first-blood markets).
    """
    q = (market.question or "").strip()
    if not q:
        return None

    for pat in _DERIVATIVE_PATTERNS:
        if pat.search(q):
            return None

    gm = _GAME_WINNER_RE.search(q)
    if gm:
        try:
            num = int(gm.group("num"))
        except (TypeError, ValueError):
            return None
        if 1 <= num <= 5:
            return MarketClassification(market_kind="game", game_number=num)
        return None

    # Series-level: question matches the event title verbatim, or is a
    # close paraphrase. The cleanest heuristic given the live data: event
    # titles look like "LoL: A vs B (BO_) - League" and the series market
    # question is the same string. We accept exact match OR a question that
    # contains both team names AND a BO marker.
    if event_title and q.strip().lower() == event_title.strip().lower():
        return MarketClassification(market_kind="series", game_number=None)
    if parsed_event.team_a and parsed_event.team_b:
        ql = q.lower()
        if (
            parsed_event.team_a.lower() in ql
            and parsed_event.team_b.lower() in ql
            and re.search(r"\bBO\d\b", q, re.IGNORECASE)
        ):
            return MarketClassification(market_kind="series", game_number=None)

    return None


async def _persist_event_with_markets(
    conn: asyncpg.Connection, event: Event,
) -> tuple[int, int]:
    """Upsert event + its markets via the existing crud helpers, then
    classify each market into polymarket_lol_market_meta.

    Returns (markets_seen, markets_classified).
    """
    derived_category = _derive_category(event.tags, fallback=event.category)
    start_time = _parse_iso(event.start_time)
    await crud.upsert_event(
        conn,
        event_id=event.id,
        slug=event.slug,
        title=event.title,
        category=derived_category,
        tags=event.tags,
        start_time=start_time,
        end_date=_parse_iso(event.end_date),
        closed=event.closed,
    )

    parsed = parse_event_title(event.title)
    # If we can't parse teams from the title, this is likely a tournament-wide
    # market (e.g. "LCK 2026 Season Winner"). Persist the event/markets via the
    # standard pipeline but don't classify into the LoL meta layer. We don't
    # snapshot tournament-wide markets in V1.
    is_per_match = parsed.team_a is not None and parsed.team_b is not None

    seen = 0
    classified = 0
    end_date = _parse_iso(event.end_date)

    for m in event.markets:
        if not m.condition_id:
            continue
        clob_yes, clob_no = pair_yes_no_tokens(m.outcomes, m.clob_token_ids)
        resolved = _infer_resolved_outcome(m)
        await crud.upsert_market(
            conn,
            condition_id=m.condition_id,
            gamma_id=m.id,
            event_id=event.id,
            slug=m.slug,
            question=m.question,
            clob_token_yes=clob_yes,
            clob_token_no=clob_no,
            outcomes=m.outcomes if m.outcomes else None,
            end_date=_parse_iso(m.end_date),
            closed=m.closed,
            resolved_outcome=resolved,
        )
        seen += 1

        if not is_per_match:
            continue
        cls = classify_market(m, parsed, event.title)
        if cls is None:
            continue
        # LoL markets often use team-name outcomes (e.g. ["Dplus KIA", "T1"])
        # rather than ["Yes", "No"], which makes pair_yes_no_tokens return
        # None. For snapshot purposes the labels are immaterial, so we pair
        # by index and store both tokens in the LoL meta layer directly.
        tok_a, tok_b, lbl_a, lbl_b = _pair_tokens_for_lol(
            m.outcomes, m.clob_token_ids,
        )
        if not tok_a or not tok_b:
            continue
        # Harvest closing-line scalar fields from the same gamma response.
        # No additional API cost — these come along for free on every market
        # record. For resolved markets they ARE the closing-line snapshot.
        closed_time = _parse_iso(m.raw.get("closedTime"))
        resolved_label: str | None = None
        if m.closed and m.outcomes and len(m.outcome_prices) == len(m.outcomes):
            # outcome_prices is a one-hot vector for settled markets:
            # the winning outcome carries 1.0, the loser 0.0. Resolve to
            # the winning label (team name or "Yes"/"No").
            try:
                winner_idx = next(
                    i for i, p in enumerate(m.outcome_prices) if p > 0.5
                )
                resolved_label = m.outcomes[winner_idx]
            except (StopIteration, IndexError):
                resolved_label = None
        await crud.upsert_lol_market_meta(
            conn,
            condition_id=m.condition_id,
            event_id=event.id,
            market_kind=cls.market_kind,
            game_number=cls.game_number,
            bo_format=parsed.bo_format,
            team_a=parsed.team_a,
            team_b=parsed.team_b,
            league=parsed.league,
            event_title=event.title,
            # Prefer true start_time over the end_date fallback. Both live
            # in the meta table for fast reads without joining to events.
            starts_at_guess=start_time if start_time is not None else end_date,
            clob_token_a=tok_a,
            clob_token_b=tok_b,
            outcome_a_label=lbl_a,
            outcome_b_label=lbl_b,
            last_trade_price=m.last_trade_price,
            best_bid_at_sync=m.best_bid,
            best_ask_at_sync=m.best_ask,
            volume_num=m.volume_num,
            closed_time=closed_time,
            market_closed=m.closed,
            resolved_outcome=resolved_label,
        )
        classified += 1

    return seen, classified


async def discover_lol_events_and_classify(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    *,
    page_size: int = 100,
    max_pages_per_tag: int = 20,
    include_closed: bool = False,
) -> tuple[int, int, int]:
    """Sweep gamma-api for LoL events, upsert them + their markets, and
    classify each market into polymarket_lol_market_meta.

    Paginates per tag_slug. Stops at max_pages_per_tag pages per tag (safety
    bound — gamma's normal active LoL set is well under this).

    Returns (events_seen, markets_seen, markets_classified).
    """
    events_seen = 0
    markets_seen = 0
    markets_classified = 0
    seen_event_ids: set[str] = set()

    for tag_slug in LOL_TAG_SLUGS:
        for page in range(max_pages_per_tag):
            offset = page * page_size
            events = await pm.get_events(
                limit=page_size,
                offset=offset,
                closed=False if not include_closed else None,
                tag_slug=tag_slug,
            )
            if not events:
                break
            for ev in events:
                if not ev.id or ev.id in seen_event_ids:
                    continue
                seen_event_ids.add(ev.id)
                events_seen += 1
                try:
                    seen, classified = await _persist_event_with_markets(conn, ev)
                    markets_seen += seen
                    markets_classified += classified
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "LoL persist failed for event %s (%s): %r",
                        ev.id, (ev.title or "")[:80], e,
                    )
            if len(events) < page_size:
                break

    log.info(
        "LoL discovery: events=%d, markets=%d, classified=%d (tags=%s)",
        events_seen, markets_seen, markets_classified, LOL_TAG_SLUGS,
    )
    return events_seen, markets_seen, markets_classified


# ---------------------------------------------------------------------------
# Snapshot path
# ---------------------------------------------------------------------------


@dataclass
class SnapshotResult:
    markets_attempted: int
    snapshots_written: int
    failures: int
    duration_seconds: float


def _midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _spread_bps(bid: float | None, ask: float | None) -> int | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return int(round((ask - bid) / mid * 10_000))


def _depth_within(
    levels: list[dict] | None, anchor: float | None, cents: float,
) -> float | None:
    """Sum the size on `levels` whose price is within `cents` cents of `anchor`.

    For a YES bid book, anchor is the best bid; we sum bid sizes where
    price >= anchor - cents/100. For a YES ask book, anchor is best ask;
    we sum sizes where price <= anchor + cents/100. The same shape used by
    the existing book metrics code (see orderbook.py compute_book_metrics).
    """
    if not levels or anchor is None:
        return None
    threshold_low = anchor - cents / 100.0
    threshold_high = anchor + cents / 100.0
    total = 0.0
    for lvl in levels:
        try:
            price = float(lvl.get("price"))
            size = float(lvl.get("size"))
        except (TypeError, ValueError):
            continue
        if threshold_low <= price <= threshold_high:
            total += size
    return total


async def snapshot_one_market(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    *,
    condition_id: str,
    clob_token_a: str | None,
    clob_token_b: str | None,
) -> bool:
    """Fetch L2 orderbook for both outcome tokens (A and B) of a binary
    LoL market and persist one row into polymarket_lol_price_snapshots.

    Outcomes A and B are arbitrary labels (team names for game-winner
    markets, "Yes"/"No" for some series markets). The price-snapshot
    columns use the legacy yes_*/no_* names to keep the schema stable;
    A maps to yes_*, B maps to no_*.

    Returns True if a row was written (even if some fields are NULL due to
    partial book data), False on full failure.
    """
    if not clob_token_a or not clob_token_b:
        await crud.insert_lol_price_snapshot(
            conn,
            condition_id=condition_id,
            fetch_source="unavailable",
            error_repr="missing_token_ids",
        )
        return False

    book_a = None
    book_b = None
    err: str | None = None
    try:
        book_a = await pm.get_orderbook(clob_token_a)
    except Exception as e:  # noqa: BLE001
        err = f"a:{type(e).__name__}:{str(e)[:80]}"
    try:
        book_b = await pm.get_orderbook(clob_token_b)
    except Exception as e:  # noqa: BLE001
        err = (err + " | " if err else "") + f"b:{type(e).__name__}:{str(e)[:80]}"

    if book_a is None and book_b is None:
        await crud.insert_lol_price_snapshot(
            conn,
            condition_id=condition_id,
            fetch_source="unavailable",
            error_repr=err or "both_books_none",
        )
        return False

    def _best(book: dict | None, side: str) -> float | None:
        if not book:
            return None
        levels = book.get(side) or []
        if not levels:
            return None
        try:
            return float(levels[0].get("price"))
        except (TypeError, ValueError):
            return None

    a_bid = _best(book_a, "bids")
    a_ask = _best(book_a, "asks")
    b_bid = _best(book_b, "bids")
    b_ask = _best(book_b, "asks")

    a_bid_size_5c = _depth_within(
        book_a.get("bids") if book_a else None, a_bid, cents=5
    )
    a_ask_size_5c = _depth_within(
        book_a.get("asks") if book_a else None, a_ask, cents=5
    )

    await crud.insert_lol_price_snapshot(
        conn,
        condition_id=condition_id,
        yes_bid=a_bid,
        yes_ask=a_ask,
        yes_mid=_midpoint(a_bid, a_ask),
        no_bid=b_bid,
        no_ask=b_ask,
        no_mid=_midpoint(b_bid, b_ask),
        yes_bid_size_5c=a_bid_size_5c,
        yes_ask_size_5c=a_ask_size_5c,
        spread_bps=_spread_bps(a_bid, a_ask),
        fetch_source="clob_l2",
        error_repr=err,
    )
    return True
