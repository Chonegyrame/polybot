# Polybot UI

Browser-side React app delivered by the third-party UI builder (2026-05-08).
No build step — React + Babel are loaded from unpkg CDN at runtime.

## Run it

**Recommended (one terminal):** the FastAPI backend serves the UI. From the
project root:

```
.\venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/ui/ (or just http://127.0.0.1:8000/ — the
backend redirects browser visits to `/ui/`). One process runs both the API
and the scheduler, and serves the UI as static files. Same-origin fetches
work without CORS.

**Alternative (separate static server):** for UI-only iteration without
the backend:

```
cd ui
python -m http.server 8000
```

Then open http://localhost:8000 in a browser. The UI will fall back to
mock data and surface an "OFFLINE" pill since it can't reach the API.
To wire it to a backend running on a different port, set
`window.POLYBOT_API_BASE` before `data.js` loads (or just edit data.js).

## Files

- `index.html` — entry point; loads styles, mock data, and Babel-compiled JSX
- `data.js` — mock data shaped exactly like the API responses in `UI-SPEC.md`.
  Designed to be swapped for real `fetch()` calls against the FastAPI backend.
- `styles.css`, `styles-extra.css` — all styling
- `tweaks-panel.jsx` — dev-only controls panel for tweaking the mock data
- `shared.jsx` — shared components (Header, Pills, etc.)
- `dashboard.jsx` — `/dashboard` route (signal feed + watchlist + top traders)
- `trader-modal.jsx` — trader drill-down modal
- `market-view.jsx` — `/testing/market/:condition_id` per-market trading view
- `testing.jsx` — `/testing` route (Portfolio, Trade, Backtest, Diagnostics)
- `app.jsx` — top-level shell + routing

## Wiring to the real backend

Currently `data.js` exposes a global `POLYBOT_DATA` populated with sample data.
To wire up the real FastAPI backend:

1. Replace `data.js`'s constants with `fetch()` calls against the API
   endpoints documented in the project root `UI-SPEC.md` (Section 9.4 has
   the full built-endpoint list).
2. Add an `API_BASE` constant at the top of the new `data.js` —
   `http://localhost:8000` for local development, env-driven for prod.
3. Built endpoints today: `/traders/top`, `/traders/{wallet}`,
   `/signals/active`, `/signals/exits/recent`, `/signals/{id}/contributors`,
   `/signals/new`, `/markets/{cid}`, `/watchlist/active`, `/insider_wallets`,
   `/system/status`, `/paper_trades*`, `/backtest/*`. Wallet endpoints,
   `/markets/search`, and `/system/errors` are NOT built — see UI-SPEC §9.4
   for the full built-vs-not-built table.

## `_reference/`

Archive of what the UI builder was given as input — the version of
`UI-SPEC.md` they worked from plus screenshots they were provided. Kept for
traceability. **Do not treat as authoritative** — the canonical UI-SPEC is
at the project root and may have diverged.
