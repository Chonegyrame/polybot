"""Sybil cluster detection — finds wallet groups that systematically trade
the same markets at the same time.

Use case: the famous "Théo / Fredi9999" case ran 4 wallets that all entered
the same election markets within seconds of each other. Naive counting
treats them as 4 distinct top traders and inflates the consensus signal.
This module clusters them so the signal detector deduplicates.

Method (v2, time-correlation only — no Polygon RPC):
  1. For each wallet in the pool, fetch recent trades.
  2. Bucket trades on TWO overlapping grids: regular (`ts // 60`) and
     offset (`(ts - 30) // 60`). A trade at t=59 and another at t=61 land
     in different regular buckets but share the offset grid — Scope 2's
     sliding window catches the boundary cases v1 missed.
  3. Within each bucket, every pair of wallets present is a "co-entry";
     additionally, any bucket with ≥3 wallets is a candidate "group co-entry".
  4. For each wallet pair, co_entry_rate = co_entry_count / min(trades_a, trades_b).
     Pairs above SYBIL_CO_ENTRY_THRESHOLD become edges.
  5. Group co-entry: any frozenset of ≥3 wallets that share at least
     SYBIL_GROUP_MIN_BUCKETS distinct buckets becomes a clique-of-edges.
     Triples are statistically rarer than pairs by chance, so a lower
     absolute count is sufficient evidence.
  6. Union-find merges all edges into clusters.
  7. Clusters of size ≥2 are persisted.

Cost: O(total_trades + bucket_events²) where most buckets contain 0-2
wallets, so effectively linear. Dual-grid doubles the constant factor
but stays in seconds, not minutes.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from app.services.polymarket_types import Trade

log = logging.getLogger(__name__)

CO_ENTRY_BUCKET_SECONDS = 60
CO_ENTRY_OFFSET_SECONDS = 30  # half-period offset for the second grid
SYBIL_CO_ENTRY_THRESHOLD = 0.30  # min fraction of shared buckets to flag as pair
MIN_TRADES_FOR_CLUSTERING = 20   # wallets with fewer trades skipped (not enough signal)

# Group co-entry: a triple+ that shares at least this many buckets is flagged.
# Random triples co-occurring in a 60s window are vastly less likely than
# pairs, so the absolute-count threshold is much smaller than the pair rate.
SYBIL_GROUP_MIN_SIZE = 3
SYBIL_GROUP_MIN_BUCKETS = 5
# Cap how many wallets we expand combinatorially in a single bucket — a
# popular market may have 8+ top-N wallets active in one minute by sheer
# coincidence; expanding C(8, 3) = 56 trios per such bucket adds noise.
# Buckets larger than this are skipped for the group pass (the pair pass
# still picks them up).
SYBIL_GROUP_MAX_BUCKET_SIZE = 6


@dataclass(frozen=True)
class SybilCluster:
    members: list[str]                    # sorted proxy_wallet addresses
    evidence: dict[str, Any]              # min/max/mean co_entry_rate, n_*_edges, etc.


# ---------------------------------------------------------------------------
# Pure-function core (testable without DB or API)
# ---------------------------------------------------------------------------


def _bucket_trades(
    wallet: str, trades: list[Trade]
) -> set[tuple[str, str, int, int]]:
    """Map a wallet's trades to (condition_id, asset, grid_id, bucket) keys.

    v2: dual-grid sliding windows. Each trade is emitted twice — once for
    the regular grid (`ts // 60`) and once for an offset grid (`(ts - 30)
    // 60`) — so a pair of trades at t=59s and t=61s, which would split
    across regular buckets, share the offset bucket. `grid_id` (0 or 1)
    namespaces the two grids so they don't collide when we count co-entries.

    Using a set so each (grid, bucket) counts at most once per wallet
    regardless of how many trades the wallet fired inside it.
    """
    out: set[tuple[str, str, int, int]] = set()
    for t in trades:
        if t.timestamp is None or not t.condition_id or not t.asset:
            continue
        ts = int(t.timestamp.timestamp())
        regular_bucket = ts // CO_ENTRY_BUCKET_SECONDS
        offset_bucket = (ts - CO_ENTRY_OFFSET_SECONDS) // CO_ENTRY_BUCKET_SECONDS
        out.add((t.condition_id, t.asset, 0, regular_bucket))
        out.add((t.condition_id, t.asset, 1, offset_bucket))
    return out


def detect_clusters(
    trades_by_wallet: dict[str, list[Trade]],
    threshold: float = SYBIL_CO_ENTRY_THRESHOLD,
) -> list[SybilCluster]:
    """Identify sybil clusters across a population.

    Pure function — takes wallet → trades dict, returns clusters. Easy to
    test with synthetic inputs. v2 (Scope 2) adds:
      - Sliding 60s windows via dual-grid bucketing (in `_bucket_trades`)
      - Group co-entry: any frozenset of ≥3 wallets sharing
        SYBIL_GROUP_MIN_BUCKETS distinct buckets becomes a clique-of-edges,
        even if no individual pair clears the per-pair rate threshold.
    """
    # Pre-bucket each wallet's trades
    buckets_by_wallet: dict[str, set[tuple[str, str, int, int]]] = {}
    for w, trades in trades_by_wallet.items():
        if len(trades) < MIN_TRADES_FOR_CLUSTERING:
            continue
        b = _bucket_trades(w, trades)
        if b:
            buckets_by_wallet[w] = b
    if len(buckets_by_wallet) < 2:
        return []

    # Build inverted index: bucket_key -> set of wallets in that bucket
    bucket_to_wallets: dict[tuple[str, str, int, int], set[str]] = defaultdict(set)
    for wallet, bset in buckets_by_wallet.items():
        for b in bset:
            bucket_to_wallets[b].add(wallet)

    # Count pair-co-occurrences per wallet pair AND group co-occurrences
    # for triples+ in a single pass over the index.
    co_count: dict[tuple[str, str], int] = defaultdict(int)
    group_count: Counter[frozenset[str]] = Counter()
    for wallets in bucket_to_wallets.values():
        n_wallets = len(wallets)
        if n_wallets < 2:
            continue
        ws = sorted(wallets)
        # Pair pass — always.
        for i in range(n_wallets):
            for j in range(i + 1, n_wallets):
                co_count[(ws[i], ws[j])] += 1
        # Group pass — only for buckets above the minimum group size.
        # Cap bucket size to avoid combinatorial blowup on busy markets.
        if SYBIL_GROUP_MIN_SIZE <= n_wallets <= SYBIL_GROUP_MAX_BUCKET_SIZE:
            for r in range(SYBIL_GROUP_MIN_SIZE, n_wallets + 1):
                for combo in combinations(ws, r):
                    group_count[frozenset(combo)] += 1

    # Edges from pair detection — rate above per-pair threshold.
    pair_edges: list[tuple[str, str, float]] = []
    for (a, b), n in co_count.items():
        denom = min(len(buckets_by_wallet[a]), len(buckets_by_wallet[b]))
        rate = n / denom if denom > 0 else 0.0
        if rate >= threshold:
            pair_edges.append((a, b, rate))

    # Edges from group detection — any triple+ that shares enough buckets.
    # Each group emits a clique: every pair within the group becomes an edge
    # tagged with rate=group_count/min_buckets so it shows up in evidence.
    group_edges: list[tuple[str, str, float]] = []
    flagged_groups: list[frozenset[str]] = []
    for grp, n in group_count.items():
        if n < SYBIL_GROUP_MIN_BUCKETS:
            continue
        flagged_groups.append(grp)
        denom = min(len(buckets_by_wallet[w]) for w in grp)
        # Effective rate (for evidence reporting only)
        rate = n / denom if denom > 0 else 0.0
        members = sorted(grp)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                group_edges.append((members[i], members[j], rate))

    if not pair_edges and not group_edges:
        return []

    all_edges = pair_edges + group_edges

    # Union-find for transitive closure (Théo's 4 wallets cluster as one).
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b, _ in all_edges:
        union(a, b)

    # Group wallets by root and emit clusters of size ≥2 with evidence.
    by_root: dict[str, list[str]] = defaultdict(list)
    for w in {x for e in all_edges for x in e[:2]}:
        by_root[find(w)].append(w)

    clusters: list[SybilCluster] = []
    for root, members in by_root.items():
        if len(members) < 2:
            continue
        member_set = set(members)
        pair_rates = [
            r for a, b, r in pair_edges if a in member_set and b in member_set
        ]
        group_buckets_for_cluster = [
            n for grp, n in group_count.items()
            if grp.issubset(member_set) and len(grp) >= SYBIL_GROUP_MIN_SIZE
            and n >= SYBIL_GROUP_MIN_BUCKETS
        ]
        n_pair_edges = len(pair_rates)
        n_group_flags = len(group_buckets_for_cluster)
        modes: list[str] = []
        if n_pair_edges > 0:
            modes.append("pair")
        if n_group_flags > 0:
            modes.append("group")
        evidence: dict[str, Any] = {
            "n_members": len(members),
            "n_pair_edges": n_pair_edges,
            "n_group_flags": n_group_flags,
            "detection_modes": modes,
        }
        if pair_rates:
            evidence["min_co_entry_rate"] = round(min(pair_rates), 4)
            evidence["max_co_entry_rate"] = round(max(pair_rates), 4)
            evidence["mean_co_entry_rate"] = round(sum(pair_rates) / len(pair_rates), 4)
        if group_buckets_for_cluster:
            evidence["max_group_shared_buckets"] = int(max(group_buckets_for_cluster))
        clusters.append(SybilCluster(members=sorted(members), evidence=evidence))
    return clusters
