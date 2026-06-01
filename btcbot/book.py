"""Order-book helpers for faithful fill simulation.

`get_orderbook` returns {bids:[{price,size}], asks:[{price,size}]}. Ordering is
not guaranteed, so we compute best levels by min/max rather than by position.
A "buy" consumes asks from the lowest price upward; this is what a marketable
(spread-crossing) order actually does, and is the only way to be *sure* of a
fill in paper that matches live.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    price: float
    size: float  # shares available at this price


def _levels(side: list) -> list[Level]:
    out: list[Level] = []
    for lvl in side or []:
        try:
            out.append(Level(float(lvl["price"]), float(lvl["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def best_bid(book: dict) -> Level | None:
    bids = _levels(book.get("bids"))
    return max(bids, key=lambda l: l.price) if bids else None


def best_ask(book: dict) -> Level | None:
    asks = _levels(book.get("asks"))
    return min(asks, key=lambda l: l.price) if asks else None


@dataclass(frozen=True)
class Fill:
    avg_price: float       # size-weighted average price paid per share
    shares: float          # shares actually filled (<= requested)
    cost: float            # avg_price * shares (pre-fee)
    levels_walked: int


def simulate_buy(book: dict, want_shares: float) -> Fill | None:
    """Simulate a marketable BUY for `want_shares`, walking asks low->high.

    Returns None if the book has no asks. May fill fewer shares than requested
    if the book is too thin — that partial fill is itself information the
    backtest should see, not paper over.
    """
    asks = sorted(_levels(book.get("asks")), key=lambda l: l.price)
    if not asks:
        return None
    remaining = want_shares
    spent = 0.0
    filled = 0.0
    walked = 0
    for lvl in asks:
        if remaining <= 0:
            break
        take = min(remaining, lvl.size)
        spent += take * lvl.price
        filled += take
        remaining -= take
        walked += 1
    if filled <= 0:
        return None
    return Fill(avg_price=spent / filled, shares=filled, cost=spent, levels_walked=walked)
