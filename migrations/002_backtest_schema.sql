-- Step 8a: backtest schema upgrade
--
-- Adds:
--   1. Entry-time snapshot columns on signal_log (`first_*`) — replaces the
--      look-ahead-biased `peak_*` fields as the canonical inputs to backtest
--      filtering. The `peak_*` fields stay for diagnostic purposes.
--   2. Executable entry-price columns (`signal_entry_offer`, mid, spread,
--      liquidity) — what you'd actually pay if you copied the signal at
--      first_fired_at. Replaces the smart-money cost basis as the trade-pricing
--      reference.
--   3. Cluster + market-shape fields (`cluster_id`, `market_type`,
--      `resolution_disputed`) — needed for honest CIs and resolution handling.
--   4. New tables for wallet classification (MM/sybil filtering) + cluster
--      detection + per-category trader stats (Specialist mode) +
--      orderbook snapshots + paper-trades + slice-lookup audit.
--
-- All new columns are nullable so existing rows aren't invalidated. We backfill
-- the 11 pre-fix rows from `peak_*` at the bottom of this file.

-- ----------------------------------------------------------------------------
-- signal_log additions
-- ----------------------------------------------------------------------------

ALTER TABLE signal_log
    -- Entry-time snapshots (frozen at first_fired_at, never updated).
    ADD COLUMN IF NOT EXISTS first_trader_count            INTEGER,
    ADD COLUMN IF NOT EXISTS first_aggregate_usdc          NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS first_net_skew                NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS first_avg_portfolio_fraction  NUMERIC(8,6),

    -- Executable entry price + book depth at fire time.
    ADD COLUMN IF NOT EXISTS signal_entry_offer            NUMERIC(8,6),
    ADD COLUMN IF NOT EXISTS signal_entry_mid              NUMERIC(8,6),
    ADD COLUMN IF NOT EXISTS signal_entry_spread_bps       INTEGER,
    ADD COLUMN IF NOT EXISTS signal_entry_captured_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS signal_entry_source           TEXT,
    ADD COLUMN IF NOT EXISTS liquidity_at_signal_usdc      NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS liquidity_tier                TEXT,

    -- Cluster + market-shape.
    ADD COLUMN IF NOT EXISTS cluster_id                    TEXT,
    ADD COLUMN IF NOT EXISTS market_type                   TEXT NOT NULL DEFAULT 'binary',

    -- Resolution upgrades.
    ADD COLUMN IF NOT EXISTS resolution_disputed           BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS resolution_source             TEXT,
    ADD COLUMN IF NOT EXISTS resolution_captured_at        TIMESTAMPTZ;

