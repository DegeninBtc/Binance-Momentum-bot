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
        self.price_cache: dict[str, dict[str, Any]] = {}

    def status(self) -> dict[str, Any]:
        with self.lock:
            config = self.last_config
            payload = {
                "running": self.running,
                "mode": self.mode,
                "last_error": self.last_error,
                "last_started_at": self.last_started_at,
                "last_finished_at": self.last_finished_at,
                "last_signal": self.last_signal,
                "last_diagnostics": self.last_diagnostics,
                "logs": self.log_handler.tail(),
            }
        if config is None:
            config = config_from_payload({})
        state_file = config.state_file
        payload["config"] = sanitize_config(config)
        state = safe_load_state(state_file)
        state = enrich_state_for_status(state, config, self)
        payload["state"] = state
        return payload

    def ticker_price_for_status(self, config: Any, symbol: str) -> tuple[Decimal | None, str]:
        cache_key = f"{config.base_url}|{symbol}"
        now = time.monotonic()
        with self.lock:
            cached = self.price_cache.get(cache_key)
            if cached and now - float(cached.get("cached_at", 0)) < 10:
                return cached.get("price"), str(cached.get("error", ""))
        try:
            price = bot_module().BinanceSpotClient(config).ticker_price(symbol)
            error = ""
        except Exception as exc:
            price = None
            error = str(exc)
        with self.lock:
            self.price_cache[cache_key] = {"price": price, "error": error, "cached_at": now}
        return price, error

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


def enrich_state_for_status(state: dict[str, Any], config: Any, runner: BotRunner) -> dict[str, Any]:
    position = state.get("position")
    if not isinstance(position, dict) or not position.get("symbol"):
        return state

    snapshot = build_position_snapshot(position, state, config, runner)
    if not snapshot:
        return state

    enriched = dict(state)
    enriched["position_snapshot"] = stringify_decimals(snapshot)
    return enriched


def build_position_snapshot(
    position: dict[str, Any],
    state: dict[str, Any],
    config: Any,
    runner: BotRunner,
) -> dict[str, Any] | None:
    quantity = decimal_from_state(position.get("quantity"))
    entry_price = decimal_from_state(position.get("entry_price"))
    if quantity is None or entry_price is None or quantity <= 0 or entry_price <= 0:
        return None

    quote_spent = decimal_from_state(position.get("quote_spent")) or quantity * entry_price
    symbol = str(position.get("symbol") or "")
    current_price, price_error = runner.ticker_price_for_status(config, symbol)
    is_dry_run = position_is_dry_run(position, state, config)
    fixed_stop_enabled = (
        bool(config.fixed_stop_after_first_round_trip)
        and int(state.get("completed_round_trips") or 0) > 0
    )
    stop_price = entry_price * (Decimal("1") - config.initial_stop_loss_pct / Decimal("100"))

    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "base_asset": position.get("base_asset") or symbol.removesuffix(config.quote_asset),
        "quote_asset": config.quote_asset,
        "dry_run": is_dry_run,
        "mode_label": "模拟" if is_dry_run else "实盘",
        "quantity": quantity,
        "entry_price": entry_price,
        "quote_spent": quote_spent,
        "opened_at": position.get("opened_at", ""),
        "current_price": current_price,
        "price_error": price_error,
        "active_stop_mode": "fixed-usdt" if fixed_stop_enabled else "percent",
        "stop_price": stop_price,
        "fixed_stop_loss_usdt": config.fixed_stop_loss_usdt,
        "initial_stop_loss_pct": config.initial_stop_loss_pct,
    }

    if current_price is None:
        return snapshot

    market_value = quantity * current_price
    unrealized_pnl = market_value - quote_spent
    unrealized_pnl_pct = (current_price - entry_price) / entry_price * Decimal("100")
    unrealized_loss = max(Decimal("0"), -unrealized_pnl)
    stop_triggered = (
        unrealized_loss >= config.fixed_stop_loss_usdt
        if fixed_stop_enabled
        else current_price <= stop_price
    )
    stop_distance_pct = (current_price - stop_price) / current_price * Decimal("100") if current_price > 0 else None
    snapshot.update(
        {
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "unrealized_loss": unrealized_loss,
            "stop_distance_pct": stop_distance_pct,
            "stop_triggered": stop_triggered,
        }
    )
    return snapshot


