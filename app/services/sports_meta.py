"""Live sports fixture lookup -- "is the match live, what's the score?"

Tier-2 enhancement for the SignalCard chip when category=='sports'. Polymarket
itself only exposes the market `end_date`, not kickoff time / live minute / score.
We map a market question to a real-world fixture by fuzzy team-name matching
against ESPN's hidden public scoreboard API.

Coverage is intentionally partial -- we silently omit the chip for any market
we can't confidently match (cup ties, friendlies, niche leagues, non-standard
question phrasing). The user has explicitly accepted this trade-off.

Provider design: ESPN is the primary source. The lookup helper is structured
behind a small provider interface so a future TheSportsDB / football-data.org
fallback can plug in without touching call sites.

Caching strategy (in-process module-level dicts, no Redis):
  - condition_id -> (sport, league, fixture_id, kickoff_ts) cached 24h.
    Once we've matched a market to a fixture, that mapping is stable for
    the life of the market.
  - fixture_id -> FixtureStatus cached 60s. Live state changes minute-by-
    minute but we don't need sub-30s freshness for the chip. The 10-min
    refresh cycle dwarfs any sub-minute cache hit benefit.

Failure modes are absorbed silently inside `lookup_live_status_for_market`:
network timeout, ESPN 5xx, no fixture matched -> returns None. Caller is
expected to omit the chip on None and never block the page on this lookup.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ESPN scoreboard endpoint shape:
#   GET https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD
# No auth, no key. Slightly slower than data-api.polymarket.com so we cache
# aggressively. Returns a JSON object with `events: [...]`.
_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Leagues we currently know how to look up. Add to this list to widen
# coverage. Keep "soccer" leagues separate from other sports because ESPN
# doesn't have an "all soccer" endpoint -- you must query each league by code.
_SOCCER_LEAGUES = (
    "eng.1",          # Premier League
    "esp.1",          # La Liga
    "ita.1",          # Serie A  <-- the Cagliari/Udinese case
    "ger.1",          # Bundesliga
    "fra.1",          # Ligue 1
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "usa.1",          # MLS
    "mex.1",          # Liga MX
)
_OTHER_LEAGUES = (
    ("basketball", "nba"),
    ("football",   "nfl"),
    ("baseball",   "mlb"),
    ("hockey",     "nhl"),
)

# Module-level caches. Both are { key: (expires_ts, value) }.
_FIXTURE_MAP_TTL = 24 * 3600
_LIVE_STATUS_TTL = 60
_fixture_map_cache: dict[str, tuple[float, "FixtureRef | None"]] = {}
_live_status_cache: dict[str, tuple[float, "FixtureStatus | None"]] = {}


@dataclass(frozen=True)
class FixtureRef:
    """Identifies a real-world match. Stable per market; cached 24h."""
    sport: str           # 'soccer' | 'basketball' | 'football' | 'baseball' | 'hockey'
    league: str          # 'eng.1' | 'nba' | etc -- ESPN league code
    fixture_id: str      # ESPN event id
    kickoff_at: datetime
    home_team: str
    away_team: str


@dataclass(frozen=True)
class FixtureStatus:
    """Live state of a fixture for the chip. Cached 60s.

    `state` mirrors ESPN's status.type.state:
      - 'pre'  : pre-game (kickoff in the future)
      - 'in'   : live (currently being played; period/clock populated)
      - 'post' : finished (final score available)
      - 'ht'   : half-time (treated separately for chip styling; ESPN reports
                 this via `period=2` + `displayClock=0:00` mid-game; we map to 'ht')
      - 'unknown' : ESPN responded with something we don't recognise

    Times are UTC. Scores can be None when the event hasn't started.
    """
    sport: str
    league: str
    fixture_id: str
    state: str
    kickoff_at: datetime
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    current_minute: int | None  # for live soccer; None for other states/sports
    period: int | None          # quarter / half / inning -- non-soccer
    display_clock: str | None
    short_detail: str | None    # ESPN's own one-line label, e.g. "HT", "Final"


# --- Question parsing ------------------------------------------------------

# Most common Polymarket sports market shape: "Will <Team> win on YYYY-MM-DD?"
# We accept some slack around punctuation and the "FC" / "Calcio" / etc suffix.
_QUESTION_RE = re.compile(
    r"will\s+(?P<team>.+?)\s+win\s+on\s+(?P<date>\d{4}-\d{2}-\d{2})\??$",
    re.IGNORECASE,
)
# Looser fallback: "Will <Team> beat <Other>?" or "<Team> vs <Other>?"
_VS_RE = re.compile(
    r"will\s+(?P<team>.+?)\s+(?:beat|defeat)\s+(?P<other>.+?)\??$",
    re.IGNORECASE,
)


def _parse_team_and_date(question: str, end_date: date | None) -> tuple[str, date] | None:
    """Extract (team_name, fixture_date) from a market question.

    Returns None when the question doesn't fit the patterns we support.
    Future patterns: tournament-winner markets, prop markets, hedged
    multi-leg lines. Out of V1 scope.
    """
    if not question:
        return None
    q = question.strip()
    m = _QUESTION_RE.match(q)
    if m:
        try:
            d = date.fromisoformat(m.group("date"))
            return m.group("team").strip(), d
        except ValueError:
            pass
    m = _VS_RE.match(q)
    if m and end_date is not None:
        # No date in the question -- fall back to the market's end_date.
        return m.group("team").strip(), end_date
    return None


# Tokens that appear in club names but carry no identifying information --
# stripped from both query and candidate before comparison so "FC Bayern
# München" and "Bayern Munich" don't disagree on FC.
_NAME_NOISE = frozenset({
    # Generic club prefixes/suffixes across leagues
    "fc", "cf", "sc", "ac", "as", "afc", "us", "ssc", "club", "the",
    # German club prefixes
    "vfl", "vfb", "bv", "bsc", "tsg", "rb", "fsv", "spvgg", "vfr",
    # Italian / Spanish suffixes
    "calcio", "deportivo",
})

# Translation map for known city / club name discrepancies between
# Polymarket questions (typically the local-language official name) and
# ESPN scoreboards (typically the English transliteration). Tokens are
# normalised to a canonical form on both sides before comparison.
# Add to this list when a market silently fails to match.
_NAME_ALIASES: dict[str, str] = {
    # German -> English
    "münchen": "munich",
    "köln": "cologne",
    "wien": "vienna",
    "nürnberg": "nuremberg",
    # Italian -> English / shortened ESPN form
    "internazionale": "inter",
    "milano": "milan",
    "roma": "roma",
    "torino": "torino",
    # Spanish -> ASCII
    "atlético": "atletico",
    "atlético-madrid": "atletico",
    # English -> standard
    "manchester-united": "manchester-united",
}


def _canonical_team_tokens(raw: str) -> set[str]:
    """Lowercase, strip punctuation, drop noise tokens, apply aliases.

    Returns a set of identifying tokens. The intent is that
      "FC Bayern München"  -> {"bayern", "munich"}
      "Bayern Munich"      -> {"bayern", "munich"}
    so they Jaccard-match perfectly. Removes the prior failure mode where
    German/English city names and noise prefixes ("FC", "VfL", "Calcio")
    diluted the Jaccard score below the 0.4 confidence floor.
    """
    s = raw.strip().lower()
    # \w in Python 3 is Unicode-aware, so 'ü' is preserved as a word char.
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    out: set[str] = set()
    for t in s.split():
        if t in _NAME_NOISE:
            continue
        out.add(_NAME_ALIASES.get(t, t))
    return out


def _team_match_score(query: str, candidate: str) -> float:
    """Token-Jaccard similarity with subset bonus. 0..1, higher is better.

    Subset bonus rewards "Cagliari" ⊂ "Cagliari Calcio" style matches, and
    after _NAME_NOISE / _NAME_ALIASES normalisation also handles
    "FC Bayern München" -> {bayern, munich} == "Bayern Munich" cleanly.
    """
    q = _canonical_team_tokens(query)
    c = _canonical_team_tokens(candidate)
    if not q or not c:
        return 0.0
    inter = q & c
    if not inter:
        return 0.0
    union = q | c
    base = len(inter) / len(union)
    if q.issubset(c) or c.issubset(q):
        base += 0.25
    return min(base, 1.0)


# --- ESPN provider ---------------------------------------------------------

async def _fetch_scoreboard(
    client: httpx.AsyncClient, sport: str, league: str, on_date: date,
) -> list[dict[str, Any]]:
    url = f"{_ESPN_BASE}/{sport}/{league}/scoreboard"
    params = {"dates": on_date.strftime("%Y%m%d")}
    try:
        r = await client.get(url, params=params, timeout=8.0)
        r.raise_for_status()
        data = r.json()
        return list(data.get("events") or [])
    except (httpx.HTTPError, ValueError) as e:
        log.debug("ESPN fetch failed for %s/%s on %s: %s", sport, league, on_date, e)
        return []


def _event_to_fixture_ref(
    sport: str, league: str, ev: dict[str, Any],
) -> FixtureRef | None:
    try:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home = next(
            (c for c in competitors if c.get("homeAway") == "home"), None,
        )
        away = next(
            (c for c in competitors if c.get("homeAway") == "away"), None,
        )
        if not home or not away:
            return None
        home_name = (home.get("team") or {}).get("displayName") or ""
        away_name = (away.get("team") or {}).get("displayName") or ""
        kickoff = ev.get("date")
        kickoff_dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00")) if kickoff else None
        if kickoff_dt is None:
            return None
        return FixtureRef(
            sport=sport, league=league,
            fixture_id=str(ev.get("id")),
            kickoff_at=kickoff_dt,
            home_team=home_name,
            away_team=away_name,
        )
    except (TypeError, ValueError, AttributeError):
        return None


def _event_to_fixture_status(
    ref: FixtureRef, ev: dict[str, Any],
) -> FixtureStatus | None:
    try:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        status = ev.get("status") or {}
        st_type = status.get("type") or {}
        raw_state = (st_type.get("state") or "").lower()
        short_detail = st_type.get("shortDetail")
        period = status.get("period")
        display_clock = status.get("displayClock")

        # Map ESPN's coarse state to our finer states. HT detection is
        # imperfect on ESPN scoreboard; rely on shortDetail string when
        # present (it tends to literally say "HT" / "Halftime").
        state = raw_state if raw_state in ("pre", "in", "post") else "unknown"
        if state == "in" and isinstance(short_detail, str) and (
            "ht" in short_detail.lower() or "half" in short_detail.lower()
        ):
            state = "ht"

        # current_minute -- soccer-specific. ESPN puts the minute in
        # displayClock (e.g. "43'") for live soccer matches. Best-effort parse.
        current_minute: int | None = None
        if ref.sport == "soccer" and state == "in" and display_clock:
            m = re.match(r"^(\d+)", display_clock)
            if m:
                try:
                    current_minute = int(m.group(1))
                except ValueError:
                    current_minute = None

        def _score(c: dict[str, Any] | None) -> int | None:
            if c is None:
                return None
            try:
                return int(c.get("score"))
            except (TypeError, ValueError):
                return None

        return FixtureStatus(
            sport=ref.sport, league=ref.league, fixture_id=ref.fixture_id,
            state=state,
            kickoff_at=ref.kickoff_at,
            home_team=ref.home_team, away_team=ref.away_team,
            home_score=_score(home),
            away_score=_score(away),
            current_minute=current_minute,
            period=period,
            display_clock=display_clock,
            short_detail=short_detail,
        )
    except (TypeError, ValueError, AttributeError):
        return None


# --- Public API ------------------------------------------------------------

async def lookup_live_status_for_market(
    *,
    condition_id: str,
    market_question: str,
    market_category: str | None,
    end_date: date | None,
) -> FixtureStatus | None:
    """Returns FixtureStatus for the underlying real-world fixture, or None.

    Two-phase: (1) fixture-mapping cached 24h, (2) live-status cached 60s.
    Returns None for non-sports markets, unparseable questions, or any lookup
    failure -- caller is expected to silently omit the chip on None.

    Network calls are best-effort; this function never raises out.
    """
    if (market_category or "").lower() != "sports":
        return None

    # Phase 1: get-or-build the fixture mapping for this market.
    now = time.monotonic()
    cached = _fixture_map_cache.get(condition_id)
    if cached is not None:
        exp, ref = cached
        if exp > now:
            if ref is None:
                return None  # known-no-match; honour the negative cache
        else:
            ref = None
            cached = None
    if cached is None:
        ref = await _resolve_fixture(market_question, end_date)
        _fixture_map_cache[condition_id] = (now + _FIXTURE_MAP_TTL, ref)
        if ref is None:
            return None

    # Phase 2: get-or-fetch live status.
    live_cached = _live_status_cache.get(ref.fixture_id)
    if live_cached is not None and live_cached[0] > now:
        return live_cached[1]
    status = await _fetch_live_status(ref)
    _live_status_cache[ref.fixture_id] = (now + _LIVE_STATUS_TTL, status)
    return status


async def _resolve_fixture(
    market_question: str, end_date: date | None,
) -> FixtureRef | None:
    parsed = _parse_team_and_date(market_question, end_date)
    if parsed is None:
        return None
    team_query, fixture_date = parsed

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Try soccer leagues first (our primary use-case based on the user's
        # current sports signals). Fan out concurrently to amortise latency.
        coros = []
        for lg in _SOCCER_LEAGUES:
            coros.append(_fetch_scoreboard(client, "soccer", lg, fixture_date))
        for sport, lg in _OTHER_LEAGUES:
            coros.append(_fetch_scoreboard(client, sport, lg, fixture_date))

        results = await asyncio.gather(*coros, return_exceptions=True)

    # Walk every event from every league looking for the best team-name match.
    # Threshold tuned to require both a token overlap AND not be ambiguous
    # ("the" matches everything otherwise).
    best_ref: FixtureRef | None = None
    best_score = 0.0
    league_tags = (
        [("soccer", lg) for lg in _SOCCER_LEAGUES]
        + list(_OTHER_LEAGUES)
    )
    for events, (sport, lg) in zip(results, league_tags):
        if isinstance(events, Exception) or not events:
            continue
        for ev in events:
            ref = _event_to_fixture_ref(sport, lg, ev)
            if ref is None:
                continue
            score_h = _team_match_score(team_query, ref.home_team)
            score_a = _team_match_score(team_query, ref.away_team)
            score = max(score_h, score_a)
            if score > best_score:
                best_score = score
                best_ref = ref

    # Confidence floor -- below this and we'd rather no chip than a wrong one.
    if best_score < 0.4:
        return None
    return best_ref


async def _fetch_live_status(ref: FixtureRef) -> FixtureStatus | None:
    """Re-fetch the day's scoreboard for the same league and re-find our
    fixture by id. Cheaper than ESPN's per-event endpoint and same data.
    Uses ref.kickoff_at.date() in case the match has rolled over a UTC day.
    """
    on_date = ref.kickoff_at.astimezone(timezone.utc).date()
    async with httpx.AsyncClient(timeout=8.0) as client:
        events = await _fetch_scoreboard(client, ref.sport, ref.league, on_date)
    for ev in events:
        if str(ev.get("id")) == ref.fixture_id:
            return _event_to_fixture_status(ref, ev)
    return None
