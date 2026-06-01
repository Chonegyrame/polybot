"""BTC Up/Down trading bot (paper-first).

Trades Polymarket's recurring "Bitcoin Up or Down" markets. The engine is
parameterized by horizon (5m / 15m / 1h) so multiple horizons can run side by
side in paper mode and be compared on realized PnL.

Read-only against Polymarket (all calls go through app.services.polymarket).
Execution is paper-only in V0 — no order signing.
"""
