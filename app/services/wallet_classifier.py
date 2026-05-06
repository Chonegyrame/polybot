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

CLASSIFIER_VERSION = "v1.1"

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


@dataclass(frozen=True)
class ClassificationResult:
    wallet_class: str
    confidence: float
    features: dict[str, Any]


def compute_features(trades: list[Trade]) -> dict[str, Any]:
    """Extract behavioral features from a wallet's recent trades.

    All features are bounded ratios or counts so the classifier is robust
    to volume scale (a $10k whale and a $10M whale produce comparable values).
    """
    valid = [t for t in trades if t.timestamp is not None]
    n = len(valid)
    if n == 0:
        return {"n_trades": 0}

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
    }


def classify(features: dict[str, Any]) -> ClassificationResult:
    """Apply the rule-based classifier. Order matters — first matching rule wins.

    Features used in the rule itself: cross_leg_arb_ratio, two_sided_ratio,
    distinct_markets_per_day. The remaining features (median_trade_size_usdc,
    buy_share, span_days) are computed and persisted for forensic inspection
    in `wallet_classifications.features` but don't drive the rule. They show
    up in the trader drill-down UI.
    """
    n = int(features.get("n_trades", 0) or 0)
    if n < MIN_TRADES_TO_CLASSIFY:
        return ClassificationResult("unknown", 0.5, features)

    arb = float(features.get("cross_leg_arb_ratio", 0) or 0)
    two_sided = float(features.get("two_sided_ratio", 0) or 0)
    markets_per_day = float(features.get("distinct_markets_per_day", 0) or 0)

    if arb > ARB_CROSS_LEG_RATIO_THRESHOLD:
        # Confidence rises with arb intensity but caps at 0.95
        return ClassificationResult(
            "arbitrage", min(0.95, 0.55 + arb * 0.5), features,
        )
    # MM rule: high two-sided ratio AND active across many markets. The
    # markets_per_day floor is the second guardrail against scale-out false
    # positives — a directional trader scaling out of one big position has
    # high two_sided_ratio but trades few markets. A real MM works dozens.
    if two_sided > MM_TWO_SIDED_RATIO_THRESHOLD and markets_per_day >= MM_MIN_MARKETS_PER_DAY:
        return ClassificationResult(
            "market_maker", min(0.95, 0.55 + two_sided * 0.5), features,
        )

    # Directional — confidence depends on sample size
    confidence = 0.75 if n >= DIRECTIONAL_HIGH_CONFIDENCE_TRADES else 0.55
    return ClassificationResult("directional", confidence, features)
