@echo off
setlocal
cd /d "%~dp0"
echo Sajjad Poly Clinic - first-time setup
echo Internet is needed to download dependencies during setup.
echo.
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto python_missing
)
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 goto failed
".venv\Scripts\python.exe" manage.py collectstatic --noinput
if errorlevel 1 goto failed
".venv\Scripts\python.exe" manage.py check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" manage.py shell -c "import sys; from clinic.models import User; sys.exit(0 if User.objects.filter(role='admin', is_active=True).exists() else 1)"
if errorlevel 1 (
    echo.
    echo Create your admin account below. Email can be left blank.
    echo Password characters will stay invisible while you type.
    ".venv\Scripts\python.exe" manage.py createsuperuser
    if errorlevel 1 goto failed
)
echo.
echo Setup complete. Double-click Start Clinic.bat to open the application.
pause
exit /b 0
:python_missing
echo.
echo Python 3.12 is required. Install it from https://www.python.org/downloads/windows/
echo Select the Python launcher and Add python.exe to PATH during installation.
echo Then run this setup again.
pause
exit /b 1
:failed
echo.
echo Setup did not complete. Keep this window open and share the error shown above.
pause
exit /b 1
