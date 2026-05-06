-- Migration 009 — F4 + F7 (Pass 2)
--
-- Two changes to signal_price_snapshots:
--
--   F4: capture BOTH bid_price and ask_price (not just bid via yes_price).
--       Pre-fix we stored only the best bid (`yes_price = bids[0].price`)
--       but compared it to entry_price which was the best ASK. The spread
--       baked a systematic "convergence" artifact into half-life and B10
--       latency math. Adding both sides lets us:
--         - half-life math: compare against mid = (bid+ask)/2
--         - latency math:   use ask (the price you'd pay to enter late)
--         - exit modeling:  use bid (the price you'd get to sell)
--
--   F7: capture additional snapshot offsets (+5 and +15 min) so the short
--       latency profiles (active 1-3, responsive 5-10, casual 12-20) have
--       real data behind them. Pre-fix only had +30/60/120, so 3 of 4
--       latency profiles silently fell back to the optimistic baseline.
--       Job cadence will drop from 30 min → 10 min so +5 is reliably
--       captured (see app/scheduler/runner.py).
--
-- yes_price is KEPT for backward compatibility but treated as deprecated:
--   - new rows: yes_price = bid_price (mirrors), so existing readers still
--     see the bid value they always saw
--   - new readers should prefer bid_price + ask_price + computed mid

ALTER TABLE signal_price_snapshots
    ADD COLUMN IF NOT EXISTS bid_price NUMERIC(8,6),
    ADD COLUMN IF NOT EXISTS ask_price NUMERIC(8,6);

COMMENT ON COLUMN signal_price_snapshots.bid_price IS
    'F4: best bid at snapshot time. Use this OR yes_price (kept for back-compat).';

COMMENT ON COLUMN signal_price_snapshots.ask_price IS
    'F4: best ask at snapshot time. Used by B10 latency simulation.';

COMMENT ON COLUMN signal_price_snapshots.yes_price IS
    'DEPRECATED (F4): mirrors bid_price for back-compat. New code: prefer bid_price + ask_price.';

-- Backfill existing rows: copy yes_price into bid_price so historical data
-- is queryable through the new column. ask_price stays NULL on old rows
-- (we never captured it). Half-life math gracefully handles NULL ask
-- (falls back to bid-only comparison + adds a quality note).
UPDATE signal_price_snapshots
SET bid_price = yes_price
WHERE bid_price IS NULL AND yes_price IS NOT NULL;
