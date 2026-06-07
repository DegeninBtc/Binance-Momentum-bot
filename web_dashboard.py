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
import re
import threading
import time
import webbrowser
from collections import deque
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


LOGGER = logging.getLogger("web-dashboard")
BOT_LOGGER = logging.getLogger("square-momentum-bot")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_BASE_URL = "https://api.binance.com"
WEB_DIST_DIR = Path(__file__).resolve().parent / "web" / "dist"
DEFAULT_SQUARE_URLS = (
    "https://www.binance.com/en/square",
    "https://www.binance.com/en/square/top",
)
CHART_RANGES = {
    "1H": ("1m", 60),
    "6H": ("5m", 72),
    "24H": ("15m", 96),
    "7D": ("2h", 84),
    "30D": ("8h", 90),
}
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

    def manual_close(self, config: Any) -> dict[str, Any]:
        if not self._claim("manual-close-live" if not config.dry_run else "manual-close-dry-run", config):
            LOGGER.info("manual close ignored because another bot task is already running")
            return self.status()
        self.worker = threading.Thread(target=self._manual_close_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.status()

    def close_position(self, config: Any, symbol: str, quantity: Decimal) -> dict[str, Any]:
        if not self._claim("close-position-live" if not config.dry_run else "close-position-dry-run", config):
            LOGGER.info("position close ignored because another bot task is already running")
            return self.status()
        self.worker = threading.Thread(target=self._close_position_worker, args=(config, symbol, quantity), daemon=True)
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

    def _manual_close_worker(self, config: Any) -> None:
        try:
            bot_module().LongOnlyMomentumBot(config).manual_close_position()
        except Exception as exc:
            LOGGER.exception("manual close failed")
            self._finish(str(exc))
            return
        self._finish()

    def _close_position_worker(self, config: Any, symbol: str, quantity: Decimal) -> None:
        try:
            bot_module().LongOnlyMomentumBot(config).manual_close_position(symbol=symbol, quantity=quantity)
        except Exception as exc:
            LOGGER.exception("position close failed")
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
    bot._sync_square_feed_state()
    module.save_state(config.state_file, bot.state)
    mentions = module.count_coin_mentions(posts, base_assets)
    candidates = bot._rank_trade_candidates(symbols, mentions)
    hot_assets = candidates[: config.top_coin_limit]
    source = "综合评分：Binance Square + 24h 涨幅榜"
    notes: list[str] = []
    market_guard = bot._market_filter_reason()
    if market_guard:
        notes.append(f"大盘过滤暂停新开仓：{market_guard}")
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
    open_positions = bot._active_positions()
    if open_positions:
        prefix = "模拟" if config.dry_run else "实盘"
        symbols_text = ", ".join(item.symbol for item in open_positions)
        notes.append(f"当前已有{prefix}仓位 {symbols_text}；最多允许 {config.max_open_positions} 个仓位。")
    if config.asset_whitelist:
        notes.append("白名单：" + ", ".join(config.asset_whitelist))
    if config.asset_blacklist:
        notes.append("黑名单：" + ", ".join(config.asset_blacklist))
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
            "last_price": candidate.last_price,
            "price_change_percent": candidate.price_change_percent,
            "volatility_percent": candidate.volatility_percent,
            "quote_volume": candidate.quote_volume,
        }
    )


def build_square_diagnostics(config: Any) -> dict[str, Any]:
    module = bot_module()
    bot = module.LongOnlyMomentumBot(config)
    diagnostics = bot.square.diagnose(
        config.top_post_limit,
        browser_mode=config.square_browser_mode,
        display_limit=config.square_diagnostic_limit,
    )
    bot._sync_square_feed_state()
    module.save_state(config.state_file, bot.state)
    diagnostics["mode"] = "browser" if config.square_browser_mode else "static"
    if not config.square_browser_mode:
        diagnostics["hint"] = "当前是静态抓取；Binance Square 可能返回空响应。到诊断页开启“浏览器抓广场”后再诊断。"
    elif diagnostics.get("browser_error"):
        diagnostics["hint"] = diagnostics.get("browser_hint", "") or "运行 fix_playwright_browser.bat 安装 Playwright Chromium。"
    else:
        diagnostics["hint"] = ""
    return stringify_decimals(diagnostics)


