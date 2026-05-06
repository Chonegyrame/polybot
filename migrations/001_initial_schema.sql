-- Polymarket Smart Money Tracker — initial schema (V1)
--
-- Tables:
--   traders                       Identity for every proxy wallet we've seen
--   leaderboard_snapshots         Daily snapshot of (rank, pnl, vol) per
--                                 (category, time_period, order_by) — the PIT
--                                 history that backs walk-forward backtesting
--   events                        Polymarket events. Carry the category
--   markets                       Polymarket markets, joined to events
--   positions                     Current open positions of tracked traders
--   portfolio_value_snapshots     Per-trader portfolio value over time
--   raw_snapshots                 Staging table for raw API responses
--   signal_log                    Durable per-(mode,category,top_n,market,direction)
--                                 signal lifetime — feeds the organic backtest
--   alerts_sent                   Debouncing audit trail for emails

-- ----------------------------------------------------------------------------
-- migration tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS _migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Trader identity
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS traders (
    proxy_wallet    TEXT PRIMARY KEY,
    user_name       TEXT,
    x_username      TEXT,
    verified_badge  BOOLEAN NOT NULL DEFAULT FALSE,
    profile_image   TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Daily leaderboard snapshots — point-in-time data for walk-forward backtest
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    category        TEXT NOT NULL,        -- overall|politics|sports|crypto|culture|tech|finance
    time_period     TEXT NOT NULL,        -- day|week|month|all
    order_by        TEXT NOT NULL,        -- PNL|VOL
    proxy_wallet    TEXT NOT NULL REFERENCES traders(proxy_wallet),
    rank            INTEGER NOT NULL,
    pnl             NUMERIC NOT NULL,
    vol             NUMERIC NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, category, time_period, order_by, proxy_wallet)
);

CREATE INDEX IF NOT EXISTS idx_lb_snap_date    ON leaderboard_snapshots (snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_lb_snap_wallet  ON leaderboard_snapshots (proxy_wallet);
CREATE INDEX IF NOT EXISTS idx_lb_snap_lookup  ON leaderboard_snapshots (category, time_period, order_by, snapshot_date DESC);

-- ----------------------------------------------------------------------------
-- Events (carry category)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,    -- gamma event id
    slug            TEXT,
    title           TEXT,
    category        TEXT,                 -- nullable; uncategorized lives in Overall only
    tags            JSONB,
    end_date        TIMESTAMPTZ,
    closed          BOOLEAN NOT NULL DEFAULT FALSE,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_category  ON events (category);
CREATE INDEX IF NOT EXISTS idx_events_closed    ON events (closed);

-- ----------------------------------------------------------------------------
-- Markets — joined to events for category derivation
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS markets (
    condition_id        TEXT PRIMARY KEY,
    gamma_id            TEXT,
    event_id            TEXT REFERENCES events(id),
    slug                TEXT,
    question            TEXT,
    clob_token_yes      TEXT,
    clob_token_no       TEXT,
    outcomes            JSONB,        -- usually ["Yes","No"]
    end_date            TIMESTAMPTZ,
    closed              BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_outcome    TEXT,         -- 'YES' | 'NO' | NULL while unresolved
    last_synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_event   ON markets (event_id);
CREATE INDEX IF NOT EXISTS idx_markets_closed  ON markets (closed);

-- ----------------------------------------------------------------------------
-- Positions — current snapshot of tracked traders' open books
-- first_seen_at is set the first time we observe the position, never updated.
-- It's the proxy entry-time used for freshness/drift labels (Option B).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS positions (
    proxy_wallet      TEXT NOT NULL REFERENCES traders(proxy_wallet),
    condition_id      TEXT NOT NULL REFERENCES markets(condition_id),
    asset             TEXT NOT NULL,    -- token_id of YES or NO outcome
    outcome           TEXT,             -- "Yes" | "No"
    size              NUMERIC NOT NULL,
    avg_price         NUMERIC,
    cur_price         NUMERIC,
    initial_value     NUMERIC,
    current_value     NUMERIC,
    cash_pnl          NUMERIC,
    realized_pnl      NUMERIC,
    percent_pnl       NUMERIC,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (proxy_wallet, condition_id, asset)
);

CREATE INDEX IF NOT EXISTS idx_positions_market      ON positions (condition_id);
CREATE INDEX IF NOT EXISTS idx_positions_first_seen  ON positions (first_seen_at);

-- ----------------------------------------------------------------------------
-- Portfolio value over time — needed as denominator for portfolio-fraction
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS portfolio_value_snapshots (
    proxy_wallet    TEXT NOT NULL REFERENCES traders(proxy_wallet),
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    value           NUMERIC NOT NULL,
    PRIMARY KEY (proxy_wallet, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_pv_wallet_recent
    ON portfolio_value_snapshots (proxy_wallet, fetched_at DESC);

-- ----------------------------------------------------------------------------
-- Raw API response staging (debug + replay)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    endpoint     TEXT NOT NULL,
    params       JSONB,
    status_code  INTEGER NOT NULL,
    response     JSONB,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_endpoint    ON raw_snapshots (endpoint);
CREATE INDEX IF NOT EXISTS idx_raw_fetched_at  ON raw_snapshots (fetched_at DESC);

-- ----------------------------------------------------------------------------
-- Signal log — durable per-lifetime record per (mode,category,top_n,market,direction)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signal_log (
    id                              BIGSERIAL PRIMARY KEY,
    mode                            TEXT NOT NULL,    -- 'absolute' | 'hybrid'
    category                        TEXT NOT NULL,
    top_n                           INTEGER NOT NULL,
    condition_id                    TEXT NOT NULL REFERENCES markets(condition_id),
    direction                       TEXT NOT NULL,    -- 'YES' | 'NO'
    first_fired_at                  TIMESTAMPTZ NOT NULL,
    last_seen_at                    TIMESTAMPTZ NOT NULL,
    peak_trader_count               INTEGER NOT NULL,
    peak_avg_portfolio_fraction     NUMERIC,
    peak_aggregate_usdc             NUMERIC,
    peak_net_skew                   NUMERIC,
    first_top_trader_entry_price    NUMERIC,
    current_price                   NUMERIC,
    resolution_outcome              TEXT,             -- 'YES' | 'NO' | NULL
    resolved_at                     TIMESTAMPTZ,
    UNIQUE (mode, category, top_n, condition_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_signal_log_market  ON signal_log (condition_id);
CREATE INDEX IF NOT EXISTS idx_signal_log_lookup  ON signal_log (mode, category, top_n);
CREATE INDEX IF NOT EXISTS idx_signal_log_active  ON signal_log (last_seen_at DESC) WHERE resolved_at IS NULL;

-- ----------------------------------------------------------------------------
-- Email alert audit trail (debouncing)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alerts_sent (
    id              BIGSERIAL PRIMARY KEY,
    signal_log_id   BIGINT NOT NULL REFERENCES signal_log(id),
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    email_to        TEXT NOT NULL,
    subject         TEXT,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_signal  ON alerts_sent (signal_log_id);