-- Add CHECK constraints. Use DO blocks so re-running is idempotent.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_log_entry_source_check') THEN
        ALTER TABLE signal_log ADD CONSTRAINT signal_log_entry_source_check
            CHECK (signal_entry_source IS NULL OR signal_entry_source IN ('clob_l2','gamma_fallback','unavailable'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_log_liquidity_tier_check') THEN
        ALTER TABLE signal_log ADD CONSTRAINT signal_log_liquidity_tier_check
            CHECK (liquidity_tier IS NULL OR liquidity_tier IN ('thin','medium','deep','unknown'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_log_market_type_check') THEN
        ALTER TABLE signal_log ADD CONSTRAINT signal_log_market_type_check
            CHECK (market_type IN ('binary','neg_risk','scalar','conditional'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_log_resolution_outcome_check') THEN
        ALTER TABLE signal_log ADD CONSTRAINT signal_log_resolution_outcome_check
            CHECK (resolution_outcome IS NULL OR resolution_outcome IN ('YES','NO','50_50','VOID','PENDING'));
    END IF;
END $$;

-- Performance indices for the backtest query shapes.
CREATE INDEX IF NOT EXISTS idx_signal_log_cluster
    ON signal_log (cluster_id) WHERE cluster_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signal_log_resolved
    ON signal_log (resolution_outcome, resolved_at) WHERE resolution_outcome IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signal_log_first_fired
    ON signal_log (first_fired_at);
CREATE INDEX IF NOT EXISTS idx_signal_log_liq_tier
    ON signal_log (liquidity_tier);

-- ----------------------------------------------------------------------------
-- Wallet classification (MM/arb/directional/likely_sybil) — recomputed nightly
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet_classifications (
    proxy_wallet        TEXT PRIMARY KEY REFERENCES traders(proxy_wallet),
    wallet_class        TEXT NOT NULL
        CHECK (wallet_class IN ('directional','market_maker','arbitrage','likely_sybil','unknown')),
    confidence          NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    features            JSONB NOT NULL,
    trades_observed     INTEGER NOT NULL,
    classified_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    classifier_version  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wallet_class_class ON wallet_classifications (wallet_class);

-- ----------------------------------------------------------------------------
-- Sybil clusters — wallets that share funding source / time-correlate
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet_clusters (
    cluster_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_label       TEXT,
    detection_method    TEXT NOT NULL
        CHECK (detection_method IN ('funding_source','time_correlation','behavioral','manual')),
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence            JSONB
);

CREATE TABLE IF NOT EXISTS cluster_membership (
    cluster_id          UUID NOT NULL REFERENCES wallet_clusters(cluster_id) ON DELETE CASCADE,
    proxy_wallet        TEXT NOT NULL REFERENCES traders(proxy_wallet),
    joined_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cluster_id, proxy_wallet)
);

CREATE INDEX IF NOT EXISTS idx_cluster_membership_wallet ON cluster_membership (proxy_wallet);

-- ----------------------------------------------------------------------------
-- Per-category trader stats — feeds Specialist ranking mode
-- Refreshed nightly from /trades for top wallets
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trader_category_stats (
    proxy_wallet            TEXT NOT NULL REFERENCES traders(proxy_wallet),
    category                TEXT NOT NULL,
    resolved_trades         INTEGER NOT NULL,
    category_volume_usdc    NUMERIC(14,2) NOT NULL,
    category_pnl_usdc       NUMERIC(14,2) NOT NULL,
    category_roi            NUMERIC(8,4) NOT NULL,
    last_trade_at           TIMESTAMPTZ,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (proxy_wallet, category)
);

CREATE INDEX IF NOT EXISTS idx_tcs_category_roi
    ON trader_category_stats (category, category_roi DESC);
CREATE INDEX IF NOT EXISTS idx_tcs_active
    ON trader_category_stats (category, last_trade_at DESC);

-- ----------------------------------------------------------------------------
-- CLOB orderbook snapshots — persisted at signal first-firing
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signal_book_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    signal_log_id       BIGINT NOT NULL REFERENCES signal_log(id) ON DELETE CASCADE,
    token_id            TEXT NOT NULL,
    side_captured       TEXT NOT NULL CHECK (side_captured IN ('YES','NO')),
    captured_at         TIMESTAMPTZ NOT NULL,
    best_bid            NUMERIC(8,6),
    best_ask            NUMERIC(8,6),
    bids                JSONB NOT NULL,
    asks                JSONB NOT NULL,
    total_bid_size_5c   NUMERIC(14,2),
    total_ask_size_5c   NUMERIC(14,2),
    raw_response_hash   TEXT
);

CREATE INDEX IF NOT EXISTS idx_book_snap_signal
    ON signal_book_snapshots (signal_log_id);
CREATE INDEX IF NOT EXISTS idx_book_snap_token_time
    ON signal_book_snapshots (token_id, captured_at DESC);

-- ----------------------------------------------------------------------------
-- Slice lookup audit — track every backtest filter the user evaluates
-- (multiple-testing correction + after-the-fact data-snooping detection)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS slice_lookups (
    id                  BIGSERIAL PRIMARY KEY,
    ran_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    slice_definition    JSONB NOT NULL,
    n_signals           INTEGER NOT NULL,
    reported_metric     TEXT NOT NULL,
    reported_value      NUMERIC,
    ci_low              NUMERIC,
    ci_high             NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_slice_lookups_ran_at ON slice_lookups (ran_at DESC);

-- ----------------------------------------------------------------------------
-- Paper trades — discretionary "fake money" entries for live system evaluation
-- Resolution job marks these closed using same model as signal_log
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS paper_trades (
    id                      BIGSERIAL PRIMARY KEY,
    signal_log_id           BIGINT REFERENCES signal_log(id),  -- NULL allowed for free-form entries
    condition_id            TEXT NOT NULL REFERENCES markets(condition_id),
    direction               TEXT NOT NULL CHECK (direction IN ('YES','NO')),
    entry_price             NUMERIC(8,6) NOT NULL,             -- book ask at click time
    entry_mid               NUMERIC(8,6),
    entry_size_usdc         NUMERIC(14,2) NOT NULL CHECK (entry_size_usdc > 0),
    entry_fee_usdc          NUMERIC(14,2) NOT NULL DEFAULT 0,
    entry_slippage_usdc     NUMERIC(14,2) NOT NULL DEFAULT 0,
    entry_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exit_price              NUMERIC(8,6),
    exit_at                 TIMESTAMPTZ,
    exit_reason             TEXT CHECK (exit_reason IS NULL OR exit_reason IN ('resolved','manual_close')),
    realized_pnl_usdc       NUMERIC(14,2),
    status                  TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','closed_resolved','closed_manual')),
    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_condition ON paper_trades (condition_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_signal ON paper_trades (signal_log_id) WHERE signal_log_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Backfill the 11 pre-fix signal_log rows from peak_* (closest available proxy
-- since they were observed at first refresh). Mark entry as 'unavailable' so
-- backtests can exclude them via that flag.
-- ----------------------------------------------------------------------------

UPDATE signal_log SET
    first_trader_count           = peak_trader_count,
    first_aggregate_usdc         = peak_aggregate_usdc,
    first_net_skew               = peak_net_skew,
    first_avg_portfolio_fraction = peak_avg_portfolio_fraction,
    signal_entry_source          = 'unavailable',
    market_type                  = 'binary'
WHERE first_trader_count IS NULL;
