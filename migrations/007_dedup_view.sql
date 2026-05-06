-- Migration 007 — Cross-mode dedup view (Phase B6)
--
-- Collapses signal_log to one row per (condition_id, direction) by picking
-- the FIRST-fired row across modes/categories and aggregating all the lenses
-- that detected the same market into `lens_count` + `lens_list`.
--
-- Why: a single market that fires under absolute/overall AND hybrid/politics
-- AND specialist/politics is ONE signal seen through 3 lenses, not 3
-- independent signals. Backtesting without dedup overcounts these.
--
-- The view is read-only and pure-SQL — no migration of existing data needed.
-- Backtest engine reads from it when `?dedup=true` is passed.

CREATE OR REPLACE VIEW vw_signals_unique_market AS
WITH first_fired AS (
    SELECT DISTINCT ON (condition_id, direction)
        id,
        condition_id, direction,
        mode, category, top_n,
        first_fired_at,
        peak_trader_count, peak_avg_portfolio_fraction,
        peak_aggregate_usdc, peak_net_skew,
        first_trader_count, first_avg_portfolio_fraction,
        first_aggregate_usdc, first_net_skew,
        first_top_trader_entry_price,
        signal_entry_offer, signal_entry_mid, signal_entry_spread_bps,
        signal_entry_source, signal_entry_captured_at,
        liquidity_at_signal_usdc, liquidity_tier,
        cluster_id, market_type
    FROM signal_log
    -- DISTINCT ON picks the row with the EARLIEST first_fired_at as the
    -- canonical entry per (cid, direction). Other rows for the same
    -- (cid, direction) are still aggregated for the lens fields below.
    ORDER BY condition_id, direction, first_fired_at ASC, id ASC
),
lenses AS (
    SELECT condition_id, direction,
           COUNT(DISTINCT (mode || '/' || category)) AS lens_count,
           ARRAY_AGG(
               DISTINCT (mode || '/' || category)
               ORDER BY (mode || '/' || category)
           ) AS lens_list
    FROM signal_log
    GROUP BY condition_id, direction
)
SELECT
    f.id, f.condition_id, f.direction,
    f.mode, f.category, f.top_n,
    f.first_fired_at,
    f.peak_trader_count, f.peak_avg_portfolio_fraction,
    f.peak_aggregate_usdc, f.peak_net_skew,
    f.first_trader_count, f.first_avg_portfolio_fraction,
    f.first_aggregate_usdc, f.first_net_skew,
    f.first_top_trader_entry_price,
    f.signal_entry_offer, f.signal_entry_mid, f.signal_entry_spread_bps,
    f.signal_entry_source, f.signal_entry_captured_at,
    f.liquidity_at_signal_usdc, f.liquidity_tier,
    f.cluster_id, f.market_type,
    l.lens_count, l.lens_list
FROM first_fired f
JOIN lenses l USING (condition_id, direction);
