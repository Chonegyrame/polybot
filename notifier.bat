@echo off
REM ============================================================
REM Esports sharp-move desktop notifier — popup + sound.
REM
REM Watches the local esports DB (read-only) and pops an always-on-top alert
REM (plus a short sound) when a tracked sharp move fires a trigger:
REM   - a FOLLOW wallet enters with >= $5k
REM   - >= 3 sharps buy the same market within 5 min
REM   - >= 2 sharps exit a market before it resolves
REM
REM Run this ALONGSIDE polybot (polybot fills the DB; this only reads it).
REM The popup shows over normal windows, browser-fullscreen, and BORDERLESS
REM games. Over a game in true EXCLUSIVE fullscreen it can't draw — the sound
REM is what gets through there. Tip: run League in Borderless to see popups
REM in-game too.
REM
REM Tune with env vars (optional): ESPORTS_ALERT_BIG_USD, _BURST_WALLETS,
REM   _BURST_WINDOW_MIN, _EXIT_COUNT, _SOUND (0 to mute), _POPUP_SECONDS.
REM Close the window to stop.
REM ============================================================

cd /d "%~dp0"
.\venv\Scripts\python -u -m esports.notifier
echo.
echo Notifier stopped. Press any key to close this window.
pause >nul
