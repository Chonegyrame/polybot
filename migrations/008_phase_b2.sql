-- Migration 008 — Phase B2 schema (consolidated for B2, B3, B4, B12)
--
-- Adds:
--   1. signal_log.counterparty_warning  (B2 — counterparty diagnostic)
--   2. watchlist_signals                (B3 — looser-floor secondary feed)
--   3. signal_price_snapshots           (B4 — +30/60/120min YES price after fire)
--   4. insider_wallets                  (B12 — manually curated wallet list)
--
-- B10 (latency simulation) reuses signal_price_snapshots — no schema needed.
-- B11 (edge decay) is endpoint-only, reuses signal_log + markets.
--
-- vw_signals_unique_market is NOT updated to include counterparty_warning.
-- Rationale: /signals/active reads signal_log directly (column is visible
-- there); the view feeds /backtest which has no use for the warning. Keeping
-- the view stable avoids re-creating it for an unrelated downstream consumer.

-- ---------------------------------------------------------------------------
-- 1. B2 — counterparty_warning on signal_log
-- ---------------------------------------------------------------------------

ALTER TABLE signal_log
    ADD COLUMN IF NOT EXISTS counterparty_warning BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN signal_log.counterparty_warning IS
    'B2: True when, at signal-fire time, any wallet in the union of all 21 '
    'tracked top-N pools appeared as a SELLER (CLOB maker) on the same token. '
    'Surfaces "smart money also selling" warning in the UI.';

-- ---------------------------------------------------------------------------
-- 2. B3 — watchlist_signals (looser floors than signal_log)
-- ---------------------------------------------------------------------------
--
-- Floors: ≥2 traders / ≥$5k aggregate / ≥60% skew (vs 5/$25k/60% for signals).
-- Mutually exclusive with signal_log: a market that crosses the official
-- floors is in signal_log only; a market that crosses the watchlist floors
-- but NOT the official ones is in watchlist_signals only.
--
-- Not eligible for paper trading or backtest — purely a UI surface.

CREATE TABLE IF NOT EXISTS watchlist_signals (
    id              BIGSERIAL PRIMARY KEY,
    mode            TEXT NOT NULL,
    category        TEXT NOT NULL,
    top_n           INTEGER NOT NULL,
    condition_id    TEXT NOT NULL REFERENCES markets(condition_id),
    direction       TEXT NOT NULL CHECK (direction IN ('YES', 'NO')),
    trader_count    INTEGER NOT NULL,
    aggregate_usdc  NUMERIC(14, 2) NOT NULL,
    net_skew        NUMERIC(5, 4) NOT NULL,
    avg_portfolio_fraction NUMERIC(8, 6),
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (mode, category, top_n, condition_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_signals_last_seen
    ON watchlist_signals (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_signals_cid
    ON watchlist_signals (condition_id);

-- ---------------------------------------------------------------------------
-- 3. B4 — signal_price_snapshots (+30/60/120 min YES price after fire)
-- ---------------------------------------------------------------------------
--
-- For each fired signal we capture the YES price at three offsets after
-- first_fired_at. Used for:
--   - Half-life endpoint: how fast does the gap-to-smart-money close?
--   - B10 latency simulation: what would entry have looked like if user
--     placed the order N minutes after the alert fired?
--
-- Job runs every 30 min with overlap so missed ticks (sleep, restart) get
-- caught when the system comes back up.

CREATE TABLE IF NOT EXISTS signal_price_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    signal_log_id       BIGINT NOT NULL
                          REFERENCES signal_log(id) ON DELETE CASCADE,
    snapshot_offset_min INTEGER NOT NULL CHECK (snapshot_offset_min IN (30, 60, 120)),
    snapshot_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    yes_price           NUMERIC(8, 6),
    token_id            TEXT NOT NULL,
    UNIQUE (signal_log_id, snapshot_offset_min)
);

CREATE INDEX IF NOT EXISTS idx_signal_price_snapshots_at
    ON signal_price_snapshots (snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_price_snapshots_signal
    ON signal_price_snapshots (signal_log_id);

-- ---------------------------------------------------------------------------
-- 4. B12 — insider_wallets (manually curated watchlist)
-- ---------------------------------------------------------------------------
--
-- Wallets the user has manually flagged as interesting beyond the
-- leaderboard top-N. CRUD-only in V1; no auto-detection.
-- These wallets are always tracked by the position refresh loop even if
-- they don't appear on any leaderboard, so we can see when they enter a
-- market that hasn't yet built consensus among the public top-N.

CREATE TABLE IF NOT EXISTS insider_wallets (
    proxy_wallet    TEXT PRIMARY KEY,
    label           TEXT,
    notes           TEXT,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_insider_wallets_last_seen
    ON insider_wallets (last_seen_at DESC NULLS LAST);

-- _migrations bookkeeping is handled by scripts/apply_migrations.py.
