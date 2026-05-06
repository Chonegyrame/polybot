-- Migration 012 — signal_price_snapshots.direction (R8, Pass 3)
--
-- Pre-fix: the price-snapshot job only fetched the YES-token orderbook,
-- regardless of signal direction. For NO signals, half-life math then
-- translated via _to_yes_space (assuming bid(NO) + ask(YES) = 1) — but
-- YES and NO books are set independently by different market makers and
-- their spreads do NOT mirror exactly. Half-life numbers for NO signals
-- were systematically biased, especially on extreme markets where one
-- side is much more illiquid than the other.
--
-- Post-fix: the snapshot job fetches the DIRECTION-side token (NO token
-- for NO signals, YES token for YES signals) and stores prices in
-- direction-space natively. The translation step is no longer needed.
--
-- This column records which direction the snapshot was captured for, so
-- legacy YES-only rows (NULL direction) can be distinguished from new
-- direction-aware rows.

ALTER TABLE signal_price_snapshots
    ADD COLUMN IF NOT EXISTS direction TEXT;

ALTER TABLE signal_price_snapshots
    ADD CONSTRAINT signal_price_snapshots_direction_check
    CHECK (direction IS NULL OR direction IN ('YES', 'NO'));

COMMENT ON COLUMN signal_price_snapshots.direction IS
    'R8: which side of the market this snapshot was captured for. '
    'New rows always populate this; NULL = legacy row predating Pass 3 '
    '(those rows used the YES token regardless of signal direction).';
