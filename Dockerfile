FROM node:20-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY web ./web
COPY tsconfig.json ./
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Playwright runtime dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
       libdrm2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
       fonts-noto-color-emoji curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps only, Chromium downloaded at runtime on demand
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Playwright lazy-load: first startup downloads Chromium, cached in volume
# Set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 to skip if not needed
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

COPY binance_square_momentum_bot.py web_dashboard.py ./
COPY --from=frontend /app/web/dist ./web/dist

EXPOSE 8787

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "web_dashboard.py", "--host", "0.0.0.0", "--port", "8787"]