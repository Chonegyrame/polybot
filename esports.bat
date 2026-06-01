@echo off
REM ============================================================
REM OPTIONAL standalone esports tracker.
REM
REM You normally do NOT need this: polybot.bat now runs the esports tracker
REM automatically inside the dashboard process (UI + API + tracker in one).
REM Use this ONLY to run the tracker headless WITHOUT the dashboard.
REM
REM ⚠ Do NOT run this at the same time as polybot — they'd both write the same
REM   SQLite file. Set ESPORTS_TRACKER_ENABLED=false in .env if you want polybot
REM   to skip the tracker and rely on this instead.
REM
REM Read-only against Polymarket; never trades. Resumes cleanly via the
REM per-wallet cursor. Close the window or Ctrl+C to stop.
REM ============================================================

cd /d "%~dp0"
.\venv\Scripts\python -m esports.watchlist
.\venv\Scripts\python -u -m esports.tracker --cycle 8
echo.
echo Tracker stopped. Press any key to close this window.
pause >nul
