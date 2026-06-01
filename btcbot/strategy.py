"""Decision logic: trade only when fair value beats the book by enough.

For each side (Up, Down) we know the model's fair probability and the price we'd
actually pay to take liquidity (the ask, walked for our size). A share bought at
price `a` that wins pays $1, so its expected value is `fair - a`. We fire only
when that expected edge, net of fees, clears a safety buffer that covers model
error (vol mis-estimate, open/strike approximation, latency).

We always BUY the cheap side rather than sell, because taking liquidity at the
ask is the only fill we can be certain of in paper — matching live.
"""

from __future__ import annotations

from dataclasses import dataclass

from btcbot import book as bookmod
from btcbot.fair_value import fair_prob_up


@dataclass(frozen=True)
class StrategyConfig:
    edge_threshold: float = 0.03      # min net edge/share to fire (3c)
    stake_usd: float = 100.0          # notional per entry
    fee_bps: float = 0.0              # taker fee; Polymarket = 0 on these today
    min_seconds_left: float = 5.0     # too late to fill near expiry
    max_seconds_left: float | None = None  # e.g. only trade final N secs if set
    min_price: float = 0.05           # avoid dust / illiquid extremes
    max_price: float = 0.95


@dataclass(frozen=True)
class Decision:
    trade: bool
    reason: str
    side: str | None = None           # "up" / "down"
    fair_prob: float | None = None    # model P(side wins)
    ask_avg: float | None = None      # avg price we'd pay (walked)
    shares: float | None = None
    cost: float | None = None         # pre-fee USD
    fee: float | None = None
    net_edge: float | None = None     # per-share, after fee


def _fee_per_share(price: float, fee_bps: float) -> float:
    # Polymarket-style fee scales with the worse of (price, 1-price); kept
    # simple + configurable. Default 0 today.
    return (fee_bps / 10_000.0) * min(price, 1.0 - price)


def _eval_side(
    side: str, fair: float, book: dict, cfg: StrategyConfig
) -> Decision | None:
    ask = bookmod.best_ask(book)
    if ask is None:
        return None
    if not (cfg.min_price <= ask.price <= cfg.max_price):
        return None
    want = cfg.stake_usd / ask.price
    fill = bookmod.simulate_buy(book, want)
    if fill is None:
        return None
    fee_ps = _fee_per_share(fill.avg_price, cfg.fee_bps)
    net_edge = fair - fill.avg_price - fee_ps
    return Decision(
        trade=net_edge >= cfg.edge_threshold,
        reason="edge_ok" if net_edge >= cfg.edge_threshold else "edge_below_threshold",
        side=side,
        fair_prob=fair,
        ask_avg=fill.avg_price,
        shares=fill.shares,
        cost=fill.cost,
        fee=fee_ps * fill.shares,
        net_edge=net_edge,
    )


def decide(
    fair_up: float,
    seconds_left: float,
    up_book: dict,
    down_book: dict,
    cfg: StrategyConfig,
) -> Decision:
    """Pick the best tradeable side, or return a no-trade Decision."""
    if seconds_left < cfg.min_seconds_left:
        return Decision(False, "too_close_to_expiry")
    if cfg.max_seconds_left is not None and seconds_left > cfg.max_seconds_left:
        return Decision(False, "outside_entry_window")

    candidates = []
    up = _eval_side("up", fair_up, up_book, cfg)
    if up is not None:
        candidates.append(up)
    down = _eval_side("down", 1.0 - fair_up, down_book, cfg)
    if down is not None:
        candidates.append(down)

    if not candidates:
        return Decision(False, "no_book")

    best = max(candidates, key=lambda d: (d.net_edge if d.net_edge is not None else -1))
    if best.trade:
        return best
    return Decision(False, best.reason, side=best.side, fair_prob=best.fair_prob,
                    ask_avg=best.ask_avg, net_edge=best.net_edge)
