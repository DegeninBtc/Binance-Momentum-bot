@echo off
setlocal

cd /d "%~dp0"

set "REMOTE_URL=git@github.com:DegeninBtc/Binance-Momentum-bot.git"
set "GIT_SSH_COMMAND=ssh -o StrictHostKeyChecking=accept-new"

echo Preparing local git identity...
git config --local user.name "DegeninBtc"
if errorlevel 1 goto :error
git config --local user.email "DegeninBtc@users.noreply.github.com"
if errorlevel 1 goto :error

echo Configuring SSH remote...
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  git remote add origin "%REMOTE_URL%"
) else (
  git remote set-url origin "%REMOTE_URL%"
)
if errorlevel 1 goto :error

echo Staging project files...
git add .gitignore README.md binance_square_momentum_bot.py web_dashboard.py requirements.txt start_dashboard.bat install_browser_mode.bat fix_playwright_browser.bat
if errorlevel 1 goto :error

echo Creating commit...
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Initial Binance momentum dashboard"
  if errorlevel 1 goto :error
) else (
  echo Nothing staged to commit.
)

echo Pushing main to GitHub over SSH...
git push -u origin main
if errorlevel 1 goto :error

echo.
echo Upload complete:
echo https://github.com/DegeninBtc/Binance-Momentum-bot
pause
exit /b 0

:error
echo.
echo Upload failed. Check the error above.
echo If this is an SSH error, add your public SSH key to GitHub and rerun this script.
pause
exit /b 1
