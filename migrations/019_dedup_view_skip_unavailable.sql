-- Migration 019 — Pass 5 #9 — dedup view skips unavailable first-fires
--
-- The original vw_signals_unique_market (migration 007) picked the
-- earliest fire per (condition_id, direction) regardless of whether
-- the order book was readable at that moment. The backtest engine then
-- filtered WHERE signal_entry_source != 'unavailable' AFTER the dedup,
-- dropping the entire (cid, direction) pair when the canonical row had
-- a glitched book — even if a later re-fire of the same market was
-- clean. This non-randomly drops re-fired markets, which correlate
-- with stronger signals.
--
-- The fix: filter unavailable rows BEFORE the DISTINCT ON, so dedup
-- picks the earliest *executable* fire. Markets where every fire was
-- unavailable are absent from the view (correct — we couldn't have
-- entered them anyway).
--
-- Column list preserved from migration 007 verbatim so downstream
-- consumers (backtest engine) require no code change. The lens
-- aggregation (`lens_count`, `lens_list`) still aggregates ALL fires
-- including unavailable ones — that's correct: the lenses that
-- detected the signal are a property of detection, not entry quality.

DROP VIEW IF EXISTS vw_signals_unique_market;

CREATE VIEW vw_signals_unique_market AS
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
    -- Pass 5 #9: filter unavailable BEFORE DISTINCT ON so the canonical
    -- row is the earliest *executable* fire, not the earliest fire
    -- regardless of book state.
    WHERE COALESCE(signal_entry_source, '') <> 'unavailable'
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

COMMENT ON VIEW vw_signals_unique_market IS
    'Dedup canonical-row view per (condition_id, direction). Pass 5 #9: '
    'unavailable first-fires are filtered BEFORE the DISTINCT ON so the '
    'view picks the earliest executable fire, not the earliest fire '
    'period. Markets where every fire was unavailable are absent (correct).';
