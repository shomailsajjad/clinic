@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Setup Clinic.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\backup_windows.py
if errorlevel 1 (
    echo External-drive backup did not complete.
    pause
    exit /b 1
)
pause
