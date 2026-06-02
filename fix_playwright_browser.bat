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
  echo Python was not found.
  pause
  exit /b 1
)

echo Using Python: %PYTHON_CMD%
echo Checking Playwright package...
"%PYTHON_CMD%" -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo Installing Playwright package...
  "%PYTHON_CMD%" -m pip install playwright
  if errorlevel 1 (
    echo Failed to install Playwright package.
    pause
    exit /b 1
  )
)

echo Downloading Chromium for Playwright...
"%PYTHON_CMD%" -m playwright install chromium
if errorlevel 1 (
  echo Failed to download Chromium.
  pause
  exit /b 1
)

echo Playwright Chromium is installed.
echo Restart start_dashboard.bat, then enable browser scraping again.
pause
