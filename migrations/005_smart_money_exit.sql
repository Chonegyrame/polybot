-- Migration 005 — Smart-money exit detector (Phase B1)
--
-- Adds:
--  1. `signal_exits` table — one row per detected exit event. Tracks how far
--     trader_count / aggregate dropped from peak so the UI can show context
--     and so backtest can reconstruct the exit timeline.
--  2. `closed_exit` as a new value of paper_trades.status, plus
--     `smart_money_exit` as a new value of paper_trades.exit_reason. These
--     are the labels paper trades get when auto-closed by an exit event.
--  3. `exit_bid_price` column on signal_exits — the current bid at exit time,
--     captured once and reused as the settlement price for any paper trades
--     auto-closed against this exit (avoids racing the market between
--     detection and settlement).

-- ---------------------------------------------------------------------------
-- 1. signal_exits table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signal_exits (
    id                   BIGSERIAL PRIMARY KEY,
    signal_log_id        BIGINT NOT NULL
                          REFERENCES signal_log(id) ON DELETE CASCADE,
    exited_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- snapshot of the metrics at the moment we detected the drop
    exit_trader_count    INTEGER NOT NULL,
    peak_trader_count    INTEGER NOT NULL,
    exit_aggregate_usdc  NUMERIC(20, 2) NOT NULL,
    peak_aggregate_usdc  NUMERIC(20, 2) NOT NULL,
    -- which threshold tripped: 'trader_count' | 'aggregate' | 'both'
    drop_reason          TEXT NOT NULL
                          CHECK (drop_reason IN ('trader_count', 'aggregate', 'both')),
    -- current YES-side bid at exit time, used to settle any paper trades
    -- on this signal. NULL if book capture failed at exit detection time.
    exit_bid_price       NUMERIC(8, 4),
    -- one signal can only exit once; subsequent drops on the same signal
    -- are a no-op until the signal_log row resolves or expires.
    UNIQUE (signal_log_id)
);

CREATE INDEX IF NOT EXISTS idx_signal_exits_signal_log
    ON signal_exits (signal_log_id);
CREATE INDEX IF NOT EXISTS idx_signal_exits_exited_at
    ON signal_exits (exited_at DESC);

-- ---------------------------------------------------------------------------
-- 2. paper_trades — accept new status + exit_reason values
-- ---------------------------------------------------------------------------

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_status_check;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_status_check
    CHECK (status IN ('open', 'closed_resolved', 'closed_manual', 'closed_exit'));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_exit_reason_check;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_exit_reason_check
    CHECK (
        exit_reason IS NULL
        OR exit_reason IN ('resolved', 'manual_close', 'smart_money_exit')
    );
