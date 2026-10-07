@echo off
if /i "%~1" neq "run" (
    "%ComSpec%" /k ""%~f0" run"
    exit /b
)
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Install this branch first. Read docs\MULTI_CLINIC.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\configure_sync.py
if errorlevel 1 (
    echo Check the branch configuration and the message above.
    pause
    exit /b 1
)
