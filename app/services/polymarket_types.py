"""Typed views over Polymarket API responses.

Plain dataclasses with `from_dict` factory methods. Validates that required fields
are present, parses Polymarket's JSON-encoded-string fields (clobTokenIds,
outcomePrices), and exposes parsed values as Python types.

Field names mirror the API where possible; we add helpers for the parsed forms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _opt_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts_to_dt(ts: int | float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _parse_json_string_list(raw: Any) -> list[Any]:
    """Polymarket double-encodes some fields: a JSON string inside JSON. Parse safely."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


# ------------------------- Leaderboard -------------------------


@dataclass(frozen=True)
class LeaderboardEntry:
    """One row from data-api.polymarket.com/v1/leaderboard.

    Carries both vol and pnl on every row regardless of how the leaderboard was
    sorted, plus rank, username and verification badge. `rank` arrives as a
    string from the API but we coerce to int.
    """

    proxy_wallet: str
    rank: int
    pnl: float
    vol: float
    user_name: str | None
    x_username: str | None
    verified_badge: bool
    profile_image: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LeaderboardEntry":
        rank_raw = d.get("rank")
        try:
            rank = int(rank_raw) if rank_raw is not None else 0
        except (TypeError, ValueError):
            rank = 0
        return cls(
            proxy_wallet=str(d.get("proxyWallet") or "").lower(),
            rank=rank,
            pnl=float(d.get("pnl") or 0.0),
            vol=float(d.get("vol") or 0.0),
            user_name=d.get("userName"),
            x_username=d.get("xUsername") or None,
            verified_badge=bool(d.get("verifiedBadge")),
            profile_image=d.get("profileImage") or None,
        )


# ------------------------- User data -------------------------


@dataclass(frozen=True)
class Position:
    """Open position from data-api.polymarket.com/positions."""

    proxy_wallet: str
    condition_id: str
    asset: str  # token_id (the YES or NO outcome token)
    outcome: str | None  # "Yes" / "No" / outcome label
    size: float  # number of shares
    avg_price: float | None
    cur_price: float | None
    initial_value: float | None
    current_value: float | None
    cash_pnl: float | None
    realized_pnl: float | None
    percent_pnl: float | None
    title: str | None
    slug: str | None
    icon: str | None
    end_date: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(
            proxy_wallet=str(d.get("proxyWallet") or d.get("user") or "").lower(),
            condition_id=str(d.get("conditionId") or ""),
            asset=str(d.get("asset") or ""),
            outcome=d.get("outcome"),
            size=float(d.get("size") or 0.0),
            avg_price=_opt_float(d.get("avgPrice")),
            cur_price=_opt_float(d.get("curPrice")),
            initial_value=_opt_float(d.get("initialValue")),
            current_value=_opt_float(d.get("currentValue")),
            cash_pnl=_opt_float(d.get("cashPnl")),
            realized_pnl=_opt_float(d.get("realizedPnl")),
            percent_pnl=_opt_float(d.get("percentPnl")),
            title=d.get("title"),
            slug=d.get("slug"),
            icon=d.get("icon"),
            end_date=d.get("endDate"),
            raw=d,
        )


@dataclass(frozen=True)
class Trade:
    """A historical trade from data-api.polymarket.com/trades.

    Richer than /activity — includes title and slug.
    """

    proxy_wallet: str
    condition_id: str
    asset: str
    side: str  # "BUY" or "SELL"
    size: float
    usdc_size: float | None
    price: float
    timestamp: datetime | None
    transaction_hash: str | None
    title: str | None
    slug: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trade":
        return cls(
            proxy_wallet=str(d.get("proxyWallet") or "").lower(),
            condition_id=str(d.get("conditionId") or ""),
            asset=str(d.get("asset") or ""),
            side=str(d.get("side") or "").upper(),
            size=float(d.get("size") or 0.0),
            usdc_size=_opt_float(d.get("usdcSize")),
            price=float(d.get("price") or 0.0),
            timestamp=_ts_to_dt(d.get("timestamp")),
            transaction_hash=d.get("transactionHash"),
            title=d.get("title"),
            slug=d.get("slug"),
            raw=d,
        )


@dataclass(frozen=True)
class PortfolioValue:
    proxy_wallet: str
    value: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PortfolioValue":
        return cls(
            proxy_wallet=str(d.get("user") or "").lower(),
            value=float(d.get("value") or 0.0),
        )


# ------------------------- Markets / Events -------------------------


