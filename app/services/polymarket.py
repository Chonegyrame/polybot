"""Polymarket API client. ALL API calls go through this module — no exceptions.

Endpoints validated in spike/FINDINGS.md (2026-05-04). Async, rate-limited,
retried with exponential backoff. Returns typed objects from polymarket_types.
"""

from __future__ import annotations

import asyncio
import logging
import random
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
)
from tenacity.wait import wait_base

from app.config import settings
from app.services import health_counters
from app.services.rate_limiter import TokenBucket, get_bucket, host_for_url
from app.services.polymarket_types import (
    Event,
    LeaderboardEntry,
    Market,
    PortfolioValue,
    Position,
    PricePoint,
    Trade,
)

# Maps Position.drop_reason() string -> health_counters constant.
# Lookup is exhaustive: any reason returned by drop_reason() must have a
# counter, otherwise we'd silently lose attribution.
_ZOMBIE_DROP_COUNTERS: dict[str, str] = {
    "redeemable": health_counters.ZOMBIE_DROP_REDEEMABLE,
    "market_closed": health_counters.ZOMBIE_DROP_MARKET_CLOSED,
    "dust_size": health_counters.ZOMBIE_DROP_DUST_SIZE,
    "resolved_price_past": health_counters.ZOMBIE_DROP_RESOLVED_PRICE_PAST,
}

log = logging.getLogger(__name__)

# Validated against data-api.polymarket.com/v1/leaderboard (see spike/FINDINGS.md).
LeaderboardTimePeriod = Literal["day", "week", "month", "all"]
LeaderboardOrderBy = Literal["VOL", "PNL"]
LeaderboardCategory = Literal[
    "overall", "politics", "sports", "crypto", "culture", "tech", "finance"
]
LEADERBOARD_PAGE_SIZE = 50  # API silently caps `limit` at 50


