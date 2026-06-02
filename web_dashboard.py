#!/usr/bin/env python3
"""
Local web dashboard for binance_square_momentum_bot.py.

The dashboard binds to 127.0.0.1 by default. API keys are read only from
environment variables and are never displayed or accepted through the UI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import webbrowser
from collections import deque
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


LOGGER = logging.getLogger("web-dashboard")
BOT_LOGGER = logging.getLogger("square-momentum-bot")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_BASE_URL = "https://api.binance.com"
DEFAULT_SQUARE_URLS = (
    "https://www.binance.com/en/square",
    "https://www.binance.com/en/square/top",
)
BOT_MODULE: Any | None = None


def bot_module() -> Any:
    global BOT_MODULE
    if BOT_MODULE is None:
        import binance_square_momentum_bot

        BOT_MODULE = binance_square_momentum_bot
    return BOT_MODULE


class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 300) -> None:
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)
        self.records_lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        with self.records_lock:
            self.records.append(message)

    def tail(self, limit: int = 80) -> list[str]:
        with self.records_lock:
            return list(self.records)[-limit:]


class BotRunner:
    def __init__(self, log_handler: MemoryLogHandler) -> None:
        self.log_handler = log_handler
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.running = False
        self.mode = "idle"
        self.last_error = ""
        self.last_started_at = ""
        self.last_finished_at = ""
        self.last_signal: dict[str, Any] | None = None
        self.last_diagnostics: dict[str, Any] | None = None
        self.last_config: Any | None = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            config = self.last_config
            state_file = config.state_file if config else os.getenv("STATE_FILE", "bot_state.json")
            state = safe_load_state(state_file)
            return {
                "running": self.running,
                "mode": self.mode,
                "last_error": self.last_error,
                "last_started_at": self.last_started_at,
                "last_finished_at": self.last_finished_at,
                "last_signal": self.last_signal,
                "last_diagnostics": self.last_diagnostics,
                "config": sanitize_config(config),
                "state": state,
                "logs": self.log_handler.tail(),
            }

    def preview_signal(self, config: Any) -> dict[str, Any]:
        if not self._claim("preview", config):
            LOGGER.info("preview ignored because another bot task is already running")
            return self.status()
        self.worker = threading.Thread(target=self._preview_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.status()

    def diagnose_square(self, config: Any) -> dict[str, Any]:
        if not self._claim("square-diagnostics", config):
            LOGGER.info("Square diagnostics ignored because another bot task is already running")
            return self.status()
        self.worker = threading.Thread(target=self._diagnostics_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.status()

    def run_once(self, config: Any) -> dict[str, Any]:
        if not self._claim("once-live" if not config.dry_run else "once-dry-run", config):
            LOGGER.info("run-once ignored because another bot task is already running")
            return self.status()
        self.worker = threading.Thread(target=self._once_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.status()

    def start_loop(self, config: Any) -> dict[str, Any]:
        if not self._claim("loop-live" if not config.dry_run else "loop-dry-run", config):
            LOGGER.info("start-loop ignored because another bot task is already running")
            return self.status()
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        with self.lock:
            if self.running:
                self.mode = "stopping"
        return self.status()

    def _claim(self, mode: str, config: Any) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.mode = mode
            self.last_error = ""
            self.last_started_at = now_text()
            self.last_finished_at = ""
            self.last_config = config
            return True

    def _finish(self, error: str = "") -> None:
        with self.lock:
            self.running = False
            self.mode = "idle" if not error else "error"
            self.last_error = error
            self.last_finished_at = now_text()

    def _preview_worker(self, config: Any) -> None:
        try:
            signal = build_signal_preview(config)
            with self.lock:
                self.last_signal = signal
        except Exception as exc:
            LOGGER.exception("signal preview failed")
            self._finish(str(exc))
            return
        self._finish()

    def _diagnostics_worker(self, config: Any) -> None:
        try:
            diagnostics = build_square_diagnostics(config)
            with self.lock:
                self.last_diagnostics = diagnostics
        except Exception as exc:
            LOGGER.exception("Square diagnostics failed")
            self._finish(str(exc))
            return
        self._finish()

    def _once_worker(self, config: Any) -> None:
        try:
            bot_module().LongOnlyMomentumBot(config).run_once()
        except Exception as exc:
            LOGGER.exception("single cycle failed")
            self._finish(str(exc))
            return
        self._finish()

    def _loop_worker(self, config: Any) -> None:
        try:
            bot = bot_module().LongOnlyMomentumBot(config)
            while not self.stop_event.is_set():
                bot.run_once()
                for _ in range(max(1, config.poll_seconds)):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)
        except Exception as exc:
            LOGGER.exception("loop failed")
            self._finish(str(exc))
            return
        self._finish()


def build_signal_preview(config: Any) -> dict[str, Any]:
    module = bot_module()
    bot = module.LongOnlyMomentumBot(config)
    bot.client.sync_time()
    symbols = bot.client.tradable_quote_symbols(config.quote_asset)
    base_assets = {data["baseAsset"] for data in symbols.values()}
    posts = bot.square.fetch_top_posts(config.top_post_limit, browser_mode=config.square_browser_mode)
    mentions = module.count_coin_mentions(posts, base_assets)
    candidates = bot._rank_trade_candidates(symbols, mentions)
    hot_assets = candidates[: config.top_coin_limit]
    source = "综合评分：Binance Square + 24h 涨幅榜"
    notes: list[str] = []
    if not mentions:
        LOGGER.warning("no valid long-only Binance Square mentions found; preview is using market momentum only")
        notes.append("广场没有有效做多提及，本次按 24h 市场动能排序。")
    candidate = candidates[0] if candidates else None
    if candidate is None:
        notes.append(
            "暂无标的同时满足 "
            f"涨幅≥{config.min_price_change_percent}%、"
            f"波动≥{config.min_volatility_percent}%、"
            f"成交额≥{config.min_quote_volume} {config.quote_asset}。"
        )
    if bot.state.position and bot.state.position.symbol:
        prefix = "模拟" if config.dry_run else "实盘"
        notes.append(f"当前已有{prefix}仓位 {bot.state.position.symbol}，执行一次不会重复开新仓。")
    return {
        "checked_at": now_text(),
        "source": source,
        "note": " ".join(notes),
        "post_count": len(posts),
        "hot_assets": [candidate_score_row(item) for item in hot_assets],
        "candidate": stringify_decimals(asdict(candidate)) if candidate else None,
    }


def candidate_score_row(candidate: Any) -> dict[str, Any]:
    return stringify_decimals(
        {
            "asset": candidate.base_asset,
            "symbol": candidate.symbol,
            "score": candidate.combined_score,
            "market_score": candidate.market_score,
            "square_score": candidate.square_score,
            "mentions": candidate.mention_count,
            "price_change_percent": candidate.price_change_percent,
            "volatility_percent": candidate.volatility_percent,
            "quote_volume": candidate.quote_volume,
        }
    )


def build_square_diagnostics(config: Any) -> dict[str, Any]:
    module = bot_module()
    bot = module.LongOnlyMomentumBot(config)
    diagnostics = bot.square.diagnose(config.top_post_limit, browser_mode=config.square_browser_mode)
    diagnostics["mode"] = "browser" if config.square_browser_mode else "static"
    if not config.square_browser_mode:
        diagnostics["hint"] = "Browser mode is off. Enable it to render Binance Square like a real browser."
    elif diagnostics.get("browser_error"):
        diagnostics["hint"] = diagnostics.get("browser_hint", "") or "Run fix_playwright_browser.bat to install Chromium for Playwright."
    else:
        diagnostics["hint"] = ""
    return stringify_decimals(diagnostics)


def reset_dry_run_state(runner: BotRunner, config: Any) -> dict[str, Any]:
    if not config.dry_run:
        raise RuntimeError("只有 dry-run / 模拟模式可以从页面清空状态；Live 模式请手动确认真实仓位。")
    with runner.lock:
        if runner.running:
            raise RuntimeError("当前有任务正在运行，请等待结束后再清空模拟仓位。")
    module = bot_module()
    module.save_state(config.state_file, module.BotState(updated_at=module.utc_now()))
    LOGGER.info("dry-run state reset: %s", config.state_file)
    with runner.lock:
        runner.last_config = config
        runner.last_error = ""
        runner.last_finished_at = now_text()
    return runner.status()


def safe_load_state(path: str) -> dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {
                "first_buy_done": False,
                "completed_round_trips": 0,
                "position": None,
                "updated_at": "",
                "trade_log": [],
            }
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        return {"error": str(exc)}


def sanitize_config(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {
            "api_key_loaded": bool(os.getenv("BINANCE_API_KEY")),
            "api_secret_loaded": bool(os.getenv("BINANCE_API_SECRET")),
        }
    data = stringify_decimals(asdict(config))
    data.pop("api_key", None)
    data.pop("api_secret", None)
    data["api_key_loaded"] = bool(config.api_key)
    data["api_secret_loaded"] = bool(config.api_secret)
    return data


def config_from_payload(payload: dict[str, Any]) -> Any:
    module = bot_module()
    square_urls = tuple(
        item.strip()
        for item in str(payload.get("square_urls") or os.getenv("BINANCE_SQUARE_URLS", ",".join(DEFAULT_SQUARE_URLS))).split(",")
        if item.strip()
    )
    testnet = bool(payload.get("testnet"))
    base_url = "https://testnet.binance.vision" if testnet else os.getenv("BINANCE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    return module.BotConfig(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        base_url=base_url,
        quote_asset=str(payload.get("quote_asset") or os.getenv("QUOTE_ASSET", "USDT")).upper(),
        order_quote_amount=decimal_value(payload, "order_quote_amount", "ORDER_QUOTE_USDT", "50"),
        min_quote_volume=decimal_value(payload, "min_quote_volume", "MIN_QUOTE_VOLUME_USDT", "5000000"),
        min_price_change_percent=decimal_value(payload, "min_price_change_percent", "MIN_PRICE_CHANGE_PERCENT", "3"),
        min_volatility_percent=decimal_value(payload, "min_volatility_percent", "MIN_VOLATILITY_PERCENT", "5"),
        top_post_limit=int_value(payload, "top_post_limit", "TOP_POST_LIMIT", 25),
        top_coin_limit=int_value(payload, "top_coin_limit", "TOP_COIN_LIMIT", 10),
        poll_seconds=int_value(payload, "poll_seconds", "POLL_SECONDS", 300),
        recv_window_ms=int_value(payload, "recv_window_ms", "RECV_WINDOW_MS", 5000),
        initial_stop_loss_pct=decimal_value(payload, "initial_stop_loss_pct", "INITIAL_STOP_LOSS_PCT", "20"),
        fixed_stop_loss_usdt=decimal_value(payload, "fixed_stop_loss_usdt", "FIXED_STOP_LOSS_USDT", "200"),
        fixed_stop_after_first_round_trip=bool_value(payload, "fixed_stop_after_first_round_trip", True),
        fixed_stop_equity_usdt=optional_decimal(payload, "fixed_stop_equity_usdt", "FIXED_STOP_EQUITY_USDT"),
        state_file=str(payload.get("state_file") or os.getenv("STATE_FILE", "bot_state.json")),
        dry_run=not bool(payload.get("live")),
        square_urls=square_urls,
        square_browser_mode=bool_value(payload, "square_browser_mode", False),
    )


def decimal_value(payload: dict[str, Any], key: str, env_name: str, default: str) -> Decimal:
    raw = payload.get(key)
    if raw in (None, ""):
        value = os.getenv(env_name, default)
        return Decimal(value) if value else Decimal(default)
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{key} must be a decimal number") from exc


def optional_decimal(payload: dict[str, Any], key: str, env_name: str) -> Decimal | None:
    raw = payload.get(key)
    if raw in (None, ""):
        value = os.getenv(env_name)
        return Decimal(value) if value else None
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{key} must be a decimal number") from exc


def int_value(payload: dict[str, Any], key: str, env_name: str, default: int) -> int:
    raw = payload.get(key)
    if raw in (None, ""):
        raw = os.getenv(env_name, str(default))
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


def bool_value(payload: dict[str, Any], key: str, default: bool) -> bool:
    if key not in payload:
        return default
    return bool(payload[key])


def stringify_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value.is_zero():
            return "0"
        text = format(value.normalize(), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {key: stringify_decimals(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify_decimals(item) for item in value]
    if isinstance(value, tuple):
        return [stringify_decimals(item) for item in value]
    return value


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def make_handler(runner: BotRunner) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "BinanceBotDashboard/1.0"

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route == "/":
                self._send_html(DASHBOARD_HTML)
                return
            if route == "/api/status":
                self._send_json(runner.status())
                return
            if route == "/api/defaults":
                self._send_json(sanitize_config(config_from_payload({})))
                return
            if route == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            try:
                payload = self._read_payload()
                if route == "/api/stop":
                    self._send_json(runner.stop())
                    return
                config = config_from_payload(payload)
                if route == "/api/preview":
                    self._send_json(runner.preview_signal(config))
                elif route == "/api/square-diagnose":
                    self._send_json(runner.diagnose_square(config))
                elif route == "/api/run-once":
                    self._send_json(runner.run_once(config))
                elif route == "/api/start-loop":
                    self._send_json(runner.start_loop(config))
                elif route == "/api/reset-dry-run-state":
                    self._send_json(reset_dry_run_state(runner, config))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except (ValueError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                LOGGER.exception("request failed")
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, fmt: str, *args: Any) -> None:
            rendered = fmt % args
            if (
                'GET / HTTP' in rendered
                or 'GET /api/status ' in rendered
                or 'GET /favicon.ico ' in rendered
            ):
                return
            LOGGER.info(fmt, *args)

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            body = self.rfile.read(length).decode("utf-8")
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(body or "{}")
            form = parse_qs(body)
            return {key: values[-1] for key, values in form.items()}

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Binance Momentum 控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1b1f24;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #147d64;
      --accent-strong: #0f5f4b;
      --danger: #b42318;
      --warn: #b54708;
      --ink: #202939;
      --shadow: 0 10px 28px rgba(16, 24, 40, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px max(24px, calc((100vw - 1440px) / 2 + 24px));
      border-bottom: 1px solid var(--line);
      background: #fff;
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 720; letter-spacing: 0; }
    main {
      width: min(1440px, calc(100% - 48px));
      margin: 0 auto;
      padding: 22px 0 32px;
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 18px;
    }
    .toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      background: #fff;
      font-size: 13px;
      white-space: nowrap;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .panel h2 {
      margin: 0;
      padding: 15px 16px 10px;
      font-size: 15px;
      color: var(--ink);
    }
    .controls { padding: 0 16px 16px; display: grid; gap: 12px; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    input {
      width: 100%;
      min-height: 38px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 8px 10px;
      color: var(--text);
      background: #fff;
      font-size: 14px;
    }
    input:focus { outline: 2px solid rgba(20, 125, 100, 0.22); border-color: var(--accent); }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .switches { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      font-size: 13px;
      min-height: 40px;
    }
    .check input { width: 16px; min-height: 16px; accent-color: var(--accent); }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding-top: 4px; }
    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      background: #fff;
      color: var(--ink);
      font-weight: 650;
      cursor: pointer;
      font-size: 14px;
    }
    button:hover { border-color: #98a2b3; }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.primary:hover { background: var(--accent-strong); }
    button.danger { color: var(--danger); border-color: #f3b5ae; }
    button:disabled { opacity: .55; cursor: wait; }
    .right { display: grid; gap: 18px; min-width: 0; }
    .notice {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 44px;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      font-size: 13px;
    }
    .notice strong { color: var(--ink); }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      min-height: 96px;
      padding: 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
    }
    .metric .k { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
    .metric .v { font-size: 22px; font-weight: 760; color: var(--ink); overflow-wrap: anywhere; }
    .content-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr); gap: 18px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 650; background: #fbfcfe; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .empty { padding: 16px; color: var(--muted); font-size: 13px; }
    .diagnostics { padding: 0 14px 14px; display: grid; gap: 12px; font-size: 13px; }
    .diagnostic-summary { color: var(--muted); line-height: 1.55; }
    .sample-post {
      border-top: 1px solid var(--line);
      padding-top: 10px;
      display: grid;
      gap: 4px;
    }
    .sample-post strong { color: var(--ink); }
    pre {
      margin: 0;
      padding: 12px 14px;
      max-height: 360px;
      overflow: auto;
      background: #111827;
      color: #e6edf3;
      border-radius: 0 0 8px 8px;
      font-size: 12px;
      line-height: 1.5;
    }
    .status-ok { color: var(--accent); }
    .status-warn { color: var(--warn); }
    .status-danger { color: var(--danger); }
    @media (max-width: 980px) {
      main { width: calc(100% - 32px); grid-template-columns: 1fr; padding: 16px 0; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .content-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; padding: 14px 16px; }
      .grid2, .switches, .buttons, .metrics { grid-template-columns: 1fr; }
      h1 { font-size: 18px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Binance Momentum 控制台</h1>
    <div class="toolbar">
      <span id="runStatus" class="pill">idle</span>
      <span id="keyStatus" class="pill">keys</span>
      <span id="updatedAt" class="pill">--</span>
    </div>
  </header>
  <main>
    <section class="panel">
      <h2>参数</h2>
      <form id="settings" class="controls">
        <div class="grid2">
          <label>计价币种<input name="quote_asset" value="USDT"></label>
          <label>单笔金额<input name="order_quote_amount" type="number" min="1" step="1" value="50"></label>
        </div>
        <div class="grid2">
          <label>最低涨幅 %<input name="min_price_change_percent" type="number" step="0.1" value="3"></label>
          <label>最低波动 %<input name="min_volatility_percent" type="number" step="0.1" value="5"></label>
        </div>
        <label>最低成交额<input name="min_quote_volume" type="number" min="0" step="100000" value="5000000"></label>
        <div class="grid2">
          <label>热门帖子<input name="top_post_limit" type="number" min="1" step="1" value="25"></label>
          <label>热门币种<input name="top_coin_limit" type="number" min="1" step="1" value="10"></label>
        </div>
        <div class="grid2">
          <label>轮询秒数<input name="poll_seconds" type="number" min="5" step="1" value="300"></label>
          <label>签名窗口 ms<input name="recv_window_ms" type="number" min="1000" step="100" value="5000"></label>
        </div>
        <div class="grid2">
          <label>初始止损 %<input name="initial_stop_loss_pct" type="number" min="0.1" step="0.1" value="20"></label>
          <label>固定止损 USDT<input name="fixed_stop_loss_usdt" type="number" min="1" step="1" value="200"></label>
        </div>
        <label>权益触发 USDT<input name="fixed_stop_equity_usdt" type="number" min="0" step="1" placeholder=""></label>
        <label>状态文件<input name="state_file" value="bot_state.json"></label>
        <div class="switches">
          <label class="check"><input name="testnet" type="checkbox">Testnet</label>
          <label class="check"><input name="live" type="checkbox">Live</label>
          <label class="check"><input name="square_browser_mode" type="checkbox">浏览器抓广场</label>
          <label class="check"><input name="fixed_stop_after_first_round_trip" type="checkbox" checked>回合止损</label>
        </div>
        <div class="buttons">
          <button type="button" id="preview">刷新信号</button>
          <button type="button" id="diagnose">诊断广场</button>
          <button type="button" id="runOnce" class="primary">执行一次</button>
          <button type="button" id="startLoop">启动循环</button>
          <button type="button" id="stopLoop" class="danger">停止</button>
          <button type="button" id="resetState" class="danger">清空模拟仓位</button>
        </div>
      </form>
    </section>
    <section class="right">
      <div class="notice">
        <span id="signalSource"><strong>数据源</strong> --</span>
        <span id="signalChecked">--</span>
      </div>
      <div class="metrics">
        <div class="metric"><div class="k">候选标的</div><div class="v" id="candidate">--</div></div>
        <div class="metric"><div class="k">当前仓位</div><div class="v" id="position">--</div></div>
        <div class="metric"><div class="k">交易回合</div><div class="v" id="roundTrips">0</div></div>
        <div class="metric"><div class="k">运行模式</div><div class="v" id="mode">idle</div></div>
      </div>
      <div class="content-grid">
        <section class="panel">
          <h2>热门币种</h2>
          <div id="hotAssets" class="empty">--</div>
        </section>
        <section class="panel">
          <h2>最近交易</h2>
          <div id="trades" class="empty">--</div>
        </section>
      </div>
      <section class="panel">
        <h2>广场诊断</h2>
        <div id="diagnostics" class="empty">尚未诊断</div>
      </section>
      <section class="panel">
        <h2>日志</h2>
        <pre id="logs">--</pre>
      </section>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const form = $("settings");
    const buttons = ["preview", "diagnose", "runOnce", "startLoop", "stopLoop", "resetState"].map($);

    function payload() {
      const data = Object.fromEntries(new FormData(form).entries());
      for (const name of ["testnet", "live", "square_browser_mode", "fixed_stop_after_first_round_trip"]) {
        data[name] = form.elements[name].checked;
      }
      return data;
    }

    async function post(path) {
      setBusy(true);
      try {
        const res = await fetch(path, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload())
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || res.statusText);
        render(data);
      } catch (err) {
        renderError(err.message);
      } finally {
        setBusy(false);
        setTimeout(refresh, 800);
      }
    }

    async function refresh() {
      try {
        const res = await fetch("/api/status", {cache: "no-store"});
        render(await res.json());
      } catch (err) {
        renderError(err.message);
      }
    }

    function setBusy(busy) {
      buttons.forEach((button) => button.disabled = busy);
    }

    function render(data) {
      const running = Boolean(data.running);
      $("runStatus").textContent = running ? "running" : (data.last_error ? "error" : "idle");
      $("runStatus").className = "pill " + (data.last_error ? "status-danger" : running ? "status-warn" : "status-ok");
      $("mode").textContent = data.mode || "idle";
      $("updatedAt").textContent = data.last_finished_at || data.last_started_at || "--";
      const cfg = data.config || {};
      $("keyStatus").textContent = (cfg.api_key_loaded && cfg.api_secret_loaded) ? "keys ready" : "keys missing";
      $("keyStatus").className = "pill " + ((cfg.api_key_loaded && cfg.api_secret_loaded) ? "status-ok" : "status-warn");

      const signal = data.last_signal || {};
      const candidate = signal.candidate;
      $("candidate").textContent = candidate
        ? `${candidate.symbol} ${formatPercent(candidate.price_change_percent)} · ${formatScore(candidate.combined_score)}`
        : "--";
      $("signalSource").innerHTML = `<strong>数据源</strong> ${escapeHtml(signal.source || "--")}${signal.note ? " · " + escapeHtml(signal.note) : ""}`;
      $("signalChecked").textContent = signal.checked_at ? `检查于 ${signal.checked_at}` : "--";
      renderHotAssets(signal.hot_assets || []);

      const state = data.state || {};
      const pos = state.position;
      const trades = state.trade_log || [];
      const lastTrade = trades.length ? trades[trades.length - 1] : {};
      const positionPrefix = lastTrade && lastTrade.dry_run ? "模拟 " : "";
      $("position").textContent = pos && pos.symbol ? `${positionPrefix}${pos.symbol} ${pos.quantity}` : "--";
      $("roundTrips").textContent = state.completed_round_trips ?? 0;
      renderTrades(trades);
      renderDiagnostics(data.last_diagnostics);
      $("logs").textContent = (data.logs || []).join("\n") || "--";
    }

    function renderError(message) {
      $("runStatus").textContent = "error";
      $("runStatus").className = "pill status-danger";
      $("mode").textContent = "error";
      $("signalSource").innerHTML = `<strong>数据源</strong> 请求失败`;
      $("signalChecked").textContent = "--";
      $("logs").textContent = message || "Request failed";
    }

    function renderHotAssets(items) {
      if (!items.length) {
        $("hotAssets").className = "empty";
        $("hotAssets").textContent = "--";
        return;
      }
      $("hotAssets").className = "";
      $("hotAssets").innerHTML = `<table><thead><tr><th>币种</th><th>综合</th><th>市场</th><th>广场</th><th>涨幅</th><th>波动</th></tr></thead><tbody>${
        items.map(item => `<tr>
          <td class="mono">${escapeHtml(item.symbol || item.asset)}</td>
          <td>${formatScore(item.score)}</td>
          <td>${formatScore(item.market_score)}</td>
          <td>${formatScore(item.square_score)}${item.mentions ? ` (${escapeHtml(item.mentions)})` : ""}</td>
          <td>${formatPercent(item.price_change_percent)}</td>
          <td>${formatPercent(item.volatility_percent)}</td>
        </tr>`).join("")
      }</tbody></table>`;
    }

    function renderTrades(items) {
      const recent = items.slice(-8).reverse();
      if (!recent.length) {
        $("trades").className = "empty";
        $("trades").textContent = "--";
        return;
      }
      $("trades").className = "";
      $("trades").innerHTML = `<table><thead><tr><th>时间</th><th>动作</th><th>标的</th><th>价格</th></tr></thead><tbody>${
        recent.map(item => `<tr><td>${escapeHtml(item.ts || "")}</td><td>${escapeHtml(item.action || "")}</td><td class="mono">${escapeHtml(item.symbol || "")}</td><td>${escapeHtml(item.price || "")}</td></tr>`).join("")
      }</tbody></table>`;
    }

    function renderDiagnostics(diagnostics) {
      if (!diagnostics) {
        $("diagnostics").className = "empty";
        $("diagnostics").textContent = "尚未诊断";
        return;
      }
      $("diagnostics").className = "diagnostics";
      const urls = diagnostics.urls || [];
      const samples = diagnostics.samples || [];
      const urlRows = urls.map(item => {
        const details = [
          `HTTP ${escapeHtml(item.status_code ?? "--")}`,
          `页面 ${escapeHtml(item.content_length ?? 0)} 字符`,
          `JSON 帖子 ${escapeHtml(item.json_posts ?? 0)}`,
          `HTML 帖子 ${escapeHtml(item.html_posts ?? 0)}`
        ].join(" · ");
        return `<tr><td>${escapeHtml(item.url || "")}</td><td>${details}${item.error ? " · " + escapeHtml(item.error) : ""}</td></tr>`;
      }).join("");
      const sampleHtml = samples.length ? samples.map(post => `
        <div class="sample-post">
          <strong>${escapeHtml(post.title || "帖子样例")}</strong>
          <span>${escapeHtml(post.text || "")}</span>
        </div>
      `).join("") : `<div class="empty">没有解析到帖子样例</div>`;
      $("diagnostics").innerHTML = `
        <div class="diagnostic-summary">
          模式：${escapeHtml(diagnostics.mode || "--")} · 可用做多帖子：${escapeHtml(diagnostics.total_posts ?? 0)}
          ${diagnostics.raw_posts !== undefined ? " · 原始解析：" + escapeHtml(diagnostics.raw_posts) : ""}
          ${diagnostics.filtered_out_posts !== undefined ? " · 已过滤：" + escapeHtml(diagnostics.filtered_out_posts) : ""}
          ${diagnostics.browser_posts_raw !== undefined ? " · 浏览器原始：" + escapeHtml(diagnostics.browser_posts_raw) : ""}
          ${diagnostics.browser_error ? "<br>浏览器错误：" + escapeHtml(diagnostics.browser_error) : ""}
          ${diagnostics.hint ? "<br>" + escapeHtml(diagnostics.hint) : ""}
        </div>
        <table><thead><tr><th>URL</th><th>结果</th></tr></thead><tbody>${urlRows}</tbody></table>
        ${sampleHtml}
      `;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function formatScore(value) {
      if (value === undefined || value === null || value === "") return "--";
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(1) : escapeHtml(value);
    }

    function formatPercent(value) {
      if (value === undefined || value === null || value === "") return "--";
      const number = Number(value);
      return Number.isFinite(number) ? `${number.toFixed(2)}%` : `${escapeHtml(value)}%`;
    }

    $("preview").addEventListener("click", () => post("/api/preview"));
    $("diagnose").addEventListener("click", () => post("/api/square-diagnose"));
    $("runOnce").addEventListener("click", () => post("/api/run-once"));
    $("startLoop").addEventListener("click", () => post("/api/start-loop"));
    $("stopLoop").addEventListener("click", () => post("/api/stop"));
    $("resetState").addEventListener("click", () => {
      if (confirm("清空 bot_state.json 中的模拟仓位和交易记录？")) post("/api/reset-dry-run-state");
    });
    refresh();
    setInterval(refresh, 2500);
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web dashboard for the Binance momentum bot")
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    parser.add_argument("--open-browser", action="store_true", help="open the dashboard after the server starts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    memory_handler = MemoryLogHandler()
    BOT_LOGGER.addHandler(memory_handler)
    LOGGER.addHandler(memory_handler)

    runner = BotRunner(memory_handler)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(runner))
    url = f"http://{args.host}:{args.port}/"
    LOGGER.info("dashboard listening on %s", url)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        runner.stop()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
