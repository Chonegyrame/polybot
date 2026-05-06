-- Migration 010 — Relax signal_price_snapshots.snapshot_offset_min CHECK (R1, Pass 3)
--
-- F7 (Pass 2) added offsets +5 and +15 to SNAPSHOT_OFFSETS_MIN in
-- app/services/half_life.py so that the short-window latency profiles
-- ("active" 1-3 min, "responsive" 5-10 min, "casual" 12-20 min) would have
-- snapshot data behind them. The cadence was also dropped from 30 min to
-- 10 min so the +5 offset would reliably be hit.
--
-- BUT migration 008 originally constrained the column to (30, 60, 120) only.
-- F7 missed the schema constraint, so every insert at offset 5 or 15 was
-- silently rejected with CheckViolationError. The job's try/except caught
-- it, logged a warning, and moved on — meaning the F7 fix has been a no-op
-- since shipping.
--
-- This migration relaxes the CHECK to permit the full set (5, 15, 30, 60, 120).

ALTER TABLE signal_price_snapshots
    DROP CONSTRAINT IF EXISTS signal_price_snapshots_snapshot_offset_min_check;

ALTER TABLE signal_price_snapshots
    ADD CONSTRAINT signal_price_snapshots_snapshot_offset_min_check
    CHECK (snapshot_offset_min IN (5, 15, 30, 60, 120));

COMMENT ON COLUMN signal_price_snapshots.snapshot_offset_min IS
    'Minutes after first_fired_at when this snapshot was captured. F7+R1: '
    '5/15 added so short-latency profiles work. Job picks closest offset '
    'within tolerance; see app/services/half_life.py:pick_offset_for_age.';
