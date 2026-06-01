"""BTC/USD reference price feed — the anchor for fair-value decisions.

The 5m/15m markets resolve on the **Chainlink BTC/USD data stream**. Getting
that exact stream in real time likely needs auth, so V0 anchors decisions on a
fast public BTC/USD spot price. Over a 5-minute window the price move dwarfs the
cross-feed basis, so spot is a fine *decision* anchor; matching Chainlink
exactly is a settlement-fidelity upgrade to make before trusting realized PnL.

Sources are USD-denominated (Coinbase primary, Binance USDT fallback) because
Chainlink BTC/USD tracks USD, not USDT. The `PriceSource` interface lets us drop
in a Chainlink reader later without touching the strategy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

_UA = {"User-Agent": "btcbot/0.1"}


@dataclass(frozen=True)
class Tick:
    price: float
    source: str
    ts: float  # unix seconds when we received it


class PriceSource(Protocol):
    async def get(self, http: httpx.AsyncClient) -> Tick: ...


class CoinbaseSpot:
    """Coinbase Exchange BTC-USD ticker — real-time, USD-denominated."""

    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"

    async def get(self, http: httpx.AsyncClient) -> Tick:
        r = await http.get(self.url, headers=_UA, timeout=5.0)
        r.raise_for_status()
        d = r.json()
        return Tick(price=float(d["price"]), source="coinbase", ts=time.time())


class BinanceSpot:
    """Binance BTC/USDT spot — fallback. Note: USDT, not USD."""

    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

    async def get(self, http: httpx.AsyncClient) -> Tick:
        r = await http.get(self.url, headers=_UA, timeout=5.0)
        r.raise_for_status()
        d = r.json()
        return Tick(price=float(d["price"]), source="binance", ts=time.time())


class ReferenceFeed:
    """Fetches a BTC/USD tick, trying sources in order until one succeeds."""

    def __init__(self, sources: list[PriceSource] | None = None) -> None:
        self._sources: list[PriceSource] = sources or [CoinbaseSpot(), BinanceSpot()]

    async def tick(self, http: httpx.AsyncClient) -> Tick | None:
        last_err: Exception | None = None
        for src in self._sources:
            try:
                return await src.get(http)
            except Exception as e:  # noqa: BLE001 - any source failure -> try next
                last_err = e
                continue
        if last_err is not None:
            # All sources down; caller decides whether to skip this cycle.
            return None
        return None
