@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto not_ready
if not exist "staticfiles\clinic\app.css" goto not_ready
echo Sajjad Poly Clinic - local testing
echo Keep this window open while using the application.
echo Close it or press Ctrl+C to stop the application.
start "Clinic browser" /b ".venv\Scripts\python.exe" scripts\open_browser.py
".venv\Scripts\python.exe" serve.py
if errorlevel 1 (
    echo.
    echo Application could not start. Share the error above.
    echo If another clinic window is already running, use that window.
    pause
    exit /b 1
)
exit /b 0
:not_ready
echo Run Setup Clinic.bat first.
pause
exit /b 1
