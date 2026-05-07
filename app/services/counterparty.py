"""B2 + R4 + R7 (Pass 3) -- positions-based counterparty diagnostic.

When a signal fires we ask: are any wallets in the tracked top-N pool
CURRENTLY positioned on the OTHER side of this market with meaningful
conviction? If yes, the UI surfaces "N top traders hold opposite side" --
a contested-signal warning that tells the user the smart-money consensus
isn't unanimous.

This is a complete rewrite of the original CLOB-fills-based check
(F12+F2 in Pass 2). The fills-based approach had two problems:

  R4: a top trader who already held YES and sold some YES to take profit
      was flagged as counterparty. But partial profit-takers are not
      adversaries -- they're still long net-YES, just lighter. The
      warning fired on essentially every winning trending market.

  R7: fills had no time bound. A wallet who exited NO three weeks ago
      got flagged as a counterparty to a fresh YES signal today.

Pass 3 fix: query the POSITIONS table directly. For each wallet in the
tracked pool that has a position on this market, classify them by
(opposite_size_usdc, concentration_ratio). Counterparty iff:

  - opposite_size_usdc >= MIN_OPPOSITE_USDC  (default $5k -- ignore
    micro-positions that are effectively noise)
  - opposite_size_usdc / (same_size + opposite_size) >= CONCENTRATION_THRESHOLD
    (default 0.75 -- ignore hedgers / partial-position traders; require
    the wallet's bet to be heavily on the opposite side)

Returns the COUNT of qualifying wallets so the UI can tier the warning:
  count == 0 -> no warning (clean signal)
  count 1-2  -> mild warning ("1 top trader holds opposite side")
  count 3+   -> strong warning ("3 top traders hold opposite side")
"""

from __future__ import annotations

import logging
from typing import Iterable

import asyncpg

from app.db import crud

log = logging.getLogger(__name__)

# Floors -- both must be cleared for a wallet to count as counterparty.
MIN_OPPOSITE_USDC = 5_000.0       # absolute size floor (matches watchlist floor)
CONCENTRATION_THRESHOLD = 0.75    # opposite_size / total_size on this market


def _opposite_outcome(signal_direction: str) -> str:
    """Map signal direction to the position outcome that would be adversarial."""
    return "No" if signal_direction == "YES" else "Yes"


def _same_outcome(signal_direction: str) -> str:
    """Map signal direction to the position outcome that's same-side."""
    return "Yes" if signal_direction == "YES" else "No"


def is_counterparty(
    same_side_usdc: float,
    opposite_side_usdc: float,
    *,
    min_opposite_usdc: float = MIN_OPPOSITE_USDC,
    concentration_threshold: float = CONCENTRATION_THRESHOLD,
) -> bool:
    """Pure decision function for "is this wallet a counterparty?"

    Returns True iff:
      - opposite_side_usdc >= min_opposite_usdc  (absolute size floor)
      - opposite_side_usdc / (same + opposite) >= concentration_threshold
    """
    if opposite_side_usdc < min_opposite_usdc:
        return False
    total = same_side_usdc + opposite_side_usdc
    if total <= 0:
        return False
    concentration = opposite_side_usdc / total
    return concentration >= concentration_threshold


async def find_counterparty_wallets(
    conn: asyncpg.Connection,
    *,
    condition_id: str,
    signal_direction: str,
    tracked_pool: Iterable[str],
    min_opposite_usdc: float = MIN_OPPOSITE_USDC,
    concentration_threshold: float = CONCENTRATION_THRESHOLD,
) -> list[dict[str, float | str]]:
    """Return list of counterparty wallets for one signal.

    Each entry: {wallet, same_usdc, opposite_usdc, concentration}.
    Sorted by opposite_usdc DESC (biggest counterparties first).

    Implementation: one bulk SQL pulls all positions for tracked-pool
    wallets on this market, aggregating per-wallet by (same vs opposite).
    Filtered to the YES/NO outcomes only (multi-outcome positions ignored,
    consistent with signal_detector).
    """
    pool_list = list(tracked_pool)
    if not pool_list:
        return []

    same_outcome = _same_outcome(signal_direction)
    opposite_outcome = _opposite_outcome(signal_direction)

    rows = await conn.fetch(
        """
        SELECT
            p.proxy_wallet,
            SUM(CASE WHEN LOWER(p.outcome) = LOWER($1) THEN p.current_value ELSE 0 END)
                AS same_usdc,
            SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END)
                AS opposite_usdc
        FROM positions p
        WHERE p.condition_id = $3
          AND p.proxy_wallet = ANY($4::TEXT[])
          AND p.size > 0
          AND LOWER(p.outcome) IN ('yes', 'no')
        GROUP BY p.proxy_wallet
        HAVING SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END) > 0
        """,
        same_outcome, opposite_outcome, condition_id, pool_list,
    )

    out: list[dict[str, float | str]] = []
    for r in rows:
        same_u = float(r["same_usdc"] or 0.0)
        opp_u = float(r["opposite_usdc"] or 0.0)
        if not is_counterparty(
            same_u, opp_u,
            min_opposite_usdc=min_opposite_usdc,
            concentration_threshold=concentration_threshold,
        ):
            continue
        total = same_u + opp_u
        out.append({
            "wallet": r["proxy_wallet"],
            "same_usdc": same_u,
            "opposite_usdc": opp_u,
            "concentration": (opp_u / total) if total > 0 else 0.0,
        })

    out.sort(key=lambda d: d["opposite_usdc"], reverse=True)  # type: ignore[arg-type]
    return out


async def check_and_persist_counterparty_count(
    conn: asyncpg.Connection,
    *,
    signal_log_id: int,
    condition_id: str,
    signal_direction: str,
    tracked_pool: Iterable[str],
) -> int:
    """R4+R7 (Pass 3): replace the old binary boolean with a count.

    Runs the positions-based check + persists the count to signal_log.
    Non-blocking: any exception is logged and the count stays 0 (the
    column default). Returns the count for caller-side logging.
    """
    try:
        wallets = await find_counterparty_wallets(
            conn,
            condition_id=condition_id,
            signal_direction=signal_direction,
            tracked_pool=tracked_pool,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "counterparty: positions query failed for signal_log_id=%s cid=%s: %s",
            signal_log_id, condition_id[:12], e,
        )
        return 0

    count = len(wallets)
    if count > 0:
        try:
            await crud.set_counterparty_count(conn, signal_log_id, count)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "counterparty: failed to persist count=%d for signal_log_id=%s: %s",
                count, signal_log_id, e,
            )
            return 0
    return count
