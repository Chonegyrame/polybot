import asyncio, os, sys, json
sys.path.insert(0, r"C:\Users\gusta\OneDrive\Dokument\ai agency codex\polymarket")
from app.db.connection import init_pool, close_pool

ACTIVITY_SQL = """
SELECT
  (SELECT MAX(last_updated_at)::text FROM positions) AS positions_max_updated,
  (SELECT MAX(fetched_at)::text FROM portfolio_value_snapshots) AS pv_max_fetched,
  (SELECT MAX(last_seen_at)::text FROM signal_log) AS signal_log_max_seen,
  (SELECT MAX(last_synced_at)::text FROM markets) AS markets_max_synced,
  (SELECT COUNT(*) FROM positions WHERE last_updated_at::date = CURRENT_DATE) AS positions_today,
  (SELECT COUNT(*) FROM portfolio_value_snapshots WHERE fetched_at::date = CURRENT_DATE) AS pv_today,
  (SELECT COUNT(*) FROM signal_log WHERE last_seen_at::date = CURRENT_DATE) AS signals_today
"""

VOLUME_SQL = """
SELECT
  (SELECT COUNT(*) FROM markets) AS markets_total,
  (SELECT COUNT(*) FROM markets WHERE closed = FALSE) AS markets_open,
  (SELECT COUNT(*) FROM markets WHERE closed = TRUE) AS markets_closed,
  (SELECT COUNT(*) FROM positions) AS positions_total,
  (SELECT COUNT(DISTINCT proxy_wallet) FROM positions) AS positions_distinct_wallets,
  (SELECT COUNT(DISTINCT condition_id) FROM positions) AS positions_distinct_markets,
  (SELECT COUNT(*) FROM traders) AS traders_total,
  (SELECT COUNT(*) FROM signal_log) AS signal_log_total,
  (SELECT COUNT(*) FROM events) AS events_total
"""

PASS3_SQL = """
SELECT
  (SELECT COUNT(*) FROM signal_log WHERE contributing_wallets IS NOT NULL) AS sl_contrib_wallets_nonnull,
  (SELECT COUNT(*) FROM signal_log WHERE counterparty_count > 0) AS sl_counterparty_gt0,
  (SELECT COUNT(*) FROM signal_log WHERE first_net_dollar_skew IS NOT NULL) AS sl_dollar_skew_nonnull,
  (SELECT COUNT(*) FROM signal_price_snapshots WHERE direction IS NOT NULL) AS sps_direction_nonnull,
  (SELECT COUNT(*) FROM traders WHERE dropout_count > 0) AS tr_dropout_gt0,
  (SELECT COUNT(*) FROM watchlist_signals WHERE dollar_skew IS NOT NULL) AS ws_dollar_skew_nonnull,
  (SELECT COUNT(*) FROM signal_exits WHERE event_type IN ('trim','exit')) AS se_trim_or_exit
"""

async def main():
    pool = await init_pool()
    async with pool.acquire() as conn:
        a = dict(await conn.fetchrow(ACTIVITY_SQL))
        v = dict(await conn.fetchrow(VOLUME_SQL))
        p = dict(await conn.fetchrow(PASS3_SQL))
    await close_pool()
    print("=== ACTIVITY ===")
    print(json.dumps(a, indent=2, default=str))
    print("=== VOLUME ===")
    print(json.dumps(v, indent=2, default=str))
    print("=== PASS 3 COLUMNS ===")
    print(json.dumps(p, indent=2, default=str))

asyncio.run(main())