def decimal_from_state(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def position_is_dry_run(position: dict[str, Any], state: dict[str, Any], config: Any) -> bool:
    symbol = position.get("symbol")
    for item in reversed(state.get("trade_log") or []):
        if item.get("symbol") == symbol and "BUY" in str(item.get("action", "")):
            return bool(item.get("dry_run", config.dry_run))
    return bool(config.dry_run)


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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{
      --mono:'SFMono-Regular',ui-monospace,Menlo,Consolas,monospace;
      --sans:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      --radius:10px;--radius-sm:6px;
      --green:#22c55e;--green-dim:rgba(34,197,94,.12);--green-glow:rgba(34,197,94,.25);
      --red:#ef4444;--red-dim:rgba(239,68,68,.12);
      --amber:#f59e0b;--amber-dim:rgba(245,158,11,.12);
      --blue:#3b82f6;--blue-dim:rgba(59,130,246,.12);
      --purple:#a78bfa;
    }
    [data-theme=dark]{
      --bg:#0b0e14;--bg2:#111621;--card:#171c28;--card-hover:#1c2233;
      --border:#252d3d;--border-light:#2e3850;
      --text:#e2e8f0;--text-muted:#7b8ba5;--text-dim:#4a5672;
      --topbar-bg:rgba(11,14,20,.85);--th-bg:rgba(0,0,0,.15);
      --row-hover:rgba(255,255,255,.02);--log-color:var(--green);
    }
    [data-theme=light]{
      --bg:#f4f6f9;--bg2:#ebeef3;--card:#ffffff;--card-hover:#f9fafb;
      --border:#dce1e8;--border-light:#c9d0da;
      --text:#1a1d24;--text-muted:#5f6b7a;--text-dim:#9ca3af;
      --topbar-bg:rgba(255,255,255,.88);--th-bg:rgba(0,0,0,.03);
      --row-hover:rgba(0,0,0,.02);--log-color:#1a6b3c;
    }
    html{font-size:14px}
    body{font-family:var(--sans);background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased;transition:background .25s,color .25s}

    /* ── Header ── */
    .topbar{
      position:sticky;top:0;z-index:10;
      display:flex;align-items:center;justify-content:space-between;gap:12px;
      padding:0 28px;height:56px;
      background:var(--topbar-bg);backdrop-filter:blur(16px);
      border-bottom:1px solid var(--border);
    }
    .topbar h1{font-size:16px;font-weight:700;letter-spacing:0;display:flex;align-items:center;gap:10px}
    .topbar h1 .logo{width:22px;height:22px;border-radius:5px;background:linear-gradient(135deg,#f0b90b,#d4a20a);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;color:#111}
    .badges{display:flex;gap:8px;align-items:center}
    .badge{
      display:inline-flex;align-items:center;gap:6px;
      height:28px;padding:0 10px;border-radius:999px;
      font-size:12px;font-weight:500;
      border:1px solid var(--border);color:var(--text-muted);background:var(--bg2);
    }
    .badge .dot{width:7px;height:7px;border-radius:50%}
    .badge.ok .dot{background:var(--green);box-shadow:0 0 6px var(--green-glow)}
    .badge.warn .dot{background:var(--amber)}
    .badge.err .dot{background:var(--red)}
    .badge.ok{color:var(--green);border-color:rgba(34,197,94,.2)}
    .badge.warn{color:var(--amber);border-color:rgba(245,158,11,.2)}
    .badge.err{color:var(--red);border-color:rgba(239,68,68,.2)}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
    .badge.running .dot{animation:pulse 1.6s ease-in-out infinite}

    /* ── Layout ── */
    .shell{max-width:1400px;margin:0 auto;padding:20px 24px 40px}
    .kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
    .kpi{
      background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
      padding:16px 18px;display:flex;flex-direction:column;gap:6px;
      transition:border-color .2s;
    }
    .kpi:hover{border-color:var(--border-light)}
    .kpi .label{font-size:12px;color:var(--text-muted);font-weight:500;text-transform:uppercase;letter-spacing:0}
    .kpi .value{font-size:20px;font-weight:700;font-family:var(--mono);color:var(--text);overflow-wrap:anywhere;line-height:1.25}
    .kpi .value.small{font-size:16px}
    .kpi .meta{font-size:12px;color:var(--text-muted);line-height:1.45;min-height:18px}
    .kpi .meta strong{color:var(--text);font-weight:600}
    .kpi.loss{border-color:rgba(239,68,68,.28)}
    .kpi.profit{border-color:rgba(34,197,94,.28)}

    /* ── Action bar ── */
    .action-bar{
      display:flex;gap:8px;flex-wrap:wrap;align-items:center;
      padding:14px 18px;margin-bottom:18px;
      background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
    }
    .action-bar .sep{width:1px;height:28px;background:var(--border);margin:0 4px}
    .btn{
      display:inline-flex;align-items:center;justify-content:center;gap:6px;
      height:36px;padding:0 16px;border-radius:var(--radius-sm);
      font-size:13px;font-weight:600;font-family:var(--sans);
      border:1px solid var(--border);background:var(--bg2);color:var(--text-muted);
      cursor:pointer;transition:all .15s;white-space:nowrap;
    }
    .btn:hover{background:var(--card-hover);color:var(--text);border-color:var(--border-light)}
    .btn:active{transform:scale(.97)}
    .btn:disabled{opacity:.4;cursor:wait;transform:none}
    .btn.primary{background:var(--green);color:#fff;border-color:var(--green)}
    .btn.primary:hover{background:#16a34a;border-color:#16a34a}
    .btn.danger{color:var(--red);border-color:rgba(239,68,68,.3)}
    .btn.danger:hover{background:var(--red-dim);border-color:rgba(239,68,68,.5)}
    .btn .icon{font-size:15px;line-height:1}
    .source-bar{
      display:flex;align-items:center;justify-content:space-between;gap:12px;
      padding:10px 16px;margin-bottom:18px;
      border-radius:var(--radius-sm);
      background:var(--bg2);border:1px solid var(--border);
      font-size:12px;color:var(--text-muted);
    }
    .source-bar strong{color:var(--text);font-weight:600}

    /* ── Tabs ── */
    .tabs-header{
      display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:0;
    }
    .tab-btn{
      padding:10px 20px;font-size:13px;font-weight:600;color:var(--text-muted);
      background:none;border:none;border-bottom:2px solid transparent;
      margin-bottom:-2px;cursor:pointer;transition:all .15s;font-family:var(--sans);
    }
    .tab-btn:hover{color:var(--text)}
    .tab-btn.active{color:var(--green);border-bottom-color:var(--green)}
    .tab-panel{display:none;background:var(--card);border:1px solid var(--border);border-top:none;border-radius:0 0 var(--radius) var(--radius);min-height:200px}
    .tab-panel.active{display:block}

    /* ── Tables ── */
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px 14px;text-align:left;font-size:13px;border-bottom:1px solid var(--border)}
    th{color:var(--text-muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0;background:var(--th-bg)}
    td{color:var(--text)}
    tbody tr{transition:background .12s}
    tbody tr:hover{background:var(--row-hover)}
    tbody tr:first-child{background:rgba(34,197,94,.04)}
    .mono{font-family:var(--mono)}
    .c-green{color:var(--green)}
    .c-red{color:var(--red)}
    .c-amber{color:var(--amber)}
    .tag{
      display:inline-flex;align-items:center;padding:2px 8px;border-radius:4px;
      font-size:11px;font-weight:600;
    }
    .tag-buy{background:var(--green-dim);color:var(--green)}
    .tag-sell{background:var(--red-dim);color:var(--red)}
    .tag-dry{background:var(--amber-dim);color:var(--amber)}
    .tag-live{background:var(--red-dim);color:var(--red)}

    /* ── Settings panel ── */
    .settings-grid{padding:20px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
    .settings-grid .full{grid-column:1/-1}
    .field{display:flex;flex-direction:column;gap:5px}
    .field-label{font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0}
    .field input[type=number],.field input[type=text],.field input:not([type]){
      height:36px;padding:0 12px;border-radius:var(--radius-sm);
      border:1px solid var(--border);background:var(--bg);color:var(--text);
      font-size:13px;font-family:var(--mono);transition:border-color .15s;width:100%;
    }
    .field input:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 2px rgba(34,197,94,.12)}
    .switches-row{display:flex;gap:10px;flex-wrap:wrap}
    .switch-item{
      display:flex;align-items:center;gap:8px;
      padding:8px 14px;border:1px solid var(--border);border-radius:var(--radius-sm);
      font-size:13px;color:var(--text-muted);cursor:pointer;transition:all .15s;
    }
    .switch-item:hover{border-color:var(--border-light);color:var(--text)}
    .switch-item input[type=checkbox]{
      width:16px;height:16px;accent-color:var(--green);cursor:pointer;
    }

    /* ── Diagnostics ── */
    .diag-content{padding:16px;font-size:13px;color:var(--text-muted);line-height:1.7}
    .diag-content strong{color:var(--text)}
    .sample-post{border-top:1px solid var(--border);padding:12px 0;display:grid;gap:4px}
    .sample-post strong{color:var(--text);font-size:13px}
    .sample-post span{color:var(--text-muted);font-size:12px}

    /* ── Logs ── */
    .log-pre{
      margin:0;padding:16px 18px;
      max-height:420px;overflow:auto;
      background:var(--bg);color:var(--log-color);
      font-family:var(--mono);font-size:12px;line-height:1.65;
      border-radius:0 0 var(--radius) var(--radius);
    }

    /* ── Empty state ── */
    .empty-state{padding:32px 20px;text-align:center;color:var(--text-dim);font-size:13px}

    /* ── Responsive ── */
    @media(max-width:900px){
      .kpi-row{grid-template-columns:1fr 1fr}
      .settings-grid{grid-template-columns:1fr}
      .shell{padding:14px 14px 32px}
    }
    @media(max-width:560px){
      .topbar{padding:0 14px;height:48px}
      .topbar h1{font-size:14px}
      .kpi-row{grid-template-columns:1fr}
      .action-bar{padding:10px 12px}
      .tab-btn{padding:8px 14px;font-size:12px}
    }
    /* ── Theme toggle ── */
    .theme-toggle{
      width:32px;height:32px;border-radius:50%;border:1px solid var(--border);
      background:var(--bg2);color:var(--text-muted);cursor:pointer;
      display:flex;align-items:center;justify-content:center;font-size:16px;
      transition:all .2s;flex-shrink:0;
    }
    .theme-toggle:hover{border-color:var(--border-light);color:var(--text);background:var(--card-hover)}
  </style>
  <script>document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark'</script>
</head>
<body>

<div class="topbar">
  <h1><span class="logo">B</span>Momentum 控制台</h1>
  <div class="badges">
    <span id="runStatus" class="badge ok"><span class="dot"></span>idle</span>
    <span id="keyStatus" class="badge warn"><span class="dot"></span>keys</span>
    <span id="updatedAt" class="badge">--</span>
    <button id="themeToggle" class="theme-toggle" title="切换主题">🌙</button>
  </div>
</div>

<div class="shell">
  <!-- KPI Cards -->
  <div class="kpi-row">
    <div class="kpi">
      <span class="label">候选标的</span>
      <span class="value" id="candidate">--</span>
      <span class="meta" id="candidateMeta">点击刷新信号后生成</span>
    </div>
    <div class="kpi">
      <span class="label">当前仓位</span>
      <span class="value" id="position">--</span>
      <span class="meta" id="positionMeta">暂无持仓</span>
    </div>
    <div class="kpi" id="pnlCard">
      <span class="label">浮动盈亏</span>
      <span class="value" id="positionPnl">--</span>
      <span class="meta" id="positionValue">等待当前价格</span>
    </div>
    <div class="kpi">
      <span class="label">运行 / 风控</span>
      <span class="value" id="mode">idle</span>
      <span class="meta" id="riskMeta">交易回合 <strong id="roundTrips">0</strong></span>
    </div>
  </div>

  <!-- Action Bar -->
  <div class="action-bar">
    <button type="button" id="preview" class="btn"><span class="icon">⟳</span>刷新信号</button>
    <button type="button" id="diagnose" class="btn"><span class="icon">⚙</span>诊断广场</button>
    <div class="sep"></div>
    <button type="button" id="runOnce" class="btn primary"><span class="icon">▶</span>执行一次</button>
    <button type="button" id="startLoop" class="btn"><span class="icon">⏵⏵</span>启动循环</button>
    <div class="sep"></div>
    <button type="button" id="stopLoop" class="btn danger"><span class="icon">■</span>停止</button>
    <button type="button" id="resetState" class="btn danger"><span class="icon">↺</span>清空模拟仓位</button>
  </div>

  <!-- Source info -->
  <div class="source-bar">
    <span id="signalSource"><strong>数据源</strong> --</span>
    <span id="signalChecked">--</span>
  </div>

  <!-- Tabs -->
  <div class="tabs-header">
    <button class="tab-btn active" data-tab="hot">热门币种</button>
    <button class="tab-btn" data-tab="trades">交易记录</button>
    <button class="tab-btn" data-tab="diag">广场诊断</button>
    <button class="tab-btn" data-tab="logs">日志</button>
    <button class="tab-btn" data-tab="settings">⚙ 设置</button>
  </div>

  <!-- Tab: Hot Assets -->
  <div class="tab-panel active" id="panel-hot">
    <div id="hotAssets" class="empty-state">点击「刷新信号」查看热门币种排行</div>
  </div>

  <!-- Tab: Trades -->
  <div class="tab-panel" id="panel-trades">
    <div id="trades" class="empty-state">暂无交易记录</div>
  </div>

  <!-- Tab: Diagnostics -->
  <div class="tab-panel" id="panel-diag">
    <div id="diagnostics" class="empty-state">点击「诊断广场」检查数据抓取状态</div>
  </div>

  <!-- Tab: Logs -->
  <div class="tab-panel" id="panel-logs">
    <pre class="log-pre" id="logs">等待日志...</pre>
  </div>

  <!-- Tab: Settings -->
  <div class="tab-panel" id="panel-settings">
    <form id="settings" class="settings-grid">
      <div class="field"><span class="field-label">计价币种</span><input name="quote_asset" value="USDT"></div>
      <div class="field"><span class="field-label">单笔金额</span><input name="order_quote_amount" type="number" min="1" step="1" value="50"></div>
      <div class="field"><span class="field-label">最低涨幅 %</span><input name="min_price_change_percent" type="number" step="0.1" value="3"></div>
      <div class="field"><span class="field-label">最低波动 %</span><input name="min_volatility_percent" type="number" step="0.1" value="5"></div>
      <div class="field full"><span class="field-label">最低成交额</span><input name="min_quote_volume" type="number" min="0" step="100000" value="5000000"></div>
      <div class="field"><span class="field-label">热门帖子数</span><input name="top_post_limit" type="number" min="1" step="1" value="25"></div>
      <div class="field"><span class="field-label">热门币种数</span><input name="top_coin_limit" type="number" min="1" step="1" value="10"></div>
      <div class="field"><span class="field-label">轮询秒数</span><input name="poll_seconds" type="number" min="5" step="1" value="300"></div>
      <div class="field"><span class="field-label">签名窗口 ms</span><input name="recv_window_ms" type="number" min="1000" step="100" value="5000"></div>
      <div class="field"><span class="field-label">初始止损 %</span><input name="initial_stop_loss_pct" type="number" min="0.1" step="0.1" value="20"></div>
      <div class="field"><span class="field-label">固定止损 USDT</span><input name="fixed_stop_loss_usdt" type="number" min="1" step="1" value="200"></div>
      <div class="field"><span class="field-label">权益触发 USDT</span><input name="fixed_stop_equity_usdt" type="number" min="0" step="1" placeholder=""></div>
      <div class="field"><span class="field-label">状态文件</span><input name="state_file" value="bot_state.json"></div>
      <div class="full switches-row">
        <label class="switch-item"><input name="testnet" type="checkbox">Testnet</label>
        <label class="switch-item"><input name="live" type="checkbox">Live 实盘</label>
        <label class="switch-item"><input name="square_browser_mode" type="checkbox">浏览器抓广场</label>
        <label class="switch-item"><input name="fixed_stop_after_first_round_trip" type="checkbox" checked>首回合后固定止损</label>
      </div>
    </form>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const form = $("settings");
const buttons = ["preview","diagnose","runOnce","startLoop","stopLoop","resetState"].map($);

/* ── Theme toggle ── */
function applyThemeIcon() {
  const isDark = document.documentElement.dataset.theme === 'dark';
  $("themeToggle").textContent = isDark ? '☀️' : '🌙';
  $("themeToggle").title = isDark ? '切换亮色' : '切换暗色';
}
$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('theme', next);
  applyThemeIcon();
});
applyThemeIcon();

/* ── Tabs ── */
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("panel-" + btn.dataset.tab).classList.add("active");
  });
});

