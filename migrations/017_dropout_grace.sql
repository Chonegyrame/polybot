-- Migration 017 -- traders.dropout_count for R13 grace period (Pass 3)
--
-- Pre-fix: phase 4 of refresh_top_trader_positions immediately deletes
-- positions of any wallet not in the current top-N pool. One bad day
-- (rank 100 -> 105) wipes their entire position history; if they bounce
-- back next cycle, we have to rebuild from scratch.
--
-- R13: require N consecutive cycles of dropout before deletion. Each
-- cycle:
--   - Wallets in current pool: dropout_count reset to 0
--   - Wallets NOT in current pool: dropout_count incremented by 1
--   - Wallets with dropout_count >= R13_GRACE_CYCLES: positions deleted
--
-- Grace cycles are 10 min apart, so 3 cycles = 30 min of leniency.
-- Normal rank-jiggling around top-N edge stops causing data loss.

ALTER TABLE traders
    ADD COLUMN IF NOT EXISTS dropout_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN traders.dropout_count IS
    'R13 (Pass 3): consecutive position-refresh cycles this wallet has '
    'been outside the tracked top-N pool. Reset to 0 when re-entering. '
    'When it reaches R13_GRACE_CYCLES (3, == 30 min), the dropout sweep '
    'deletes their stale positions.';

CREATE INDEX IF NOT EXISTS idx_traders_dropout_count
    ON traders (dropout_count) WHERE dropout_count > 0;
