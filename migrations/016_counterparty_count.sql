-- Migration 016 -- counterparty_count INTEGER on signal_log (R4 + R7, Pass 3)
--
-- Pre-fix counterparty was a binary BOOLEAN flag set by the fills-based
-- check (counterparty_warning column from migration 008). Two problems:
--
--   R4: a top trader who already held YES and sold some YES to take
--       profit was flagged as counterparty -- but partial profit-takers
--       are NOT adversaries (they're still long net-YES, just less so).
--       The warning fired on essentially every winning trending market,
--       making it noise that the user learns to ignore.
--
--   R7: the fills-based check had no time bound, so a wallet who exited
--       NO three weeks ago got flagged as a counterparty to a YES signal
--       fired today. Pure noise on quiet markets.
--
-- Pass 3 rewrite: positions-based check. For each signal, query the
-- positions table for wallets in the tracked pool. A wallet is counted
-- as a counterparty IFF:
--   - It currently holds an opposing-side position >= $5k (R4 fix:
--     ignore partial profit-takers whose residual same-side position
--     dominates -- captured via concentration ratio)
--   - The opposing position is >= 75% of their total position on this
--     market (concentration test -- net-confused hedgers don't trip)
--
-- This column persists the COUNT of qualifying counterparty wallets so
-- the UI can tier the warning ("1 top trader on opposite side" mild vs
-- "3+ on opposite side" strong) instead of binary on/off.
--
-- The legacy counterparty_warning BOOLEAN stays for backward compat;
-- new code reads counterparty_count instead. It can be derived as
-- (counterparty_count > 0) for any UI still on the old column.

ALTER TABLE signal_log
    ADD COLUMN IF NOT EXISTS counterparty_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN signal_log.counterparty_count IS
    'R4+R7: number of tracked-pool wallets currently holding opposing-side '
    'positions on this market that meet both >=$5k absolute and >=75% '
    'concentration thresholds. 0 = no contested positioning. NULL not '
    'allowed; 0 means "checked, none found."';
