"""Roll logged sharp actions up into match-level consensus signals.

The whole project thesis is *smart-money consensus*: many tracked specialists
independently landing on the same side of the same market. The flat action feed
hides that — six sharps buying ThunderTalk shows up as six unrelated rows. This
module groups actions by match (the two teams) and, within a match, by market,
so the UI can surface "5 of 6 sharps on ThunderTalk @ avg 0.52 — you'd pay 0.56
now".

Pure functions over already-shaped action dicts (see api/routes/esports.py): no
I/O, no DB, so it's trivially testable and never contends with the tracker.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# "LoL: ThunderTalk Gaming vs LGD Gaming - Winner" -> ("ThunderTalk Gaming",
# "LGD Gaming"). Also handles "Counter-Strike: Heroic vs Sharks (BO1) - ...".
_MATCH_RE = re.compile(r"^[^:]*:\s*(.+?)\s+vs\.?\s+(.+?)\s*(?:[-(]|$)", re.IGNORECASE)
_GAME_RE = re.compile(r"\b(?:game|map)\s*(\d+)", re.IGNORECASE)
_BO_RE = re.compile(r"\(BO(\d+)\)", re.IGNORECASE)
# Strip the "LoL: A vs B (BO5) - " lead, leaving just the market descriptor.
_PREFIX_RE = re.compile(
    r"^[^:]*:\s*.+?\s+vs\.?\s+.+?(?:\s*\([^)]*\))?\s*[-–]\s*(.+)$", re.IGNORECASE)


def market_label(title: str | None, market_type: str | None) -> str:
    """Short, distinguishing label for ONE market within a match — so a trade
    reads as "Game 3 winner" or "Match winner · BO5", not just the team."""
    t = title or ""
    g = _GAME_RE.search(t)
    if market_type == "winner":
        if g:
            return f"Game {g.group(1)} winner"
        bo = _BO_RE.search(t)
        return "Match winner" + (f" · BO{bo.group(1)}" if bo else "")
    if market_type == "handicap":
        return f"Game {g.group(1)} handicap" if g else "Handicap"
    if market_type in ("total", "prop"):
        m = _PREFIX_RE.match(t)
        return (m.group(1).strip() if m else t).strip() or market_type.title()
    m = _PREFIX_RE.match(t)
    return (m.group(1).strip() if m else t).strip() or (market_type or "Market")


def _is_series_winner(mk: dict) -> bool:
    """A whole-match (series) winner market — no game/map number in the title."""
    return mk.get("market_type") == "winner" and not _GAME_RE.search(mk.get("title") or "")


def match_of(title: str | None) -> tuple[str | None, str | None]:
    """(stable key, display 'A vs B') for a market title, or (None, None).

    The key is order-independent (teams sorted) so the winner, handicap and
    totals markets of one game all collapse to the same match.
    """
    m = _MATCH_RE.search(title or "")
    if not m:
        return None, None
    a, b = m.group(1).strip(), m.group(2).strip()
    return " | ".join(sorted((a.lower(), b.lower()))), f"{a} vs {b}"


def _avg(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _market_consensus(actions: list[dict]) -> dict[str, Any]:
    """Consensus within ONE market (condition_id).

    Lean = the outcome the most *distinct* sharps BUY (entries are the signal);
    SELLs are surfaced as an exit count, not as direction.
    """
    buys = [a for a in actions if a.get("side") == "BUY"]
    sells = [a for a in actions if a.get("side") == "SELL"]
    by_outcome: dict[str, list] = defaultdict(list)
    for a in buys:
        by_outcome[a.get("outcome") or "?"].append(a)

    ranked = sorted(
        by_outcome.items(),
        key=lambda kv: (len({a["wallet"] for a in kv[1]}),
                        sum((a.get("notional") or 0) for a in kv[1])),
        reverse=True,
    )
    total_buyers = len({a["wallet"] for a in buys})
    lean_outcome = ranked[0][0] if ranked else None
    lean_acts = sorted(ranked[0][1], key=lambda a: a.get("detected_at") or "") if ranked else []
    lean_count = len({a["wallet"] for a in lean_acts})

    # Per-outcome breakdown (handles totals' Over/Under, not just two teams).
    outcomes = sorted(
        [{
            "outcome": oc,
            "buyers": len({a["wallet"] for a in acts}),
            "notional": sum((a.get("notional") or 0) for a in acts),
            "avg_entry": _avg([a.get("their_price") for a in acts]),
        } for oc, acts in by_outcome.items()],
        key=lambda o: (o["buyers"], o["notional"]), reverse=True,
    )

    title = next((a.get("title") for a in actions), None)
    mtype = next((a.get("market_type") for a in actions), None)
    avg_entry = _avg([a.get("their_price") for a in lean_acts])
    our_ask = next((a.get("live_ask") for a in reversed(lean_acts)
                    if a.get("live_ask") is not None), None)

    # Resolution + honest forward-test result for the consensus (lean) side.
    resolved_outcome = next((a.get("resolved_outcome") for a in actions
                             if a.get("resolved_outcome")), None)
    resolved = resolved_outcome is not None
    consensus_correct = (resolved and lean_outcome is not None
                         and resolved_outcome.strip().lower() == lean_outcome.strip().lower())
    # P&L per $1 staked on the lean side at the price you'd actually pay.
    follow_price = our_ask if (our_ask and 0.02 <= our_ask <= 0.98) else avg_entry
    follow_pnl = None
    if resolved and follow_price and follow_price > 0:
        follow_pnl = (1.0 / follow_price - 1.0) if consensus_correct else -1.0

    return {
        "condition_id": next((a.get("condition_id") for a in actions), None),
        "market_type": mtype,
        "title": title,
        "label": market_label(title, mtype),
        "market_open": next((a.get("market_open") for a in actions
                             if a.get("market_open") is not None), None),
        "resolved": resolved,
        "resolved_outcome": resolved_outcome,
        "consensus_correct": consensus_correct if resolved else None,
        "follow_pnl": follow_pnl,
        "buyers": total_buyers,
        "outcomes": outcomes,
        "lean_outcome": lean_outcome,
        "lean_count": lean_count,
        "against_count": total_buyers - lean_count,
        "skew": (lean_count / total_buyers) if total_buyers else None,
        "avg_entry": avg_entry,
        # most-recent *captured* ask on the lean side ≈ what you'd pay to follow now
        "our_ask": our_ask,
        "notional": sum((a.get("notional") or 0) for a in actions),
        "exits": len(sells),
        "actions": sorted(actions, key=lambda a: a.get("detected_at") or "", reverse=True),
    }


def group_into_matches(actions: list[dict], max_matches: int = 40) -> list[dict]:
    """Group shaped action dicts into match cards, live matches first."""
    groups: dict[str, list] = defaultdict(list)
    display: dict[str, str] = {}
    for a in actions:
        key, disp = match_of(a.get("title"))
        if key is None:  # un-parseable title — keep it as its own card
            key = a.get("condition_id") or "?"
            disp = (a.get("title") or "Unknown market")[:60]
        groups[key].append(a)
        display.setdefault(key, disp)

    matches: list[dict] = []
    for key, acts in groups.items():
        by_cid: dict[str, list] = defaultdict(list)
        for a in acts:
            by_cid[a.get("condition_id")].append(a)
        markets = [_market_consensus(v) for v in by_cid.values()]
        # Headline = the series/match winner if present (what "who wins" means),
        # else any winner market, else whatever most sharps touched. Per-game and
        # totals markets follow; the UI labels each so trades aren't ambiguous.
        markets.sort(key=lambda m: (_is_series_winner(m), m["market_type"] == "winner",
                                    m["buyers"]), reverse=True)

        wallets = {a["wallet"] for a in acts}
        follow_wallets = {a["wallet"] for a in acts if a.get("follow")}
        last = max((a.get("detected_at") for a in acts if a.get("detected_at")), default=None)
        # All sub-markets of a match share the event start; take the earliest seen.
        start_time = min((a.get("start_time") for a in acts if a.get("start_time")), default=None)
        open_flags = [a.get("market_open") for a in acts]
        is_live = any(f is True for f in open_flags)

        matches.append({
            "match_key": key,
            "title": display[key],
            "game": next((a.get("game") for a in acts if a.get("game")), None),
            "sharps": len(wallets),
            "follow_sharps": len(follow_wallets),
            "total_notional": sum((a.get("notional") or 0) for a in acts),
            "action_count": len(acts),
            "last_detected_at": last,
            "start_time": start_time,
            "is_live": is_live,
            "primary": markets[0] if markets else None,
            "markets": markets,
        })

    # Live first, then strongest consensus (more sharps agreeing = bigger signal),
    # then most-recent activity. Ranking by headcount keeps the order stable
    # instead of reshuffling every time a single new trade lands elsewhere.
    matches.sort(key=lambda m: (m["is_live"], m["sharps"], m["last_detected_at"] or ""),
                 reverse=True)
    return matches[:max_matches]