@dataclass(frozen=True)
class Market:
    """A single Polymarket market (outcome of a question).

    NOTE: gamma `/markets` returns category=null. Category lives on the parent event.
    Build a market->event mapping when ingesting.
    """

    id: str
    slug: str | None
    question: str | None
    condition_id: str
    clob_token_ids: list[str]  # parsed from JSON-encoded string
    outcomes: list[str]
    outcome_prices: list[float]  # parsed from JSON-encoded string
    volume_num: float | None
    liquidity_num: float | None
    end_date: str | None
    closed: bool
    active: bool
    last_trade_price: float | None
    best_bid: float | None
    best_ask: float | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Market":
        token_ids = [str(t) for t in _parse_json_string_list(d.get("clobTokenIds"))]
        outcomes = [str(o) for o in _parse_json_string_list(d.get("outcomes"))]
        prices_raw = _parse_json_string_list(d.get("outcomePrices"))
        prices = [float(p) for p in prices_raw if p not in (None, "")]
        return cls(
            id=str(d.get("id") or ""),
            slug=d.get("slug"),
            question=d.get("question"),
            condition_id=str(d.get("conditionId") or ""),
            clob_token_ids=token_ids,
            outcomes=outcomes,
            outcome_prices=prices,
            volume_num=_opt_float(d.get("volumeNum")),
            liquidity_num=_opt_float(d.get("liquidityNum")),
            end_date=d.get("endDate"),
            closed=bool(d.get("closed")),
            active=bool(d.get("active")),
            last_trade_price=_opt_float(d.get("lastTradePrice")),
            best_bid=_opt_float(d.get("bestBid")),
            best_ask=_opt_float(d.get("bestAsk")),
            raw=d,
        )


def pair_yes_no_tokens(
    outcomes: list[str], clob_token_ids: list[str],
) -> tuple[str | None, str | None]:
    """F6: Pair the YES and NO CLOB token IDs by matching outcome labels.

    Pre-fix code in market_sync.py used `clob_token_ids[0]` as YES and
    `clob_token_ids[1]` as NO unconditionally. Some Polymarket markets ship
    with `outcomes=["No", "Yes"]` (sports markets ordered by team name,
    negation prompts), in which case the index-based mapping silently picks
    the wrong token. Every downstream calc on those markets — `signal_entry_offer`,
    paper-trade entry, P&L, B1 exit bid, B4 snapshot — runs against the
    wrong side of the book.

    Returns `(yes_token, no_token)`. If the inputs don't form a clean binary
    YES/NO mapping (length mismatch, multi-outcome, custom labels, both
    labels match yes), returns `(None, None)` so the caller can mark the
    market as non-binary and skip signal/snapshot logic for it. This is the
    same defensive behavior as `_outcome_to_direction` in signal_detector.

    Matching is case-insensitive and whitespace-tolerant.
    """
    if len(outcomes) != 2 or len(clob_token_ids) != 2:
        return (None, None)
    yes_idx: int | None = None
    no_idx: int | None = None
    for i, label in enumerate(outcomes):
        if not isinstance(label, str):
            continue
        norm = label.strip().lower()
        if norm == "yes":
            yes_idx = i
        elif norm == "no":
            no_idx = i
    if yes_idx is None or no_idx is None or yes_idx == no_idx:
        return (None, None)
    return (clob_token_ids[yes_idx], clob_token_ids[no_idx])


@dataclass(frozen=True)
class Event:
    """A Polymarket event — groups multiple related markets and carries the category."""

    id: str
    slug: str | None
    title: str | None
    category: str | None
    tags: list[dict[str, Any]]
    end_date: str | None
    updated_at: str | None  # gamma's `updatedAt` — drives incremental sync
    closed: bool
    markets: list[Market]
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        markets_raw = d.get("markets") or []
        markets = [Market.from_dict(m) for m in markets_raw if isinstance(m, dict)]
        return cls(
            id=str(d.get("id") or ""),
            slug=d.get("slug"),
            title=d.get("title"),
            category=d.get("category"),
            tags=list(d.get("tags") or []),
            end_date=d.get("endDate"),
            updated_at=d.get("updatedAt"),
            closed=bool(d.get("closed")),
            markets=markets,
            raw=d,
        )


# ------------------------- Pricing -------------------------


@dataclass(frozen=True)
class PricePoint:
    timestamp: datetime
    price: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PricePoint":
        return cls(
            timestamp=_ts_to_dt(d.get("t")) or datetime.fromtimestamp(0, tz=timezone.utc),
            price=float(d.get("p") or 0.0),
        )
