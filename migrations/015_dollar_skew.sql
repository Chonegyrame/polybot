-- Migration 015 -- first_net_dollar_skew on signal_log + watchlist_signals (R2, Pass 3)
--
-- Pre-fix: signal eligibility used headcount-only skew (trader_count /
-- traders_any_direction). 6 minnows on YES + 1 whale on NO would fire YES
-- even though dollar consensus was 99% NO. R2 adds dual-axis: BOTH
-- headcount AND dollar-weighted skew must clear MIN_NET_DIRECTION_SKEW
-- (now 0.65, was 0.60) for the signal to fire.
--
-- This migration adds the dollar-skew columns so analytics + UI can show
-- both numbers per signal. Both nullable for legacy rows -- new code
-- populates on insert.

ALTER TABLE signal_log
    ADD COLUMN IF NOT EXISTS first_net_dollar_skew NUMERIC(5, 4);

COMMENT ON COLUMN signal_log.first_net_dollar_skew IS
    'R2: USDC-weighted direction skew at first-fire time. NULL for pre-Pass-3 rows.';

ALTER TABLE watchlist_signals
    ADD COLUMN IF NOT EXISTS dollar_skew NUMERIC(5, 4);

COMMENT ON COLUMN watchlist_signals.dollar_skew IS
    'R2: USDC-weighted direction skew. NULL for pre-Pass-3 rows.';
