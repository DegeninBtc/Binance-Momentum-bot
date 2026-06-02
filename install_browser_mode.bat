@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_CMD="
if exist "C:\Users\inlif\AppData\Local\Python\pythoncore-3.14-64\python.exe" (
  set "PYTHON_CMD=C:\Users\inlif\AppData\Local\Python\pythoncore-3.14-64\python.exe"
)

if not defined PYTHON_CMD (
  if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
  )
)

if not defined PYTHON_CMD (
where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"
)

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
echo Installing Python packages...
"%PYTHON_CMD%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Python package installation failed.
  pause
  exit /b 1
)

echo Installing Chromium for Playwright...
"%PYTHON_CMD%" -m playwright install chromium
if errorlevel 1 (
  echo Chromium installation failed.
  pause
  exit /b 1
)

echo Browser scraping support is ready.
pause
