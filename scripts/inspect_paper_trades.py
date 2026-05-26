"""Read-only inspection: dump every paper trade joined with its signal_log
row and market metadata, so we can eyeball what criteria the user has been
implicitly selecting on.

Usage:
    ./venv/Scripts/python.exe scripts/inspect_paper_trades.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402


SQL = """
WITH matched AS (
    -- For each paper trade, pick the signal_log row with the largest
    -- peak_aggregate_usdc on the same (cid, direction). Mirrors the
    -- "canonical representative" convention used in list_lost_signals
    -- and list_recent_signals.
    SELECT DISTINCT ON (pt.id)
        pt.id AS pt_id,
        sl.id AS sl_id
    FROM paper_trades pt
    LEFT JOIN signal_log sl
           ON sl.condition_id = pt.condition_id
          AND sl.direction    = pt.direction
    ORDER BY pt.id,
             sl.peak_aggregate_usdc DESC NULLS LAST,
             sl.first_fired_at ASC
)
SELECT
    pt.id                       AS trade_id,
    pt.status,
    pt.entry_at,
    pt.exit_at,
    pt.direction                AS pt_direction,
    pt.entry_price::float       AS pt_entry_price,
    pt.entry_size_usdc::float   AS pt_entry_size_usdc,
    pt.exit_price::float        AS pt_exit_price,
    pt.realized_pnl_usdc::float AS pt_realized_pnl,
    pt.exit_reason,
    pt.signal_log_id            AS linked_signal_log_id,
    pt.condition_id,

    -- signal_log: criteria captured at fire time (matched via CTE,
    -- since paper_trades.signal_log_id was never populated for these rows)
    sl.id        AS matched_signal_log_id,
    sl.mode, sl.category, sl.top_n,
    sl.first_fired_at,
    sl.last_seen_at,
    sl.first_trader_count,
    sl.first_aggregate_usdc::float    AS first_aggregate_usdc,
    sl.first_net_skew::float          AS first_net_skew,
    sl.first_avg_portfolio_fraction::float AS first_avg_portfolio_fraction,
    sl.peak_trader_count,
    sl.peak_aggregate_usdc::float     AS peak_aggregate_usdc,
    sl.signal_entry_offer::float      AS signal_entry_offer,
    sl.signal_entry_spread_bps,
    sl.signal_entry_source,
    sl.liquidity_at_signal_usdc::float AS liquidity_at_signal_usdc,
    sl.liquidity_tier,
    sl.market_type,
    sl.first_top_trader_entry_price::float AS smart_money_entry_price,

    -- market metadata
    m.question AS market_question,
    m.slug     AS market_slug,
    m.closed   AS market_closed,
    m.resolved_outcome,
    m.end_date,
    e.category AS market_category

