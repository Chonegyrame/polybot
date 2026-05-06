# Polymarket Smart Money Tracker

Personal tool that identifies Polymarket's top traders, surfaces markets where many of them have overlapping positions ("smart money consensus signals"), and helps the user manually decide on entries. Read-only V1 — no auto-trading.

## Tech Stack
- Python 3.11+ + FastAPI
- APScheduler with Postgres jobstore (persistent missed-job replay)
- Supabase (Postgres) for data + scheduler state
- Resend for email alerts
- Polymarket public APIs (gamma-api, data-api, CLOB) — no auth for reads
- Hosting: local laptop initially, Railway later (cloud-ready architecture from day 1)

## Project Rules
- Never commit .env (already gitignored)
- All Polymarket API calls go through `app/services/polymarket.py` only
- Business logic lives in `services/`, not in `routes/`
- Routes are thin — they call a service and return the result
- All DB access goes through `db/crud.py`
- Scheduler jobs live in `scheduler/jobs.py` only
- Configuration via env vars — no hardcoded paths or hostnames anywhere
- Raw API responses staged to `raw_snapshots` before processing (debugging + replay)
- Every fired signal logged to `signal_log` for organic walk-forward backtest

## Trader Ranking
Two modes the user toggles in the UI:
- **Absolute PnL**: rank ALL traders by raw dollar profit, take top N. No filters.
- **Hybrid**: filter to traders with ≥10 resolved trades AND ≥$5k cumulative volume; rank that pool by averaging PnL-rank and ROI-rank; take top N.

Top-N is configurable 25–100, step 5, default 50. Per-category sub-rankings using Polymarket's native categories + "Overall."

## Signal Methodology
- Track each top trader's open positions
- Two metrics surfaced separately (NOT combined): trader headcount + average portfolio fraction allocated
- Net direction skew: only fire when |YES_weight − NO_weight| / total ≥ 0.6
- Eligibility floors: ≥5 distinct top traders + aggregate USDC ≥ $25k threshold
- Freshness + price-drift labels are approximate (derived from snapshot history, accuracy improves after ~1 week of running)

## Refresh Cadence
- Every 10 minutes — live data refresh (top traders, current positions, signals)
- Once daily — leaderboard snapshot (with catch-up on startup if last >24h)
- Once daily — backtest recompute
- Once daily — heartbeat email confirming system is alive

## Key Decisions
- Backend-only V1; UI built externally (see UI-SPEC.md)
- Read-only Polymarket integration for V1
- Survivorship-aware backtest: organic walk-forward via signal_log + biased "today's top-N looking back" available with disclaimer
- Two-metric signal display, not a combined conviction score
- Daily snapshots from day 1 to build true point-in-time history

## API Endpoints
- `GET /traders/top?mode=&category=&top_n=`
- `GET /signals/active?mode=&category=&top_n=`
- `GET /markets/:id`
- `GET /backtest/summary?mode=&category=`

## Phase 2 (not in scope for V1)
- Individual trader drill-down endpoints
- Frontend dashboard (built externally)
- Trade execution via signed CLOB API
- Reconstructing historical top-N lists beyond what daily snapshots give us
- Insider watchlist (manually curated wallet list)

## References
- UI-SPEC.md — UI/UX specification for the external frontend builder
- session-state.md — current build progress, updated each session
- spike/FINDINGS.md — validated API endpoint behavior from Step 0 spike
