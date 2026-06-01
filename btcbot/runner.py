"""BTC up/down paper-trading runner.

One loop drives every horizon in parallel (5m, 15m, ...), so they compete on
the same live data and we compare realized PnL afterward. Each iteration:

  1. Pull a BTC/USD reference tick, fold it into the rolling vol estimate.
  2. For each horizon: resolve the live window. The first time we see a new
     window we sample the reference as its OPEN (the strike isn't published),
     recording how stale that sample is. Windows we didn't catch near their
     start are skipped — a guessed strike isn't faithful.
  3. Compute fair P(up), read both books, and let the strategy decide. A fired
     decision is recorded as a paper trade (one per window+side).
  4. Settle any finished windows against Polymarket's actual resolution.

Run:  python -m btcbot.runner --horizons 5m,15m --stake 100 --edge 0.03
Stop with Ctrl-C; the ledger persists. `--summary` prints results and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx

from app.services.polymarket import PolymarketClient
from btcbot import book as bookmod
from btcbot import ledger
from btcbot.discovery import HORIZONS, Horizon, LiveWindow, resolve_live_window
from btcbot.fair_value import RollingVol, fair_prob_up
from btcbot.reference import ReferenceFeed
from btcbot.strategy import StrategyConfig, decide


# How stale (seconds after candle start) an open-price sample may be and still
# be trusted as the strike. Beyond this we skip the window rather than guess.
OPEN_CAPTURE_MAX_LAG = 8.0


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def _log(msg: str) -> None:
    # flush so a detached .bat console / log file shows activity in real time
    print(msg, flush=True)


def _winner(window_market) -> str | None:
    """Map a closed market's outcome to 'up'/'down', or None if unresolved."""
    prices = window_market.outcome_prices
    outcomes = window_market.outcomes
    if len(prices) != 2 or len(outcomes) != 2:
        return None
    # Resolved markets have a 1.0 / 0.0 split; mid values mean not-yet-resolved.
    hi = max(prices)
    if hi < 0.99:
        return None
    win_label = outcomes[prices.index(hi)]
    return str(win_label).strip().lower()


