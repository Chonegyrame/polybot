-- Migration 006 — trader_category_stats indexes for Phase B5
--
-- Note: the table itself was created in migration 002 with columns
-- `category_pnl_usdc` / `category_volume_usdc` / `category_roi` /
-- `resolved_trades` / `last_trade_at`. This migration only adds the indexes
-- B5 needs for fast filter lookups (recency + sample size).
--
-- The CREATE TABLE IF NOT EXISTS below is a no-op when the table exists
-- (the case in this codebase), but is preserved as documentation of the
-- expected shape so a fresh-DB bootstrap still works. The crud helper
-- `upsert_trader_category_stats_bulk` writes against the existing 002 schema.

CREATE TABLE IF NOT EXISTS trader_category_stats (
    proxy_wallet         TEXT NOT NULL
                          REFERENCES traders(proxy_wallet) ON DELETE CASCADE,
    category             TEXT NOT NULL,
    category_pnl_usdc    NUMERIC(14, 2) NOT NULL DEFAULT 0,
    category_volume_usdc NUMERIC(14, 2) NOT NULL DEFAULT 0,
    category_roi         NUMERIC(8, 4)  NOT NULL DEFAULT 0,
    resolved_trades      INTEGER NOT NULL DEFAULT 0,
    last_trade_at        TIMESTAMPTZ,
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (proxy_wallet, category)
);

-- B5 lookup indexes for the new ranker filters.
CREATE INDEX IF NOT EXISTS idx_tcs_category_recent
    ON trader_category_stats (category, last_trade_at DESC);
CREATE INDEX IF NOT EXISTS idx_tcs_category_resolved
    ON trader_category_stats (category, resolved_trades DESC);
