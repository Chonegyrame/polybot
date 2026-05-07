"""R10 (Pass 3): unified paper-trade close helper.

Three close paths used to compute realized P&L with three slightly different
formulas:

  1. Manual close (POST /paper_trades/{id}/close)
     -- pre-fix: ignored entry_slippage_usdc and entry_fee_usdc entirely;
        used (exit/entry - 1) x size - exit_fee_at_old_rate.
     -- bias: realized P&L overstated vs auto-close paths by the entry-side costs.

  2. Auto-close on resolution (jobs.auto_close_resolved_paper_trades)
     -- used effective_entry = entry + slip via _settle_paper_trade_at_exit
        but old fee model.

  3. Auto-close on smart-money exit (jobs._settle_paper_trade_at_exit)
     -- same mechanism as resolution path.

This module unifies all three onto one formula, using the correct Polymarket
fee math (D1 / app/services/fees.compute_taker_fee_usdc).

Reusable from API + scheduler. Pure function (no DB / no I/O).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.fees import compute_taker_fee_usdc

ExitKind = Literal["resolution", "manual", "smart_money_exit"]


@dataclass(frozen=True)
class CloseResult:
    """Outcome of closing a paper trade. All amounts in USDC."""
    realized_pnl_usdc: float
    effective_entry_price: float          # entry_price + slippage_per_share
    gross_revenue_usdc: float             # what you receive at close (before exit fee)
    entry_fee_usdc: float                 # fee charged at OPEN time (informational)
    exit_fee_usdc: float                  # fee charged at THIS close (0 for resolution)
    total_cost_usdc: float                # stake + entry_fee + entry_slip
    exit_price_used: float                # the price plugged in (bid for non-resolution,
                                          # 1/0/0.5 for resolution)


def compute_realized_pnl(
    *,
    entry_price: float,
    entry_size_usdc: float,
    entry_slippage_usdc: float,
    entry_fee_usdc: float,
    exit_price: float,
    exit_kind: ExitKind,
    category: str | None,
) -> CloseResult:
    """Unified close P&L for paper trades. Used by all three close paths.

    Args:
        entry_price: the per-share price at which we bought (best_ask at open).
        entry_size_usdc: the dollar amount staked at open (the "size" field).
        entry_slippage_usdc: slippage charged at open (already in DB).
        entry_fee_usdc: fee charged at open (already in DB; per Polymarket
            formula stake x rate x (1 - entry_price)).
        exit_price: the per-share value received at close.
            - resolution path: 1.0 (winner), 0.0 (loser), 0.5 (50_50)
            - manual + smart_money_exit: best_bid at close time
        exit_kind: 'resolution' | 'manual' | 'smart_money_exit'.
        category: market category for fee lookup. None falls back to default.

    Returns:
        CloseResult with realized_pnl_usdc and the components.

    Math (per $size stake, then scaled to size):
        shares      = size / entry_price
        revenue     = shares x exit_price
        exit_fee    = 0 if exit_kind=='resolution' (not a trade)
                      else shares x rate x exit_price x (1 - exit_price)
        realized    = revenue - size - entry_fee - entry_slippage - exit_fee

    Notes:
      - entry_fee is treated as already-paid (just a deduction here).
      - entry_slippage is similarly already-baked-in cost.
      - resolution payouts are NOT charged trading fees (per Polymarket docs).
      - For 50_50 the exit_price=0.5 still gives no fee (resolution path).
    """
    if entry_price <= 0 or entry_price >= 1.0:
        # Defensive: caller should have caught this before now
        raise ValueError(f"entry_price must be in (0, 1); got {entry_price}")
    if entry_size_usdc <= 0:
        raise ValueError(f"entry_size_usdc must be > 0; got {entry_size_usdc}")

    shares = entry_size_usdc / entry_price
    gross_revenue_usdc = shares * exit_price

    if exit_kind == "resolution":
        # Resolution payouts are not trades -- no fee
        exit_fee = 0.0
    else:
        # Trading exit (selling shares back into the book) -- taker fee applies
        # Compute fee on the revenue side using the correct formula:
        # fee = shares x rate x exit_price x (1 - exit_price)
        # which equals revenue x rate x (1 - exit_price)
        exit_fee = compute_taker_fee_usdc(gross_revenue_usdc, exit_price, category)

    total_cost = entry_size_usdc + entry_fee_usdc + entry_slippage_usdc
    realized = gross_revenue_usdc - total_cost - exit_fee

    effective_entry = entry_price + (entry_slippage_usdc / shares if shares > 0 else 0)

    return CloseResult(
        realized_pnl_usdc=realized,
        effective_entry_price=effective_entry,
        gross_revenue_usdc=gross_revenue_usdc,
        entry_fee_usdc=entry_fee_usdc,
        exit_fee_usdc=exit_fee,
        total_cost_usdc=total_cost,
        exit_price_used=exit_price,
    )


def estimate_open_costs(
    *,
    entry_price: float,
    size_usdc: float,
    category: str | None,
    liquidity_5c_usdc: float | None,
) -> tuple[float, float, float]:
    """R10 (Pass 3): unified open-cost estimator using correct fee math.

    Replaces routes/paper_trades._estimate_costs which used the wrong
    flat-percentage fee model. Returns (effective_entry, fee_usdc,
    slippage_usdc) so the API route can persist all three to the DB.

    Slippage: square-root impact, capped at 10% (or 5% if no liquidity info).
    Fee: Polymarket formula = size x rate x (1 - entry_price).
    """
    import math
    from app.services.backtest_engine import SLIPPAGE_K

    if liquidity_5c_usdc and liquidity_5c_usdc > 0:
        slip_pct = min(0.10, SLIPPAGE_K * math.sqrt(size_usdc / liquidity_5c_usdc))
    else:
        slip_pct = min(0.05, size_usdc / 50_000.0)

    effective_entry = min(0.999, entry_price + slip_pct)
    # Slippage cost in USDC: shares_at_planned_entry x slippage_per_share
    shares_planned = size_usdc / max(entry_price, 1e-9)
    slippage_usdc = shares_planned * slip_pct

    fee_usdc = compute_taker_fee_usdc(size_usdc, entry_price, category)

    return effective_entry, fee_usdc, slippage_usdc