function payload() {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const name of ["testnet","live","square_browser_mode","fixed_stop_after_first_round_trip"]) {
    data[name] = form.elements[name].checked;
  }
  return data;
}

async function post(path) {
  setBusy(true);
  try {
    const res = await fetch(path, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload())
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    render(data);
  } catch(err) {
    renderError(err.message);
  } finally {
    setBusy(false);
    setTimeout(refresh, 800);
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/status",{cache:"no-store"});
    render(await res.json());
  } catch(err) {
    renderError(err.message);
  }
}

function setBusy(busy) {
  buttons.forEach(b => b.disabled = busy);
}

function render(data) {
  const running = Boolean(data.running);
  const status = $("runStatus");
  const hasError = Boolean(data.last_error);
  status.textContent = running ? "running" : (hasError ? "error" : "idle");
  status.className = "badge " + (hasError ? "err" : running ? "warn running" : "ok");

  $("mode").textContent = data.mode || "idle";
  $("updatedAt").textContent = data.last_finished_at || data.last_started_at || "--";

  const cfg = data.config || {};
  const keysOk = cfg.api_key_loaded && cfg.api_secret_loaded;
  $("keyStatus").textContent = keysOk ? "Keys ✓" : "Keys ✗";
  $("keyStatus").className = "badge " + (keysOk ? "ok" : "warn");

  const signal = data.last_signal || {};
  const candidate = signal.candidate;
  renderCandidate(candidate);

  $("signalSource").innerHTML = '<strong>数据源</strong> ' + esc(signal.source || "--") + (signal.note ? " · " + esc(signal.note) : "");
  $("signalChecked").textContent = signal.checked_at ? "检查于 " + signal.checked_at : "--";
  renderHotAssets(signal.hot_assets || []);

  const state = data.state || {};
  const pos = state.position;
  const snapshot = state.position_snapshot || null;
  const trades = state.trade_log || [];
  renderPosition(pos, snapshot);
  $("roundTrips").textContent = state.completed_round_trips ?? 0;
  renderTrades(trades);
  renderDiagnostics(data.last_diagnostics);
  $("logs").textContent = (data.logs || []).join("\n") || "--";
}

