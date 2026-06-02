@echo off
REM ============================================================
REM Test the esports notifier — fires 3 sample popups (burst, big
REM position, exit) with sound, ~1s apart, so you can check the look,
REM the sound, and whether it shows over your game.
REM
REM Does NOT need polybot running. While it fires, pull up your League
REM client (in Borderless window mode) to confirm the popup overlays it.
REM ============================================================

cd /d "%~dp0"
.\venv\Scripts\python -u -m esports.notifier --test
echo.
echo Done. Press any key to close.
pause >nul
