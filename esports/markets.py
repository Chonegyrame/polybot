"""Refresh the esports market universe (the tag-based detection source).

Sweeps OPEN LoL/CS events via gamma (each event bundles ALL its sub-markets —
winner, game-handicap, games-total, total-kills, props — and carries the
`league-of-legends` / `counter-strike` tag), plus recently-closed events so a
trade on a just-resolved market still resolves. Writes (condition_id, game,
market_type) into `esports_markets`, which the tracker checks for membership.

This is what lets the tracker catch HANDICAP / TOTAL / PROP markets whose title
omits the game name — the old title-keyword check silently dropped them.

Only OPEN markets can receive NEW trades, so the open sweep is exactly the set
forward-detection needs; recent-closed is a small safety margin.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.services.polymarket import PolymarketClient

SECTOR_TAGS = {
    "lol": ["league-of-legends"],
    "cs": ["counter-strike", "counter-strike-2", "cs2", "csgo"],
}

# Recently-closed lookback so a fill on a market that resolved minutes ago is
# still classified (the tracker polls recent trades, which can include these).
_CLOSED_LOOKBACK_DAYS = 3


def classify_market_type(title: str | None) -> str:
    """Bucket a market title into a user-facing type for filtering."""
    t = (title or "").lower()
    if "handicap" in t:
        return "handicap"
    if "total" in t or "over/under" in t or "o/u" in t:
        return "total"
    # straight winners: series ("(BO5)"), game/map winner ("Game N Winner")
    if "winner" in t or re.search(r"\(bo\d\)", t):
        return "winner"
    # everything else under an esports event is a side/prop market
    # (first blood, baron, dragon, penta, odd/even, etc.)
    return "prop"


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def refresh_esports_markets(pm: PolymarketClient, conn) -> tuple[int, int]:
    """Sweep open + recently-closed esports events; upsert their markets.

    Returns (markets_upserted, events_seen).
    """
    from esports import db

    cutoff = datetime.now(timezone.utc) - timedelta(days=_CLOSED_LOOKBACK_DAYS)
    rows: dict[str, dict] = {}
    events_seen = 0

    for game, tags in SECTOR_TAGS.items():
        for tag in tags:
            # open events (the live universe)
            offset = 0
            while True:
                page = await pm.get_events(limit=100, offset=offset, closed=False, tag_slug=tag)
                if not page:
                    break
                events_seen += len(page)
                for ev in page:
                    ev_start = _parse_dt(ev.start_time)
                    start_epoch = ev_start.timestamp() if ev_start else None
                    for m in ev.markets:
                        if m.condition_id:
                            rows[m.condition_id] = {
                                "condition_id": m.condition_id, "game": game,
                                "title": m.question or ev.title,
                                "market_type": classify_market_type(m.question),
                                "start_time": start_epoch,
                                "closed": 0,
                            }
                if len(page) < 100:
                    break
                offset += 100

            # recently-closed events (safety margin for just-resolved markets)
            offset = 0
            done = False
            while not done:
                page = await pm.get_events(limit=100, offset=offset, closed=True,
                                           tag_slug=tag, order="closedTime", ascending=False)
                if not page:
                    break
                for ev in page:
                    ct = _parse_dt(ev.raw.get("closedTime")) or _parse_dt(ev.end_date)
                    if ct is not None and ct < cutoff:
                        done = True
                        break
                    events_seen += 1
                    ev_start = _parse_dt(ev.start_time)
                    start_epoch = ev_start.timestamp() if ev_start else None
                    for m in ev.markets:
                        if m.condition_id and m.condition_id not in rows:
                            rows[m.condition_id] = {
                                "condition_id": m.condition_id, "game": game,
                                "title": m.question or ev.title,
                                "market_type": classify_market_type(m.question),
                                "start_time": start_epoch,
                                "closed": 1,
                            }
                if len(page) < 100:
                    break
                offset += 100

    n = db.replace_esports_markets(conn, list(rows.values())) if rows else 0
    return n, events_seen


def _infer_winner(m) -> str | None:
    """Winning outcome label from a settled market's one-hot outcome_prices."""
    if not (m.closed and m.outcomes and m.outcome_prices
            and len(m.outcomes) == len(m.outcome_prices)):
        return None
    try:
        idx = next(i for i, p in enumerate(m.outcome_prices) if p > 0.5)
        return m.outcomes[idx]
    except (StopIteration, IndexError):
        return None


async def refresh_active_resolutions(pm: PolymarketClient, conn) -> int:
    """Fast, cheap resolution check for ONLY the markets we have live sharp
    action in (a few dozen), so a finished game flips to 'done' within seconds
    instead of waiting on the 15-min universe sweep. Returns # newly resolved.
    """
    from esports import db

    cids = db.hot_condition_ids(conn)
    if not cids:
        return 0
    markets = await pm.get_markets_by_condition_ids(cids, closed=True)
    newly = 0
    for m in markets:
        winner = _infer_winner(m)
        if winner and m.condition_id and db.set_market_resolution(conn, m.condition_id, winner):
            newly += 1
    return newly
