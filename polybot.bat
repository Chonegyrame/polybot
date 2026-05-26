@echo off
REM Polybot launcher — starts the backend (API + scheduler + UI) and opens the dashboard in your browser.
REM Close this window or press Ctrl+C to stop the system.

cd /d "%~dp0"
start "" /min cmd /c "timeout /t 4 >nul & start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" "http://127.0.0.1:8000""
.\venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
