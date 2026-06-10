FROM node:20-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY web ./web
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Only install curl for entrypoint; Playwright system deps installed at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps only, no browser binaries
COPY requirements.txt ./
RUN pip install -r requirements.txt

# All Playwright setup deferred to first container start
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

COPY binance_square_momentum_bot.py web_dashboard.py ./
COPY --from=frontend /app/web/dist ./web/dist

EXPOSE 8787

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "web_dashboard.py", "--host", "0.0.0.0", "--port", "8787"]