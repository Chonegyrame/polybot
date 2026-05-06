"""Sync active Polymarket events and markets into our DB.

The gamma `/events` endpoint returns events with their child markets embedded.
Categories live in the `tags` array (the legacy top-level `category` field is
null for active events), so we derive each event's leaderboard-canonical
category via _derive_category from the tags slugs.

Incremental sync: on every run after the first, we only fetch events that
have been touched on Polymarket since our last successful sync. We page
events newest-first by `updatedAt` and stop the moment we hit one older than
our cutoff. The first run does a full pull (no cutoff); subsequent runs
typically touch only a handful of pages.

Run this before / alongside position refreshes so any condition_id we
encounter in /positions has a corresponding row in `markets` (the FK target).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.db import crud
from app.db.connection import init_pool
from app.services.polymarket import PolymarketClient
from app.services.polymarket_types import Event, Market, pair_yes_no_tokens

log = logging.getLogger(__name__)


# Map gamma tag-slugs (what events actually carry) to our leaderboard category
# names (the only values the leaderboard API supports). The legacy `category`
# field on events is null for active events — the real taxonomy lives in tags.
# Order matters: if an event is tagged both 'politics' and 'sports', the first
# match wins.
_TAG_SLUG_TO_LEADERBOARD_CATEGORY: tuple[tuple[str, str], ...] = (
    ("politics", "politics"),
    ("sports", "sports"),
    ("crypto", "crypto"),
    ("finance", "finance"),
    ("tech", "tech"),
    ("pop-culture", "culture"),
    ("culture", "culture"),
)


def _derive_category(tags: list[dict[str, object]] | None, fallback: str | None) -> str | None:
    """Pick the leaderboard-canonical category from an event's tags.

    Returns None if no tag matches any of our 7 categories — these events end
    up only in 'Overall' (which doesn't filter by category).
    """
    if fallback:  # legacy `category` field still wins if present
        return fallback
    if not tags:
        return None
    have_slugs = {str(t.get("slug") or "").lower() for t in tags if isinstance(t, dict)}
    for tag_slug, canonical in _TAG_SLUG_TO_LEADERBOARD_CATEGORY:
        if tag_slug in have_slugs:
            return canonical
    return None


@dataclass
class MarketSyncResult:
    events_seen: int
    markets_seen: int
    duration_seconds: float
    full_sync: bool             # True iff there was no prior cutoff (first run)
    stopped_at_cutoff: bool     # True if we ended early because we hit a known event


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # gamma sometimes uses 'Z' suffix; fromisoformat doesn't accept it before py3.11
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _persist_event_and_markets(
    conn: asyncpg.Connection, event: Event
) -> int:
    derived_category = _derive_category(event.tags, fallback=event.category)
    await crud.upsert_event(
        conn,
        event_id=event.id,
        slug=event.slug,
        title=event.title,
        category=derived_category,
        tags=event.tags,
        end_date=_parse_iso(event.end_date),
        closed=event.closed,
    )
    written = 0
    for m in event.markets:
        if not m.condition_id:
            continue  # skip pre-deploy / draft markets
        # F6: pair YES/NO tokens by outcome label, not by array index — some
        # markets ship outcomes in [No, Yes] order, which the index-based
        # approach silently maps to the wrong tokens.
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
        written += 1
    return written


def _coerce_outcome_label(outcome: object) -> str | None:
    """Normalize an outcome entry to a lowercase string, robust to gamma quirks.

    Polymarket sometimes returns outcomes as plain strings (`"Yes"`) and
    sometimes as embedded dicts (`{"outcome": "Yes"}` or `{"label": "Yes"}`).
    We accept either form and return a stripped lowercase label, or None if
    we can't extract one.
    """
    if outcome is None:
        return None
    if isinstance(outcome, str):
        return outcome.strip().lower() or None
    if isinstance(outcome, dict):
        for key in ("outcome", "label", "name"):
            v = outcome.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    return None


def _coerce_price(price: object) -> float | None:
    """Coerce a price entry (float or numeric string) to a float, or None."""
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _infer_resolved_outcome(m: Market) -> str | None:
    """Best-effort guess at resolution from outcome prices.

    Returns:
      - "YES" / "NO" — one outcome resolved at $1.00
      - "50_50" — oracle resolved Invalid (both outcomes at $0.50)
      - "VOID" — market closed without outcome prices (cancelled/purged)
      - None — market not closed, or shape unrecognized
    """
    if not m.closed:
        return None

    # VOID case — market closed but no/empty outcome_prices (cancelled,
    # voided, or gamma stripped the prices after archival). Without this,
    # such markets stayed `resolved_outcome=NULL` forever and silently
    # excluded from backtest, biasing toward easy markets.
    if not m.outcome_prices or not m.outcomes:
        return "VOID"
    if len(m.outcomes) != len(m.outcome_prices):
        # Shape mismatch — log once at caller, treat as VOID
        return "VOID"

    # Coerce each (outcome, price) pair into normalized form
    pairs: list[tuple[str | None, float | None]] = [
        (_coerce_outcome_label(o), _coerce_price(p))
        for o, p in zip(m.outcomes, m.outcome_prices)
    ]
    if any(p is None for _, p in pairs):
        log.warning(
            "_infer_resolved_outcome: non-numeric price for %s — outcomes=%s prices=%s",
            m.condition_id, m.outcomes, m.outcome_prices,
        )
        return "VOID"

    # YES/NO winner: one side at 1.0
    for label, price in pairs:
        if price is not None and abs(price - 1.0) < 1e-3:
            if label == "yes":
                return "YES"
            if label == "no":
                return "NO"
            # F15: custom-label resolution (e.g. "Trump wins" / "Biden wins").
            # Returning None silently excluded these from backtest, biasing
            # category edge estimates toward vanilla Yes/No markets (politics
            # and sports are over-represented in custom-label markets). We
            # still can't honestly assign YES/NO without knowing which side
            # our paper-trade was on (would need a position-data lookup —
            # deferred to V2). For now, log loudly so the operator sees the
            # magnitude of what's being excluded; treat as VOID so the
            # market shows up in the resolved set rather than vanishing.
            log.warning(
                "F15: custom-label binary resolution for cid=%s — "
                "outcomes=%s prices=%s — winner='%s' but no yes/no mapping. "
                "Marking VOID; backtest will skip. Re-evaluate in V2 with "
                "position-data lookup.",
                m.condition_id, m.outcomes, m.outcome_prices, label,
            )
            return "VOID"

    # 50/50 case — both outcomes near 0.5 (oracle "Invalid" resolution).
    # Polymarket pays $0.50 to BOTH sides in this case.
    if len(pairs) == 2:
        p1 = pairs[0][1] or 0.0
        p2 = pairs[1][1] or 0.0
        if abs(p1 - 0.5) < 0.05 and abs(p2 - 0.5) < 0.05:
            return "50_50"

    # All zeros — closed but no winner declared (rare; treat as VOID)
    if all((p or 0.0) < 1e-3 for _, p in pairs):
        return "VOID"

    return None


async def discover_and_persist_markets(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    condition_ids: set[str],
) -> int:
    """Just-in-time market discovery.

    For each condition_id we don't already have, fetch the gamma market and
    its parent event (which carries the tags we use to derive the canonical
    category). Persist event then market. Returns the number of new markets
    actually written.
    """
    if not condition_ids:
        return 0

    rows = await conn.fetch(
        "SELECT condition_id FROM markets WHERE condition_id = ANY($1::TEXT[])",
        list(condition_ids),
    )
    have = {r["condition_id"] for r in rows}
    missing = sorted(condition_ids - have)
    if not missing:
        return 0

    log.info("discovering %d new markets via gamma...", len(missing))
    fetched = await pm.get_markets_by_condition_ids(missing)
    fetched_cids = {m.condition_id for m in fetched if m.condition_id}

    # A29: partial-result detection + retry. Gamma's default `/markets` query
    # filters out closed markets — anything missing from the active fetch is
    # most likely resolved/archived. Do one follow-up `closed=true` sweep so
    # the markets table also contains the closed metadata (used by paper-trade
    # auto-close in A28 and by backtest resolution lookup). Anything still
    # missing after BOTH sweeps is a real gap (gamma genuinely doesn't know
    # the cid, or transient drop) — log so it isn't silent.
    still_missing_after_active = [c for c in missing if c not in fetched_cids]
    fetched_closed: list[Market] = []
    if still_missing_after_active:
        try:
            fetched_closed = await pm.get_markets_by_condition_ids(
                still_missing_after_active, closed=True
            )
        except Exception as e:  # noqa: BLE001
            log.warning("closed=true retry failed for %d cids: %r",
                        len(still_missing_after_active), e)
            fetched_closed = []
        closed_cids = {m.condition_id for m in fetched_closed if m.condition_id}
        unrecovered = [c for c in still_missing_after_active if c not in closed_cids]
        log.info(
            "discovery breakdown: requested=%d, active=%d, closed=%d, unrecovered=%d",
            len(missing), len(fetched_cids), len(closed_cids), len(unrecovered),
        )
        if unrecovered:
            log.warning(
                "%d cids unrecovered after active+closed sweeps — gamma genuinely doesn't know them",
                len(unrecovered),
            )

    fetched = fetched + fetched_closed
    if not fetched:
        log.warning(
            "discovery returned zero markets for %d condition_ids — gamma silent on all",
            len(missing),
        )
        return 0

    # Markets carry an embedded `events` array, but those embedded events lack
    # `tags` — so we need a separate /events?id=... fetch to get the category.
    event_ids: set[str] = set()
    market_to_event: dict[str, str] = {}
    for m in fetched:
        ev_list = m.raw.get("events") or []
        if ev_list and isinstance(ev_list[0], dict):
            eid = str(ev_list[0].get("id") or "")
            if eid:
                event_ids.add(eid)
                market_to_event[m.condition_id] = eid

    full_events = await pm.get_events_by_ids(sorted(event_ids))
    events_by_id = {e.id: e for e in full_events}
    log.info(
        "discovery: requested %d distinct events, got %d back from gamma",
        len(event_ids), len(events_by_id),
    )
    # F26: surface the SPECIFIC event_ids gamma dropped so we can investigate.
    # Markets whose parent event refetch glitched get persisted with
    # category=NULL, silently missing from category-filtered signal lenses.
    # Pre-fix this only showed as the aggregate count above; you couldn't tell
    # which events to retry / investigate.
    missing_event_ids = sorted(event_ids - set(events_by_id.keys()))
    if missing_event_ids:
        affected_cids = sorted({
            cid for cid, eid in market_to_event.items()
            if eid in missing_event_ids
        })
        log.warning(
            "F26: gamma dropped %d event(s) from refetch; %d market(s) will "
            "have NULL category (only visible in 'Overall' lens). "
            "Missing event_ids: %s. Affected market cids (first 5): %s",
            len(missing_event_ids), len(affected_cids),
            missing_event_ids[:10],
            affected_cids[:5],
        )

    written_markets = 0
    async with conn.transaction():
        # Upsert all events first (FK targets for markets)
        for ev in full_events:
            await crud.upsert_event(
                conn,
                event_id=ev.id,
                slug=ev.slug,
                title=ev.title,
                category=_derive_category(ev.tags, fallback=ev.category),
                tags=ev.tags,
                end_date=_parse_iso(ev.end_date),
                closed=ev.closed,
            )
        # Then markets — link to event_id only if we actually persisted that
        # event. Otherwise leave NULL (no FK violation, just no category).
        for m in fetched:
            if not m.condition_id:
                continue
            # F6: see comment in upsert_event_with_markets for why we pair
            # by outcome label rather than by index.
            clob_yes, clob_no = pair_yes_no_tokens(m.outcomes, m.clob_token_ids)
            mapped_event_id = market_to_event.get(m.condition_id)
            event_id = mapped_event_id if mapped_event_id in events_by_id else None
            await crud.upsert_market(
                conn,
                condition_id=m.condition_id,
                gamma_id=m.id,
                event_id=event_id,
                slug=m.slug,
                question=m.question,
                clob_token_yes=clob_yes,
                clob_token_no=clob_no,
                outcomes=m.outcomes if m.outcomes else None,
                end_date=_parse_iso(m.end_date),
                closed=m.closed,
                resolved_outcome=_infer_resolved_outcome(m),
            )
            written_markets += 1

    log.info(
        "discovery wrote %d new markets / %d new events",
        written_markets, len(full_events),
    )
    return written_markets


async def _get_cutoff(conn: asyncpg.Connection) -> datetime | None:
    """Return our high-water mark — the most recent `last_synced_at` from events.

    Used as the lower bound on the next sync: any event with
    `updatedAt > cutoff` is fresh and must be re-fetched. Anything older than
    cutoff was already current at our last sync. NULL (None) on first run.
    """
    row = await conn.fetchrow("SELECT MAX(last_synced_at) AS m FROM events")
    return row["m"] if row and row["m"] else None


async def sync_active_markets(
    max_pages: int = 50,
    force_full: bool = False,
) -> MarketSyncResult:
    """Incremental sync. Pages newest-first by updatedAt, stops at the cutoff.

    First run (no events in DB): does a full pull (subject to `max_pages`).
    Subsequent runs: stops as soon as we encounter an event whose `updatedAt`
    is older than our most recent `last_synced_at`. Typically just a few
    pages of work.

    Set `force_full=True` to override the cutoff and re-sync everything.
    """
    started = datetime.now(timezone.utc)
    pool = await init_pool(min_size=1, max_size=4)
    events_seen = 0
    markets_seen = 0
    stopped_early = False

    async with pool.acquire() as conn:
        cutoff = None if force_full else await _get_cutoff(conn)

    full_sync = cutoff is None
    log.info(
        "=== sync_active_markets starting (cutoff=%s, mode=%s) ===",
        cutoff.isoformat() if cutoff else "<none — full sync>",
        "full" if full_sync else "incremental",
    )

    async with PolymarketClient() as pm:
        async with pool.acquire() as conn:
            async for event in pm.iter_events(
                page_size=100,
                closed=False,
                max_pages=max_pages,
                order="updatedAt",
                ascending=False,
            ):
                # Early stop: when we hit an event that hasn't been touched
                # since our last sync, neither will any subsequent ones.
                if cutoff is not None and event.updated_at:
                    ev_updated = _parse_iso(event.updated_at)
                    if ev_updated and ev_updated <= cutoff:
                        log.info(
                            "  stopping early — reached cutoff (event updatedAt=%s <= %s)",
                            ev_updated.isoformat(), cutoff.isoformat(),
                        )
                        stopped_early = True
                        break

                events_seen += 1
                async with conn.transaction():
                    n = await _persist_event_and_markets(conn, event)
                markets_seen += n
                if events_seen % 100 == 0:
                    log.info("  ...%d events / %d markets so far",
                             events_seen, markets_seen)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "=== done in %.1fs — %d events written, %d markets, stopped_early=%s ===",
        duration, events_seen, markets_seen, stopped_early,
    )
    return MarketSyncResult(
        events_seen=events_seen,
        markets_seen=markets_seen,
        duration_seconds=duration,
        full_sync=full_sync,
        stopped_at_cutoff=stopped_early,
    )