def build_market_chart(symbol: str, range_key: str, testnet: bool = False) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", normalized_symbol):
        raise ValueError("symbol must be a Binance spot symbol")
    normalized_range = range_key.strip().upper()
    if normalized_range not in CHART_RANGES:
        raise ValueError("range must be one of 1H, 6H, 24H, 7D, 30D")

    interval, limit = CHART_RANGES[normalized_range]
    module = bot_module()
    base_url = "https://testnet.binance.vision" if testnet else DEFAULT_BASE_URL
    config = module.BotConfig(api_key="", api_secret="", base_url=base_url)
    client = module.BinanceSpotClient(config)
    rows = client.klines(normalized_symbol, interval, limit)

    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        points.append(
            {
                "time": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
            }
        )
    if not points:
        raise ValueError("no kline data returned")

    first_close = Decimal(str(points[0]["close"]))
    last_close = Decimal(str(points[-1]["close"]))
    highs = [Decimal(str(item["high"])) for item in points]
    lows = [Decimal(str(item["low"])) for item in points]
    change_pct = ((last_close - first_close) / first_close * Decimal("100")) if first_close else Decimal("0")
    return stringify_decimals(
        {
            "symbol": normalized_symbol,
            "range": normalized_range,
            "interval": interval,
            "points": points,
            "first_close": first_close,
            "last_close": last_close,
            "high": max(highs),
            "low": min(lows),
            "change_percent": change_pct,
        }
    )


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


