-- Migration 013 — signal_exits.event_type (R3a, Pass 3)
--
-- Splits the single "exit" event into two tiers:
--
--   trim — drop ≥20% but <50% on either trader_count or aggregate_usdc.
--          Notification only, paper trades stay open. Captures the
--          "smart money is taking profit but still holds material position"
--          case that pre-fix was misclassified as a full exit.
--
--   exit — drop ≥50% on either metric. Notification + auto-close paper
--          trades at current bid. The "smart money truly fled" event.
--
-- Default 'exit' for backwards compatibility — every existing row
-- (which used the old single-tier ≥30% threshold) gets labelled as
-- exit, which matches their actual semantic.

ALTER TABLE signal_exits
    ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'exit';

ALTER TABLE signal_exits
    ADD CONSTRAINT signal_exits_event_type_check
    CHECK (event_type IN ('trim', 'exit'));

COMMENT ON COLUMN signal_exits.event_type IS
    'R3a: trim (≥20%, <50% drop, notification only) or exit (≥50% drop, '
    'auto-closes paper trades). Pre-Pass-3 rows default to exit.';

-- Update the recent-exits feed to expose the new column.
-- (The application route /signals/exits/recent already SELECTs * so
-- the new column will be available without route changes.)