class BtcBot:
    def __init__(self, horizons: list[Horizon], cfg: StrategyConfig,
                 db_path=None, poll_seconds: float = 1.0,
                 starting_bankroll: float = 1000.0,
                 collect_only: bool = False) -> None:
        self.horizons = horizons
        self.cfg = cfg
        self.poll = poll_seconds
        self.collect_only = collect_only
        self.vol = RollingVol()
        self.opens: dict[str, dict] = {}   # slug -> {open, lag, start}
        self.conn = ledger.connect(db_path) if db_path else ledger.connect()
        ledger.ensure_account(self.conn, starting_bankroll)
        self._win_cache: dict[str, LiveWindow] = {}  # horizon key -> live window

    async def _get_window(self, pm: PolymarketClient, h: Horizon,
                          now: float) -> LiveWindow | None:
        """Resolve the live window, cached per horizon until it rolls over.

        The window's tokens/times don't change within its life, so we only hit
        the events API when the computed start advances — keeping us well under
        rate limits even at 1s polling.
        """
        start = h.current_window_start(int(now))
        cached = self._win_cache.get(h.key)
        if cached is not None and cached.start_unix == start:
            return cached
        w = await resolve_live_window(pm, h, int(now))
        if w is not None:
            self._win_cache[h.key] = w
        return w

    def _capture_open(self, w: LiveWindow, spot: float, now: float) -> dict | None:
        rec = self.opens.get(w.slug)
        if rec is not None:
            return rec
        lag = now - w.start_unix
        rec = {"open": spot, "lag": lag, "start": w.start_unix}
        self.opens[w.slug] = rec
        tag = "OK" if lag <= OPEN_CAPTURE_MAX_LAG else "STALE"
        _log(f"[{_ts()}] {w.horizon.key} new window {w.slug} open~{spot:.2f} "
             f"(lag {lag:.1f}s {tag})")
        return rec

    async def _eval_horizon(self, pm: PolymarketClient, h: Horizon,
                            spot: float, now: float) -> None:
        w = await self._get_window(pm, h, int(now))
        if w is None:
            return
        rec = self._capture_open(w, spot, now)
        secs = w.seconds_left(now)
        sigma = self.vol.sigma_per_sqrt_sec
        open_ok = rec["lag"] <= OPEN_CAPTURE_MAX_LAG

        up_book = await pm.get_orderbook(w.up_token)
        down_book = await pm.get_orderbook(w.down_token)
        fair_up = fair_prob_up(spot, rec["open"], secs, sigma) if open_ok else None

        # Always log the observation — this is the research dataset, captured
        # whether or not we have a tradeable strike.
        ub = bookmod.best_bid(up_book or {});  ua = bookmod.best_ask(up_book or {})
        db_ = bookmod.best_bid(down_book or {}); da = bookmod.best_ask(down_book or {})
        ledger.log_snapshot(
            self.conn, ts=now, horizon=h.key, slug=w.slug, secs_left=secs,
            spot=spot, open_price=(rec["open"] if open_ok else None),
            sigma=sigma, fair_up=fair_up,
            up_bid=ub.price if ub else None, up_ask=ua.price if ua else None,
            down_bid=db_.price if db_ else None, down_ask=da.price if da else None,
        )

        # Collect-only: log the dataset, never trade. Used to build history
        # before any strategy is designed.
        if self.collect_only:
            return

        # Trade only when we caught the open and both books are present.
        if not open_ok or not up_book or not down_book or fair_up is None:
            return
        if ledger.has_position(self.conn, w.slug, "up") and \
           ledger.has_position(self.conn, w.slug, "down"):
            return

        d = decide(fair_up, secs, up_book, down_book, self.cfg)
        if not d.trade or d.side is None:
            return
        if ledger.has_position(self.conn, w.slug, d.side):
            return

        rid = ledger.record_trade(self.conn, ledger.TradeRecord(
            ts_entry=now, horizon=h.key, slug=w.slug,
            condition_id=w.market.condition_id, side=d.side,
            fair_prob=d.fair_prob or 0.0, entry_price=d.ask_avg or 0.0,
            shares=d.shares or 0.0, cost_usd=d.cost or 0.0, fee_usd=d.fee or 0.0,
            net_edge=d.net_edge or 0.0, spot_at_entry=spot, open_price=rec["open"],
            sigma=sigma, seconds_left=secs,
        ))
        if rid is not None:
            _log(f"[{_ts()}] TRADE {h.key} {d.side.upper()} {w.slug} "
                 f"@ {d.ask_avg:.3f}  fair={d.fair_prob:.3f}  edge={d.net_edge:+.3f}  "
                 f"{d.shares:.1f} sh  spot={spot:.2f} open={rec['open']:.2f} "
                 f"{secs:.0f}s left  bal=${ledger.balance(self.conn):.0f}")

    async def _settle_due(self, pm: PolymarketClient, now: float) -> None:
        for row in ledger.open_trades(self.conn):
            # window end = start + horizon length; only check after it closed
            h = HORIZONS.get(row["horizon"])
            if h is None:
                continue
            start = int(row["slug"].rsplit("-", 1)[-1])
            if now < start + h.window_seconds + 2:
                continue
            ev = await pm.get_event_by_slug(row["slug"])
            if ev is None or not ev.markets:
                continue
            outcome = _winner(ev.markets[0])
            if outcome is None:
                continue
            pnl = ledger.settle(self.conn, row["id"], outcome)
            _log(f"[{_ts()}] SETTLE {row['horizon']} {row['side'].upper()} "
                 f"{row['slug']} -> {outcome.upper()}  pnl={pnl:+.2f}  "
                 f"bal=${ledger.balance(self.conn):.0f}")

    async def run(self) -> None:
        mode = "COLLECT-ONLY (no trades)" if self.collect_only else "PAPER-TRADING"
        _log(f"[{_ts()}] btcbot starting [{mode}] | horizons={[h.key for h in self.horizons]} "
             f"| stake=${self.cfg.stake_usd} edge>={self.cfg.edge_threshold} "
             f"| bankroll=${ledger.balance(self.conn):.0f} poll={self.poll}s")
        feed = ReferenceFeed()
        async with PolymarketClient() as pm, httpx.AsyncClient() as http:
            while True:
                loop_start = time.time()
                tick = await feed.tick(http)
                if tick is not None:
                    self.vol.update(tick.price, tick.ts)
                    now = time.time()
                    for h in self.horizons:
                        try:
                            await self._eval_horizon(pm, h, tick.price, now)
                        except Exception as e:  # noqa: BLE001 keep the loop alive
                            _log(f"[{_ts()}] eval {h.key} error: {e}")
                    try:
                        await self._settle_due(pm, now)
                    except Exception as e:  # noqa: BLE001
                        _log(f"[{_ts()}] settle error: {e}")
                else:
                    _log(f"[{_ts()}] reference feed down, skipping cycle")
                elapsed = time.time() - loop_start
                await asyncio.sleep(max(0.0, self.poll - elapsed))


def _parse_args():
    p = argparse.ArgumentParser(description="BTC up/down paper-trading bot")
    p.add_argument("--horizons", default="5m,15m", help="comma list: 5m,15m")
    p.add_argument("--stake", type=float, default=100.0)
    p.add_argument("--edge", type=float, default=0.03)
    p.add_argument("--poll", type=float, default=1.0)
    p.add_argument("--bankroll", type=float, default=1000.0,
                   help="starting paper bankroll (only used on first run)")
    p.add_argument("--collect-only", action="store_true",
                   help="log market data but place NO trades (build dataset)")
    p.add_argument("--summary", action="store_true", help="print ledger summary and exit")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.summary:
        import json
        conn = ledger.connect()
        print(json.dumps(ledger.summary(conn), indent=2))
        return
    horizons = [HORIZONS[k.strip()] for k in args.horizons.split(",") if k.strip() in HORIZONS]
    if not horizons:
        raise SystemExit("no valid horizons (choose from 5m,15m)")
    cfg = StrategyConfig(edge_threshold=args.edge, stake_usd=args.stake)
    bot = BtcBot(horizons, cfg, poll_seconds=args.poll,
                 starting_bankroll=args.bankroll, collect_only=args.collect_only)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] stopped. ledger summary:")
        import json
        print(json.dumps(ledger.summary(bot.conn), indent=2))


if __name__ == "__main__":
    main()
