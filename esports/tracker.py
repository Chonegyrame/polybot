"""Live esports sharp-action tracker.

Polls each watchlist wallet's most-recent trades via the shared PolymarketClient
(rate-limited, so it can't stampede the API or starve the polybot jobs), detects
NEW esports entries/exits, and logs each one with the LIVE book at detection —
i.e. what WE would actually pay/receive to follow, spread included. This is the
honest forward-test data the backtest panel (Phase 4) will run on.

Design notes:
  - Baseline on first sight: a wallet with no cursor gets its newest trade ts
    recorded WITHOUT logging history, so we only capture genuinely new actions
    from start-of-tracking forward (true forward-test, no backfill flood).
  - Crash-resilient: every wallet poll is wrapped; one failure never kills the
    loop. The SQLite cursor means a restart resumes exactly where it left off.
  - Only the LIGHT /trades?user query is in the hot loop. The heavier book fetch
    runs only when a NEW action is detected (rare), never every poll.

Run:  python -m esports.tracker            # default 8s cycle
      python -m esports.tracker --cycle 5
First seed the watchlist:  python -m esports.watchlist
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from app.services.polymarket import PolymarketClient
from esports import db
from esports.markets import (
    classify_market_type,
    refresh_active_resolutions,
    refresh_esports_markets,
)

sys.stdout.reconfigure(encoding="utf-8")

# Re-sweep the esports market universe this often (open matches + handicaps/
# totals/props are created when the event is, so this comfortably precedes
# trading). Detection is membership in esports_markets (tag-based).
REFRESH_SECONDS = 900

# Fast resolution check for markets we hold live action in — so a finished game
# greys out within ~a minute, not on the 15-min universe cadence. Cheap: only
# the handful of condition_ids in recent actions.
RESOLUTION_SECONDS = 45

# Title fallback ONLY for the obvious winner markets, in case a brand-new
# market is traded before the next universe refresh. Tag-based membership is
# the primary path and is what catches handicap/total/prop markets.
_FALLBACK_KEYS = {
    "lol:": "lol", "league of legends": "lol",
    "cs2:": "cs", "cs:go": "cs", "csgo:": "cs", "counter-strike": "cs",
}


def classify(conn, condition_id: str, title: str | None) -> tuple[str, str] | None:
    """Return (game, market_type) if this is an esports market, else None.

    Primary: membership in the swept esports_markets universe (tag-based, so it
    includes handicap/total/prop markets). Fallback: title keyword for obvious
    winner markets not yet in the universe.
    """
    row = db.lookup_market(conn, condition_id)
    if row is not None:
        return row["game"], row["market_type"]
    tl = (title or "").lower()
    for key, game in _FALLBACK_KEYS.items():
        if key in tl:
            return game, classify_market_type(title)
    return None


def _bbo(book: dict | None) -> tuple[float | None, float | None]:
    """Best bid / best ask from a CLOB book dict. None if no/closed book."""
    if not book:
        return None, None

    def _px(side: str, agg):
        levels = book.get(side) or []
        prices = [float(l["price"]) for l in levels if l.get("price") is not None]
        return agg(prices) if prices else None

    return _px("bids", max), _px("asks", min)


async def poll_wallet(pm: PolymarketClient, conn, wallet: str) -> int:
    """Poll one wallet, log new esports actions, advance its cursor. Returns # logged."""
    trades = await pm.get_trades(wallet, limit=100, offset=0)  # newest-first
    if not trades:
        return 0

    cursor = db.get_cursor(conn, wallet)
    newest = max((t.timestamp.timestamp() for t in trades if t.timestamp), default=None)

    # First sight: baseline only, don't backfill history as "detected now".
    if cursor is None:
        if newest is not None:
            db.set_cursor(conn, wallet, newest)
        return 0

    # New trades since cursor, oldest-first so they log in chronological order.
    # classify() returns (game, market_type) for esports markets, else None.
    fresh = []
    for t in trades:
        if not (t.timestamp and t.timestamp.timestamp() > cursor):
            continue
        cls = classify(conn, t.condition_id, t.title)
        if cls is not None:
            fresh.append((t, cls[0], cls[1]))
    fresh.sort(key=lambda x: x[0].timestamp.timestamp())

    logged = 0
    now = time.time()
    for t, game, mtype in fresh:
        live_bid, live_ask = _bbo(await pm.get_orderbook(t.asset)) if t.asset else (None, None)
        rid = db.log_action(
            conn,
            wallet=wallet, tx_hash=t.transaction_hash, condition_id=t.condition_id,
            asset=t.asset, title=t.title, slug=t.slug,
            outcome=t.raw.get("outcome"), side=t.side, game=game, market_type=mtype,
            their_price=t.price, size=t.size, usdc_size=t.usdc_size,
            traded_at=t.timestamp.timestamp(), detected_at=now,
            live_bid=live_bid, live_ask=live_ask,
        )
        if rid is not None:
            logged += 1
            edge = (f" theirs={t.price:.2f} ours_ask={live_ask:.2f}"
                    if live_ask is not None else f" theirs={t.price:.2f}")
            print(f"  [{time.strftime('%H:%M:%S')}] {wallet[:10]} {t.side} "
                  f"{(t.title or '')[:48]!r}{edge}")

    if newest is not None and newest > cursor:
        db.set_cursor(conn, wallet, newest)
    return logged


