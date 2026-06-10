#!/bin/bash
set -e

# Playwright Chromium lazy install
# Browser binary cached in PLAYWRIGHT_BROWSERS_PATH (default /root/.cache/ms-playwright)
# docker-compose pw_cache volume persists it across restarts
if [ "${PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD:-0}" != "1" ]; then
  BROWSER_DIR="${PLAYWRIGHT_BROWSERS_PATH:-/root/.cache/ms-playwright}"
  if [ ! -d "$BROWSER_DIR/chromium-"* ] 2>/dev/null; then
    echo "[entrypoint] Playwright Chromium not found, downloading..."
    python -m playwright install chromium
    echo "[entrypoint] Chromium installed to $BROWSER_DIR"
  else
    echo "[entrypoint] Playwright Chromium already cached, skipping download."
  fi
else
  echo "[entrypoint] PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1, skipping browser install."
fi

exec "$@"