def close_position_from_payload(runner: BotRunner, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol):
        raise ValueError("symbol must be a Binance spot symbol")
    raw_quantity = payload.get("close_quantity")
    try:
        quantity = Decimal(str(raw_quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("close_quantity must be a positive number") from None
    if quantity <= 0:
        raise ValueError("close_quantity must be a positive number")
    return runner.close_position(config, symbol, quantity)


def test_telegram(config: Any) -> dict[str, Any]:
    module = bot_module()
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return {"ok": False, "error": "Telegram Bot Token 和 Chat ID 不能为空"}
    notifier = module.TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    try:
        notifier.send("Binance Momentum Bot 测试通知")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def safe_load_state(path: str) -> dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {
                "first_buy_done": False,
                "completed_round_trips": 0,
                "position": None,
                "positions": [],
                "updated_at": "",
                "trade_log": [],
            }
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        return {"error": str(exc)}


def enrich_state_for_status(state: dict[str, Any], config: Any, runner: BotRunner) -> dict[str, Any]:
    enriched = dict(state)
    enriched["entry_guard_snapshot"] = stringify_decimals(build_entry_guard_snapshot(state, config))
    enriched["performance_stats"] = stringify_decimals(build_performance_stats(state, config.quote_asset))

    positions = active_positions_from_state(state)
    enriched["positions"] = positions
    if not positions:
        enriched["position"] = None
        return enriched

    snapshots = [
        snapshot
        for snapshot in (build_position_snapshot(position, state, config, runner) for position in positions)
        if snapshot
    ]
    if not snapshots:
        return enriched

    enriched["position"] = positions[0]
    enriched["position_snapshot"] = stringify_decimals(snapshots[0])
    enriched["position_snapshots"] = stringify_decimals(snapshots)
    return enriched


def active_positions_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    positions = [item for item in state.get("positions") or [] if isinstance(item, dict) and item.get("symbol")]
    legacy = state.get("position")
    if isinstance(legacy, dict) and legacy.get("symbol"):
        if all(item.get("symbol") != legacy.get("symbol") for item in positions):
            positions.insert(0, legacy)
    return positions


def build_performance_stats(state: dict[str, Any], quote_asset: str) -> dict[str, Any]:
    module = bot_module()
    completed: list[dict[str, Any]] = []
    open_trades: dict[str, list[dict[str, Any]]] = {}

    for item in state.get("trade_log") or []:
        action = str(item.get("action", ""))
        symbol = str(item.get("symbol", ""))
        qty = decimal_from_state(item.get("quantity"))
        price = decimal_from_state(item.get("price"))
        if not symbol or qty is None or price is None:
            continue
        amount = decimal_from_state(item.get("quote_amount")) or qty * price
        ts = module.parse_timestamp(item.get("ts"))
        if "BUY" in action:
            open_trades.setdefault(symbol, []).append(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "amount": amount,
                    "ts": ts,
                    "dry_run": bool(item.get("dry_run", True)),
                }
            )
        elif "SELL" in action and open_trades.get(symbol):
            remaining_sell_qty = qty
            queue = open_trades[symbol]
            while remaining_sell_qty > 0 and queue:
                open_trade = queue[0]
                open_qty = open_trade["qty"]
                closed_qty = min(open_qty, remaining_sell_qty)
                ratio = closed_qty / qty if qty > 0 else Decimal("1")
                open_ratio = closed_qty / open_qty if open_qty > 0 else Decimal("1")
                exit_amount = amount * ratio
                entry_amount = open_trade["amount"] * open_ratio
                pnl = exit_amount - entry_amount
                return_pct = pnl / entry_amount * Decimal("100") if entry_amount > 0 else Decimal("0")
                completed.append(
                    {
                        "symbol": symbol,
                        "pnl": pnl,
                        "return_pct": return_pct,
                        "entry_amount": entry_amount,
                        "exit_amount": exit_amount,
                        "opened_at": open_trade["ts"],
                        "closed_at": ts,
                        "dry_run": open_trade["dry_run"],
                        "exit_action": action,
                    }
                )
                open_trade["qty"] = open_qty - closed_qty
                open_trade["amount"] = open_trade["amount"] - entry_amount
                remaining_sell_qty -= closed_qty
                if open_trade["qty"] <= 0:
                    queue.pop(0)

    total = len(completed)
    wins = [item for item in completed if item["pnl"] > 0]
    losses = [item for item in completed if item["pnl"] < 0]
    total_pnl = sum((item["pnl"] for item in completed), Decimal("0"))
    gross_profit = sum((item["pnl"] for item in wins), Decimal("0"))
    gross_loss = sum((-item["pnl"] for item in losses), Decimal("0"))
    avg_pnl = total_pnl / total if total else Decimal("0")
    avg_return_pct = sum((item["return_pct"] for item in completed), Decimal("0")) / total if total else Decimal("0")
    win_rate = Decimal(len(wins)) / Decimal(total) * Decimal("100") if total else Decimal("0")
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    best_trade = max((item["pnl"] for item in completed), default=Decimal("0"))
    worst_trade = min((item["pnl"] for item in completed), default=Decimal("0"))

    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    current_streak = 0
    current_streak_type = ""
    for item in completed:
        equity += item["pnl"]
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if item["pnl"] > 0:
            current_streak = current_streak + 1 if current_streak_type == "win" else 1
            current_streak_type = "win"
        elif item["pnl"] < 0:
            current_streak = current_streak + 1 if current_streak_type == "loss" else 1
            current_streak_type = "loss"

    return {
        "quote_asset": quote_asset,
        "completed_trades": total,
        "trade_count": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_pnl": avg_pnl,
        "avg_return_pct": avg_return_pct,
        "profit_factor": profit_factor,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "max_drawdown": max_drawdown,
        "current_streak": current_streak,
        "current_streak_type": current_streak_type,
    }


def build_entry_guard_snapshot(state: dict[str, Any], config: Any) -> dict[str, Any]:
    module = bot_module()
    today = module.datetime.now(module.timezone.utc).date()
    buy_count = 0
    realized_pnl = Decimal("0")
    open_costs: dict[str, list[dict[str, Decimal]]] = {}

    for item in state.get("trade_log") or []:
        action = str(item.get("action", ""))
        symbol = str(item.get("symbol", ""))
        ts = module.parse_timestamp(item.get("ts"))
        qty = decimal_from_state(item.get("quantity"))
        price = decimal_from_state(item.get("price"))
        if not symbol or qty is None or price is None:
            continue
        amount = decimal_from_state(item.get("quote_amount")) or qty * price
        if "BUY" in action:
            open_costs.setdefault(symbol, []).append({"qty": qty, "amount": amount})
            if ts and ts.date() == today:
                buy_count += 1
        elif "SELL" in action:
            queue = open_costs.get(symbol) or []
            remaining_sell_qty = qty
            while remaining_sell_qty > 0 and queue:
                open_trade = queue[0]
                open_qty = open_trade["qty"]
                closed_qty = min(open_qty, remaining_sell_qty)
                ratio = closed_qty / qty if qty > 0 else Decimal("1")
                open_ratio = closed_qty / open_qty if open_qty > 0 else Decimal("1")
                exit_amount = amount * ratio
                entry_amount = open_trade["amount"] * open_ratio
                if ts and ts.date() == today:
                    realized_pnl += exit_amount - entry_amount
                open_trade["qty"] = open_qty - closed_qty
                open_trade["amount"] = open_trade["amount"] - entry_amount
                remaining_sell_qty -= closed_qty
                if open_trade["qty"] <= 0:
                    queue.pop(0)

    trade_limit_hit = config.max_daily_trades > 0 and buy_count >= config.max_daily_trades
    loss_limit_hit = config.max_daily_loss_usdt > 0 and realized_pnl <= -config.max_daily_loss_usdt
    return {
        "buy_count": buy_count,
        "realized_pnl": realized_pnl,
        "max_daily_trades": config.max_daily_trades,
        "max_daily_loss_usdt": config.max_daily_loss_usdt,
        "cooldown_minutes": config.cooldown_minutes,
        "trade_limit_hit": trade_limit_hit,
        "loss_limit_hit": loss_limit_hit,
        "entry_blocked": trade_limit_hit or loss_limit_hit,
    }


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
    position_mode = str(position.get("position_mode") or "spot")
    is_contract_sim = position_mode == "contract-sim"
    leverage_multiplier = decimal_from_state(position.get("leverage_multiplier")) or config.leverage_multiplier
    if leverage_multiplier <= 0:
        leverage_multiplier = Decimal("1")
    margin_quote = decimal_from_state(position.get("margin_quote")) or (quote_spent if is_contract_sim else Decimal("0"))
    notional_quote = decimal_from_state(position.get("notional_quote")) or quantity * entry_price
    symbol = str(position.get("symbol") or "")
    current_price, price_error = runner.ticker_price_for_status(config, symbol)
    highest_price = decimal_from_state(position.get("highest_price")) or entry_price
    if current_price is not None and current_price > highest_price:
        highest_price = current_price
    is_dry_run = position_is_dry_run(position, state, config)
    fixed_stop_enabled = (
        bool(config.fixed_stop_after_first_round_trip)
        and int(state.get("completed_round_trips") or 0) > 0
    )
    stop_price = entry_price * (Decimal("1") - config.initial_stop_loss_pct / Decimal("100"))
    dynamic_stop_price, dynamic_stop_mode = bot_module().dynamic_stop_price(
        config,
        entry_price,
        highest_price,
        stop_price,
    )
    take_profit_price = entry_price * (Decimal("1") + config.take_profit_pct / Decimal("100"))

    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "base_asset": position.get("base_asset") or symbol.removesuffix(config.quote_asset),
        "quote_asset": config.quote_asset,
        "leverage_multiplier": leverage_multiplier,
        "position_mode": position_mode,
        "contract_simulation": is_contract_sim,
        "margin_quote": margin_quote,
        "notional_quote": notional_quote,
        "dry_run": is_dry_run,
        "mode_label": "合约模拟" if is_contract_sim else "模拟" if is_dry_run else "实盘",
        "quantity": quantity,
        "entry_price": entry_price,
        "highest_price": highest_price,
        "quote_spent": quote_spent,
        "opened_at": position.get("opened_at", ""),
        "current_price": current_price,
        "price_error": price_error,
        "active_stop_mode": "fixed-usdt+" + dynamic_stop_mode if fixed_stop_enabled else dynamic_stop_mode,
        "stop_price": stop_price,
        "dynamic_stop_price": dynamic_stop_price,
        "dynamic_stop_mode": dynamic_stop_mode,
        "take_profit_price": take_profit_price,
        "fixed_stop_loss_usdt": config.fixed_stop_loss_usdt,
        "initial_stop_loss_pct": config.initial_stop_loss_pct,
        "take_profit_pct": config.take_profit_pct,
        "breakeven_trigger_pct": config.breakeven_trigger_pct,
        "breakeven_offset_pct": config.breakeven_offset_pct,
        "trailing_start_pct": config.trailing_start_pct,
        "trailing_stop_pct": config.trailing_stop_pct,
    }

    if current_price is None:
        return snapshot

    market_value = quantity * current_price
    unrealized_pnl = (current_price - entry_price) * quantity if is_contract_sim else market_value - quote_spent
    price_change_pct = (current_price - entry_price) / entry_price * Decimal("100")
    unrealized_pnl_pct = (
        unrealized_pnl / margin_quote * Decimal("100")
        if is_contract_sim and margin_quote > 0
        else price_change_pct
    )
    leveraged_unrealized_pnl_pct = price_change_pct * leverage_multiplier
    liquidation_price = (
        entry_price * (Decimal("1") - (Decimal("1") / leverage_multiplier))
        if is_contract_sim and leverage_multiplier > 0
        else None
    )
    unrealized_loss = max(Decimal("0"), -unrealized_pnl)
    stop_triggered = (
        unrealized_loss >= config.fixed_stop_loss_usdt
        if fixed_stop_enabled
        else current_price <= dynamic_stop_price
    )
    stop_triggered = stop_triggered or current_price <= dynamic_stop_price
    take_profit_triggered = config.take_profit_pct > 0 and dynamic_stop_mode != "trailing" and current_price >= take_profit_price
    stop_distance_pct = (current_price - dynamic_stop_price) / current_price * Decimal("100") if current_price > 0 else None
    take_profit_distance_pct = (
        (take_profit_price - current_price) / current_price * Decimal("100")
        if current_price > 0 and not take_profit_triggered
        else Decimal("0")
    )
    snapshot.update(
        {
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "price_change_pct": price_change_pct,
            "leveraged_unrealized_pnl_pct": leveraged_unrealized_pnl_pct,
            "liquidation_price": liquidation_price,
            "unrealized_loss": unrealized_loss,
            "stop_distance_pct": stop_distance_pct,
            "stop_triggered": stop_triggered,
            "take_profit_distance_pct": take_profit_distance_pct,
            "take_profit_triggered": take_profit_triggered,
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
    data.pop("telegram_bot_token", None)
    data["api_key_loaded"] = bool(config.api_key)
    data["api_secret_loaded"] = bool(config.api_secret)
    data["telegram_token_loaded"] = bool(config.telegram_bot_token)
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
    order_quote_amount = decimal_value(payload, "order_quote_amount", "ORDER_QUOTE_USDT", "50")
    fixed_stop_loss_usdt = optional_decimal(payload, "fixed_stop_loss_usdt", "FIXED_STOP_LOSS_USDT")
    if fixed_stop_loss_usdt is None:
        fixed_stop_loss_usdt = module.default_fixed_stop_loss_usdt(order_quote_amount)
    leverage_multiplier = decimal_value(payload, "leverage_multiplier", "LEVERAGE_MULTIPLIER", "10")
    if leverage_multiplier <= 0:
        raise ValueError("leverage_multiplier must be greater than zero")

    return module.BotConfig(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        base_url=base_url,
        quote_asset=str(payload.get("quote_asset") or os.getenv("QUOTE_ASSET", "USDT")).upper(),
        order_quote_amount=order_quote_amount,
        max_open_positions=int_value(payload, "max_open_positions", "MAX_OPEN_POSITIONS", 1),
        leverage_multiplier=leverage_multiplier,
        contract_simulation_enabled=bool_value(payload, "contract_simulation_enabled", True),
        min_quote_volume=decimal_value(payload, "min_quote_volume", "MIN_QUOTE_VOLUME_USDT", "5000000"),
        min_price_change_percent=decimal_value(payload, "min_price_change_percent", "MIN_PRICE_CHANGE_PERCENT", "3"),
        min_volatility_percent=decimal_value(payload, "min_volatility_percent", "MIN_VOLATILITY_PERCENT", "5"),
        top_post_limit=int_value(payload, "top_post_limit", "TOP_POST_LIMIT", 25),
        top_coin_limit=int_value(payload, "top_coin_limit", "TOP_COIN_LIMIT", 10),
        poll_seconds=int_value(payload, "poll_seconds", "POLL_SECONDS", 300),
        recv_window_ms=int_value(payload, "recv_window_ms", "RECV_WINDOW_MS", 5000),
        initial_stop_loss_pct=decimal_value(payload, "initial_stop_loss_pct", "INITIAL_STOP_LOSS_PCT", "4"),
        take_profit_pct=decimal_value(payload, "take_profit_pct", "TAKE_PROFIT_PCT", "0"),
        breakeven_trigger_pct=decimal_value(payload, "breakeven_trigger_pct", "BREAKEVEN_TRIGGER_PCT", "3"),
        breakeven_offset_pct=decimal_value(payload, "breakeven_offset_pct", "BREAKEVEN_OFFSET_PCT", "0.2"),
        trailing_start_pct=decimal_value(payload, "trailing_start_pct", "TRAILING_START_PCT", "6"),
        trailing_stop_pct=decimal_value(payload, "trailing_stop_pct", "TRAILING_STOP_PCT", "3"),
        fixed_stop_loss_usdt=fixed_stop_loss_usdt,
        fixed_stop_after_first_round_trip=bool_value(payload, "fixed_stop_after_first_round_trip", False),
        fixed_stop_equity_usdt=optional_decimal(payload, "fixed_stop_equity_usdt", "FIXED_STOP_EQUITY_USDT"),
        cooldown_minutes=int_value(payload, "cooldown_minutes", "COOLDOWN_MINUTES", 30),
        max_daily_trades=int_value(payload, "max_daily_trades", "MAX_DAILY_TRADES", 5),
        max_daily_loss_usdt=decimal_value(payload, "max_daily_loss_usdt", "MAX_DAILY_LOSS_USDT", "25"),
        fee_rate_pct=decimal_value(payload, "fee_rate_pct", "FEE_RATE_PCT", "0.1"),
        slippage_pct=decimal_value(payload, "slippage_pct", "SLIPPAGE_PCT", "0.05"),
        asset_whitelist=symbol_list_value(payload, "asset_whitelist", "ASSET_WHITELIST"),
        asset_blacklist=symbol_list_value(payload, "asset_blacklist", "ASSET_BLACKLIST"),
        market_filter_enabled=bool_value(payload, "market_filter_enabled", False),
        market_filter_assets=symbol_list_value(payload, "market_filter_assets", "MARKET_FILTER_ASSETS", "BTC,ETH"),
        market_filter_min_change_pct=decimal_value(payload, "market_filter_min_change_pct", "MARKET_FILTER_MIN_CHANGE_PCT", "-1"),
        market_filter_require_all=bool_value(payload, "market_filter_require_all", False),
        account_sync_enabled=bool_value(payload, "account_sync_enabled", True),
        state_file=str(payload.get("state_file") or os.getenv("STATE_FILE", "bot_state.json")),
        dry_run=not bool(payload.get("live")),
        square_urls=square_urls,
        square_browser_mode=bool_value(payload, "square_browser_mode", True),
        square_diagnostic_limit=int_value(payload, "square_diagnostic_limit", "SQUARE_DIAGNOSTIC_LIMIT", 10),
        telegram_bot_token=str(payload.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")),
        telegram_chat_id=str(payload.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")),
        telegram_enabled=bool_value(payload, "telegram_enabled", False),
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


def symbol_list_value(payload: dict[str, Any], key: str, env_name: str, default: str = "") -> tuple[str, ...]:
    raw = payload.get(key)
    if raw in (None, ""):
        raw = os.getenv(env_name, default)
    return bot_module().parse_symbol_list(str(raw or ""))


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
            parsed = urlparse(self.path)
            route = parsed.path
            if route == "/api/status":
                self._send_json(runner.status())
                return
            if route == "/api/defaults":
                self._send_json(sanitize_config(config_from_payload({})))
                return
            if route == "/api/market-chart":
                params = parse_qs(parsed.query)
                symbol = (params.get("symbol") or [""])[-1]
                range_key = (params.get("range") or ["24H"])[-1]
                testnet = str((params.get("testnet") or [""])[-1]).lower() in {"1", "true", "yes", "on"}
                try:
                    self._send_json(build_market_chart(symbol, range_key, testnet=testnet))
                except (ValueError, RuntimeError) as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            if route == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return
            if self._send_static(route):
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
                elif route == "/api/manual-close":
                    self._send_json(runner.manual_close(config))
                elif route == "/api/close-position":
                    self._send_json(close_position_from_payload(runner, config, payload))
                elif route == "/api/start-loop":
                    self._send_json(runner.start_loop(config))
                elif route == "/api/reset-dry-run-state":
                    self._send_json(reset_dry_run_state(runner, config))
                elif route == "/api/test-telegram":
                    self._send_json(test_telegram(config))
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

        def _send_static(self, route: str) -> bool:
            if route == "/":
                target = WEB_DIST_DIR / "index.html"
            else:
                target = WEB_DIST_DIR / route.lstrip("/")
            try:
                resolved = target.resolve()
                resolved.relative_to(WEB_DIST_DIR.resolve())
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return True
            if not resolved.is_file():
                if route == "/":
                    self._send_frontend_missing()
                    return True
                return False
            content_type = content_type_for(resolved)
            self._send_file(resolved, content_type, no_store=route == "/")
            return True

        def _send_frontend_missing(self) -> None:
            message = (
                "<!doctype html><meta charset='utf-8'>"
                "<title>Dashboard frontend missing</title>"
                "<body style='font-family:system-ui;padding:32px'>"
                "<h1>Web frontend has not been built.</h1>"
                "<p>Run <code>npm install</code> and <code>npm run build</code> in the project root.</p>"
                "</body>"
            )
            body = message.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str, no_store: bool = False) -> None:
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store" if no_store else "max-age=31536000, immutable")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    return "application/octet-stream"


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