function renderError(msg) {
  $("runStatus").textContent = "error";
  $("runStatus").className = "badge err";
  $("mode").textContent = "error";
  $("signalSource").innerHTML = '<strong>数据源</strong> 请求失败';
  $("signalChecked").textContent = "--";
  $("logs").textContent = msg || "Request failed";
}

function renderCandidate(candidate) {
  const el = $("candidate");
  const meta = $("candidateMeta");
  if (!candidate) {
    el.textContent = "--";
    el.classList.remove("c-green");
    meta.textContent = "点击刷新信号后生成";
    return;
  }
  el.textContent = candidate.symbol || "--";
  el.classList.add("c-green");
  meta.innerHTML =
    "涨幅 <strong>" + formatPercent(candidate.price_change_percent) + "</strong>" +
    " · 分数 <strong>" + formatScore(candidate.combined_score) + "</strong>" +
    " · 波动 " + formatPercent(candidate.volatility_percent);
}

function renderPosition(pos, snapshot) {
  const positionEl = $("position");
  const metaEl = $("positionMeta");
  const pnlEl = $("positionPnl");
  const valueEl = $("positionValue");
  const riskEl = $("riskMeta");
  const pnlCard = $("pnlCard");
  pnlCard.classList.remove("profit", "loss");

  if (!pos || !pos.symbol) {
    positionEl.textContent = "--";
    positionEl.classList.remove("small");
    metaEl.textContent = "暂无持仓";
    pnlEl.textContent = "--";
    pnlEl.className = "value";
    valueEl.textContent = "等待模拟或实盘买入";
    riskEl.innerHTML = '交易回合 <strong id="roundTrips">' + esc($("roundTrips")?.textContent || "0") + "</strong>";
    return;
  }

  const mode = snapshot?.mode_label || "持仓";
  positionEl.textContent = mode + " " + pos.symbol;
  positionEl.classList.remove("small");
  const qty = snapshot?.quantity ?? pos.quantity;
  const entry = snapshot?.entry_price ?? pos.entry_price;
  const current = snapshot?.current_price;
  metaEl.innerHTML =
    "数量 <strong>" + formatQty(qty) + "</strong>" +
    " · 成本 <strong>" + formatPrice(entry) + "</strong>" +
    (current ? " · 现价 <strong>" + formatPrice(current) + "</strong>" : "");

  if (!snapshot || !snapshot.market_value) {
    pnlEl.textContent = "--";
    pnlEl.className = "value";
    valueEl.textContent = snapshot?.price_error ? "当前价获取失败：" + snapshot.price_error : "等待当前价格";
  } else {
    const pnl = Number(snapshot.unrealized_pnl);
    pnlEl.textContent = signedMoney(snapshot.unrealized_pnl, snapshot.quote_asset) + " · " + signedPercent(snapshot.unrealized_pnl_pct);
    pnlEl.className = "value " + (pnl >= 0 ? "c-green" : "c-red");
    pnlCard.classList.add(pnl >= 0 ? "profit" : "loss");
    valueEl.innerHTML =
      "市值 <strong>" + formatMoney(snapshot.market_value, snapshot.quote_asset) + "</strong>" +
      " · 本金 " + formatMoney(snapshot.quote_spent, snapshot.quote_asset);
  }

  const stopMode = snapshot?.active_stop_mode === "fixed-usdt" ? "固定金额止损" : "百分比止损";
  const stopText = snapshot?.stop_price ? "止损价 " + formatPrice(snapshot.stop_price) : "止损价 --";
  const distanceText = snapshot?.stop_distance_pct ? " · 距止损 " + formatPercent(snapshot.stop_distance_pct) : "";
  const triggered = snapshot?.stop_triggered;
  riskEl.innerHTML =
    (triggered ? '<span class="c-red">已触发止损</span>' : '<span class="c-green">风控正常</span>') +
    " · " + stopMode + " · " + stopText + distanceText +
    ' · 交易回合 <strong id="roundTrips">' + esc($("roundTrips")?.textContent || "0") + "</strong>";
}

