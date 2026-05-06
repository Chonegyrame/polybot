"""Polymarket API client. ALL API calls go through this module — no exceptions.

Endpoints validated in spike/FINDINGS.md (2026-05-04). Async, rate-limited,
retried with exponential backoff. Returns typed objects from polymarket_types.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Literal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.services.rate_limiter import TokenBucket
from app.services.polymarket_types import (
    Event,
    LeaderboardEntry,
    Market,
    PortfolioValue,
    Position,
    PricePoint,
    Trade,
)

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


def _safe_list_from_response(
    data: Any,
    endpoint: str,
    list_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """F13: Coerce an API response to a list of dicts; log loudly on suspicious shape.

    Pre-fix code had `if not isinstance(data, list): return []` everywhere,
    which silently masked API errors (e.g. Polymarket sometimes returns a
    JSON-wrapped error object instead of a list during overload). The probe
    found this is exactly what was happening for the CLOB /trades endpoint.

    This helper distinguishes:
      - Real empty list (return [], silent)        — legit "no results"
      - Wrapped list ({"data": [...]}, etc.)       — unwrap, return inner list
      - Dict with no expected wrapper key          — log WARN, return []
      - Anything else (None, str, int, etc.)       — log WARN, return []

    Args:
      data: The parsed JSON body.
      endpoint: A short label for log context (e.g. "data-api/positions").
      list_keys: Dict-keys that may wrap a list response. Tried in order.
    """
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in list_keys:
            inner = data.get(key)
            if isinstance(inner, list):
                return [d for d in inner if isinstance(d, dict)]
        log.warning(
            "F13: %s returned dict instead of list (likely API error). "
            "Top-level keys=%s",
            endpoint, sorted(data.keys())[:8],
        )
        return []
    log.warning(
        "F13: %s returned %s instead of list. Body preview=%r",
        endpoint, type(data).__name__, str(data)[:120],
    )
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
        self._limiter = TokenBucket(
            rate=rate_limit_per_second or settings.rate_limit_per_second
        )
        self._timeout = timeout or settings.http_timeout_seconds
        self._client: httpx.AsyncClient | None = None

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
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
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
                await self._limiter.acquire()
                r = await self._client.get(url, params=params)
                # Treat 5xx and 429 as retryable; bubble 4xx as terminal
                if r.status_code == 429 or r.status_code >= 500:
                    log.warning("retryable status %d on %s", r.status_code, url)
                    r.raise_for_status()
                if r.status_code >= 400:
                    log.error("client error %d on %s: %s", r.status_code, url, r.text[:200])
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
        items = _safe_list_from_response(data, "data-api/leaderboard")
        return [LeaderboardEntry.from_dict(d) for d in items]

    async def get_leaderboard(
        self,
        order_by: LeaderboardOrderBy = "PNL",
        time_period: LeaderboardTimePeriod = "all",
        category: LeaderboardCategory = "overall",
        depth: int = 100,
    ) -> list[LeaderboardEntry]:
        """Fetch the leaderboard up to `depth` ranks, paging in 50s as needed."""
        out: list[LeaderboardEntry] = []
        offset = 0
        while len(out) < depth:
            page = await self.get_leaderboard_page(
                order_by=order_by,
                time_period=time_period,
                category=category,
                offset=offset,
                limit=LEADERBOARD_PAGE_SIZE,
            )
            if not page:
                break
            out.extend(page)
            if len(page) < LEADERBOARD_PAGE_SIZE:
                break
            offset += LEADERBOARD_PAGE_SIZE
        return out[:depth]

    # ---------- per-user ----------

    async def get_positions(self, proxy_wallet: str, limit: int = 500) -> list[Position]:
        url = f"{settings.data_api_base}/positions"
        data = await self._get_json(url, params={"user": proxy_wallet, "limit": limit})
        items = _safe_list_from_response(data, "data-api/positions")
        return [Position.from_dict(d) for d in items]

    async def get_trades(
        self, proxy_wallet: str, limit: int = 500, offset: int = 0
    ) -> list[Trade]:
        """One page of historical trades. Use `iter_trades` for full history."""
        url = f"{settings.data_api_base}/trades"
        data = await self._get_json(
            url, params={"user": proxy_wallet, "limit": limit, "offset": offset}
        )
        items = _safe_list_from_response(data, "data-api/trades?user")
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
        items = _safe_list_from_response(data, "gamma-api/events")
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
        items = _safe_list_from_response(data, "gamma-api/markets")
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
            items = _safe_list_from_response(data, "gamma-api/markets?condition_ids")
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
            items = _safe_list_from_response(data, "gamma-api/events?id")
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
        return _safe_list_from_response(
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
