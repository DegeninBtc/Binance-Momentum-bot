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

# Playwright 浏览器运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
       libdrm2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
       fonts-noto-color-emoji curl \
    && rm -rf /var/lib/apt/lists/*

# 只装 Python 库，不下载 Chromium（运行时按需安装）
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Playwright 按需加载：首次启动时才下载 Chromium，缓存到 /root/.cache
# 如不需要浏览器抓取，设 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 跳过
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

COPY binance_square_momentum_bot.py web_dashboard.py ./
COPY --from=frontend /app/web/dist ./web/dist

EXPOSE 8787

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "web_dashboard.py", "--host", "0.0.0.0", "--port", "8787"]