function renderHotAssets(items) {
  const el = $("hotAssets");
  if (!items.length) { el.className = "empty-state"; el.textContent = "点击「刷新信号」查看热门币种排行"; return; }
  el.className = "";
  el.innerHTML = '<table><thead><tr><th>#</th><th>币种</th><th>综合分</th><th>市场分</th><th>广场分</th><th>24h 涨幅</th><th>波动率</th></tr></thead><tbody>' +
    items.map((item, i) => '<tr>' +
      '<td class="mono" style="color:var(--text-dim)">' + (i+1) + '</td>' +
      '<td class="mono" style="font-weight:600">' + esc(item.symbol || item.asset) + '</td>' +
      '<td class="mono" style="font-weight:700;color:var(--purple)">' + formatScore(item.score) + '</td>' +
      '<td class="mono">' + formatScore(item.market_score) + '</td>' +
      '<td class="mono">' + formatScore(item.square_score) + (item.mentions ? ' <span style="color:var(--text-dim)">(' + esc(item.mentions) + ')</span>' : '') + '</td>' +
      '<td class="mono c-green">' + formatPercent(item.price_change_percent) + '</td>' +
      '<td class="mono c-amber">' + formatPercent(item.volatility_percent) + '</td>' +
    '</tr>').join("") +
  '</tbody></table>';
}

