"""Wallet behavioral classifier.

Tags each tracked wallet as one of:
  directional   — places one-sided bets and holds (the kind we want)
  market_maker  — provides two-sided liquidity, churn-driven (NOT directional)
  arbitrage     — runs cross-leg / cross-market arbs (NOT directional)
  likely_sybil  — flagged by separate cluster detector (set externally)
  unknown       — too few trades observed to classify

We use only the `/trades` history (no Polygon RPC) for v1. Features are
behavioral signatures, calibrated against published Polymarket research
(Columbia wash-trading study, IMDEA arbitrage paper). Thresholds in
`classify()` are educated guesses for v1 — verify against Dune distributions
before locking.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.services.polymarket_types import Trade

log = logging.getLogger(__name__)

CLASSIFIER_VERSION = "v1.2"

# Thresholds — single source of truth so they're easy to tune.
MM_TWO_SIDED_RATIO_THRESHOLD = 0.40
ARB_CROSS_LEG_RATIO_THRESHOLD = 0.30
MIN_TRADES_TO_CLASSIFY = 5
DIRECTIONAL_HIGH_CONFIDENCE_TRADES = 50

# v1.1: BUY/SELL pair must round-trip within this window AND match in size to
# count as a market-making pair. v1.0 used a 1-hour window with no size
# tolerance, which mistook directional traders scaling out of winners for MMs.
# Real MM round-trips cycle in seconds-to-minutes and have matched sizes;
# scale-outs span hours and are partial.
MM_PAIR_WINDOW_MINUTES = 10
MM_PAIR_SIZE_TOLERANCE = 0.30  # SELL size within 30% of BUY size
# Plus an activity floor: MMs spread across many markets, scale-outs concentrate
# on whatever the trader is in. Below this distinct-markets-per-day rate we
# don't trust the two-sided ratio enough to flag MM.
MM_MIN_MARKETS_PER_DAY = 0.5

# v1.2: position-state MM detection. Catches wallets running both-sides books
# (YES + NO on the same condition_id, each leg held with non-trivial size).
# Trade-history features miss this — both legs are just BUYs with no SELL pair,
# and slow hedges fall outside the 5-min cross-leg arb window. A wallet sitting
# on 3+ such markets is not discretionary. Confirmed empirically: 25 wallets
# tagged directional currently run this pattern (swisstony: 65 markets,
# GamblingIsAllYouNeed: 4 markets / $3.3M, debased: 11 markets / $735k).
BOTH_SIDES_MM_COUNT_THRESHOLD = 3
BOTH_SIDES_MIN_LEG_USD = 50.0  # dust filter — each leg must be ≥ $50

# v1.2: breadth-only MM detection. No human discretionary trader hits 20+
# distinct markets per day — that rate alone identifies automation regardless
# of two-sided / arb ratios.
BREADTH_ONLY_MM_MARKETS_PER_DAY = 20.0


@dataclass(frozen=True)
class ClassificationResult:
    wallet_class: str
    confidence: float
    features: dict[str, Any]


def compute_features(
    trades: list[Trade],
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract behavioral features from a wallet's recent trades + current
    open positions.

    All trade-based features are bounded ratios or counts so the classifier
    is robust to volume scale (a $10k whale and a $10M whale produce
    comparable values). Position-state features (v1.2) catch wallets running
    both-sides books that trade-history features miss — see module docstring.
    """
    valid = [t for t in trades if t.timestamp is not None]
    n = len(valid)
    if n == 0:
        return {"n_trades": 0, **_compute_position_features(positions)}

    # Order-stable by timestamp (ties broken by tx hash for determinism)
    valid.sort(key=lambda t: (t.timestamp, t.transaction_hash or ""))

    # ---- Two-sided ratio: BUY then SELL same asset within MM_PAIR_WINDOW_MINUTES,
    # with matched sizes (within MM_PAIR_SIZE_TOLERANCE). v1.1 tightened both
    # the time window (1h -> 10min) and added size matching to stop mistaking
    # directional scale-outs for market-making round trips.
    by_asset: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in valid:
        by_asset[(t.condition_id, t.asset)].append(t)

    pair_window = timedelta(minutes=MM_PAIR_WINDOW_MINUTES)
    two_sided_pair_trades = 0
    for asset_trades in by_asset.values():
        buys = [t for t in asset_trades if t.side == "BUY"]
        sells = [t for t in asset_trades if t.side == "SELL"]
        used = set()
        for b in buys:
            b_size = b.usdc_size or 0.0
            for i, s in enumerate(sells):
                if i in used:
                    continue
                if not (s.timestamp >= b.timestamp
                        and (s.timestamp - b.timestamp) <= pair_window):
                    continue
                s_size = s.usdc_size or 0.0
                # Size match check — both legs must be present and within
                # tolerance. Partial scale-outs (much smaller SELL than BUY)
                # don't count.
                if b_size <= 0 or s_size <= 0:
                    continue
                ratio = max(b_size, s_size) / max(min(b_size, s_size), 1e-9)
                if ratio > 1.0 + MM_PAIR_SIZE_TOLERANCE:
                    continue
                two_sided_pair_trades += 2  # BUY + SELL each count once
                used.add(i)
                break
    two_sided_ratio = two_sided_pair_trades / n

    # ---- Cross-leg arbitrage: trades on YES and NO of same condition within 5m ----
    by_condition: dict[str, list[Trade]] = defaultdict(list)
    for t in valid:
        by_condition[t.condition_id].append(t)

    arb_trades = 0
    for cond_trades in by_condition.values():
        # Bucket by 5-minute windows; if both YES-asset and NO-asset hit the
        # same bucket it's a cross-leg pair.
        bucket_assets: dict[int, set[str]] = defaultdict(set)
        bucket_trades: dict[int, list[Trade]] = defaultdict(list)
        for t in cond_trades:
            b = int(t.timestamp.timestamp()) // 300  # 5-minute bucket
            bucket_assets[b].add(t.asset)
            bucket_trades[b].append(t)
        for b, assets in bucket_assets.items():
            if len(assets) >= 2:
                arb_trades += len(bucket_trades[b])
    cross_leg_arb_ratio = arb_trades / n

    # ---- Median trade size (USDC notional) ----
    sizes = sorted(
        t.usdc_size for t in valid
        if t.usdc_size is not None and t.usdc_size > 0
    )
    median_trade_size_usdc = sizes[len(sizes) // 2] if sizes else 0.0

    # ---- Distinct markets per active day ----
    first = valid[0].timestamp
    last = valid[-1].timestamp
    span_days = max(1.0, (last - first).total_seconds() / 86_400)
    distinct_markets = len({t.condition_id for t in valid})
    markets_per_day = distinct_markets / span_days

    # ---- BUY/SELL skew (very directional traders mostly BUY) ----
    n_buys = sum(1 for t in valid if t.side == "BUY")
    buy_share = n_buys / n

    return {
        "n_trades": n,
        "two_sided_ratio": round(two_sided_ratio, 4),
        "cross_leg_arb_ratio": round(cross_leg_arb_ratio, 4),
        "median_trade_size_usdc": round(median_trade_size_usdc, 2),
        "distinct_markets_per_day": round(markets_per_day, 4),
        "buy_share": round(buy_share, 4),
        "span_days": round(span_days, 2),
        **_compute_position_features(positions),
    }


def _compute_position_features(
    positions: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """v1.2: position-state features. Catches both-sides-book wallets.

    `position_both_sides_count` — number of distinct condition_ids where the
    wallet currently holds both YES and NO with at least
    `BOTH_SIDES_MIN_LEG_USD` of current value on EACH leg. Dust filter
    excludes near-resolved decayed positions and inconsequential rounding.

    `distinct_conditions_held` — total distinct condition_ids in the wallet's
    open positions (after the same dust filter). For denominator context in
    forensic inspection.
    """
    if not positions:
        return {"position_both_sides_count": 0, "distinct_conditions_held": 0}
    by_condition: dict[str, set[str]] = defaultdict(set)
    for p in positions:
        cv = p.get("current_value")
        if cv is None or float(cv) < BOTH_SIDES_MIN_LEG_USD:
            continue
        cid = str(p.get("condition_id") or "")
        outcome = str(p.get("outcome") or "")
        if not cid or not outcome:
            continue
        by_condition[cid].add(outcome)
    distinct = len(by_condition)
    both_sides = sum(1 for sides in by_condition.values() if len(sides) >= 2)
    return {
        "position_both_sides_count": both_sides,
        "distinct_conditions_held": distinct,
    }


def classify(features: dict[str, Any]) -> ClassificationResult:
    """Apply the rule-based classifier. Order matters — first matching rule wins.

    Rule order (v1.2):
      1. cross_leg_arb_ratio > 0.30                 → arbitrage
      2. position_both_sides_count >= 3             → market_maker  (NEW)
      3. distinct_markets_per_day >= 20             → market_maker  (NEW)
      4. two_sided_ratio > 0.40 + markets_per_day>=0.5 → market_maker
      5. default                                    → directional

    Rules 2 + 3 added to catch wallets that trade-history features miss:
    rule 2 catches slow hedgers running both-sides books (no SELL trades to
    pair), rule 3 catches breadth-only bots (no human checks 20+ markets/day).
    Features median_trade_size_usdc, buy_share, span_days,
    distinct_conditions_held are computed and persisted for forensic
    inspection but don't drive the rule.
    """
    n = int(features.get("n_trades", 0) or 0)
    if n < MIN_TRADES_TO_CLASSIFY:
        return ClassificationResult("unknown", 0.5, features)

    arb = float(features.get("cross_leg_arb_ratio", 0) or 0)
    two_sided = float(features.get("two_sided_ratio", 0) or 0)
    markets_per_day = float(features.get("distinct_markets_per_day", 0) or 0)
    both_sides_positions = int(features.get("position_both_sides_count", 0) or 0)

    if arb > ARB_CROSS_LEG_RATIO_THRESHOLD:
        # Confidence rises with arb intensity but caps at 0.95
        return ClassificationResult(
            "arbitrage", min(0.95, 0.55 + arb * 0.5), features,
        )

    # v1.2 — Position-state MM: the strongest single signal for hedgers/MMs.
    # A wallet currently sitting on 3+ markets with both YES and NO held
    # (each leg ≥ $50) is not running a discretionary book.
    if both_sides_positions >= BOTH_SIDES_MM_COUNT_THRESHOLD:
        return ClassificationResult("market_maker", 0.85, features)

    # v1.2 — Breadth-only MM: 20+ distinct markets per day is automation.
    if markets_per_day >= BREADTH_ONLY_MM_MARKETS_PER_DAY:
        return ClassificationResult("market_maker", 0.80, features)

    # Existing MM rule: high two-sided ratio AND active across many markets.
    # The markets_per_day floor is the second guardrail against scale-out
    # false positives — a directional trader scaling out of one big position
    # has high two_sided_ratio but trades few markets. A real MM works dozens.
    if two_sided > MM_TWO_SIDED_RATIO_THRESHOLD and markets_per_day >= MM_MIN_MARKETS_PER_DAY:
        return ClassificationResult(
            "market_maker", min(0.95, 0.55 + two_sided * 0.5), features,
        )

    # Directional — confidence depends on sample size
    confidence = 0.75 if n >= DIRECTIONAL_HIGH_CONFIDENCE_TRADES else 0.55
    return ClassificationResult("directional", confidence, features)
