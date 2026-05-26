-- 021 — insider_actions: durable feed of NEW/TRIM/SELL events on insider wallets.
--
-- Populated by `refresh_top_trader_positions` (every ~10 min) when an insider
-- wallet's fresh position set differs from what's in DB. Drives the sidebar
-- badge (unseen count) and the per-wallet activity strip on the UI.
--
-- Thresholds (locked 2026-05-18):
--   NEW   — wallet now holds a (condition_id, asset) it didn't before
--   TRIM  — size shrank by ≥25% but didn't go to zero
--   SELL  — wallet held it last cycle, now doesn't
--
-- "Start fresh from now": existing positions in the `positions` table act as
-- the baseline. The first refresh after deploy diffs against current state
-- so no retroactive NEW rows fire for already-tracked positions.

CREATE TABLE IF NOT EXISTS insider_actions (
    id              BIGSERIAL PRIMARY KEY,
    proxy_wallet    TEXT NOT NULL REFERENCES insider_wallets(proxy_wallet) ON DELETE CASCADE,
    condition_id    TEXT NOT NULL,
    asset           TEXT NOT NULL,
    outcome         TEXT,
    action_type     TEXT NOT NULL CHECK (action_type IN ('NEW', 'TRIM', 'SELL')),
    size_before     NUMERIC,
    size_after      NUMERIC,
    size_delta      NUMERIC,
    cur_price       NUMERIC,
    value_delta_usd NUMERIC,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seen_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_insider_actions_occurred
    ON insider_actions (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_insider_actions_unseen
    ON insider_actions (occurred_at DESC) WHERE seen_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_insider_actions_wallet
    ON insider_actions (proxy_wallet, occurred_at DESC);
