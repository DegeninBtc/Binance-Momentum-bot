#!/bin/bash
set -e

CACHE_MARKER="/root/.cache/.pw_ready"
PW_LOG="/root/.cache/.pw_install.log"

install_playwright() {
  echo "[entrypoint] Installing Playwright system dependencies..." | tee "$PW_LOG"
  python -m playwright install-deps chromium >> "$PW_LOG" 2>&1
  echo "[entrypoint] Downloading Chromium browser..." | tee -a "$PW_LOG"
  python -m playwright install chromium >> "$PW_LOG" 2>&1
  touch "$CACHE_MARKER"
  echo "[entrypoint] Playwright ready." | tee -a "$PW_LOG"
}

if [ "${PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD:-0}" != "1" ]; then
  if [ ! -f "$CACHE_MARKER" ]; then
    echo "[entrypoint] Playwright not ready yet, installing in background..."
    install_playwright &
  else
    echo "[entrypoint] Playwright already cached."
  fi
else
  echo "[entrypoint] PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1, skipping."
fi

exec "$@"