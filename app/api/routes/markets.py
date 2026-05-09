"""GET /markets/{condition_id} — enriched single-market view for drill-down."""

from __future__ import annotations

import time
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.db import crud
from app.services.orderbook import compute_book_metrics
from app.services.polymarket import PolymarketClient
from app.services import sports_meta

router = APIRouter(prefix="/markets", tags=["markets"])

# 5-min cache for the trending feed -- gamma-api's /events?order=volume is
# stable enough on that timescale and this caps Polymarket load even if a
# user spam-flips between Tracked and Trending.
_TRENDING_TTL = 300
_trending_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


_VALID_BROWSE_SORT = {"smart_money", "trader_count", "current_price", "end_date", "alpha"}
_VALID_BROWSE_STATUS = {"active", "resolved", "all"}


@router.get("/browse")
async def browse_markets(
    search: str | None = Query(None, max_length=200),
    category: str | None = Query(None),
    status: str = Query("all"),
    sort: str = Query("smart_money"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Markets browser -- every market in our local DB joined with smart-money
    aggregation. Powers the "Tracked" view of the Markets tab.

    Pre-filtered to markets any tracked wallet (top-N pool + insiders) has
    touched. Each row includes per-side trader_count + aggregate USDC so the
    UI can render "5 YES · $200k / 2 NO · $50k" inline.

    Sort options:
      - smart_money   (default) -- total USDC held by tracked pool, descending
      - trader_count  -- distinct cluster-collapsed entities holding, descending
      - current_price -- last observed price, descending
      - end_date      -- soonest-closing first
      - alpha         -- alphabetical
    """
    if sort not in _VALID_BROWSE_SORT:
        raise HTTPException(400, f"sort must be one of {sorted(_VALID_BROWSE_SORT)}")
    if status not in _VALID_BROWSE_STATUS:
        raise HTTPException(400, f"status must be one of {sorted(_VALID_BROWSE_STATUS)}")

    rows, total = await crud.list_browseable_markets(
        conn,
        search=search.strip() if search else None,
        category=category,
        status=status,
        sort=sort,
        limit=limit,
        offset=offset,
    )

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "condition_id": r["condition_id"],
            "question": r["question"],
            "slug": r["slug"],
            "event_id": r["event_id"],
            "event_title": r["event_title"],
            "category": r["category"],
            "end_date": r["end_date"].isoformat() if r["end_date"] is not None else None,
            "closed": r["closed"],
            "resolved_outcome": r["resolved_outcome"],
            "current_price": r["current_price"],
            "smart_money": {
                "trader_count": r["smart_money_trader_count"],
                "total_usdc": r["smart_money_total_usdc"],
                "yes_traders": r["smart_money_yes_traders"],
                "no_traders": r["smart_money_no_traders"],
                "yes_usdc": r["smart_money_yes_usdc"],
                "no_usdc": r["smart_money_no_usdc"],
            },
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": out,
    }


@router.get("/trending")
async def trending_markets(
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Top events by 24h volume from gamma-api, with a TRACKED flag for any
    event we already have in our local DB.

    Powers the "Trending" view of the Markets tab. Caches the gamma response
    for 5 minutes per category so the user can flip back and forth without
    re-hitting gamma. Each row identifies the event's primary (first) market
    via condition_id so click-through opens the existing MarketView modal.
    """
    # Polymarket's category filter: gamma returns `category: null` on most
    # high-volume events. The reliable signal is the `tags` array (each
    # event tagged with one or more slugs like 'politics', 'sports',
    # 'crypto', 'culture', 'pop-culture', 'business', 'tech', 'esports',
    # 'weather'). We match the user's `category` against ANY tag.slug,
    # with a small alias map for our normalized names vs gamma's slugs.
    cat_aliases: dict[str, set[str]] = {
        "politics": {"politics", "elections", "geopolitics"},
        "sports": {"sports", "soccer", "football", "basketball", "nba", "nfl", "mlb", "nhl"},
        "crypto": {"crypto", "bitcoin", "ethereum"},
        "culture": {"culture", "pop-culture", "entertainment", "music", "movies"},
        "tech": {"tech", "ai", "technology"},
        "finance": {"finance", "business", "economics", "markets"},
    }
    wanted_tags = cat_aliases.get((category or "").lower(), set())
    if category and not wanted_tags:
        # Unknown category passed -- fall back to literal match on the slug.
        wanted_tags = {category.lower()}

    # Fetch a bigger pool when filtering so we don't end up with 0 rows just
    # because the global top-50 happened to skip the requested category.
    fetch_limit = 200 if category else limit

    cache_key = f"{category or '_all'}::{limit}"
    now = time.monotonic()
    cached = _trending_cache.get(cache_key)
    events_data: list[dict[str, Any]]
    if cached is not None and cached[0] > now:
        events_data = cached[1]
    else:
        async with PolymarketClient() as pm:
            evs = await pm.get_events(
                limit=fetch_limit, closed=False, order="volume", ascending=False,
            )
        events_data = []
        for e in evs:
            # Skip events with no markets -- nothing to click through to.
            if not e.markets:
                continue
            event_tags = {
                str(t.get("slug", "")).lower()
                for t in (e.tags or [])
                if isinstance(t, dict) and t.get("slug")
            }
            # Category filter -- match against any of the wanted tag slugs,
            # OR the literal category field if it's populated.
            if category:
                cat_match = (
                    bool(event_tags & wanted_tags)
                    or (e.category or "").lower() in wanted_tags
                )
                if not cat_match:
                    continue
            primary = e.markets[0]
            # Event-level volume = sum across all child markets. Gamma sorts
            # by this total, so displaying only the primary's volume_num made
            # the visible numbers look out of order (a 128-outcome event has
            # huge total volume but tiny per-child volume).
            event_volume = sum(
                (m.volume_num or 0.0) for m in e.markets
            )
            event_liquidity = sum(
                (m.liquidity_num or 0.0) for m in e.markets
            )
            # Full outcomes list for multi-market events. The frontend uses
            # this to render an outcome picker when n_markets > 1 -- you
            # bet on a SPECIFIC market (e.g. "Will Spain win?"), not on the
            # event abstractly. Sorted by volume desc so the most-traded
            # outcome is at the top.
            #
            # Filter out closed/inactive child markets: even if the parent
            # event is open (e.g. FIFA cup hasn't happened), individual
            # team-wins markets get formally resolved NO as teams are
            # eliminated. Showing dead $0.00 markets you can't trade clutters
            # the picker. We use both `closed` and `active` to be defensive
            # since gamma sets these inconsistently.
            outcomes = []
            for m in e.markets:
                if m.closed or not m.active:
                    continue
                outcomes.append({
                    "condition_id": m.condition_id,
                    "question": m.question,
                    "current_price": m.last_trade_price,
                    "volume_num": m.volume_num,
                    "liquidity_num": m.liquidity_num,
                    "best_bid": m.best_bid,
                    "best_ask": m.best_ask,
                })
            outcomes.sort(key=lambda o: o.get("volume_num") or 0, reverse=True)
            if not outcomes:
                # All children resolved/closed -- nothing tradable left,
                # don't surface the event at all.
                continue
            # Re-pin "primary" to the highest-volume LIVE outcome so the
            # row's click target is meaningful even if e.markets[0] was
            # closed. Same shape as before, just sourced from filtered list.
            primary = next(
                m for m in e.markets if m.condition_id == outcomes[0]["condition_id"]
            )
            events_data.append({
                "event_id": e.id,
                "event_title": e.title,
                "category": e.category,
                "tags": sorted(event_tags),
                "end_date": e.end_date,
                "primary_condition_id": primary.condition_id,
                "primary_question": primary.question,
                "volume_num": event_volume if event_volume > 0 else primary.volume_num,
                "liquidity_num": event_liquidity if event_liquidity > 0 else primary.liquidity_num,
                "n_markets": len(outcomes),
                "current_price": primary.last_trade_price,
                "outcomes": outcomes,
            })
            if len(events_data) >= limit:
                break
        _trending_cache[cache_key] = (now + _TRENDING_TTL, events_data)

    # Flag rows whose event_id we already have locally -- those have smart-
    # money data the user can drill into.
    event_ids = [r["event_id"] for r in events_data if r.get("event_id")]
    tracked_ids = await crud.list_event_ids_in_db(conn, event_ids)

    out: list[dict[str, Any]] = []
    for r in events_data:
        out.append({
            **r,
            "tracked": r.get("event_id") in tracked_ids,
        })

    return {
        "category": category,
        "count": len(out),
        "rows": out,
    }


@router.get("/{condition_id}")
async def get_market(
    condition_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Single market with event context, all tracked positions, and signal history.

    F23: SQL queries refactored into crud.py helpers (CLAUDE.md rule).
    Behavior unchanged.
    """
    market = await crud.get_market_with_event(conn, condition_id)
    if market is None:
        raise HTTPException(404, f"market {condition_id} not found")

    positions_summary = await crud.get_market_positions_summary(conn, condition_id)
    per_trader = await crud.get_market_per_trader(conn, condition_id)
    signals = await crud.get_market_signal_history(conn, condition_id)

    return {
        "market": market,
        "tracked_positions_by_outcome": positions_summary,
        "tracked_positions_per_trader": per_trader,
        "signal_history": signals,
    }


@router.get("/{condition_id}/live_quote")
async def get_live_quote(
    condition_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Live CLOB best bid + best ask for both YES and NO sides of the market.

    The market modal calls this on open + every 30s to render real prices on
    both sides — without this, the user sees only stale `current_price` from
    the position-refresh job (up to 10 min old) and only on the side smart
    money is trading. Returns nulls per-side if that token's book is empty
    or crossed; the caller renders "—" for any null.
    """
    yes_token, no_token = await crud.get_market_clob_tokens(conn, condition_id)
    if yes_token is None and no_token is None:
        raise HTTPException(404, f"market {condition_id} has no CLOB tokens")

    async with PolymarketClient() as pm:
        yes_book = await pm.get_orderbook(yes_token) if yes_token else None
        no_book = await pm.get_orderbook(no_token) if no_token else None

    yes_m = compute_book_metrics(yes_book, "YES")
    no_m = compute_book_metrics(no_book, "NO")

    def _side(m) -> dict[str, float | int | None]:
        if not m.available:
            return {"bid": None, "ask": None, "mid": None, "spread_bps": None}
        return {
            "bid": m.best_bid,
            "ask": m.best_ask,
            "mid": m.mid,
            "spread_bps": m.spread_bps,
        }

    return {
        "condition_id": condition_id,
        "yes": _side(yes_m),
        "no": _side(no_m),
    }


@router.get("/{condition_id}/live_status")
async def get_live_status(
    condition_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Live sports fixture status for the SignalCard chip.

    Looks up the underlying real-world fixture (ESPN scoreboard) for sports
    markets and returns kickoff time / live minute / score / FT etc.

    Returns 404 with a structured `detail` (not a raised error) for any
    market the lookup couldn't match -- non-sports, unparseable question,
    fixture not in any of our covered leagues. Caller is expected to
    silently omit the chip in that case.

    Cached per-fixture for 60s; per-market mapping cached 24h. Both caches
    are in-process module dicts so no Redis dependency.
    """
    market = await crud.get_market_with_event(conn, condition_id)
    if market is None:
        raise HTTPException(404, f"market {condition_id} not found")

    # market here is a dict; pull what sports_meta needs.
    end_date = market.get("end_date")
    if end_date is not None and hasattr(end_date, "date"):
        end_date = end_date.date()

    status = await sports_meta.lookup_live_status_for_market(
        condition_id=condition_id,
        market_question=market.get("question") or "",
        market_category=market.get("event_category") or market.get("category"),
        end_date=end_date,
    )
    if status is None:
        raise HTTPException(404, "no fixture matched for this market")

    return {
        "condition_id": condition_id,
        "sport": status.sport,
        "league": status.league,
        "fixture_id": status.fixture_id,
        "state": status.state,
        "kickoff_at": status.kickoff_at.isoformat(),
        "home_team": status.home_team,
        "away_team": status.away_team,
        "home_score": status.home_score,
        "away_score": status.away_score,
        "current_minute": status.current_minute,
        "period": status.period,
        "display_clock": status.display_clock,
        "short_detail": status.short_detail,
    }
