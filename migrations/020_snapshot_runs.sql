-- Migration 020 — Pass 5 #16 — snapshot_runs completeness ledger
--
-- daily_leaderboard_snapshot runs 28 sub-combos sequentially. Pre-fix,
-- partial failures (one combo fails, the others commit) left a half-
-- populated leaderboard with no completeness flag. Downstream readers
-- doing MAX(snapshot_date) GROUP BY category mixed today's incomplete
-- combos with yesterday's complete data, with no operator-visible
-- signal except log inspection.
--
-- This table records each run's completeness so readers can gate on
-- failed_combos = 0 and so the /system/errors page (UI-SPEC Section 8)
-- can surface the failure with full context.
--
-- One row per snapshot_date (PRIMARY KEY) — re-running the snapshot job
-- on the same date overwrites the prior row via UPSERT.

CREATE TABLE IF NOT EXISTS snapshot_runs (
    snapshot_date    DATE PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL,
    completed_at     TIMESTAMPTZ NOT NULL,
    total_combos     INTEGER NOT NULL,
    succeeded_combos INTEGER NOT NULL,
    failed_combos    INTEGER NOT NULL,
    failures         JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_seconds NUMERIC NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshot_runs_completed_at
    ON snapshot_runs (completed_at DESC);

COMMENT ON TABLE snapshot_runs IS
    'One row per daily_leaderboard_snapshot run. Pass 5 #16: failures '
    'JSONB list of {combo_label, error_repr}. Downstream readers gate '
    'on failed_combos = 0 to avoid mixing partial data.';
