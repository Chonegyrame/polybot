-- Migration 004 — drop unused tables from V1 simplification (Phase A session 2).
--
-- Both tables were created in 001_initial_schema.sql but never wired into
-- the running system after V1 scope decisions:
--
--   raw_snapshots  Was meant as a staging table for raw API responses
--                  (debugging / replay). The `insert_raw_snapshot` helper
--                  in crud.py is never called — we settled on parsed
--                  dataclasses instead and the staging step never landed.
--
--   alerts_sent    Was for the Resend email-alert debouncing trail. Email
--                  alerts were dropped from V1 (2026-05-04) in favor of
--                  UI-native notifications (status pill + new-signals
--                  badge + browser Notification API). Table never written.
--
-- Dropping reduces schema clutter and avoids confusion for future readers.
-- Re-add via a future migration if either feature gets picked back up.

DROP TABLE IF EXISTS raw_snapshots;
DROP TABLE IF EXISTS alerts_sent;
