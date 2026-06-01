@echo off
REM BTC up/down DATA COLLECTOR launcher (collect-only: logs data, no trades).
REM Runs continuously, logging per-second market snapshots (BTC price + both
REM order books + outcome) to a local SQLite dataset for later research.
REM Close this window or press Ctrl+C to stop. Re-running resumes the dataset.
REM
REM No strategy is being traded yet — this only builds the dataset.
REM Inspect any time:  .\venv\Scripts\python -m btcbot.runner --summary

cd /d "%~dp0"
.\venv\Scripts\python -m btcbot.runner --horizons 5m,15m --poll 1 --collect-only
