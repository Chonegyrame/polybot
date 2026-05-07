"""Polymarket taker fee math (Pass 3 / D1).

Polymarket's actual fee formula:

    fee_usdc = shares × rate × price × (1 - price)

Where rate is per-category. Per dollar of stake (since shares = stake/price):

    fee_usdc = stake × rate × (1 - price)

Per dollar of sale revenue (since shares = revenue/price):

    fee_usdc = revenue × rate × (1 - price)

Notes:
- Only TAKERS pay fees (we always cross the spread → always taker).
- Resolution payouts are NOT trades, so no fee on the $1 settlement.
- Geopolitics markets are fee-free (rate=0).
- Fees peak at p=0.5 and approach zero at the extremes (p→0 or p→1).
- Fee is symmetric: trade at $0.30 and trade at $0.70 incur the same USDC fee.

Pre-Pass-3 the engine used `gross_per_dollar × (1 - rate)` which is a
flat-percentage-of-payout model. That's mathematically a different shape
entirely (linear in payout vs. quadratic in price). Crypto markets were
hit hardest — actual rate is 0.07 (~6× the placeholder we were using).

Source: https://docs.polymarket.com/concepts/fees
"""

from __future__ import annotations

from typing import Final

# Per-category taker fee rates from Polymarket docs (live as of 2026-05-07).
# Categories not in this dict fall through to DEFAULT_FEE_RATE.
TAKER_FEE_RATES: Final[dict[str, float]] = {
    "Crypto":      0.07,
    "Sports":      0.03,
    "Finance":     0.04,
    "Politics":    0.04,
    "Tech":        0.04,
    "Mentions":    0.04,
    "Economics":   0.05,
    "Culture":     0.05,
    "Weather":     0.05,
    "Other":       0.05,
    "Geopolitics": 0.00,
}

# Conservative fallback for categories Polymarket adds in the future or
# markets where category is null / unrecognized. Picks the higher tier
# so we don't accidentally underestimate fees for a new category.
DEFAULT_FEE_RATE: Final[float] = 0.05


def _resolve_rate(category: str | None) -> float:
    """Look up the taker fee rate for a market category.

    Case-insensitive match (since markets table category casing has varied
    historically — gamma sometimes returns "crypto" vs "Crypto").
    """
    if not category:
        return DEFAULT_FEE_RATE
    # Try exact match first (fast path), fall back to case-insensitive.
    if category in TAKER_FEE_RATES:
        return TAKER_FEE_RATES[category]
    cat_lower = category.strip().lower()
    for known_cat, rate in TAKER_FEE_RATES.items():
        if known_cat.lower() == cat_lower:
            return rate
    return DEFAULT_FEE_RATE


def compute_taker_fee_usdc(
    notional_usdc: float,
    price: float,
    category: str | None,
) -> float:
    """Compute Polymarket taker fee for a trade.

    Args:
        notional_usdc: dollar amount of the trade. For an entry buy, this is
            the stake. For an exit sell, this is the sale revenue
            (= shares × sell_price). Either way the formula is the same.
        price: the trade price (the price at which shares were bought or sold).
        category: market category for rate lookup. Maps via TAKER_FEE_RATES;
            unknown categories fall back to DEFAULT_FEE_RATE.

    Returns:
        Fee amount in USDC. Always >= 0. Returns 0 for invalid inputs
        (non-positive notional or out-of-range price).
    """
    if notional_usdc <= 0 or price <= 0 or price >= 1:
        return 0.0
    rate = _resolve_rate(category)
    if rate == 0:
        return 0.0
    return notional_usdc * rate * (1.0 - price)


def compute_taker_fee_per_dollar(price: float, category: str | None) -> float:
    """Convenience: fee per $1 of notional. Useful in the backtest engine
    where we work in per-dollar units throughout.

    Equivalent to compute_taker_fee_usdc(1.0, price, category).
    """
    return compute_taker_fee_usdc(1.0, price, category)