def _should_retry(exc: BaseException) -> bool:
    """F14: Retry network-transient errors and 429/5xx only.

    Pre-fix retried every HTTPStatusError (including 400/401/403/404),
    burning rate-limit tokens on requests that would never succeed.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


# ---------------------------------------------------------------------------
# Pass 5 R17 — retry/backoff helpers
# ---------------------------------------------------------------------------

# Cap on Retry-After honoring. Pathological values (e.g. server returning
# Retry-After: 3600 by mistake) are clamped to this so we don't stall a
# whole cycle. Polymarket has not been observed sending Retry-After above
# a few seconds in practice.
RETRY_AFTER_CAP_SECONDS = 60.0

# Decorrelated jitter parameters (AWS Architecture Blog formula).
# sleep(n) = min(cap, uniform(base, sleep(n-1) * 3))
# Better p99 than wait_exponential and desynchronizes retries when many
# concurrent calls 429 at the same boundary.
_JITTER_BASE_SECONDS = 0.5
_JITTER_CAP_SECONDS = 8.0


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value to seconds.

    Supports both numeric ("5") and HTTP-date ("Wed, 21 Oct 2015 07:28:00 GMT")
    formats per RFC 7231. Returns None on parse failure or non-positive.
    Capped at RETRY_AFTER_CAP_SECONDS to avoid pathological values stalling
    a cycle.
    """
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        seconds = (target - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return None
    return min(seconds, RETRY_AFTER_CAP_SECONDS)


class _DecorrelatedJitterWait(wait_base):
    """tenacity wait strategy: decorrelated jitter (AWS-recommended).

    For attempt N: sleep = min(cap, uniform(base, prev * 3))
    where prev defaults to base on the first retry.

    Honors a Retry-After value passed via the exception's `_retry_after`
    attribute (set in `_get_json` when the server includes that header).
    The Retry-After path takes precedence — server knows when it'll have
    capacity, we should listen.
    """

    def __init__(
        self,
        base: float = _JITTER_BASE_SECONDS,
        cap: float = _JITTER_CAP_SECONDS,
    ) -> None:
        self.base = base
        self.cap = cap

    def __call__(self, retry_state: Any) -> float:
        # Server-suggested Retry-After takes precedence
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            retry_after = getattr(exc, "_retry_after", None)
            if retry_after is not None:
                return float(retry_after)
        # Fall back to decorrelated jitter
        prev = (
            retry_state.idle_for
            if retry_state.idle_for and retry_state.idle_for > 0
            else self.base
        )
        upper = max(self.base, prev * 3)
        return min(self.cap, random.uniform(self.base, upper))


class ResponseShapeError(Exception):
    """R15 (Pass 3): API returned a parseable JSON body but not in the
    expected list/wrapped-list shape. Distinct from "real empty result."

    Pre-fix `_safe_list_from_response` returned `[]` for both:
      - "API legitimately returned an empty list" (end of pagination)
      - "API returned a dict because of an error/overload, couldn't unwrap"

    Paginators (e.g. `get_leaderboard`) need to distinguish these — silent
    `break` on the second case caused leaderboard truncation when
    Polymarket had a hiccup. Now the helper raises this exception on the
    "couldn't parse" path, paginators catch + fail loudly, single-shot
    callers catch + return [] (preserves their previous behavior).
    """
    def __init__(self, endpoint: str, payload_preview: str):
        self.endpoint = endpoint
        self.payload_preview = payload_preview
        super().__init__(
            f"R15: {endpoint} returned unparseable shape — preview={payload_preview!r}"
        )


def _safe_list_from_response(
    data: Any,
    endpoint: str,
    list_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """F13 + R15: Coerce an API response to a list of dicts.

    Behavior:
      - Real list → return list of dicts (legit, silent)
      - Wrapped list ({"data": [...]}, etc.) → unwrap + return
      - Dict with no expected wrapper key → log WARN, raise ResponseShapeError
      - Anything else (None, str, int, etc.) → log WARN, raise ResponseShapeError

    Callers that don't care about distinguishing error-from-empty (most
    single-shot callers) should wrap in try/except ResponseShapeError and
    return []. Paginators MUST catch it and either retry or fail loudly,
    not treat as end-of-pages.

    Args:
      data: The parsed JSON body.
      endpoint: A short label for log context (e.g. "data-api/positions").
      list_keys: Dict-keys that may wrap a list response. Tried in order.

    Raises:
      ResponseShapeError: when the payload can't be coerced to a list.
    """
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in list_keys:
            inner = data.get(key)
            if isinstance(inner, list):
                return [d for d in inner if isinstance(d, dict)]
        preview = f"keys={sorted(data.keys())[:8]}"
        log.warning(
            "R15: %s returned dict instead of list (likely API error). %s",
            endpoint, preview,
        )
        raise ResponseShapeError(endpoint, preview)
    preview = f"type={type(data).__name__} body={str(data)[:120]!r}"
    log.warning("R15: %s returned %s instead of list.", endpoint, preview)
    raise ResponseShapeError(endpoint, preview)


def _safe_list_or_empty(
    data: Any,
    endpoint: str,
    list_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Wrapper around _safe_list_from_response for non-paginator callers
    that want the pre-R15 behavior of "treat unparseable as empty."

    The paginators (currently just `get_leaderboard`) call
    `_safe_list_from_response` directly so they can react to
    ResponseShapeError.
    """
    try:
        return _safe_list_from_response(data, endpoint, list_keys)
    except ResponseShapeError:
        return []


class PolymarketClient:
    """Async wrapper around Polymarket's four public APIs.

    Use as an async context manager:
        async with PolymarketClient() as pm:
            top = await pm.get_leaderboard("profit", "all")
    """

    def __init__(
        self,
        rate_limit_per_second: float | None = None,
        timeout: float | None = None,
    ) -> None:
        # Pass 5 R17: bucket lives in the module-level registry keyed by
        # host so concurrent PolymarketClient instances (12 call sites)
        # share one ceiling per host. Pre-fix every __init__ created its
        # own bucket → cron-overlap doubled the configured rate, cascading
        # into 429s.
        #
        # The optional `rate_limit_per_second` arg becomes a per-instance
        # override (used by tests passing a high value to skip pacing,
        # e.g. rate=1000). When the override is set, the client uses a
        # private TokenBucket and bypasses the registry entirely. When not
        # set (production path), every hit goes through `get_bucket(host)`.
        self._rate_override: float | None = rate_limit_per_second
        self._private_buckets: dict[str, TokenBucket] = {}
        self._timeout = timeout or settings.http_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    def _bucket_for(self, url: str) -> TokenBucket:
        host = host_for_url(url)
        if self._rate_override is not None:
            bucket = self._private_buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(rate=self._rate_override)
                self._private_buckets[host] = bucket
            return bucket
        return get_bucket(host, settings.rate_limit_per_second)

    # ---------- lifecycle ----------

    async def __aenter__(self) -> "PolymarketClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- core HTTP ----------

    async def _get_json(
        self,
        url: str,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Any:
        if self._client is None:
            raise RuntimeError("PolymarketClient must be used as an async context manager")

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            # Pass 5 R17: decorrelated jitter (AWS-recommended) replaces
            # wait_exponential. Tighter p99 than exponential and
            # desynchronizes retries when many concurrent calls 429 at the
            # same boundary. Honors Retry-After when the server provides
            # it (see _DecorrelatedJitterWait.__call__).
            wait=_DecorrelatedJitterWait(),
            # F14: only retry transient errors. Pre-fix retried every
            # HTTPStatusError including 4xx (terminal: bad params, 401, 404),
            # burning 4 rate-limit tokens on requests that would never succeed.
            # Retry policy now: TransportError (network) or 429/5xx
            # (rate-limit / server hiccup). Other 4xx fail fast.
            retry=retry_if_exception(_should_retry),
            reraise=True,
        ):
            with attempt:
                # Re-acquire the rate-limit token on EACH attempt. Putting
                # this outside the retry loop meant a 429-then-retry burst
                # could push effective request rate well past the limit
                # (one acquire, four sends), causing cascading 429s.
                # Pass 5 R17: bucket is per-host, shared across all live
                # PolymarketClient instances via the module-level registry.
                bucket = self._bucket_for(url)
                await bucket.acquire()
                r = await self._client.get(url, params=params)
                # Treat 5xx and 429 as retryable; bubble 4xx as terminal
                if r.status_code == 429 or r.status_code >= 500:
                    log.warning("retryable status %d on %s", r.status_code, url)
                    # D5 (Pass 3): tally rate-limit hits + 5xx for /system/status
                    from app.services.health_counters import (
                        record, RATE_LIMIT_HIT, API_FAILURE,
                    )
                    if r.status_code == 429:
                        record(RATE_LIMIT_HIT)
                    else:
                        record(API_FAILURE)
                    # Pass 5 R17: stash any Retry-After value on the
                    # exception so _DecorrelatedJitterWait honors it on
                    # the next attempt's sleep. Server knows when it'll
                    # have capacity; we shouldn't second-guess.
                    retry_after = (
                        _parse_retry_after(r.headers.get("Retry-After"))
                        if r.status_code == 429 else None
                    )
                    try:
                        r.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        if retry_after is not None:
                            e._retry_after = retry_after  # type: ignore[attr-defined]
                            log.info(
                                "Retry-After=%.1fs honored on %s",
                                retry_after, url,
                            )
                        raise
                if r.status_code >= 400:
                    log.error("client error %d on %s: %s", r.status_code, url, r.text[:200])
                    # D5: terminal 4xx counts as API failure
                    from app.services.health_counters import record, API_FAILURE
                    record(API_FAILURE)
                    r.raise_for_status()
                return r.json()
        return None  # unreachable; reraise above

    # ---------- leaderboard ----------

    async def get_leaderboard_page(
        self,
        order_by: LeaderboardOrderBy = "PNL",
        time_period: LeaderboardTimePeriod = "all",
        category: LeaderboardCategory = "overall",
        offset: int = 0,
        limit: int = LEADERBOARD_PAGE_SIZE,
    ) -> list[LeaderboardEntry]:
        """One page of the leaderboard from data-api.polymarket.com/v1/leaderboard.

        The API silently caps `limit` at 50. Use `get_leaderboard` for any depth.
        """
        url = f"{settings.data_api_base}/v1/leaderboard"
        data = await self._get_json(
            url,
            params={
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": min(limit, LEADERBOARD_PAGE_SIZE),
                "offset": offset,
                "category": category,
            },
        )
        items = _safe_list_or_empty(data, "data-api/leaderboard")
        return [LeaderboardEntry.from_dict(d) for d in items]

    async def get_leaderboard(
        self,
        order_by: LeaderboardOrderBy = "PNL",
        time_period: LeaderboardTimePeriod = "all",
        category: LeaderboardCategory = "overall",
        depth: int = 100,
    ) -> list[LeaderboardEntry]:
        """Fetch the leaderboard up to `depth` ranks, paging in 50s as needed.

        R15 (Pass 3): paginates with the LOUD helper so an API error
        returning a non-list shape is distinguishable from a legit empty
        page. Pre-fix used the silent helper, meaning Polymarket's overload
        responses (200 OK with garbage body) were treated as "end of list"
        and silently truncated the leaderboard. The daily snapshot would
        then have half the wallets it should have, with no warning.

        Now: on a shape error we log loudly and abort the pagination —
        better to fail loudly than to write a half-broken snapshot.
        """
        url = f"{settings.data_api_base}/v1/leaderboard"
        out: list[LeaderboardEntry] = []
        offset = 0
        while len(out) < depth:
            data = await self._get_json(
                url,
                params={
                    "timePeriod": time_period,
                    "orderBy": order_by,
                    "limit": LEADERBOARD_PAGE_SIZE,
                    "offset": offset,
                    "category": category,
                },
            )
            try:
                items = _safe_list_from_response(data, "data-api/leaderboard")
            except ResponseShapeError as e:
                # Loud failure — better to error out than to write a
                # silently-truncated leaderboard snapshot.
                log.error(
                    "R15: leaderboard pagination aborted at offset=%d due to "
                    "unparseable response shape — got %d entries so far. %s",
                    offset, len(out), e,
                )
                raise
            if not items:
                # Real empty page — legit end of leaderboard.
                break
            page = [LeaderboardEntry.from_dict(d) for d in items]
            out.extend(page)
            if len(page) < LEADERBOARD_PAGE_SIZE:
                break
            offset += LEADERBOARD_PAGE_SIZE
        return out[:depth]

    # ---------- per-user ----------

    async def get_positions(
        self,
        proxy_wallet: str,
        limit: int = 500,
        include_resolved: bool = False,
    ) -> list[Position]:
        """Fetch positions for a wallet from data-api.polymarket.com/positions.

        By default (include_resolved=False) drops zombie/dust positions at the
        API boundary so they never reach downstream consumers (signal
        detector, market-sync metadata fetch, persistence). See
        Position.drop_reason() for the multi-signal predicate.

        Why filter here: this module is the single seam through which all
        Polymarket calls flow (project rule). Filtering at the seam means
        every consumer (refresh job, diagnostic scripts, future paper-trade
        sync) inherits the filter without duplicating logic, and the 25k
        condition_ids per cycle drops to ~3-5k -- collapsing Phase 2's
        market-metadata fetch from ~15min to ~30sec.

        Set include_resolved=True for diagnostic scripts that legitimately
        need the raw API response unfiltered (e.g., scripts that audit
        zombie accumulation per wallet). Production code should leave the
        default.
        """
        url = f"{settings.data_api_base}/positions"
        data = await self._get_json(url, params={"user": proxy_wallet, "limit": limit})
        items = _safe_list_or_empty(data, "data-api/positions")
        parsed = [Position.from_dict(d) for d in items]

        if include_resolved:
            return parsed

        kept: list[Position] = []
        dropped_by_reason: dict[str, int] = {}
        for p in parsed:
            reason = p.drop_reason()
            if reason is None:
                kept.append(p)
                continue
            counter = _ZOMBIE_DROP_COUNTERS.get(reason)
            if counter is None:
                # Defensive: drop_reason() returned a label we don't have a
                # counter for. Keep the position rather than lose it silently
                # and log loudly so the operator sees the mismatch.
                log.warning(
                    "zombie filter: unknown drop_reason=%r for wallet=%s cid=%s "
                    "-- KEEPING the position (fail-open)",
                    reason, proxy_wallet[:12], p.condition_id[:12],
                )
                kept.append(p)
                continue
            health_counters.record(counter)
            dropped_by_reason[reason] = dropped_by_reason.get(reason, 0) + 1

        if dropped_by_reason:
            log.debug(
                "positions filter: wallet=%s kept=%d dropped=%d (%s)",
                proxy_wallet[:12], len(kept), sum(dropped_by_reason.values()),
                ", ".join(f"{k}={v}" for k, v in sorted(dropped_by_reason.items())),
            )
        return kept

    async def get_trades(
        self, proxy_wallet: str, limit: int = 500, offset: int = 0
    ) -> list[Trade]:
        """One page of historical trades. Use `iter_trades` for full history."""
        url = f"{settings.data_api_base}/trades"
        data = await self._get_json(
            url, params={"user": proxy_wallet, "limit": limit, "offset": offset}
        )
        items = _safe_list_or_empty(data, "data-api/trades?user")
        return [Trade.from_dict(d) for d in items]

    async def iter_trades(
        self, proxy_wallet: str, page_size: int = 500
    ) -> AsyncIterator[Trade]:
        """Yield every trade for a wallet, paging until exhausted."""
        offset = 0
        while True:
            page = await self.get_trades(proxy_wallet, limit=page_size, offset=offset)
            if not page:
                return
            for t in page:
                yield t
            if len(page) < page_size:
                return
            offset += page_size

    async def get_portfolio_value(self, proxy_wallet: str) -> PortfolioValue | None:
        url = f"{settings.data_api_base}/value"
        data = await self._get_json(url, params={"user": proxy_wallet})
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return PortfolioValue.from_dict(data[0])
        if isinstance(data, dict):
            return PortfolioValue.from_dict(data)
        return None

    # ---------- markets / events ----------

    async def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        closed: bool | None = None,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> list[Event]:
        url = f"{settings.gamma_api_base}/events"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if order is not None:
            params["order"] = order
        if ascending is not None:
            params["ascending"] = "true" if ascending else "false"
        data = await self._get_json(url, params=params)
        items = _safe_list_or_empty(data, "gamma-api/events")
        return [Event.from_dict(d) for d in items]

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        closed: bool | None = None,
    ) -> list[Market]:
        url = f"{settings.gamma_api_base}/markets"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        data = await self._get_json(url, params=params)
        items = _safe_list_or_empty(data, "gamma-api/markets")
        return [Market.from_dict(d) for d in items]

    async def get_markets_by_condition_ids(
        self, condition_ids: list[str], batch_size: int = 50,
        closed: bool | None = None,
    ) -> list[Market]:
        """Batch-fetch markets by their on-chain condition_id.

        Uses the repeated-key form `?condition_ids=A&condition_ids=B...`. Cids
        that don't exist on Polymarket are silently dropped (response just
        omits them). Splits the input across multiple requests if larger than
        `batch_size` to keep URL length reasonable. We always pass `limit`
        equal to the chunk size — gamma's default page is 20, which would
        silently drop most of a batch otherwise.

        `closed`:
          - None (default): gamma's default is `closed=false` — only active
            markets are returned, closed/resolved ones are silently filtered.
          - True: returns only closed markets (use to sweep resolved markets
            for paper-trade auto-close + signal_log resolution lookup).
          - False: explicitly request only active markets (rarely needed).
        """
        if not condition_ids:
            return []
        url = f"{settings.gamma_api_base}/markets"
        out: list[Market] = []
        for i in range(0, len(condition_ids), batch_size):
            chunk = condition_ids[i : i + batch_size]
            params: list[tuple[str, Any]] = [("condition_ids", c) for c in chunk]
            params.append(("limit", len(chunk)))
            if closed is not None:
                params.append(("closed", "true" if closed else "false"))
            data = await self._get_json(url, params=params)
            items = _safe_list_or_empty(data, "gamma-api/markets?condition_ids")
            out.extend(Market.from_dict(d) for d in items)
        return out

    async def get_events_by_ids(
        self, event_ids: list[str], batch_size: int = 50
    ) -> list[Event]:
        """Batch-fetch events by gamma id, repeated-key style. Carries tags.

        Always passes `limit` equal to the chunk size — gamma's default page
        is 20, which would silently drop most of a batch otherwise.
        """
        if not event_ids:
            return []
        url = f"{settings.gamma_api_base}/events"
        out: list[Event] = []
        for i in range(0, len(event_ids), batch_size):
            chunk = event_ids[i : i + batch_size]
            params: list[tuple[str, Any]] = [("id", e) for e in chunk]
            params.append(("limit", len(chunk)))
            data = await self._get_json(url, params=params)
            items = _safe_list_or_empty(data, "gamma-api/events?id")
            out.extend(Event.from_dict(d) for d in items)
        return out

    async def iter_events(
        self,
        page_size: int = 100,
        closed: bool | None = None,
        max_pages: int | None = None,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> AsyncIterator[Event]:
        """Yield every event, paging until exhausted (or until `max_pages` reached)."""
        offset = 0
        pages = 0
        while True:
            page = await self.get_events(
                limit=page_size,
                offset=offset,
                closed=closed,
                order=order,
                ascending=ascending,
            )
            if not page:
                return
            for ev in page:
                yield ev
            if len(page) < page_size:
                return
            offset += page_size
            pages += 1
            if max_pages is not None and pages >= max_pages:
                return

    # ---------- pricing ----------

    async def get_prices_history(
        self, token_id: str, interval: str = "1d"
    ) -> list[PricePoint]:
        """Historical prices for an outcome token. Empty for resolved markets."""
        url = f"{settings.clob_api_base}/prices-history"
        data = await self._get_json(url, params={"market": token_id, "interval": interval})
        if isinstance(data, dict):
            history = data.get("history") or []
            return [PricePoint.from_dict(p) for p in history if isinstance(p, dict)]
        return []

    async def get_market_trades(
        self, condition_id: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recent fills on a market via data-api. Used by B2 counterparty diagnostic.

        F12: switched from `clob.polymarket.com/trades` (which requires API
        key authentication and silently 401'd in our defensive code) to
        `data-api.polymarket.com/trades?market=<conditionId>` which is
        public + no auth.

        Each fill is a single trader's action on the market with fields
        `proxyWallet, side, outcome, outcomeIndex, size, price, timestamp,
        conditionId, asset` etc. The (outcome, side) pair tells us which
        side of the market each trader took — much cleaner than maker/taker
        semantics.

        Returns [] on error or unexpected response shape — counterparty
        detection is non-blocking, missing fills means warning stays False.
        """
        url = f"{settings.data_api_base}/trades"
        try:
            data = await self._get_json(
                url, params={"market": condition_id, "limit": limit}
            )
        except httpx.HTTPStatusError as e:
            # F13: log loudly with status code + body excerpt so silent
            # failure modes (auth / quota / route changes) become visible.
            log.error(
                "F13: data-api /trades?market=%s HTTP error: status=%s, body=%r",
                condition_id[:12],
                getattr(e.response, "status_code", "?"),
                getattr(e.response, "text", "")[:200],
            )
            return []
        return _safe_list_or_empty(
            data, "data-api/trades?market", list_keys=("data", "trades"),
        )

    async def get_orderbook(self, token_id: str) -> dict[str, Any] | None:
        """L2 orderbook for an outcome token from CLOB.

        Returns `{market, asset_id, hash, bids:[{price,size}], asks:[{price,size}]}`.
        Returns None if the market has no book (e.g. resolved/archived) or
        the request fails after retries.
        """
        url = f"{settings.clob_api_base}/book"
        try:
            data = await self._get_json(url, params={"token_id": token_id})
        except httpx.HTTPStatusError as e:
            # F13: include status + body excerpt so silent failure modes
            # (auth, rate-limit, route changes) are diagnosable.
            log.warning(
                "orderbook fetch failed for %s: status=%s body=%r",
                token_id[:12],
                getattr(e.response, "status_code", "?"),
                getattr(e.response, "text", "")[:200],
            )
            return None
        if isinstance(data, dict):
            return data
        log.warning(
            "F13: clob /book?token_id=%s returned %s instead of dict",
            token_id[:12], type(data).__name__,
        )
        return None
