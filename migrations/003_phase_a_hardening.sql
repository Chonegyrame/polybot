-- Migration 003 — Phase A correctness fixes
-- Bundles small schema-level changes for the Phase A hardening pass (session 1).
--
-- Changes:
-- 1. Index on positions.last_updated_at — supports the TTL filter we add to
--    signal aggregation queries (excludes stale positions from failed
--    refreshes from contributing to phantom signals).

-- ---------------------------------------------------------------------------
-- 1. positions TTL index
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_positions_last_updated
    ON positions (last_updated_at);