function renderTrades(items) {
  const recent = items.slice(-10).reverse();
  const el = $("trades");
  if (!recent.length) { el.className = "empty-state"; el.textContent = "暂无交易记录"; return; }
  el.className = "";
  el.innerHTML = '<table><thead><tr><th>时间</th><th>模式</th><th>动作</th><th>标的</th><th>数量</th><th>价格</th><th>成交额</th></tr></thead><tbody>' +
    recent.map(item => {
      const action = (item.action || "");
      const isBuy = action.includes("BUY");
      const isDry = Boolean(item.dry_run);
      const tagClass = isBuy ? "tag tag-buy" : "tag tag-sell";
      return '<tr>' +
        '<td>' + esc(formatTime(item.ts)) + '</td>' +
        '<td><span class="tag ' + (isDry ? "tag-dry" : "tag-live") + '">' + (isDry ? "模拟" : "实盘") + '</span></td>' +
        '<td><span class="' + tagClass + '">' + esc(actionLabel(action)) + '</span></td>' +
        '<td class="mono" style="font-weight:600">' + esc(item.symbol || "") + '</td>' +
        '<td class="mono">' + formatQty(item.quantity) + '</td>' +
        '<td class="mono">' + formatPrice(item.price) + '</td>' +
        '<td class="mono">' + formatMoney(tradeAmount(item), "") + '</td>' +
      '</tr>';
    }).join("") +
  '</tbody></table>';
}

