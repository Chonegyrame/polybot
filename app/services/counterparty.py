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
) -> list[dict]:
    """Return list of counterparty entities for one signal.

    Each entry: {wallet, wallets, same_usdc, opposite_usdc, concentration}.
    Sorted by opposite_usdc DESC (biggest counterparties first).

    Pass 5 #2: cluster-aware. The `is_counterparty` decision is now
    applied at the entity level (cluster or lone wallet), not per raw
    proxy_wallet. A 4-wallet sybil cluster on the opposite side at $20k
    each contributes ONE entity at $80k same/opposite USDC -- not four
    wallets each at $20k. The MIN_OPPOSITE_USDC floor and concentration
    threshold are evaluated against the entity totals.

      - `wallets` is the underlying proxy_wallet list for the entity
        (length 1 for a lone wallet; >1 for a cluster).
      - `wallet` is a representative address (the alphabetically-first
        proxy_wallet of the entity), kept for backwards-compat with
        existing call sites that print or display "the counterparty".
        Use `wallets` when you need the full membership.

    Implementation: bulk SQL joins tracked-pool wallets to
    cluster_membership, then groups positions by entity (cluster_id if
    present, else raw proxy_wallet). Filtered to YES/NO outcomes only,
    consistent with signal_detector.
    """
    pool_list = list(tracked_pool)
    if not pool_list:
        return []

    same_outcome = _same_outcome(signal_direction)
    opposite_outcome = _opposite_outcome(signal_direction)

    rows = await conn.fetch(
        """
        WITH wallet_identity AS (
            SELECT
                tp.proxy_wallet,
                COALESCE(cm.cluster_id::text, tp.proxy_wallet) AS identity
            FROM unnest($4::TEXT[]) AS tp(proxy_wallet)
            LEFT JOIN cluster_membership cm USING (proxy_wallet)
        )
        SELECT
            wi.identity,
            SUM(CASE WHEN LOWER(p.outcome) = LOWER($1) THEN p.current_value ELSE 0 END)
                AS same_usdc,
            SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END)
                AS opposite_usdc,
            ARRAY_AGG(DISTINCT p.proxy_wallet ORDER BY p.proxy_wallet) AS wallets
        FROM positions p
        JOIN wallet_identity wi USING (proxy_wallet)
        WHERE p.condition_id = $3
          AND p.size > 0
          AND LOWER(p.outcome) IN ('yes', 'no')
        GROUP BY wi.identity
        HAVING SUM(CASE WHEN LOWER(p.outcome) = LOWER($2) THEN p.current_value ELSE 0 END) > 0
        """,
        same_outcome, opposite_outcome, condition_id, pool_list,
    )

    out: list[dict] = []
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
        wallets = list(r["wallets"] or [])
        out.append({
            # Pass 5 #2: representative wallet for back-compat callers.
            # First wallet alphabetically -- deterministic for clusters.
            "wallet": wallets[0] if wallets else "",
            "wallets": wallets,
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
