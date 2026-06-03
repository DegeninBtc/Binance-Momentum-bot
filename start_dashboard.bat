@echo off
setlocal

cd /d "%~dp0"

if not defined DASHBOARD_HOST set "DASHBOARD_HOST=127.0.0.1"
if not defined DASHBOARD_PORT set "DASHBOARD_PORT=8787"

set "PYTHON_CMD="
where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    set "PYTHON_CMD=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  )
)

if not defined PYTHON_CMD (
  echo Python was not found. Install Python 3.11+ or add it to PATH.
  pause
  exit /b 1
)

echo Using Python: %PYTHON_CMD%
echo Checking trading dependencies...
"%PYTHON_CMD%" -c "import requests, bs4" >nul 2>nul
if errorlevel 1 (
  echo Trading dependencies are not installed yet.
  echo The web dashboard will still open. Install requirements.txt before using signal/trading buttons.
)

echo Checking for an old dashboard on port %DASHBOARD_PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%DASHBOARD_PORT% " ^| findstr "LISTENING"') do (
  echo Stopping old listener PID %%P...
  taskkill /PID %%P /F >nul 2>nul
)

echo Starting dashboard at http://%DASHBOARD_HOST%:%DASHBOARD_PORT%/
echo Keep this window open while using the dashboard.
"%PYTHON_CMD%" -B web_dashboard.py --host %DASHBOARD_HOST% --port %DASHBOARD_PORT% --open-browser

echo Dashboard stopped.
pause
