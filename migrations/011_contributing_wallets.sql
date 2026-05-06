-- Migration 011 — signal_log.contributing_wallets (R3b, Pass 3)
--
-- Persists the list of proxy_wallet addresses that contributed to a fired
-- signal at the moment it was first detected. Without this, the exit
-- detector recomputes "current trader_count + dollars" against the CURRENT
-- top-N pool — which causes false exits when wallets temporarily drop off
-- the leaderboard (their positions get cleaned up by the dropout sweep,
-- making the recompute see fewer dollars than peak even though no smart
-- money has actually exited).
--
-- With this column, exit_detector queries positions for the historical
-- contributing wallets specifically. A wallet that fell from rank 50 to
-- rank 105 is still checked — if they still hold the position, they still
-- count toward "current."
--
-- Also enables UI display of contributors per signal ("Contributing top
-- traders: Theo4Trump, PolyWhale1, ...") and the operator's workflow of
-- manually pinning interesting wallets to the insider list.
--
-- TEXT[] is nullable so existing pre-Pass-3 rows aren't disturbed; new code
-- populates the field on insert via crud.upsert_signal_log_entry.

ALTER TABLE signal_log
    ADD COLUMN IF NOT EXISTS contributing_wallets TEXT[];

COMMENT ON COLUMN signal_log.contributing_wallets IS
    'R3b: proxy_wallet addresses contributing to this signal at first-fire '
    'time. Used by exit_detector to recompute aggregates against the original '
    'cohort (not the current top-N pool, which churns). NULL for rows '
    'inserted before Pass 3.';

-- GIN index for fast "does this wallet appear in any signal" lookups,
-- useful for the UI signal-detail view and trader-drilldown features.
CREATE INDEX IF NOT EXISTS idx_signal_log_contributing_wallets
    ON signal_log USING GIN (contributing_wallets);
