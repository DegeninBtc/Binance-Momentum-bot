#!/bin/bash
set -e

# On first start: install Playwright system deps + Chromium, then cache everything.
# On restart: detect cached marker and skip.
CACHE_MARKER="/root/.cache/.pw_ready"

if [ "${PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD:-0}" != "1" ]; then
  if [ ! -f "$CACHE_MARKER" ]; then
    echo "[entrypoint] First run: installing Playwright system dependencies..."
    python -m playwright install-deps chromium
    echo "[entrypoint] Downloading Chromium browser..."
    python -m playwright install chromium
    touch "$CACHE_MARKER"
    echo "[entrypoint] Playwright ready."
  else
    echo "[entrypoint] Playwright already cached, skipping."
  fi
else
  echo "[entrypoint] PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1, skipping."
fi

exec "$@"