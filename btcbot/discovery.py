"""Resolve the currently-live "BTC Up or Down" market for a given horizon.

Polymarket's short-horizon up/down markets use a slug that encodes the candle's
START time as a unix timestamp aligned to the horizon length:

    btc-updown-5m-<unixstart>     5-minute  (start aligned to 300s)
    btc-updown-15m-<unixstart>    15-minute (start aligned to 900s)

Because the timestamp is computable, we never search — we compute the current
window's slug directly and fetch that one event. Several windows are open at
once (the live candle plus a few pre-created future ones); the bot trades the
*live* candle, i.e. the one whose [start, start+len) interval contains now.

Resolution rule (confirmed live 2026-06-01):
  5m/15m resolve on the **Chainlink BTC/USD data stream**: "Up" if price at the
  end of the window >= price at the start. (The hourly series resolves on
  Binance BTC/USDT instead — different anchor, handled separately when added.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.services.polymarket import PolymarketClient
from app.services.polymarket_types import Event, Market


@dataclass(frozen=True)
class Horizon:
    """A tradeable up/down cadence."""

    key: str  # "5m" / "15m"
    window_seconds: int
    asset: str = "btc"  # slug prefix; btc/eth/sol/bnb all exist

    def slug_for_start(self, start_unix: int) -> str:
        return f"{self.asset}-updown-{self.key}-{start_unix}"

    def current_window_start(self, now_unix: int | None = None) -> int:
        """Unix start of the live candle (the one now falls inside)."""
        n = int(now_unix if now_unix is not None else time.time())
        return (n // self.window_seconds) * self.window_seconds


HORIZONS: dict[str, Horizon] = {
    "5m": Horizon("5m", 300),
    "15m": Horizon("15m", 900),
}


@dataclass(frozen=True)
class LiveWindow:
    """A live up/down market resolved to its tradeable essentials."""

    horizon: Horizon
    slug: str
    start_unix: int           # candle open time (resolution start)
    end_unix: int             # candle close time (resolution end)
    up_token: str             # CLOB token id for the "Up" outcome
    down_token: str           # CLOB token id for the "Down" outcome
    market: Market
    event: Event

    def seconds_left(self, now_unix: int | None = None) -> float:
        n = now_unix if now_unix is not None else time.time()
        return self.end_unix - n

    def fraction_elapsed(self, now_unix: int | None = None) -> float:
        n = now_unix if now_unix is not None else time.time()
        span = self.end_unix - self.start_unix
        if span <= 0:
            return 1.0
        return max(0.0, min(1.0, (n - self.start_unix) / span))


def _up_down_tokens(market: Market) -> tuple[str | None, str | None]:
    """Map the (Up, Down) CLOB token ids by outcome label.

    These markets ship outcomes ["Up", "Down"] (not Yes/No), so the repo's
    pair_yes_no_tokens helper doesn't apply. Match by label, case-insensitive,
    rather than trusting positional order.
    """
    outcomes = market.outcomes
    tokens = market.clob_token_ids
    if len(outcomes) != 2 or len(tokens) != 2:
        return (None, None)
    up_idx = down_idx = None
    for i, label in enumerate(outcomes):
        norm = str(label).strip().lower()
        if norm == "up":
            up_idx = i
        elif norm == "down":
            down_idx = i
    if up_idx is None or down_idx is None or up_idx == down_idx:
        return (None, None)
    return (tokens[up_idx], tokens[down_idx])


async def resolve_live_window(
    client: PolymarketClient,
    horizon: Horizon,
    now_unix: int | None = None,
) -> LiveWindow | None:
    """Resolve the live candle for `horizon`, or None if not tradeable.

    Returns None when: the window event doesn't exist yet, the market is
    already closed, or the Up/Down tokens can't be cleanly identified.
    """
    start = horizon.current_window_start(now_unix)
    slug = horizon.slug_for_start(start)
    event = await client.get_event_by_slug(slug)
    if event is None or not event.markets:
        return None
    market = event.markets[0]
    if market.closed:
        return None
    up_token, down_token = _up_down_tokens(market)
    if up_token is None or down_token is None:
        return None
    return LiveWindow(
        horizon=horizon,
        slug=slug,
        start_unix=start,
        end_unix=start + horizon.window_seconds,
        up_token=up_token,
        down_token=down_token,
        market=market,
        event=event,
    )