function renderDiagnostics(diagnostics) {
  const el = $("diagnostics");
  if (!diagnostics) { el.className = "empty-state"; el.textContent = "点击「诊断广场」检查数据抓取状态"; return; }
  el.className = "diag-content";
  const urls = diagnostics.urls || [];
  const samples = diagnostics.samples || [];
  const urlRows = urls.map(item => {
    const d = [
      'HTTP ' + esc(item.status_code ?? "--"),
      '页面 ' + esc(item.content_length ?? 0) + ' 字符',
      'JSON ' + esc(item.json_posts ?? 0),
      'HTML ' + esc(item.html_posts ?? 0)
    ].join(' · ');
    return '<tr><td style="word-break:break-all">' + esc(item.url || "") + '</td><td>' + d + (item.error ? ' · <span class="c-red">' + esc(item.error) + '</span>' : '') + '</td></tr>';
  }).join("");
  const sampleHtml = samples.length ? samples.map(p =>
    '<div class="sample-post"><strong>' + esc(p.title||"帖子样例") + '</strong><span>' + esc(p.text||"") + '</span></div>'
  ).join("") : '<div class="empty-state">没有解析到帖子样例</div>';
  el.innerHTML =
    '<div style="margin-bottom:12px">' +
      '<strong>模式</strong> ' + esc(diagnostics.mode||"--") + ' · <strong>有效帖子</strong> ' + esc(diagnostics.total_posts??0) +
      (diagnostics.raw_posts!==undefined ? ' · <strong>原始</strong> '+esc(diagnostics.raw_posts) : '') +
      (diagnostics.filtered_out_posts!==undefined ? ' · <strong>过滤</strong> '+esc(diagnostics.filtered_out_posts) : '') +
      (diagnostics.browser_posts_raw!==undefined ? ' · <strong>浏览器</strong> '+esc(diagnostics.browser_posts_raw) : '') +
      (diagnostics.browser_error ? '<br><span class="c-red">浏览器错误：'+esc(diagnostics.browser_error)+'</span>' : '') +
      (diagnostics.hint ? '<br><span class="c-amber">'+esc(diagnostics.hint)+'</span>' : '') +
    '</div>' +
    '<table><thead><tr><th>URL</th><th>结果</th></tr></thead><tbody>'+urlRows+'</tbody></table>' +
    sampleHtml;
}

