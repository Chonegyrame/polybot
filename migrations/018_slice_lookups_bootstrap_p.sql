-- Migration 018 — Pass 5 #8 — slice_lookups.bootstrap_p column
--
-- F21 (Pass 2) added empirical bootstrap p-values to BacktestResult so
-- BH-FDR ranking would not depend on a Gaussian-from-CI approximation
-- that breaks down on skewed P&L distributions. F21 deferred persisting
-- the value into slice_lookups (would have needed this migration). Pass 5
-- #8 closes the gap: every prior session entry returned NULL for
-- bootstrap_p (the column did not exist), so compute_corrections fell
-- back to the broken approximation for every comparator.
--
-- Adding the column is purely additive. Existing rows get NULL and the
-- engine's compute_corrections continues falling back to _pvalue_from_ci
-- for those rows; new rows persist the real bootstrap_p so future
-- corrections use the accurate value.

ALTER TABLE slice_lookups
    ADD COLUMN IF NOT EXISTS bootstrap_p NUMERIC;

COMMENT ON COLUMN slice_lookups.bootstrap_p IS
    'Empirical 2-sided bootstrap p-value vs H0: mean=0. F21 + Pass 5 #8. '
    'NULL on rows persisted before this migration — compute_corrections '
    'falls back to _pvalue_from_ci for those (Gaussian-from-CI approx).';
