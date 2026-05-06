"""B2 — counterparty diagnostic.

When a signal fires we ask: are any of the wallets in our tracked top-N
pool currently trading AGAINST our signal direction on this market? If
yes, the UI should warn "smart money is also on the other side" — same
pool, opposite conviction, a strong indicator that the consensus may be
less unanimous than the signal detector suggests.

F12 + F2 (combined): switched data source from `clob.polymarket.com/trades`
(which required API auth and silently 401'd) to `data-api.polymarket.com/trades?
market=<conditionId>` (public, no auth). The new endpoint returns trades from
each trader's perspective with `(outcome, side)` pairs — cleaner than the
maker/taker semantics the original implementation tried to use.

Counterparty rule: a trader is a counterparty to our signal if they took
a position on the OPPOSITE side. For a YES-direction signal:
  - outcome="Yes" + side="SELL"  → exited a Yes position
  - outcome="No"  + side="BUY"   → bet against Yes via a No buy
For a NO-direction signal, mirror.

Pure-function `detect_counterparty_overlap` keeps the logic testable; the
DB / API plumbing lives in `check_and_persist_counterparty_warning`.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import asyncpg

from app.db import crud
from app.services.polymarket import PolymarketClient

log = logging.getLogger(__name__)

# Default fills page — tuned to be cheap. Polymarket data-api usually
# responds in well under 1s.
DEFAULT_FILLS_LIMIT = 100


def _normalise_wallet(addr: str | None) -> str | None:
    if not addr or not isinstance(addr, str):
        return None
    addr = addr.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        return None
    return addr


def _is_counterparty_fill(fill: dict[str, Any], signal_direction: str) -> bool:
    """Decide whether a single fill represents a trade AGAINST the signal.

    For YES signals (we're buying YES tokens), a counterparty is anyone who:
      - sold YES tokens         (outcome="Yes", side="SELL")
      - bought NO tokens        (outcome="No",  side="BUY")
    For NO signals, mirrored.

    Returns False on any fill where we can't determine outcome+side cleanly
    (defensive — false negative is preferable to false positive on a
    diagnostic the user reads as a "warn me" indicator).
    """
    outcome = fill.get("outcome")
    side = fill.get("side")
    if not isinstance(outcome, str) or not isinstance(side, str):
        return False
    out_norm = outcome.strip().lower()
    side_norm = side.strip().upper()
    # Reject fills that aren't on a clean YES/NO outcome — multi-outcome
    # markets or weird labels are out of scope for V1 (consistent with
    # signal_detector's _outcome_to_direction filter).
    if out_norm not in ("yes", "no") or side_norm not in ("BUY", "SELL"):
        return False
    if signal_direction == "YES":
        # counterparty = exited Yes OR entered No
        return (out_norm == "yes" and side_norm == "SELL") or (
            out_norm == "no" and side_norm == "BUY"
        )
    if signal_direction == "NO":
        # counterparty = exited No OR entered Yes
        return (out_norm == "no" and side_norm == "SELL") or (
            out_norm == "yes" and side_norm == "BUY"
        )
    return False


def _extract_counterparty_wallets(
    fills: Iterable[dict[str, Any]], signal_direction: str,
) -> set[str]:
    """Pull `proxyWallet` addresses for fills that are counterparty to our
    signal direction.

    F12+F2: replaces the old maker-side filter that targeted the CLOB
    /trades endpoint. The data-api /trades response is per-trader, so we
    use `proxyWallet` directly as the wallet identity (no maker/taker
    disambiguation needed).
    """
    out: set[str] = set()
    for fill in fills:
        if not _is_counterparty_fill(fill, signal_direction):
            continue
        norm = _normalise_wallet(fill.get("proxyWallet"))
        if norm:
            out.add(norm)
    return out


def detect_counterparty_overlap(
    fills: list[dict[str, Any]],
    tracked_pool: set[str],
    signal_direction: str,
) -> bool:
    """Return True iff at least one counterparty wallet is in the tracked pool.

    `tracked_pool` is the union of every wallet seen across recent
    leaderboards — i.e., "every wallet our top-N filters could surface."
    `signal_direction` is "YES" or "NO" — determines which fills count
    as counterparties (see _is_counterparty_fill). Both arguments use
    lowercased proxy wallet addresses.
    """
    if not fills or not tracked_pool:
        return False
    counterparties = _extract_counterparty_wallets(fills, signal_direction)
    pool_lower = {w.lower() for w in tracked_pool}
    return bool(counterparties & pool_lower)


async def check_and_persist_counterparty_warning(
    conn: asyncpg.Connection,
    pm: PolymarketClient,
    *,
    signal_log_id: int,
    condition_id: str,
    signal_direction: str,
    tracked_pool: set[str],
) -> bool:
    """Run the check for one freshly-fired signal and write the result.

    F12+F2: signature changed — now takes `condition_id` + `signal_direction`
    (used to select the correct counterparty side) instead of `token_id`
    (which was the wrong handle anyway since we're not using the CLOB
    endpoint anymore).

    Non-blocking: any exception is logged and the warning stays False
    (it defaults to False at INSERT time anyway). Returns the persisted
    bool so the caller can log a summary count.
    """
    try:
        fills = await pm.get_market_trades(condition_id, limit=DEFAULT_FILLS_LIMIT)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "counterparty: data-api /trades raised for signal_log_id=%s cid=%s: %s",
            signal_log_id, condition_id[:12], e,
        )
        return False

    overlap = detect_counterparty_overlap(fills, tracked_pool, signal_direction)
    if overlap:
        try:
            await crud.set_counterparty_warning(conn, signal_log_id)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "counterparty: failed to persist warning for signal_log_id=%s: %s",
                signal_log_id, e,
            )
            return False
    return overlap