function esc(v){return String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function asNumber(v){const n=Number(v);return Number.isFinite(n)?n:null}
function trimNumber(v, maxDigits, minDigits=0){
  const n=asNumber(v);
  if(n===null)return esc(v ?? "--");
  return n.toLocaleString("en-US",{minimumFractionDigits:minDigits,maximumFractionDigits:maxDigits});
}
function formatScore(v){if(v==null||v==="")return"--";return trimNumber(v,1,1)}
function formatPercent(v){if(v==null||v==="")return"--";return trimNumber(v,2,2)+"%"}
function signedPercent(v){
  const n=asNumber(v);
  if(n===null)return"--";
  const sign=n>0?"+":"";
  return sign+trimNumber(n,2,2)+"%";
}
function formatQty(v){if(v==null||v==="")return"--";return trimNumber(v,6)}
function formatPrice(v){if(v==null||v==="")return"--";return trimNumber(v,8)}
function formatMoney(v, quoteAsset){
  if(v==null||v==="")return"--";
  const text=trimNumber(v,2,2);
  return quoteAsset ? text+" "+esc(quoteAsset) : text;
}
function signedMoney(v, quoteAsset){
  const n=asNumber(v);
  if(n===null)return"--";
  const sign=n>0?"+":n<0?"-":"";
  return sign+formatMoney(Math.abs(n), quoteAsset);
}
function tradeAmount(item){
  const qty=asNumber(item.quantity);
  const price=asNumber(item.price);
  return qty!==null&&price!==null ? qty*price : null;
}
function actionLabel(action){
  if(action.includes("BUY"))return"买入";
  if(action.includes("SELL"))return action.includes("STOP") ? "止损卖出" : "卖出";
  return action || "--";
}
function formatTime(value){
  if(!value)return"";
  const d=new Date(value);
  if(Number.isNaN(d.getTime()))return value;
  return d.toLocaleString("zh-CN",{hour12:false});
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
