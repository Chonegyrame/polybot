"""Per-wallet recent-form esports equity curve.

Reconstructs cumulative esports PnL over time from a wallet's most-recent
(<=2500, the data-api offset cap) trades + market resolutions — the same PnL
math as scripts/vet_candidates, but emitted as a time series ordered by market
resolution date. Powers the wallet-detail sparkline.

Cheap in-process TTL cache: the data-api pull is a few seconds, and the curve
only changes as markets resolve, so a few minutes of staleness is fine.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from app.services.polymarket import PolymarketClient

_ESPORTS_PREFIXES = ("lol:", "cs2:", "csgo:", "cs:go", "counter-strike", "valorant:", "dota")

# wallet -> (monotonic-ish epoch, payload). Stamped by the caller's clock.
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_SECONDS = 300.0


def _notional(t) -> float:
    return t.usdc_size if t.usdc_size else t.price * t.size


def _is_esports(title: str | None, teams: set[str]) -> bool:
    tl = (title or "").lower()
    if any(p in tl for p in _ESPORTS_PREFIXES) or "league of legends" in tl:
        return True
    if "handicap" in tl or "game" in tl or "map" in tl:
        return any(tm in tl for tm in teams if len(tm) > 2)
    return False


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def wallet_equity_curve(
    pm: PolymarketClient, wallet: str, now_epoch: float, max_trades: int = 2500,
) -> dict:
    cached = _CACHE.get(wallet)
    if cached and (now_epoch - cached[0]) < _TTL_SECONDS:
        return cached[1]

    trades = []
    async for t in pm.iter_trades(wallet, page_size=500):
        trades.append(t)
        if len(trades) >= max_trades:
            break
    if not trades:
        payload = {"points": [], "markets": 0, "total_pnl": 0.0, "win_rate": None}
        _CACHE[wallet] = (now_epoch, payload)
        return payload

    teams: set[str] = set()
    for t in trades:
        tl = (t.title or "").lower()
        if tl.startswith(("lol:", "cs2:", "csgo:")):
            m = re.search(r":\s*(.+?)\s+vs\s+(.+?)\s*(?:-|\(|$)", tl)
            if m:
                teams.add(m.group(1).strip())
                teams.add(m.group(2).strip())

    cids = list({t.condition_id for t in trades if t.condition_id})
    markets = await pm.get_markets_by_condition_ids(cids, closed=True)
    winner_token: dict[str, str] = {}
    end_by: dict[str, datetime | None] = {}
    title_by: dict[str, str | None] = {}
    for m in markets:
        title_by[m.condition_id] = m.question
        end_by[m.condition_id] = (_parse_dt(m.raw.get("closedTime"))
                                  or _parse_dt(m.end_date))
        if (len(m.outcome_prices) == len(m.clob_token_ids) == 2
                and max(m.outcome_prices) > 0.99):
            winner_token[m.condition_id] = m.clob_token_ids[
                m.outcome_prices.index(max(m.outcome_prices))]

    by_cid: dict[str, list] = defaultdict(list)
    for t in trades:
        by_cid[t.condition_id].append(t)

    resolved = []  # (resolution_dt, pnl)
    for cid, ts_ in by_cid.items():
        title = title_by.get(cid) or ts_[0].title
        if not _is_esports(title, teams):
            continue
        wt = winner_token.get(cid)
        if wt is None:
            continue  # unresolved
        net: dict[str, float] = defaultdict(float)
        cash = 0.0
        for t in ts_:
            amt = _notional(t)
            if t.side == "BUY":
                net[t.asset] += t.size; cash -= amt
            elif t.side == "SELL":
                net[t.asset] -= t.size; cash += amt
        pnl = cash + max(net.get(wt, 0.0), 0.0)
        when = end_by.get(cid) or (ts_[-1].timestamp if ts_ else None)
        if when is None:
            continue
        resolved.append((when, pnl))

    resolved.sort(key=lambda x: x[0])
    points = []
    cum = 0.0
    wins = 0
    for when, pnl in resolved:
        cum += pnl
        if pnl > 0:
            wins += 1
        points.append({"t": when.astimezone(timezone.utc).isoformat(),
                       "pnl": round(pnl, 2), "cum": round(cum, 2)})

    payload = {
        "points": points,
        "markets": len(resolved),
        "total_pnl": round(cum, 2),
        "win_rate": (wins / len(resolved)) if resolved else None,
    }
    _CACHE[wallet] = (now_epoch, payload)
    return payload