FROM paper_trades pt
LEFT JOIN matched   x  ON x.pt_id = pt.id
LEFT JOIN signal_log sl ON sl.id = x.sl_id
LEFT JOIN markets    m  ON m.condition_id = pt.condition_id
LEFT JOIN events     e  ON e.id = m.event_id
ORDER BY pt.entry_at ASC
"""


def _fmt_usd(n):
    if n is None:
        return "—"
    if abs(n) >= 1000:
        return f"${n/1000:,.1f}k"
    return f"${n:,.2f}"


def _pct(n, decimals=1):
    if n is None:
        return "—"
    return f"{n*100:.{decimals}f}%"


def _bucket(values, label, fmt=str):
    """Print a tiny stats line for a numeric column."""
    clean = [v for v in values if v is not None]
    if not clean:
        print(f"  {label:30s}  n/a (all null)")
        return
    s = sorted(clean)
    mid = s[len(s) // 2]
    print(f"  {label:30s}  min={fmt(s[0])}  median={fmt(mid)}  max={fmt(s[-1])}  n={len(clean)}")


def _bar(count, total, width=20):
    if total == 0:
        return "·" * width
    filled = round((count / total) * width)
    return "█" * filled + "·" * (width - filled)


async def main() -> int:
    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = [dict(r) for r in await conn.fetch(SQL)]
    finally:
        await close_pool()

    if not rows:
        print("No paper trades found.")
        return 0

    print(f"\n=== {len(rows)} paper trades ===\n")

    # ---------- per-trade table ----------
    print(f"{'#':>2} {'status':>14} {'mode':>10} {'category':>10} {'topN':>4} "
          f"{'dir':>3} {'tr':>3} {'agg':>7} {'skew':>5} {'pfrac':>6} "
          f"{'spread':>6} {'liq':>5} {'entry':>5} {'pnl':>8} {'question':<60}")
    print("-" * 175)

    wins = losses = open_count = 0
    pnl_total = 0.0
    by_mode_pnl: dict[str, list[float]] = {}
    by_cat_pnl: dict[str, list[float]] = {}
    by_dir_pnl: dict[str, list[float]] = {}
    trader_counts_pnl: list[tuple[int, float | None]] = []
    aggs_pnl: list[tuple[float, float | None]] = []
    spreads_pnl: list[tuple[int, float | None]] = []
    skews_pnl: list[tuple[float, float | None]] = []
    pfrac_pnl: list[tuple[float, float | None]] = []
    entry_offer_pnl: list[tuple[float, float | None]] = []

    for i, r in enumerate(rows, 1):
        pnl = r["pt_realized_pnl"]
        status = r["status"]
        if status == "open":
            open_count += 1
        elif pnl is not None and pnl > 0:
            wins += 1
        elif pnl is not None and pnl <= 0:
            losses += 1
        if pnl is not None:
            pnl_total += pnl

        q = (r["market_question"] or r["condition_id"] or "?")[:58]
        print(
            f"{i:>2} "
            f"{status:>14} "
            f"{(r['mode'] or '—'):>10} "
            f"{(r['category'] or '—'):>10} "
            f"{(r['top_n'] or 0):>4} "
            f"{r['pt_direction']:>3} "
            f"{(r['first_trader_count'] or 0):>3} "
            f"{_fmt_usd(r['first_aggregate_usdc']):>7} "
            f"{_pct(r['first_net_skew'], 0):>5} "
            f"{_pct(r['first_avg_portfolio_fraction'], 1):>6} "
            f"{(r['signal_entry_spread_bps'] or 0):>5}b "
            f"{(r['liquidity_tier'] or '—'):>5} "
            f"${(r['signal_entry_offer'] or 0):>4.2f} "
            f"{_fmt_usd(pnl):>8} "
            f"{q:<60}"
        )

        # Accumulate for stats
        if r["mode"]:
            by_mode_pnl.setdefault(r["mode"], []).append(pnl or 0.0)
        if r["market_category"] or r["category"]:
            cat = r["market_category"] or r["category"]
            by_cat_pnl.setdefault(cat, []).append(pnl or 0.0)
        by_dir_pnl.setdefault(r["pt_direction"], []).append(pnl or 0.0)
        if r["first_trader_count"] is not None:
            trader_counts_pnl.append((r["first_trader_count"], pnl))
        if r["first_aggregate_usdc"] is not None:
            aggs_pnl.append((r["first_aggregate_usdc"], pnl))
        if r["signal_entry_spread_bps"] is not None:
            spreads_pnl.append((r["signal_entry_spread_bps"], pnl))
        if r["first_net_skew"] is not None:
            skews_pnl.append((r["first_net_skew"], pnl))
        if r["first_avg_portfolio_fraction"] is not None:
            pfrac_pnl.append((r["first_avg_portfolio_fraction"], pnl))
        if r["signal_entry_offer"] is not None:
            entry_offer_pnl.append((r["signal_entry_offer"], pnl))

    # ---------- summary ----------
    print(f"\n=== Summary ===")
    resolved = wins + losses
    print(f"  trades        {len(rows)}  (resolved {resolved}, open {open_count})")
    if resolved:
        print(f"  win rate      {wins}/{resolved} = {wins/resolved*100:.0f}%")
    print(f"  total PnL     {_fmt_usd(pnl_total)}")

    # ---------- criteria distributions (overall, not split by outcome yet) ----------
    print(f"\n=== Criteria the user has been picking ===")
    _bucket([r["first_trader_count"] for r in rows], "first_trader_count", str)
    _bucket([r["first_aggregate_usdc"] for r in rows], "first_aggregate_usdc", _fmt_usd)
    _bucket([r["first_net_skew"] for r in rows], "first_net_skew", lambda n: f"{n*100:.0f}%")
    _bucket([r["first_avg_portfolio_fraction"] for r in rows], "first_avg_portfolio_fraction", lambda n: f"{n*100:.1f}%")
    _bucket([r["signal_entry_spread_bps"] for r in rows], "signal_entry_spread_bps", lambda n: f"{n}bps")
    _bucket([r["signal_entry_offer"] for r in rows], "signal_entry_offer", lambda n: f"${n:.2f}")
    _bucket([r["peak_aggregate_usdc"] for r in rows], "peak_aggregate_usdc", _fmt_usd)

    # ---------- mode/category/direction breakdown ----------
    def _agg_show(d, label):
        print(f"\n  {label}:")
        for k, vals in sorted(d.items(), key=lambda kv: -sum(kv[1])):
            n = len(vals)
            total = sum(vals)
            print(f"    {k:>14}  n={n:>2}  total_pnl={_fmt_usd(total):>9}  avg={_fmt_usd(total/n) if n else '—':>8}")

    _agg_show(by_mode_pnl, "by mode (which leaderboard mode was the signal on)")
    _agg_show(by_cat_pnl, "by market category")
    _agg_show(by_dir_pnl, "by direction (YES vs NO)")

    # ---------- winner-vs-loser deltas on each criterion ----------
    print(f"\n=== Winner vs loser criteria (resolved trades only) ===")
    if resolved >= 3:
        def _split(pairs):
            ws = [v for v, p in pairs if p is not None and p > 0]
            ls = [v for v, p in pairs if p is not None and p <= 0]
            return ws, ls

        def _median(xs):
            return statistics.median(xs) if xs else None

        for label, pairs, fmt in [
            ("first_trader_count",       trader_counts_pnl, str),
            ("first_aggregate_usdc",     aggs_pnl,          _fmt_usd),
            ("first_net_skew",           skews_pnl,         lambda n: f"{n*100:.0f}%"),
            ("first_avg_portfolio_frac", pfrac_pnl,         lambda n: f"{n*100:.2f}%"),
            ("spread_bps",               spreads_pnl,       lambda n: f"{n}bps"),
            ("signal_entry_offer",       entry_offer_pnl,   lambda n: f"${n:.2f}"),
        ]:
            ws, ls = _split(pairs)
            wm, lm = _median(ws), _median(ls)
            wm_s = fmt(wm) if wm is not None else "—"
            lm_s = fmt(lm) if lm is not None else "—"
            print(f"  {label:30s}  winners median={wm_s:>10}  losers median={lm_s:>10}  (n_w={len(ws)} n_l={len(ls)})")
    else:
        print(f"  Not enough resolved trades yet (need >=3, have {resolved}).")

    # ---------- gap to smart-money entry (did we chase?) ----------
    print(f"\n=== Did you chase smart money? (entry vs smart-money basis) ===")
    chases = []
    for r in rows:
        sm = r["smart_money_entry_price"]
        e = r["pt_entry_price"] or r["signal_entry_offer"]
        if sm and e and sm > 0:
            gap = e / sm - 1.0
            chases.append((gap, r["pt_realized_pnl"], r["market_question"]))
    if chases:
        chases.sort(key=lambda x: x[0])
        _bucket([g for g, _, _ in chases], "entry / smart_money_basis - 1", lambda n: f"{n*100:+.1f}%")
        print(f"  (positive = you paid more than smart money; negative = you got in cheaper)")
    else:
        print(f"  No paper trades with both entry_price and smart_money_entry_price.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