async def run(cycle_seconds: float) -> None:
    conn = db.connect()
    wallets = [r["wallet"] for r in db.active_wallets(conn)]
    if not wallets:
        # Self-seed so running inside polybot (no manual seed step) just works.
        from esports.watchlist import seed
        print("esports: watchlist empty — seeding…")
        seed()
        wallets = [r["wallet"] for r in db.active_wallets(conn)]
    if not wallets:
        print("esports: no active wallets after seed — tracker idle.")
        return
    print(f"esports: tracking {len(wallets)} sharps | cycle {cycle_seconds}s | "
          f"db {db.DEFAULT_DB}")

    async with PolymarketClient() as pm:
        # Build the esports market universe before the first poll so handicap/
        # total/prop markets are detectable from cycle 1.
        last_refresh = 0.0
        last_resolution = 0.0
        cycles = 0
        while True:
            t0 = time.time()
            if t0 - last_refresh >= REFRESH_SECONDS:
                try:
                    n, ev = await refresh_esports_markets(pm, conn)
                    last_refresh = t0
                    print(f"[universe] {n} esports markets across {ev} events "
                          f"(LoL+CS, open + recent)")
                except Exception as e:  # noqa: BLE001
                    print(f"  ! universe refresh error: {type(e).__name__}: {str(e)[:80]}")
            if t0 - last_resolution >= RESOLUTION_SECONDS:
                try:
                    resolved = await refresh_active_resolutions(pm, conn)
                    last_resolution = t0
                    if resolved:
                        print(f"[resolved] {resolved} active market(s) settled")
                except Exception as e:  # noqa: BLE001
                    print(f"  ! resolution refresh error: {type(e).__name__}: {str(e)[:80]}")
            total = 0
            errors = 0
            last_err = None
            for w in wallets:
                try:
                    total += await poll_wallet(pm, conn, w)
                except Exception as e:  # noqa: BLE001 — loop must never die
                    errors += 1
                    last_err = f"{type(e).__name__}: {str(e)[:80]}"
                    print(f"  ! {w[:10]} poll error: {last_err}")
            cycles += 1
            if cycles == 1:
                print(f"baseline pass done ({len(wallets)} wallets), "
                      f"{db.market_count(conn)} markets tracked. watching…")
            elif total:
                print(f"[cycle {cycles}] logged {total} new action(s)")
            elapsed = time.time() - t0
            # Heartbeat for the UI liveness ring (real: stops/errors if it stalls).
            try:
                db.set_tracker_status(
                    conn, last_cycle_at=t0, last_cycle_ms=elapsed * 1000.0,
                    cycle_seconds=cycle_seconds, cycles=cycles, wallets=len(wallets),
                    errors_last_cycle=errors, last_error=last_err,
                    last_error_at=(time.time() if last_err else None),
                )
            except Exception:  # noqa: BLE001 — heartbeat must never kill the loop
                pass
            await asyncio.sleep(max(0.0, cycle_seconds - elapsed))


def main() -> None:
    ap = argparse.ArgumentParser(description="Live esports sharp-action tracker.")
    ap.add_argument("--cycle", type=float, default=8.0, help="seconds between full passes")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.cycle))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
