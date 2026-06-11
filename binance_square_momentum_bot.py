#!/usr/bin/env python3
"""
Long-only Binance momentum bot driven by Binance Square mentions.

Default mode is dry-run. Use --live only after you have reviewed the logic,
tested with small order sizes, and accepted the risk of automated trading.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import math
import os
import re
import signal
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from bs4 import BeautifulSoup
except ImportError:  # The scraper can still extract from raw HTML without bs4.
    BeautifulSoup = None


LOGGER = logging.getLogger("square-momentum-bot")
DEFAULT_STATE_FILE = "bot_state.json"
DEFAULT_SIGNAL_RECORD_FILE = "signal_records.jsonl"
DEFAULT_TRADE_JOURNAL_FILE = "trade_journal.sqlite3"
DEFAULT_BASE_URL = "https://api.binance.com"
DEFAULT_FUTURES_BASE_URL = "https://fapi.binance.com"
DEFAULT_FUTURES_TESTNET_BASE_URL = "https://demo-fapi.binance.com"
DEFAULT_FIXED_STOP_LOSS_RATIO = Decimal("0.2")
MIN_FIXED_STOP_LOSS_USDT = Decimal("1")
MARKET_FUTURES = "futures"
MARKET_SPOT = "spot"
TRADE_MARKET_MODES = {"futures_preferred", "futures_only", "spot_only"}
DEFAULT_SQUARE_URLS = (
    "https://www.binance.com/en/square",
    "https://www.binance.com/en/square/top",
)
COMMON_FALSE_SYMBOLS = {
    "A",
    "AI",
    "API",
    "APR",
    "ATH",
    "CEO",
    "CPI",
    "ETF",
    "FED",
    "FOMO",
    "GDP",
    "IPO",
    "KYC",
    "NFT",
    "P2P",
    "SEC",
    "TVL",
    "USD",
    "USDC",
    "USDT",
}
EXCLUDED_MOMENTUM_ASSETS = {
    "AEUR",
    "BIDR",
    "BRL",
    "BUSD",
    "DAI",
    "EURI",
    "EUR",
    "FDUSD",
    "GBP",
    "IDRT",
    "JPY",
    "PAX",
    "RON",
    "RUB",
    "TUSD",
    "TRY",
    "UAH",
    "USD",
    "USDC",
    "USDE",
    "USDP",
    "USDS",
    "USDT",
    "UST",
    "USTC",
}
PREFIX_REQUIRED_SYMBOLS = {
    "AT",
    "ALL",
    "BABY",
    "FOR",
    "HIGH",
    "HOME",
    "IN",
    "LAB",
    "MEME",
    "MOVE",
    "NOT",
    "ON",
    "ONE",
    "OPEN",
    "PEOPLE",
    "PORTAL",
    "PUMP",
    "SIGN",
    "THE",
    "TO",
    "TRUMP",
}
STRONG_BARE_SYMBOLS = {
    "ADA",
    "AVAX",
    "BNB",
    "BTC",
    "DOGE",
    "DOT",
    "ETH",
    "LINK",
    "SOL",
    "TON",
    "TRX",
    "XRP",
}
LONG_ONLY_REJECT_PATTERNS = (
    r"\bshort\s+(?:position|entry|setup|signal|trade|idea)\b",
    r"\bshort(?:ing)?\s+\$?[A-Z0-9]{2,12}\b",
    r"\bsell(?:ing)?\s+pressure\b",
    r"\bsell\s+signal\b",
    r"\bsold\b",
    r"\bdrops?\s+below\b",
    r"\bbearish\b",
    r"\bdump(?:ed|ing)?\b",
    r"\bcrash(?:ed|ing)?\b",
)
MARKET_CONTEXT_PATTERN = re.compile(
    r"\b(?:bitcoin|crypto|coin|token|market|trader|trading|price|entry|tp|sl|"
    r"support|resistance|breakout|rally|pump|volume|liquidat(?:ed|ion)|"
    r"bull(?:ish)?|bear(?:ish)?|buy|sell|sold|usdt|btc|eth)\b|\$[0-9]",
    flags=re.IGNORECASE,
)


@dataclass
class BotConfig:
    api_key: str
    api_secret: str
    base_url: str = DEFAULT_BASE_URL
    futures_base_url: str = DEFAULT_FUTURES_BASE_URL
    quote_asset: str = "USDT"
    trade_market_mode: str = "futures_preferred"
    futures_margin_type: str = "ISOLATED"
    order_quote_amount: Decimal = Decimal("50")
    max_open_positions: int = 15
    leverage_multiplier: Decimal = Decimal("3")
    contract_simulation_enabled: bool = True
    contract_max_margin_loss_pct: Decimal = Decimal("20")
    liquidation_stop_buffer_pct: Decimal = Decimal("2")
    min_quote_volume: Decimal = Decimal("5000000")
    min_price_change_percent: Decimal = Decimal("3")
    min_volatility_percent: Decimal = Decimal("5")
    top_post_limit: int = 25
    top_coin_limit: int = 10
    poll_seconds: int = 300
    recv_window_ms: int = 5000
    initial_stop_loss_pct: Decimal = Decimal("4")
    take_profit_pct: Decimal = Decimal("0")
    breakeven_trigger_pct: Decimal = Decimal("3")
    breakeven_offset_pct: Decimal = Decimal("0.2")
    trailing_start_pct: Decimal = Decimal("6")
    trailing_stop_pct: Decimal = Decimal("3")
    fixed_stop_loss_usdt: Decimal = Decimal("10")
    fixed_stop_after_first_round_trip: bool = False
    fixed_stop_equity_usdt: Decimal | None = None
    cooldown_minutes: int = 30
    max_daily_trades: int = 5
    max_daily_loss_usdt: Decimal = Decimal("25")
    max_total_exposure_pct: Decimal = Decimal("0")
    max_symbol_exposure_pct: Decimal = Decimal("0")
    max_consecutive_losses: int = 0
    max_intraday_drawdown_pct: Decimal = Decimal("0")
    risk_per_trade_pct: Decimal = Decimal("0")
    fee_rate_pct: Decimal = Decimal("0.1")
    slippage_pct: Decimal = Decimal("0.05")
    asset_whitelist: tuple[str, ...] = ()
    asset_blacklist: tuple[str, ...] = ()
    market_filter_enabled: bool = False
    market_filter_assets: tuple[str, ...] = ("BTC", "ETH")
    market_filter_min_change_pct: Decimal = Decimal("-1")
    market_filter_require_all: bool = False
    account_sync_enabled: bool = True
    kline_confirmation_enabled: bool = True
    min_square_confidence_score: Decimal = Decimal("35")
    max_spread_bps: Decimal = Decimal("50")
    min_orderbook_depth_usdt: Decimal = Decimal("1000")
    exchange_protection_enabled: bool = True
    oco_stop_limit_slippage_pct: Decimal = Decimal("0.5")
    signal_recording_enabled: bool = True
    signal_record_file: str = DEFAULT_SIGNAL_RECORD_FILE
    trade_journal_file: str = DEFAULT_TRADE_JOURNAL_FILE
    state_file: str = DEFAULT_STATE_FILE
    dry_run: bool = True
    square_urls: tuple[str, ...] = DEFAULT_SQUARE_URLS
    square_browser_mode: bool = True
    square_diagnostic_limit: int = 10
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False


@dataclass
class SquarePost:
    title: str
    text: str
    traffic_score: float = 0.0
    url: str | None = None
    created_at: str | None = None
    post_id: str | None = None
    author: str | None = None
    source: str = "binance_square"
    extractor_mode: str = "unknown"


@dataclass
class SquareFeedState:
    seen_post_ids: set[str] = field(default_factory=set)
    latest_post_time: str | None = None
    consecutive_failures: int = 0
    interface_hits: Counter[str] = field(default_factory=Counter)


@dataclass
class TradeCandidate:
    symbol: str
    base_asset: str
    mention_count: float
    price_change_percent: Decimal
    volatility_percent: Decimal
    quote_volume: Decimal
    last_price: Decimal
    market_score: Decimal = Decimal("0")
    square_score: Decimal = Decimal("0")
    combined_score: Decimal = Decimal("0")
    market_type: str = MARKET_SPOT


@dataclass
class OrderRules:
    symbol: str
    min_qty: Decimal = Decimal("0")
    step_size: Decimal = Decimal("0.00000001")
    tick_size: Decimal = Decimal("0.00000001")
    min_notional: Decimal = Decimal("0")


@dataclass
class PositionState:
    symbol: str = ""
    base_asset: str = ""
    quantity: str = "0"
    entry_price: str = "0"
    highest_price: str = "0"
    quote_spent: str = "0"
    opened_at: str = ""
    order_id: int | None = None
    position_mode: str = "spot"
    margin_quote: str = "0"
    notional_quote: str = "0"
    leverage_multiplier: str = "1"
    market_type: str = MARKET_SPOT
    margin_type: str = ""


@dataclass
class PendingOrderState:
    symbol: str = ""
    side: str = ""
    client_order_id: str = ""
    quote_amount: str = ""
    quantity: str = ""
    created_at: str = ""
    action: str = ""
    status: str = "pending"
    error: str = ""
    market_type: str = MARKET_SPOT


@dataclass
class ProtectionOrderState:
    symbol: str = ""
    client_order_id: str = ""
    order_list_id: int | None = None
    quantity: str = "0"
    take_profit_price: str = "0"
    stop_price: str = "0"
    stop_limit_price: str = "0"
    status: str = "missing"
    kind: str = "oco"
    dry_run: bool = True
    created_at: str = ""
    error: str = ""


@dataclass
class BotState:
    first_buy_done: bool = False
    completed_round_trips: int = 0
    position: PositionState | None = None
    positions: list[PositionState] = field(default_factory=list)
    updated_at: str = ""
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    pending_order: PendingOrderState | None = None
    protection_orders: list[ProtectionOrderState] = field(default_factory=list)
    last_safety_check: dict[str, Any] = field(default_factory=dict)
    entry_confirmation: dict[str, Any] = field(default_factory=dict)
    square_confidence: dict[str, Any] = field(default_factory=dict)
    account_risk_snapshot: dict[str, Any] = field(default_factory=dict)
    square_seen_post_ids: list[str] = field(default_factory=list)
    square_latest_post_time: str = ""
    square_consecutive_failures: int = 0


class BinanceAPIError(RuntimeError):
    pass


class BinanceSpotClient:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.session = build_retry_session()
        self._time_offset_ms = 0
        self._exchange_info: dict[str, Any] | None = None

    def sync_time(self) -> None:
        data = self.public_get("/api/v3/time")
        server_time = int(data["serverTime"])
        local_time = int(time.time() * 1000)
        self._time_offset_ms = server_time - local_time
        LOGGER.info("Binance time offset: %sms", self._time_offset_ms)

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.config.base_url + path
        try:
            response = self.session.get(url, params=params, timeout=15)
            return self._handle_response(response)
        except requests.RequestException as exc:
            raise BinanceAPIError(f"public GET failed: {path}: {exc}") from exc

    def signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self.config.api_key or not self.config.api_secret:
            raise BinanceAPIError("BINANCE_API_KEY and BINANCE_API_SECRET are required for signed endpoints")

        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        payload["recvWindow"] = self.config.recv_window_ms
        query = urlencode(payload, doseq=True)
        signature = hmac.new(
            self.config.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature

        headers = {"X-MBX-APIKEY": self.config.api_key}
        url = self.config.base_url + path
        try:
            response = self.session.request(method, url, params=payload, headers=headers, timeout=15)
            return self._handle_response(response)
        except requests.RequestException as exc:
            raise BinanceAPIError(f"signed {method} failed: {path}: {exc}") from exc

    def account(self) -> dict[str, Any]:
        return self.signed_request("GET", "/api/v3/account")

    def exchange_info(self) -> dict[str, Any]:
        if self._exchange_info is None:
            self._exchange_info = self.public_get("/api/v3/exchangeInfo")
        return self._exchange_info

    def tradable_quote_symbols(self, quote_asset: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in self.exchange_info().get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("isSpotTradingAllowed")
                and item.get("quoteAsset") == quote_asset
            ):
                item = dict(item)
                item["market_type"] = MARKET_SPOT
                result[item["symbol"]] = item
        return result

    def ticker_24hr(self) -> list[dict[str, Any]]:
        return self.public_get("/api/v3/ticker/24hr")

    def ticker_price(self, symbol: str) -> Decimal:
        data = self.public_get("/api/v3/ticker/price", {"symbol": symbol})
        return Decimal(str(data["price"]))

    def klines(self, symbol: str, interval: str, limit: int, start_time: int | None = None) -> list[Any]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        return self.public_get("/api/v3/klines", params)

    def depth(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        return self.public_get("/api/v3/depth", {"symbol": symbol, "limit": limit})

    def market_buy_quote(self, symbol: str, quote_order_qty: Decimal, client_order_id: str | None = None) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": format_decimal(quote_order_qty),
            "newOrderRespType": "FULL",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self.signed_request("POST", "/api/v3/order", params)

    def market_sell_quantity(self, symbol: str, quantity: Decimal, client_order_id: str | None = None) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": format_decimal(quantity),
            "newOrderRespType": "FULL",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self.signed_request("POST", "/api/v3/order", params)

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self.signed_request(
            "GET",
            "/api/v3/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    def cancel_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self.signed_request(
            "DELETE",
            "/api/v3/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    def cancel_order_list(self, symbol: str, order_list_id: int | None = None, list_client_order_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if order_list_id is not None:
            params["orderListId"] = order_list_id
        elif list_client_order_id:
            params["listClientOrderId"] = list_client_order_id
        else:
            raise BinanceAPIError("order_list_id or list_client_order_id is required to cancel OCO")
        return self.signed_request("DELETE", "/api/v3/orderList", params)

    def stop_loss_limit_sell(
        self,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        stop_limit_price: Decimal,
        client_order_id: str,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "timeInForce": "GTC",
            "quantity": format_decimal(quantity),
            "stopPrice": format_decimal(stop_price),
            "price": format_decimal(stop_limit_price),
            "newClientOrderId": client_order_id,
        }
        return self.signed_request("POST", "/api/v3/order", params)

    def oco_sell(
        self,
        symbol: str,
        quantity: Decimal,
        take_profit_price: Decimal,
        stop_price: Decimal,
        stop_limit_price: Decimal,
        list_client_order_id: str,
        limit_client_order_id: str,
        stop_client_order_id: str,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": format_decimal(quantity),
            "price": format_decimal(take_profit_price),
            "stopPrice": format_decimal(stop_price),
            "stopLimitPrice": format_decimal(stop_limit_price),
            "stopLimitTimeInForce": "GTC",
            "listClientOrderId": list_client_order_id,
            "limitClientOrderId": limit_client_order_id,
            "stopClientOrderId": stop_client_order_id,
        }
        return self.signed_request("POST", "/api/v3/order/oco", params)

    @staticmethod
    def _handle_response(response: requests.Response) -> Any:
        text = response.text
        try:
            data = response.json()
        except ValueError:
            data = text

        if response.status_code >= 400:
            raise BinanceAPIError(f"HTTP {response.status_code}: {data}")
        return data


class BinanceFuturesClient:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.session = build_retry_session()
        self._time_offset_ms = 0
        self._exchange_info: dict[str, Any] | None = None

    def sync_time(self) -> None:
        data = self.public_get("/fapi/v1/time")
        server_time = int(data["serverTime"])
        local_time = int(time.time() * 1000)
        self._time_offset_ms = server_time - local_time
        LOGGER.info("Binance futures time offset: %sms", self._time_offset_ms)

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.config.futures_base_url + path
        try:
            response = self.session.get(url, params=params, timeout=15)
            return BinanceSpotClient._handle_response(response)
        except requests.RequestException as exc:
            raise BinanceAPIError(f"futures public GET failed: {path}: {exc}") from exc

    def signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.config.api_key or not self.config.api_secret:
            raise BinanceAPIError("BINANCE_API_KEY and BINANCE_API_SECRET are required for signed futures endpoints")
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        payload["recvWindow"] = self.config.recv_window_ms
        query = urlencode(payload, doseq=True)
        signature = hmac.new(
            self.config.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        headers = {"X-MBX-APIKEY": self.config.api_key}
        url = self.config.futures_base_url + path
        try:
            response = self.session.request(method, url, params=payload, headers=headers, timeout=15)
            return BinanceSpotClient._handle_response(response)
        except requests.RequestException as exc:
            raise BinanceAPIError(f"futures signed {method} failed: {path}: {exc}") from exc

    def account(self) -> dict[str, Any]:
        return self.signed_request("GET", "/fapi/v2/account")

    def exchange_info(self) -> dict[str, Any]:
        if self._exchange_info is None:
            self._exchange_info = self.public_get("/fapi/v1/exchangeInfo")
        return self._exchange_info

    def tradable_quote_symbols(self, quote_asset: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in self.exchange_info().get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == quote_asset
                and str(item.get("contractType", "")).upper() == "PERPETUAL"
            ):
                item = dict(item)
                item["market_type"] = MARKET_FUTURES
                result[item["symbol"]] = item
        return result

    def ticker_24hr(self) -> list[dict[str, Any]]:
        return self.public_get("/fapi/v1/ticker/24hr")

    def ticker_price(self, symbol: str) -> Decimal:
        data = self.public_get("/fapi/v1/ticker/price", {"symbol": symbol})
        return Decimal(str(data["price"]))

    def klines(self, symbol: str, interval: str, limit: int, start_time: int | None = None) -> list[Any]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        return self.public_get("/fapi/v1/klines", params)

    def depth(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        return self.public_get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def change_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        return self.signed_request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type.upper()})

    def change_leverage(self, symbol: str, leverage: Decimal) -> dict[str, Any]:
        return self.signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})

    def market_buy_quantity(self, symbol: str, quantity: Decimal, client_order_id: str | None = None) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": format_decimal(quantity),
            "newOrderRespType": "RESULT",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self.signed_request("POST", "/fapi/v1/order", params)

    def market_sell_quantity(self, symbol: str, quantity: Decimal, client_order_id: str | None = None) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": format_decimal(quantity),
            "reduceOnly": "true",
            "newOrderRespType": "RESULT",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self.signed_request("POST", "/fapi/v1/order", params)

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self.signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id})


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, text: str) -> None:
        response = requests.post(
            self._url,
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        data: Any
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if response.status_code >= 400 or (isinstance(data, dict) and not data.get("ok", True)):
            raise BinanceAPIError(f"Telegram HTTP {response.status_code}: {data}")


class BinanceSquareScraper:
    def __init__(self, session: requests.Session, urls: Iterable[str]) -> None:
        self.session = session
        self.urls = tuple(urls)
        self.feed_state = SquareFeedState()
        self.last_diagnostics: dict[str, Any] = {}

    def fetch_top_posts(self, limit: int, browser_mode: bool = False) -> list[SquarePost]:
        self.last_diagnostics = {}
        started_at = time.perf_counter()
        if browser_mode:
            try:
                posts, diagnostics = self._fetch_top_posts_with_browser(limit)
                self.last_diagnostics = diagnostics
                if posts:
                    LOGGER.info("Binance Square browser mode extracted %s posts", len(posts))
                    self._record_fetch_success(posts)
                    return self._rank_posts_for_signal(posts)[:limit]
            except Exception as exc:
                self._record_fetch_failure()
                self.last_diagnostics = {"browser_error": str(exc)}
                LOGGER.warning("Binance Square browser mode failed: %s", exc)

        posts: list[SquarePost] = []
        json_post_count = 0
        html_post_count = 0
        for url in self.urls:
            try:
                response = self.session.get(
                    url,
                    timeout=20,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0 Safari/537.36"
                        ),
                        "Accept-Language": "en-US,en;q=0.8",
                    },
                )
                response.raise_for_status()
                extracted = self._extract_posts(response.text, url)
                posts.extend(extracted)
                json_post_count += len([post for post in extracted if post.extractor_mode == "script_json"])
                html_post_count += len([post for post in extracted if post.extractor_mode == "html"])
            except requests.RequestException as exc:
                LOGGER.warning("Binance Square fetch failed for %s: %s", url, exc)

        deduped = dedupe_posts(posts)
        self._record_fetch_success(deduped) if deduped else self._record_fetch_failure()
        self.last_diagnostics = {
            "extractor_mode": "static_json" if json_post_count else "static_html" if html_post_count else "none",
            "square_fetch_latency_ms": int((time.perf_counter() - started_at) * 1000),
            "json_post_count": json_post_count,
            "html_post_count": html_post_count,
            "api_response_count": 0,
            "api_post_count": 0,
            "rendered_text_post_count": 0,
        }
        return self._rank_posts_for_signal(deduped)[:limit]

    def diagnose(self, limit: int, browser_mode: bool = False, display_limit: int = 10) -> dict[str, Any]:
        started_at = time.perf_counter()
        result: dict[str, Any] = {
            "checked_at": utc_now(),
            "browser_mode": browser_mode,
            "display_limit": display_limit,
            "urls": [],
            "total_posts": 0,
            "samples": [],
            "display_posts": [],
            "browser_hint": "",
            "extractor_mode": "none",
            "square_fetch_latency_ms": 0,
            "api_response_count": 0,
            "api_post_count": 0,
            "json_post_count": 0,
            "html_post_count": 0,
            "rendered_text_post_count": 0,
            "new_post_count": 0,
            "duplicate_post_count": 0,
            "latest_post_time": self.feed_state.latest_post_time or "",
            "consecutive_failures": self.feed_state.consecutive_failures,
        }
        all_posts: list[SquarePost] = []
        for url in self.urls:
            item: dict[str, Any] = {"url": url}
            try:
                response = self.session.get(
                    url,
                    timeout=20,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0 Safari/537.36"
                        ),
                        "Accept-Language": "en-US,en;q=0.8",
                    },
                )
                item["status_code"] = response.status_code
                item["final_url"] = response.url
                item["content_length"] = len(response.text)
                if not response.text.strip():
                    item["error"] = "empty response; Binance Square likely requires browser rendering"
                item["script_count"] = len(re.findall(r"<script[^>]*>", response.text, flags=re.IGNORECASE))
                json_posts = self._extract_posts_from_json_blobs(response.text, url)
                html_posts = self._extract_posts_from_html(response.text, url)
                item["json_posts"] = len(json_posts)
                item["html_posts"] = len(html_posts)
                result["json_post_count"] += len(json_posts)
                result["html_post_count"] += len(html_posts)
                all_posts.extend(json_posts or html_posts)
            except requests.RequestException as exc:
                item["error"] = str(exc)
            result["urls"].append(item)

        if browser_mode:
            try:
                browser_posts, browser_diagnostics = self._fetch_top_posts_with_browser(limit, validate=False)
                result["browser_posts_raw"] = len(browser_posts)
                result.update(browser_diagnostics)
                all_posts.extend(browser_posts)
            except Exception as exc:
                self._record_fetch_failure()
                result["browser_error"] = str(exc)
                result["browser_hint"] = (
                    "Install or repair browser scraping support by running fix_playwright_browser.bat, "
                    "or run: python -m pip install playwright && python -m playwright install chromium"
                )

        raw_deduped = dedupe_posts(all_posts, validate=False)
        deduped = dedupe_posts(all_posts)
        ranked = self._rank_posts_for_signal(deduped)
        new_post_count = self._count_new_posts(raw_deduped)
        self._record_fetch_success(raw_deduped) if raw_deduped else self._record_fetch_failure()
        latest_post_time = latest_post_time_from_posts(raw_deduped) or self.feed_state.latest_post_time or ""
        result["raw_posts"] = len(raw_deduped)
        result["duplicate_post_count"] = max(0, len(all_posts) - len(raw_deduped))
        result["filtered_out_posts"] = max(0, len(raw_deduped) - len(deduped))
        result["total_posts"] = len(deduped)
        result["new_post_count"] = new_post_count
        result["latest_post_time"] = latest_post_time
        result["consecutive_failures"] = self.feed_state.consecutive_failures
        if result.get("api_post_count"):
            result["extractor_mode"] = "network_api"
        elif result.get("json_post_count"):
            result["extractor_mode"] = "script_json"
        elif result.get("html_post_count"):
            result["extractor_mode"] = "html"
        elif result.get("rendered_text_post_count"):
            result["extractor_mode"] = "rendered_text"
        result["displayed_posts"] = min(max(1, display_limit), len(raw_deduped))
        result["display_posts"] = score_diagnostic_posts(raw_deduped, max(1, display_limit))
        result["samples"] = [post_to_dict(post) for post in ranked[: min(limit, 8)]]
        result["square_fetch_latency_ms"] = int((time.perf_counter() - started_at) * 1000)
        return result

    def _fetch_top_posts_with_browser(self, limit: int, validate: bool = True) -> tuple[list[SquarePost], dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: python -m pip install playwright && "
                "python -m playwright install chromium"
            ) from exc

        posts: list[SquarePost] = []
        diagnostics: dict[str, Any] = {
            "api_response_count": 0,
            "api_post_count": 0,
            "json_post_count": 0,
            "html_post_count": 0,
            "rendered_text_post_count": 0,
            "extractor_mode": "none",
            "candidate_api_urls": [],
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                page.route("**/*", block_square_heavy_resource)

                def handle_response(response: Any) -> None:
                    url = str(getattr(response, "url", "") or "")
                    if not is_candidate_square_api_url(url):
                        return
                    diagnostics["api_response_count"] += 1
                    candidate_urls = diagnostics.setdefault("candidate_api_urls", [])
                    if len(candidate_urls) < 12 and url not in candidate_urls:
                        candidate_urls.append(url)
                    try:
                        data = response.json()
                    except Exception:
                        return
                    extracted = extract_square_posts_from_api_payload(data, url)
                    if extracted:
                        diagnostics["api_post_count"] += len(extracted)
                        posts.extend(extracted)

                page.on("response", handle_response)
                for url in self.urls:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
                    for _ in range(3):
                        page.mouse.wheel(0, 1400)
                        page.wait_for_timeout(1200)
                    html = page.content()
                    page_posts = self._extract_posts(html, url)
                    diagnostics["json_post_count"] += len([post for post in page_posts if post.extractor_mode == "script_json"])
                    diagnostics["html_post_count"] += len([post for post in page_posts if post.extractor_mode == "html"])
                    posts.extend(page_posts)
                    if len(posts) < limit:
                        try:
                            body_text = page.locator("body").inner_text(timeout=5000)
                            rendered_posts = self._extract_posts_from_rendered_text(body_text, url)
                            diagnostics["rendered_text_post_count"] += len(rendered_posts)
                            posts.extend(rendered_posts)
                        except Exception:
                            LOGGER.debug("failed to extract rendered Square text", exc_info=True)
            finally:
                browser.close()

        deduped = dedupe_posts(posts, validate=validate)
        ranked = self._rank_posts_for_signal(deduped)
        if diagnostics.get("api_post_count"):
            diagnostics["extractor_mode"] = "network_api"
        elif diagnostics.get("json_post_count"):
            diagnostics["extractor_mode"] = "script_json"
        elif diagnostics.get("html_post_count"):
            diagnostics["extractor_mode"] = "html"
        elif diagnostics.get("rendered_text_post_count"):
            diagnostics["extractor_mode"] = "rendered_text"
        return ranked[:limit], diagnostics

    def _extract_posts(self, html: str, source_url: str) -> list[SquarePost]:
        posts = self._extract_posts_from_json_blobs(html, source_url)
        if posts:
            return posts
        return self._extract_posts_from_html(html, source_url)

    def _extract_posts_from_json_blobs(self, html: str, source_url: str) -> list[SquarePost]:
        posts: list[SquarePost] = []
        for blob in re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE):
            if "Square" not in blob and "article" not in blob and "feed" not in blob:
                continue
            posts.extend(extract_square_posts_from_api_payload(blob, source_url, extractor_mode="script_json"))
        return posts

    def _extract_posts_from_html(self, html: str, source_url: str) -> list[SquarePost]:
        text_blocks: list[str] = []
        if BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all(["article", "section", "div"]):
                text = clean_text(tag.get_text(" ", strip=True))
                if len(text) >= 80 and contains_market_symbol_hint(text):
                    text_blocks.append(text)
        else:
            stripped = re.sub(r"<[^>]+>", " ", html)
            text_blocks = [clean_text(stripped)]

        posts = []
        for block in text_blocks[:100]:
            posts.append(
                SquarePost(
                    title=block[:120],
                    text=block,
                    traffic_score=float(len(block)),
                    url=source_url,
                    extractor_mode="html",
                )
            )
        return posts

    def _extract_posts_from_rendered_text(self, text: str, source_url: str) -> list[SquarePost]:
        lines = [clean_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        posts: list[SquarePost] = []
        buffer: list[str] = []
        for line in lines:
            if len(buffer) >= 8 or (buffer and is_square_boundary_line(line)):
                block = clean_text(" ".join(buffer))
                if len(block) >= 80 and contains_market_symbol_hint(block):
                    posts.append(
                        SquarePost(
                            title=block[:120],
                            text=block,
                            traffic_score=float(len(block)),
                            url=source_url,
                            extractor_mode="rendered_text",
                        )
                    )
                buffer = []
            buffer.append(line)
        block = clean_text(" ".join(buffer))
        if len(block) >= 80 and contains_market_symbol_hint(block):
            posts.append(
                SquarePost(
                    title=block[:120],
                    text=block,
                    traffic_score=float(len(block)),
                    url=source_url,
                    extractor_mode="rendered_text",
                )
            )
        return posts

    def _post_identity(self, post: SquarePost) -> str:
        if post.post_id:
            return f"id:{post.post_id}"
        return "text:" + clean_text(f"{post.title} {post.text}")[:300].lower()

    def _count_new_posts(self, posts: list[SquarePost]) -> int:
        return sum(1 for post in posts if self._post_identity(post) not in self.feed_state.seen_post_ids)

    def _record_fetch_success(self, posts: list[SquarePost]) -> None:
        self.feed_state.consecutive_failures = 0
        for post in posts:
            self.feed_state.seen_post_ids.add(self._post_identity(post))
            if post.extractor_mode:
                self.feed_state.interface_hits[post.extractor_mode] += 1
        if len(self.feed_state.seen_post_ids) > 2000:
            self.feed_state.seen_post_ids = set(list(self.feed_state.seen_post_ids)[-1500:])
        latest = latest_post_time_from_posts(posts)
        if latest:
            self.feed_state.latest_post_time = latest

    def _record_fetch_failure(self) -> None:
        self.feed_state.consecutive_failures += 1

    def _rank_posts_for_signal(self, posts: list[SquarePost]) -> list[SquarePost]:
        return sorted(posts, key=post_signal_weight, reverse=True)


class LongOnlyMomentumBot:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.spot_client = BinanceSpotClient(config)
        self.futures_client = BinanceFuturesClient(config)
        self.client = self.spot_client
        self.square = BinanceSquareScraper(build_retry_session(), config.square_urls)
        self.state = load_state(config.state_file)
        migrate_trade_log_to_journal(config.trade_journal_file, self.state.trade_log)
        self._load_square_feed_state()
        self._sync_legacy_position()
        self.stop_requested = False
        self.last_signal_record: dict[str, Any] | None = None
        self.notifier: TelegramNotifier | None = (
            TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
            if config.telegram_enabled and config.telegram_bot_token and config.telegram_chat_id
            else None
        )

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

    def _notify(self, text: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.send(text)
        except Exception as exc:
            LOGGER.warning("telegram send failed: %s", exc)

    def _market_client(self, market_type: str) -> BinanceSpotClient | BinanceFuturesClient:
        return self.futures_client if market_type == MARKET_FUTURES else self.spot_client

    def _candidate_market_type(self, candidate: TradeCandidate) -> str:
        return MARKET_FUTURES if candidate.market_type == MARKET_FUTURES else MARKET_SPOT

    def _position_market_type(self, position: PositionState) -> str:
        if position.position_mode in {"futures-live", "contract-sim"}:
            return MARKET_FUTURES
        if position.market_type in {MARKET_FUTURES, MARKET_SPOT}:
            return position.market_type
        return MARKET_SPOT

    def _position_is_futures_live(self, position: PositionState) -> bool:
        return (not self.config.dry_run) and self._position_market_type(position) == MARKET_FUTURES

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        while not self.stop_requested:
            self.run_once()
            for _ in range(self.config.poll_seconds):
                if self.stop_requested:
                    break
                time.sleep(1)

    def run_once(self) -> None:
        try:
            self.last_signal_record = None
            self.spot_client.sync_time()
            if self.config.trade_market_mode != "spot_only":
                self.futures_client.sync_time()
            self._ensure_live_account_safety()
            self._recover_pending_order()
            self._sync_open_position_with_account()
            self._manage_open_position()
            if len(self._active_positions()) < max(1, self.config.max_open_positions):
                self._scan_and_enter()
            else:
                self.last_signal_record = build_signal_record(
                    self.config,
                    source="run_once",
                    posts=[],
                    candidates=[],
                    candidate=None,
                    entry_confirmation=self.state.entry_confirmation,
                    square_confidence=self.state.square_confidence,
                    account_risk_snapshot=self.state.account_risk_snapshot,
                    final_action="skipped",
                    note="max open positions reached",
                )
            self._sync_square_feed_state()
            save_state(self.config.state_file, self.state)
            if self.config.signal_recording_enabled and self.last_signal_record:
                append_signal_record(self.config.signal_record_file, self.last_signal_record)
        except Exception:
            LOGGER.exception("cycle failed")
            self._notify("Cycle failed. Check dashboard logs.")

    def _active_positions(self) -> list[PositionState]:
        positions = [item for item in self.state.positions if item and item.symbol]
        if self.state.position and self.state.position.symbol:
            if all(item.symbol != self.state.position.symbol for item in positions):
                positions.insert(0, self.state.position)
        return positions

    def _load_square_feed_state(self) -> None:
        self.square.feed_state.seen_post_ids = set(self.state.square_seen_post_ids or [])
        self.square.feed_state.latest_post_time = self.state.square_latest_post_time or None
        self.square.feed_state.consecutive_failures = int(self.state.square_consecutive_failures or 0)

    def _sync_square_feed_state(self) -> None:
        seen = list(self.square.feed_state.seen_post_ids)
        self.state.square_seen_post_ids = seen[-1500:]
        self.state.square_latest_post_time = self.square.feed_state.latest_post_time or ""
        self.state.square_consecutive_failures = self.square.feed_state.consecutive_failures

    def _set_positions(self, positions: list[PositionState]) -> None:
        self.state.positions = [item for item in positions if item and item.symbol]
        self.state.position = self.state.positions[0] if self.state.positions else None

    def _sync_legacy_position(self) -> None:
        self.state.position = self.state.positions[0] if self.state.positions else self.state.position
        if self.state.position and not self.state.position.symbol:
            self.state.position = None
        if self.state.position is None and self.state.positions:
            self.state.position = self.state.positions[0]
        if self.state.position and all(item.symbol != self.state.position.symbol for item in self.state.positions):
            self.state.positions.insert(0, self.state.position)

    def _set_pending_order(
        self,
        symbol: str,
        side: str,
        client_order_id: str,
        action: str,
        quote_amount: Decimal | None = None,
        quantity: Decimal | None = None,
        market_type: str = MARKET_SPOT,
    ) -> None:
        self.state.pending_order = PendingOrderState(
            symbol=symbol,
            side=side,
            client_order_id=client_order_id,
            quote_amount=format_decimal(quote_amount) if quote_amount is not None else "",
            quantity=format_decimal(quantity) if quantity is not None else "",
            created_at=utc_now(),
            action=action,
            market_type=market_type,
        )
        self._touch_state()

    def _clear_pending_order(self, client_order_id: str | None = None) -> None:
        if client_order_id and self.state.pending_order and self.state.pending_order.client_order_id != client_order_id:
            return
        self.state.pending_order = None
        self._touch_state()

    def _recover_pending_order(self) -> None:
        pending = self.state.pending_order
        if self.config.dry_run or not pending or not pending.client_order_id or not pending.symbol:
            return
        market_type = pending.market_type if pending.market_type in {MARKET_FUTURES, MARKET_SPOT} else MARKET_SPOT
        try:
            order = self._market_client(market_type).get_order_by_client_id(pending.symbol, pending.client_order_id)
        except Exception as exc:
            LOGGER.warning("pending order lookup failed for %s %s: %s", pending.symbol, pending.client_order_id, exc)
            return
        status = str(order.get("status", "")).upper()
        if status in {"NEW", "PARTIALLY_FILLED", "PENDING_NEW"}:
            LOGGER.warning("pending order still open: %s %s status=%s", pending.symbol, pending.client_order_id, status)
            return
        if status in {"CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}:
            LOGGER.warning("clearing failed pending order: %s %s status=%s", pending.symbol, pending.client_order_id, status)
            self._clear_pending_order(pending.client_order_id)
            return
        if status != "FILLED":
            LOGGER.warning("keeping pending order with unknown status: %s %s status=%s", pending.symbol, pending.client_order_id, status)
            return

        if pending.side.upper() == "BUY":
            if pending.symbol in self._held_symbols():
                self._clear_pending_order(pending.client_order_id)
                return
            qty = Decimal(str(order.get("executedQty") or order.get("origQty") or "0"))
            avg_price = average_fill_price(order)
            if qty <= 0 or avg_price is None:
                LOGGER.warning("filled pending BUY lacks executable quantity/price: %s", order)
                return
            candidate = TradeCandidate(
                symbol=pending.symbol,
                base_asset=pending.symbol.removesuffix(self.config.quote_asset),
                mention_count=0,
                price_change_percent=Decimal("0"),
                volatility_percent=Decimal("0"),
                quote_volume=Decimal("0"),
                last_price=avg_price,
                market_type=market_type,
            )
            self._clear_pending_order(pending.client_order_id)
            self._open_position(candidate, qty, avg_price, order)
            LOGGER.warning("recovered filled BUY from pending order %s", pending.client_order_id)
            return

        if pending.side.upper() == "SELL":
            LOGGER.warning("recovered filled SELL from pending order %s; syncing account state", pending.client_order_id)
            self._clear_pending_order(pending.client_order_id)
            if market_type == MARKET_FUTURES:
                self.state.completed_round_trips += 1
                self._remove_position(pending.symbol)
                self._touch_state()
            else:
                self._sync_open_position_with_account()

    def _order_after_submit_failure(self, symbol: str, client_order_id: str, market_type: str = MARKET_SPOT) -> dict[str, Any] | None:
        try:
            return self._market_client(market_type).get_order_by_client_id(symbol, client_order_id)
        except Exception as exc:
            LOGGER.warning("order lookup after submit failure failed for %s %s: %s", symbol, client_order_id, exc)
            return None

    def _ensure_live_account_safety(self) -> None:
        if self.config.dry_run:
            return
        check = account_safety_snapshot(self.spot_client, self.config)
        self.state.last_safety_check = check
        self._touch_state()
        if not check.get("api_key_loaded") or not check.get("api_secret_loaded"):
            raise BinanceAPIError("live mode requires BINANCE_API_KEY and BINANCE_API_SECRET")
        if self.config.trade_market_mode == "spot_only" and check.get("error"):
            raise BinanceAPIError(f"live account safety check failed: {check['error']}")
        if self.config.trade_market_mode == "spot_only" and check.get("can_trade") is False:
            raise BinanceAPIError("live account safety check failed: account canTrade is false")
        if self.config.trade_market_mode == "spot_only" and check.get("spot_trading_allowed") is False:
            raise BinanceAPIError("live account safety check failed: API key/account does not report SPOT permission")
        if self.config.trade_market_mode != "spot_only":
            try:
                self.futures_client.account()
            except Exception as exc:
                raise BinanceAPIError(f"live futures account safety check failed: {exc}") from exc

    def _remove_position(self, symbol: str) -> None:
        self._set_positions([item for item in self._active_positions() if item.symbol != symbol])
        self.state.protection_orders = [item for item in self.state.protection_orders if item.symbol != symbol]

    def _replace_position(self, position: PositionState) -> None:
        replaced = False
        positions: list[PositionState] = []
        for item in self._active_positions():
            if item.symbol == position.symbol:
                positions.append(position)
                replaced = True
            else:
                positions.append(item)
        if not replaced:
            positions.append(position)
        self._set_positions(positions)

    def _held_symbols(self) -> set[str]:
        return {item.symbol for item in self._active_positions()}

    def _manage_open_position(self) -> None:
        for position in list(self._active_positions()):
            self._manage_single_position(position)

    def _manage_single_position(self, position: PositionState) -> None:
        if not position.symbol:
            return

        symbol = position.symbol
        qty = Decimal(position.quantity)
        entry_price = Decimal(position.entry_price)
        last_price = self._market_client(self._position_market_type(position)).ticker_price(symbol)
        highest_price = decimal_from_any(position.highest_price) or entry_price
        highest_updated = False
        if last_price > highest_price:
            highest_price = last_price
            position.highest_price = format_decimal(highest_price)
            highest_updated = True
        leverage_multiplier = self._position_leverage(position)
        contract_simulation = self._position_is_contract_sim(position)
        margin_quote = self._position_margin(position)
        unrealized_pnl = (last_price - entry_price) * qty
        unrealized_loss = max(Decimal("0"), -unrealized_pnl)
        unrealized_pnl_pct = (
            unrealized_pnl / margin_quote * Decimal("100")
            if contract_simulation and margin_quote > 0
            else (last_price - entry_price) / entry_price * Decimal("100")
        )
        pct_stop, stop_guard = effective_initial_stop_price(self.config, entry_price, leverage_multiplier, contract_simulation)
        dynamic_stop, dynamic_stop_mode = self._dynamic_stop_price(entry_price, highest_price, pct_stop)
        take_profit_price = entry_price * (Decimal("1") + self.config.take_profit_pct / Decimal("100"))
        fixed_mode = self._fixed_stop_enabled()

        LOGGER.info(
            "position %s qty=%s entry=%s last=%s high=%s pnl=%s roi=%s loss=%s stop_mode=%s stop_pct=%s effective_stop_pct=%s dynamic_stop=%s take_profit=%s",
            symbol,
            qty,
            entry_price,
            last_price,
            highest_price,
            unrealized_pnl,
            unrealized_pnl_pct,
            unrealized_loss,
            "fixed-usdt+" + dynamic_stop_mode if fixed_mode else dynamic_stop_mode,
            self.config.initial_stop_loss_pct,
            stop_guard["effective_stop_loss_pct"],
            dynamic_stop,
            take_profit_price,
        )

        should_price_stop = last_price <= dynamic_stop
        should_fixed_stop = fixed_mode and unrealized_loss >= self.config.fixed_stop_loss_usdt
        should_stop = should_fixed_stop or should_price_stop
        should_take_profit = (
            self.config.take_profit_pct > 0
            and dynamic_stop_mode != "trailing"
            and last_price >= take_profit_price
        )
        if should_stop:
            exit_label = "stop loss"
            dry_action = "DRY_RUN_STOP_SELL"
            live_action = "STOP_SELL"
        elif should_take_profit:
            exit_label = "take profit"
            dry_action = "DRY_RUN_TAKE_PROFIT_SELL"
            live_action = "TAKE_PROFIT_SELL"
        else:
            if highest_updated:
                self._touch_state()
            return

        self._close_position(position, last_price, dry_action, live_action, exit_label)

    def manual_close_position(self, symbol: str | None = None, quantity: Decimal | None = None) -> None:
        if not self.config.dry_run:
            self.spot_client.sync_time()
            if self.config.trade_market_mode != "spot_only":
                self.futures_client.sync_time()
            self._ensure_live_account_safety()
            self._recover_pending_order()
            self._sync_open_position_with_account()
        positions = list(self._active_positions())
        if not positions:
            raise BinanceAPIError("no open position to close")
        if symbol:
            wanted_symbol = symbol.upper()
            positions = [position for position in positions if position.symbol == wanted_symbol]
            if not positions:
                raise BinanceAPIError(f"no open position for {wanted_symbol}")
        for position in positions:
            last_price = self._market_client(self._position_market_type(position)).ticker_price(position.symbol)
            self._close_position(position, last_price, "DRY_RUN_MANUAL_SELL", "MANUAL_SELL", "manual close", quantity)

    def _close_position(
        self,
        position: PositionState,
        last_price: Decimal,
        dry_action: str,
        live_action: str,
        exit_label: str,
        close_quantity: Decimal | None = None,
    ) -> None:
        symbol = position.symbol
        qty = Decimal(position.quantity)
        wanted_qty = min(qty, close_quantity) if close_quantity is not None else qty
        full_close_requested = close_quantity is None or wanted_qty >= qty
        if wanted_qty <= 0:
            raise BinanceAPIError(f"close quantity for {symbol} must be positive")
        market_type = self._position_market_type(position)
        LOGGER.warning("%s triggered for %s", exit_label, symbol)
        if not self.config.dry_run:
            self._release_exchange_protection_for_close(position)
        sell_qty = self._safe_sell_quantity(symbol, position.base_asset, wanted_qty, market_type)
        if sell_qty <= 0:
            if self.config.dry_run and full_close_requested:
                LOGGER.info("[dry-run] cleared %s residual quantity below sell step after full close request: %s", symbol, qty)
                self.state.completed_round_trips += 1
                self._remove_position(symbol)
                self._touch_state()
                self._notify(f"[dry-run] {exit_label} {symbol} cleared residual qty={qty}")
                return
            LOGGER.error("no sellable balance for %s; clearing local position is unsafe, keeping state", symbol)
            return
        order_check_price = (
            last_price * (Decimal("1") - self.config.slippage_pct / Decimal("100"))
            if self.config.dry_run
            else last_price
        )
        sell_error = self._sell_order_error(symbol, sell_qty, order_check_price, market_type)
        if sell_error:
            if self.config.dry_run and full_close_requested:
                LOGGER.info("[dry-run] ignoring close validation for full close %s: %s", symbol, sell_error)
            else:
                LOGGER.error("cannot close %s: %s; keeping state", symbol, sell_error)
                return

        if self.config.dry_run:
            fill_price, fee_amount, quote_received = self._dry_run_sell_fill(last_price, sell_qty)
            if self._position_is_contract_sim(position):
                gross_quote = sell_qty * fill_price
                fee_amount = gross_quote * self.config.fee_rate_pct / Decimal("100")
                entry_price = Decimal(position.entry_price)
                realized_pnl = (fill_price - entry_price) * sell_qty - fee_amount
                position_margin = self._position_margin(position)
                closed_margin = position_margin * (sell_qty / qty) if qty > 0 else position_margin
                quote_received = closed_margin + realized_pnl
            LOGGER.warning("[dry-run] would SELL %s %s at market price=%s fee=%s", sell_qty, symbol, fill_price, fee_amount)
            self._append_trade(
                dry_action,
                symbol,
                sell_qty,
                fill_price,
                None,
                fee_amount=fee_amount,
                quote_amount=quote_received,
            )
            remaining_qty = max(Decimal("0"), qty - sell_qty)
            if remaining_qty > 0 and not full_close_requested:
                if self._position_is_contract_sim(position):
                    remaining_margin = max(Decimal("0"), self._position_margin(position) - (self._position_margin(position) * (sell_qty / qty)))
                    position.margin_quote = format_decimal(remaining_margin)
                    position.quote_spent = format_decimal(remaining_margin)
                    position.notional_quote = format_decimal(remaining_qty * Decimal(position.entry_price))
                else:
                    position.quote_spent = format_decimal(remaining_qty * Decimal(position.entry_price))
                position.quantity = format_decimal(remaining_qty)
                self._replace_position(position)
            else:
                if remaining_qty > 0:
                    LOGGER.info("[dry-run] cleared %s residual quantity after full close request: %s", symbol, remaining_qty)
                self.state.completed_round_trips += 1
                self._remove_position(symbol)
            self._touch_state()
            mode = "[dry-run] " if self.config.dry_run else ""
            self._notify(f"{mode}{exit_label} {symbol} qty={sell_qty} price={fill_price}")
            return

        client_order_id = build_client_order_id(live_action.lower(), symbol)
        self._set_pending_order(symbol, "SELL", client_order_id, live_action, quantity=sell_qty, market_type=market_type)
        try:
            order = self._market_client(market_type).market_sell_quantity(symbol, sell_qty, client_order_id=client_order_id)
        except Exception:
            recovered = self._order_after_submit_failure(symbol, client_order_id, market_type)
            if not recovered or str(recovered.get("status", "")).upper() != "FILLED":
                raise
            order = recovered
        self._clear_pending_order(client_order_id)
        avg_price = average_fill_price(order) or last_price
        self._append_trade(live_action, symbol, sell_qty, avg_price, order)
        remaining_qty = max(Decimal("0"), qty - sell_qty)
        if self.config.account_sync_enabled:
            if market_type == MARKET_SPOT:
                remaining_qty = round_down_to_step(
                    self._account_asset_balance(position.base_asset),
                    symbol_step_size(self.spot_client.exchange_info(), symbol),
                )
        if Decimal("0") < remaining_qty < qty:
            LOGGER.warning("account sync kept residual %s quantity after sell: %s", symbol, remaining_qty)
            position.quantity = format_decimal(remaining_qty)
            position.quote_spent = format_decimal(remaining_qty * Decimal(position.entry_price))
            self._replace_position(position)
        else:
            self.state.completed_round_trips += 1
            self._remove_position(symbol)
        self._touch_state()
        self._notify(f"{exit_label} {symbol} qty={sell_qty} price={avg_price}")

    def _scan_and_enter(self) -> None:
        daily_guard = self._daily_entry_guard_reason()
        if daily_guard:
            LOGGER.warning("entry skipped: %s", daily_guard)
            self._notify(f"Entry skipped: {daily_guard}")
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=[],
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=self.state.square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note=daily_guard,
            )
            return
        if len(self._active_positions()) >= max(1, self.config.max_open_positions):
            LOGGER.info("entry skipped: max open positions reached (%s)", self.config.max_open_positions)
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=[],
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=self.state.square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note="max open positions reached",
            )
            return
        market_guard = self._market_filter_reason()
        if market_guard:
            LOGGER.warning("entry skipped: %s", market_guard)
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=[],
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=self.state.square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note=market_guard,
            )
            return
        account_risk_guard = self._account_risk_guard_reason()
        if account_risk_guard:
            LOGGER.warning("entry skipped: %s", account_risk_guard)
            self._notify(f"Entry skipped: {account_risk_guard}")
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=[],
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=self.state.square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note=account_risk_guard,
            )
            return

        symbols = self._tradable_market_symbols()
        base_assets = {data["baseAsset"] for data in symbols.values()}
        posts = self.square.fetch_top_posts(self.config.top_post_limit, browser_mode=self.config.square_browser_mode)
        square_confidence = square_confidence_snapshot(posts, self.square.last_diagnostics, self.square.feed_state)
        self.state.square_confidence = square_confidence
        mentions = count_coin_mentions(posts, base_assets)
        source = "Binance Square browser + futures preferred" if self.config.square_browser_mode else "Binance Square + futures preferred"
        if Decimal(str(square_confidence.get("score", "0"))) < self.config.min_square_confidence_score:
            reason = (
                f"Square confidence {square_confidence.get('score')} below "
                f"{self.config.min_square_confidence_score}; automatic entry skipped"
            )
            LOGGER.warning(reason)
            self.state.entry_confirmation = {
                "passed": False,
                "symbol": "",
                "reason": reason,
                "square_confidence": square_confidence,
                "checked_at": utc_now(),
            }
            self._touch_state()
            self._notify(f"Entry skipped: {reason}")
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=posts,
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note=reason,
            )
            return
        if not mentions:
            reason = "no valid long-only Binance Square mentions found; automatic market-only fallback is disabled"
            LOGGER.warning(reason)
            self.state.entry_confirmation = {
                "passed": False,
                "symbol": "",
                "reason": reason,
                "square_confidence": square_confidence,
                "checked_at": utc_now(),
            }
            self._touch_state()
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=posts,
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note=reason,
            )
            return

        candidates = self._rank_trade_candidates(symbols, mentions)
        ranked_assets = [item.base_asset for item in candidates[: self.config.top_coin_limit]]
        LOGGER.info("ranked assets from %s + 24h market movers: %s", source, ranked_assets)

        if not candidates:
            LOGGER.info("no candidate passed momentum filters")
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=posts,
                candidates=[],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note="no candidate passed momentum filters",
            )
            return
        candidate = self._first_allowed_candidate(candidates)
        if candidate is None:
            LOGGER.info("all candidates are blocked by entry guards")
            self._touch_state()
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=posts,
                candidates=candidates[: self.config.top_coin_limit],
                candidate=None,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="skipped",
                note="all candidates are blocked by entry guards",
            )
            return

        LOGGER.info("selected candidate: %s", asdict(candidate))
        market_type = self._candidate_market_type(candidate)
        if self.config.dry_run:
            fill_price, qty, fee_amount, quote_spent = self._dry_run_buy_fill(candidate.last_price, market_type)
            LOGGER.warning(
                "[dry-run] would BUY %s with %s %s price=%s fee=%s mode=%s leverage=%sx",
                candidate.symbol,
                self.config.order_quote_amount,
                self.config.quote_asset,
                fill_price,
                fee_amount,
                "contract-sim" if market_type == MARKET_FUTURES else "spot",
                self.config.leverage_multiplier if market_type == MARKET_FUTURES else Decimal("1"),
            )
            self._open_position(candidate, qty, fill_price, None, quote_spent=quote_spent, fee_amount=fee_amount)
            self.last_signal_record = build_signal_record(
                self.config,
                source="run_once",
                posts=posts,
                candidates=candidates[: self.config.top_coin_limit],
                candidate=candidate,
                entry_confirmation=self.state.entry_confirmation,
                square_confidence=square_confidence,
                account_risk_snapshot=self.state.account_risk_snapshot,
                final_action="entered",
                note="dry-run position opened",
            )
            return

        client_order_id = build_client_order_id("buy", candidate.symbol)
        self._set_pending_order(
            candidate.symbol,
            "BUY",
            client_order_id,
            "BUY",
            quote_amount=self.config.order_quote_amount,
            market_type=market_type,
        )
        try:
            if market_type == MARKET_FUTURES:
                self._prepare_futures_symbol_for_live(candidate.symbol)
                qty_estimate = self._futures_order_quantity(candidate.symbol, self.config.order_quote_amount, candidate.last_price)
                order = self.futures_client.market_buy_quantity(candidate.symbol, qty_estimate, client_order_id=client_order_id)
            else:
                order = self.spot_client.market_buy_quote(candidate.symbol, self.config.order_quote_amount, client_order_id=client_order_id)
        except Exception:
            recovered = self._order_after_submit_failure(candidate.symbol, client_order_id, market_type)
            if not recovered or str(recovered.get("status", "")).upper() != "FILLED":
                raise
            order = recovered
        self._clear_pending_order(client_order_id)
        qty = Decimal(str(order.get("executedQty") or order.get("origQty") or "0"))
        avg_price = average_fill_price(order) or candidate.last_price
        if qty <= 0:
            raise BinanceAPIError(f"market buy returned zero executedQty: {order}")
        if market_type == MARKET_SPOT and self.config.account_sync_enabled:
            synced_qty = round_down_to_step(
                self._account_asset_balance(candidate.base_asset),
                symbol_step_size(self.spot_client.exchange_info(), candidate.symbol),
            )
            if Decimal("0") < synced_qty < qty:
                LOGGER.warning("account sync reduced buy quantity for %s from %s to %s", candidate.symbol, qty, synced_qty)
                qty = synced_qty
        self._open_position(candidate, qty, avg_price, order)
        self.last_signal_record = build_signal_record(
            self.config,
            source="run_once",
            posts=posts,
            candidates=candidates[: self.config.top_coin_limit],
            candidate=candidate,
            entry_confirmation=self.state.entry_confirmation,
            square_confidence=square_confidence,
            account_risk_snapshot=self.state.account_risk_snapshot,
            final_action="entered",
            note="live futures position opened" if market_type == MARKET_FUTURES else "live spot position opened",
        )

    def _position_leverage(self, position: PositionState) -> Decimal:
        parsed = decimal_from_any(position.leverage_multiplier)
        return parsed if parsed and parsed > 0 else Decimal("1")

    def _position_margin(self, position: PositionState) -> Decimal:
        margin = decimal_from_any(position.margin_quote)
        if margin and margin > 0:
            return margin
        return decimal_from_any(position.quote_spent) or Decimal("0")

    def _position_is_contract_sim(self, position: PositionState) -> bool:
        return self.config.dry_run and self._position_market_type(position) == MARKET_FUTURES

    def _tradable_market_symbols(self) -> dict[str, dict[str, Any]]:
        mode = self.config.trade_market_mode
        result: dict[str, dict[str, Any]] = {}
        if mode != "futures_only":
            result.update(self.spot_client.tradable_quote_symbols(self.config.quote_asset))
        if mode != "spot_only":
            futures_symbols = self.futures_client.tradable_quote_symbols(self.config.quote_asset)
            result.update(futures_symbols)
        return result

    def _ticker_24hr_for_symbols(self, symbols: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        spot_symbols = {
            symbol
            for symbol, info in symbols.items()
            if info.get("market_type") == MARKET_SPOT
        }
        futures_symbols = {
            symbol
            for symbol, info in symbols.items()
            if info.get("market_type") == MARKET_FUTURES
        }
        rows: list[dict[str, Any]] = []
        if spot_symbols:
            for ticker in self.spot_client.ticker_24hr():
                if ticker.get("symbol") in spot_symbols:
                    item = dict(ticker)
                    item["market_type"] = MARKET_SPOT
                    rows.append(item)
        if futures_symbols:
            for ticker in self.futures_client.ticker_24hr():
                if ticker.get("symbol") in futures_symbols:
                    item = dict(ticker)
                    item["market_type"] = MARKET_FUTURES
                    rows.append(item)
        return rows

    def _select_trade_candidate(
        self,
        symbols: dict[str, dict[str, Any]],
        mentions: Counter[str],
        hot_assets: list[str],
    ) -> TradeCandidate | None:
        del hot_assets
        candidates = self._rank_trade_candidates(symbols, mentions)
        return candidates[0] if candidates else None

    def _rank_trade_candidates(
        self,
        symbols: dict[str, dict[str, Any]],
        mentions: Counter[str],
    ) -> list[TradeCandidate]:
        tickers = self._ticker_24hr_for_symbols(symbols)
        candidates: list[TradeCandidate] = []
        max_mentions = max(mentions.values(), default=0)

        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            if symbol in self._held_symbols():
                continue
            symbol_info = symbols.get(symbol)
            if symbol_info is None:
                continue
            base_asset = symbol_info["baseAsset"]
            market_type = str(symbol_info.get("market_type") or ticker.get("market_type") or MARKET_SPOT)
            if not is_momentum_asset(base_asset, self.config.quote_asset):
                continue
            if not self._asset_allowed(symbol, base_asset):
                continue

            price_change = Decimal(str(ticker.get("priceChangePercent", "0")))
            quote_volume = Decimal(str(ticker.get("quoteVolume", "0")))
            high = Decimal(str(ticker.get("highPrice", "0")))
            low = Decimal(str(ticker.get("lowPrice", "0")))
            last_price = Decimal(str(ticker.get("lastPrice", "0")))
            if low <= 0:
                continue
            volatility = (high - low) / low * Decimal("100")
            if price_change < self.config.min_price_change_percent:
                continue
            if quote_volume < self.config.min_quote_volume:
                continue
            if volatility < self.config.min_volatility_percent:
                continue

            mention_count = mentions.get(base_asset, 0)
            volume_score = volume_rank_score(quote_volume, self.config.min_quote_volume)
            market_score = (price_change * Decimal("10")) + (volatility * Decimal("4")) + volume_score
            square_score = square_rank_score(mention_count, max_mentions)
            combined_score = market_score + square_score
            candidates.append(
                TradeCandidate(
                    symbol=symbol,
                    base_asset=base_asset,
                    mention_count=mention_count,
                    price_change_percent=price_change,
                    volatility_percent=volatility,
                    quote_volume=quote_volume,
                    last_price=last_price,
                    market_score=market_score,
                    square_score=square_score,
                    combined_score=combined_score,
                    market_type=market_type,
                )
            )

        candidates.sort(
            key=lambda item: (
                item.combined_score,
                item.market_score,
                item.volatility_percent,
                item.price_change_percent,
                item.quote_volume,
            ),
            reverse=True,
        )
        return candidates

    def _market_momentum_mentions(self, symbols: dict[str, dict[str, Any]]) -> Counter[str]:
        tickers = self._ticker_24hr_for_symbols(symbols)
        ranked: list[tuple[str, Decimal, Decimal, Decimal]] = []
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            symbol_info = symbols.get(symbol)
            if symbol_info is None:
                continue
            base_asset = symbol_info["baseAsset"]
            if not is_momentum_asset(base_asset, self.config.quote_asset):
                continue
            quote_volume = Decimal(str(ticker.get("quoteVolume", "0")))
            price_change = Decimal(str(ticker.get("priceChangePercent", "0")))
            high = Decimal(str(ticker.get("highPrice", "0")))
            low = Decimal(str(ticker.get("lowPrice", "0")))
            if low <= 0 or quote_volume < self.config.min_quote_volume or price_change <= 0:
                continue
            volatility = (high - low) / low * Decimal("100")
            if volatility < self.config.min_volatility_percent:
                continue
            ranked.append((base_asset, price_change, volatility, quote_volume))

        ranked.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
        mentions: Counter[str] = Counter()
        for rank, (asset, price_change, volatility, _) in enumerate(ranked[: self.config.top_coin_limit], start=1):
            score = int((price_change * Decimal("10")) + (volatility * Decimal("5"))) + self.config.top_coin_limit - rank + 1
            mentions[asset] = max(score, 1)
        return mentions

    def _open_position(
        self,
        candidate: TradeCandidate,
        quantity: Decimal,
        entry_price: Decimal,
        order: dict[str, Any] | None,
        quote_spent: Decimal | None = None,
        fee_amount: Decimal | None = None,
    ) -> None:
        spent = quote_spent if quote_spent is not None else quantity * entry_price
        market_type = self._candidate_market_type(candidate)
        leverage = self.config.leverage_multiplier if market_type == MARKET_FUTURES else Decimal("1")
        if market_type == MARKET_FUTURES:
            position_mode = "contract-sim" if self.config.dry_run else "futures-live"
        else:
            position_mode = "spot"
        margin_quote = spent if market_type == MARKET_FUTURES else Decimal("0")
        notional_quote = quantity * entry_price
        new_position = PositionState(
            symbol=candidate.symbol,
            base_asset=candidate.base_asset,
            quantity=format_decimal(quantity),
            entry_price=format_decimal(entry_price),
            quote_spent=format_decimal(spent),
            highest_price=format_decimal(entry_price),
            opened_at=utc_now(),
            order_id=int(order["orderId"]) if order and "orderId" in order else None,
            position_mode=position_mode,
            margin_quote=format_decimal(margin_quote),
            notional_quote=format_decimal(notional_quote),
            leverage_multiplier=format_decimal(leverage),
            market_type=market_type,
            margin_type=self.config.futures_margin_type.upper() if market_type == MARKET_FUTURES else "",
        )
        positions = [item for item in self._active_positions() if item.symbol != candidate.symbol]
        positions.append(new_position)
        self._set_positions(positions)
        self.state.first_buy_done = True
        self._append_trade("BUY", candidate.symbol, quantity, entry_price, order, fee_amount=fee_amount, quote_amount=spent)
        self._touch_state()
        self._ensure_exchange_protection(new_position)
        mode = "[dry-run] " if self.config.dry_run else ""
        self._notify(f"{mode}BUY {candidate.symbol} qty={quantity} price={entry_price} spent={spent}")

    def _ensure_exchange_protection(self, position: PositionState) -> None:
        if not self.config.exchange_protection_enabled:
            return
        if self._position_market_type(position) == MARKET_FUTURES and not self.config.dry_run:
            LOGGER.warning("exchange-side futures protection is not enabled; local polling will manage reduce-only exits for %s", position.symbol)
            self._notify(f"Futures exchange protection is not enabled for {position.symbol}; local polling will manage exits.")
            return
        protection = self._build_protection_order_state(position)
        if self.config.dry_run:
            protection.status = "simulated"
            protection.dry_run = True
            self._store_protection_order(protection)
            LOGGER.info("[dry-run] simulated protection order for %s: %s", position.symbol, asdict(protection))
            return
        try:
            if protection.kind == "oco":
                result = self.client.oco_sell(
                    position.symbol,
                    Decimal(protection.quantity),
                    Decimal(protection.take_profit_price),
                    Decimal(protection.stop_price),
                    Decimal(protection.stop_limit_price),
                    protection.client_order_id,
                    f"{protection.client_order_id}-tp"[:36],
                    f"{protection.client_order_id}-sl"[:36],
                )
                protection.order_list_id = int(result["orderListId"]) if "orderListId" in result else None
            else:
                result = self.client.stop_loss_limit_sell(
                    position.symbol,
                    Decimal(protection.quantity),
                    Decimal(protection.stop_price),
                    Decimal(protection.stop_limit_price),
                    protection.client_order_id,
                )
                protection.order_list_id = int(result["orderId"]) if "orderId" in result else None
            protection.status = "active"
            protection.dry_run = False
            self._store_protection_order(protection)
            LOGGER.info("exchange protection created for %s: %s", position.symbol, asdict(protection))
        except Exception as exc:
            protection.status = "failed"
            protection.dry_run = False
            protection.error = str(exc)
            self._store_protection_order(protection)
            LOGGER.error("exchange protection missing for %s: %s", position.symbol, exc)
            self._notify(f"Exchange protection missing for {position.symbol}: {exc}")

    def _build_protection_order_state(self, position: PositionState) -> ProtectionOrderState:
        market_type = self._position_market_type(position)
        rules = symbol_order_rules(self._market_client(market_type).exchange_info(), position.symbol)
        entry = Decimal(position.entry_price)
        quantity = round_down_to_step(Decimal(position.quantity), rules.step_size)
        leverage_multiplier = self._position_leverage(position)
        contract_simulation = self._position_is_contract_sim(position)
        effective_stop_price, _ = effective_initial_stop_price(self.config, entry, leverage_multiplier, contract_simulation)
        stop_price = round_down_to_step(
            effective_stop_price,
            rules.tick_size,
        )
        stop_limit_price = round_down_to_step(
            stop_price * (Decimal("1") - self.config.oco_stop_limit_slippage_pct / Decimal("100")),
            rules.tick_size,
        )
        take_profit_price = Decimal("0")
        kind = "stop_loss_limit"
        if self.config.take_profit_pct > 0:
            take_profit_price = round_down_to_step(
                entry * (Decimal("1") + self.config.take_profit_pct / Decimal("100")),
                rules.tick_size,
            )
            kind = "oco"
        if quantity <= 0:
            raise BinanceAPIError(f"cannot create protection for {position.symbol}: quantity is zero after step rounding")
        sell_error = self._sell_order_error(position.symbol, quantity, stop_limit_price, market_type)
        if sell_error:
            raise BinanceAPIError(f"cannot create protection for {position.symbol}: {sell_error}")
        return ProtectionOrderState(
            symbol=position.symbol,
            client_order_id=build_client_order_id("protect", position.symbol),
            quantity=format_decimal(quantity),
            take_profit_price=format_decimal(take_profit_price),
            stop_price=format_decimal(stop_price),
            stop_limit_price=format_decimal(stop_limit_price),
            kind=kind,
            dry_run=self.config.dry_run,
            created_at=utc_now(),
        )

    def _store_protection_order(self, protection: ProtectionOrderState) -> None:
        orders = [item for item in self.state.protection_orders if item.symbol != protection.symbol]
        orders.append(protection)
        self.state.protection_orders = orders[-50:]
        self._touch_state()

    def _release_exchange_protection_for_close(self, position: PositionState) -> None:
        active = [
            item
            for item in self.state.protection_orders
            if item.symbol == position.symbol and item.status in {"active", "simulated"}
        ]
        for protection in active:
            if protection.dry_run:
                continue
            try:
                if protection.kind == "oco":
                    self.client.cancel_order_list(
                        position.symbol,
                        order_list_id=protection.order_list_id,
                        list_client_order_id=protection.client_order_id,
                    )
                else:
                    self.client.cancel_order_by_client_id(position.symbol, protection.client_order_id)
                protection.status = "cancelled"
                LOGGER.info("cancelled protection order before close: %s %s", position.symbol, protection.client_order_id)
            except Exception as exc:
                protection.status = "cancel_failed"
                protection.error = str(exc)
                LOGGER.warning("failed to cancel protection order before close for %s: %s", position.symbol, exc)
        if active:
            self._touch_state()

    def _dynamic_stop_price(self, entry_price: Decimal, highest_price: Decimal, pct_stop: Decimal) -> tuple[Decimal, str]:
        return dynamic_stop_price(self.config, entry_price, highest_price, pct_stop)

    def _daily_entry_guard_reason(self) -> str | None:
        stats = self._daily_trade_stats()
        if self.config.max_daily_trades > 0 and stats["buy_count"] >= self.config.max_daily_trades:
            return f"daily trade limit reached ({stats['buy_count']}/{self.config.max_daily_trades})"
        if self.config.max_daily_loss_usdt > 0 and stats["realized_pnl"] <= -self.config.max_daily_loss_usdt:
            return f"daily loss limit reached ({stats['realized_pnl']} {self.config.quote_asset})"
        return None

    def _first_allowed_candidate(self, candidates: list[TradeCandidate]) -> TradeCandidate | None:
        for candidate in candidates:
            cooldown_until = self._cooldown_until(candidate.symbol)
            if cooldown_until is None:
                account_risk_guard = self._account_risk_guard_reason(candidate)
                if account_risk_guard:
                    LOGGER.info("candidate %s skipped by account risk: %s", candidate.symbol, account_risk_guard)
                    self.state.entry_confirmation = self._entry_confirmation_payload(candidate, False, account_risk_guard)
                    continue
                buy_error = self._buy_order_error(candidate.symbol, self.config.order_quote_amount, candidate.last_price, self._candidate_market_type(candidate))
                if buy_error:
                    LOGGER.info("candidate %s skipped by order rules: %s", candidate.symbol, buy_error)
                    self.state.entry_confirmation = self._entry_confirmation_payload(candidate, False, buy_error)
                    continue
                confirmation = self._confirm_candidate_entry(candidate)
                self.state.entry_confirmation = confirmation
                if not confirmation.get("passed"):
                    LOGGER.info("candidate %s skipped by entry confirmation: %s", candidate.symbol, confirmation.get("reason"))
                    continue
                return candidate
            LOGGER.info("candidate %s skipped for cooldown until %s", candidate.symbol, cooldown_until.isoformat())
            self.state.entry_confirmation = self._entry_confirmation_payload(candidate, False, f"cooldown until {cooldown_until.isoformat()}")
        return None

    def _confirm_candidate_entry(self, candidate: TradeCandidate) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        if self.config.kline_confirmation_enabled:
            kline_check = self._kline_confirmation(candidate)
            checks["kline"] = kline_check
            if not kline_check.get("passed"):
                return self._entry_confirmation_payload(candidate, False, str(kline_check.get("reason", "kline confirmation failed")), checks)
        liquidity_check = self._orderbook_liquidity_confirmation(candidate)
        checks["liquidity"] = liquidity_check
        if not liquidity_check.get("passed"):
            return self._entry_confirmation_payload(candidate, False, str(liquidity_check.get("reason", "liquidity filter failed")), checks)
        return self._entry_confirmation_payload(candidate, True, "entry confirmation passed", checks)

    def _entry_confirmation_payload(
        self,
        candidate: TradeCandidate,
        passed: bool,
        reason: str,
        checks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "passed": passed,
            "symbol": candidate.symbol,
            "base_asset": candidate.base_asset,
            "reason": reason,
            "checks": checks or {},
            "square_confidence": self.state.square_confidence,
            "checked_at": utc_now(),
        }

    def _kline_confirmation(self, candidate: TradeCandidate) -> dict[str, Any]:
        client = self._market_client(self._candidate_market_type(candidate))
        snapshots = {
            "5m": kline_confirmation_snapshot(client.klines(candidate.symbol, "5m", 24)),
            "15m": kline_confirmation_snapshot(client.klines(candidate.symbol, "15m", 24)),
            "1h": kline_confirmation_snapshot(client.klines(candidate.symbol, "1h", 24)),
        }
        failures = []
        if snapshots["15m"]["roc_pct"] <= Decimal("0"):
            failures.append("15m ROC is not positive")
        if snapshots["1h"]["roc_pct"] <= Decimal("0"):
            failures.append("1h ROC is not positive")
        if snapshots["5m"]["roc_pct"] < Decimal("-0.2"):
            failures.append("5m pullback is deeper than -0.2%")
        if not snapshots["5m"]["above_ema9"]:
            failures.append("5m close is below EMA9")
        if candidate.price_change_percent >= Decimal("20") and (
            snapshots["5m"]["roc_pct"] < Decimal("0") or snapshots["15m"]["roc_pct"] < Decimal("0")
        ):
            failures.append("24h mover is high but short-term ROC is rolling over")
        return stringify_decimals(
            {
                "passed": not failures,
                "reason": "; ".join(failures) if failures else "short-term trend confirmed",
                "intervals": snapshots,
            }
        )

    def _orderbook_liquidity_confirmation(self, candidate: TradeCandidate) -> dict[str, Any]:
        if self.config.max_spread_bps <= 0 and self.config.min_orderbook_depth_usdt <= 0:
            return {"passed": True, "reason": "liquidity filter disabled"}
        snapshot = orderbook_liquidity_snapshot(self._market_client(self._candidate_market_type(candidate)).depth(candidate.symbol, 50))
        failures = []
        spread_bps = snapshot.get("spread_bps")
        ask_depth = snapshot.get("ask_depth_usdt")
        if self.config.max_spread_bps > 0 and spread_bps is not None and spread_bps > self.config.max_spread_bps:
            failures.append(f"spread {spread_bps} bps exceeds {self.config.max_spread_bps}")
        if self.config.min_orderbook_depth_usdt > 0 and ask_depth is not None and ask_depth < self.config.min_orderbook_depth_usdt:
            failures.append(f"ask depth {ask_depth} below {self.config.min_orderbook_depth_usdt} {self.config.quote_asset}")
        if spread_bps is None or ask_depth is None:
            failures.append("orderbook depth is unavailable")
        snapshot["passed"] = not failures
        snapshot["reason"] = "; ".join(failures) if failures else "orderbook liquidity confirmed"
        return stringify_decimals(snapshot)

    def _asset_allowed(self, symbol: str, base_asset: str) -> bool:
        whitelist = {item.upper() for item in self.config.asset_whitelist}
        blacklist = {item.upper() for item in self.config.asset_blacklist}
        normalized_symbol = symbol.upper()
        normalized_base = base_asset.upper()
        if whitelist and normalized_symbol not in whitelist and normalized_base not in whitelist:
            LOGGER.info("candidate %s skipped by whitelist", symbol)
            return False
        if normalized_symbol in blacklist or normalized_base in blacklist:
            LOGGER.info("candidate %s skipped by blacklist", symbol)
            return False
        return True

    def _market_filter_reason(self) -> str | None:
        if not self.config.market_filter_enabled:
            return None
        assets = [item.upper() for item in self.config.market_filter_assets if item.strip()]
        if not assets:
            return None
        changes: dict[str, Decimal] = {}
        symbols = self._tradable_market_symbols()
        for ticker in self._ticker_24hr_for_symbols(symbols):
            symbol = str(ticker.get("symbol", "")).upper()
            for asset in assets:
                if symbol == f"{asset}{self.config.quote_asset}".upper():
                    changes[asset] = Decimal(str(ticker.get("priceChangePercent", "0")))
        missing = [asset for asset in assets if asset not in changes]
        if missing:
            return f"market filter data missing for {', '.join(missing)}"
        passed = {
            asset: change >= self.config.market_filter_min_change_pct
            for asset, change in changes.items()
        }
        ok = all(passed.values()) if self.config.market_filter_require_all else any(passed.values())
        if ok:
            LOGGER.info("market filter passed: %s", changes)
            return None
        detail = ", ".join(f"{asset}={changes[asset]}%" for asset in assets)
        mode = "all" if self.config.market_filter_require_all else "any"
        return f"market filter blocked entry ({mode} of {assets} must be >= {self.config.market_filter_min_change_pct}%): {detail}"

    def _account_risk_guard_reason(self, candidate: TradeCandidate | None = None) -> str | None:
        snapshot = self._account_risk_snapshot(candidate)
        self.state.account_risk_snapshot = snapshot
        self._touch_state()
        if snapshot.get("entry_blocked"):
            return str(snapshot.get("reason") or "account risk guard blocked entry")
        return None

    def _account_risk_snapshot(self, candidate: TradeCandidate | None = None) -> dict[str, Any]:
        positions = self._active_positions()
        position_values: dict[str, Decimal] = {}
        total_exposure = Decimal("0")
        unrealized_pnl = Decimal("0")
        for position in positions:
            qty = Decimal(position.quantity)
            entry_price = Decimal(position.entry_price)
            try:
                price = self._market_client(self._position_market_type(position)).ticker_price(position.symbol)
            except Exception:
                price = entry_price
            value = qty * price
            position_values[position.symbol] = value
            total_exposure += value
            unrealized_pnl += (price - entry_price) * qty

        quote_balance = Decimal("0")
        if not self.config.dry_run:
            try:
                quote_balance = self._account_asset_balance(self.config.quote_asset)
            except Exception:
                LOGGER.exception("account risk quote balance lookup failed; using local exposure basis")
        equity = quote_balance + total_exposure
        if equity <= 0:
            equity = max(
                total_exposure + self.config.order_quote_amount,
                self.config.order_quote_amount * Decimal(max(1, self.config.max_open_positions)),
            )

        proposed_quote = self.config.order_quote_amount if candidate else Decimal("0")
        if candidate and candidate.market_type == MARKET_FUTURES:
            proposed_quote *= self.config.leverage_multiplier
        proposed_total_exposure = total_exposure + proposed_quote
        proposed_symbol_exposure = proposed_quote
        if candidate:
            proposed_symbol_exposure += position_values.get(candidate.symbol, Decimal("0"))

        stats = self._daily_trade_stats()
        loss_streak = current_loss_streak(self.state.trade_log)
        daily_unrealized_drawdown = -unrealized_pnl if unrealized_pnl < 0 else Decimal("0")
        daily_drawdown = daily_unrealized_drawdown + (-stats["realized_pnl"] if stats["realized_pnl"] < 0 else Decimal("0"))
        drawdown_pct = daily_drawdown / equity * Decimal("100") if equity > 0 else Decimal("0")
        total_exposure_pct = proposed_total_exposure / equity * Decimal("100") if equity > 0 else Decimal("0")
        symbol_exposure_pct = proposed_symbol_exposure / equity * Decimal("100") if equity > 0 else Decimal("0")
        risk_based_quote = Decimal("0")
        if self.config.risk_per_trade_pct > 0 and self.config.initial_stop_loss_pct > 0:
            risk_budget = equity * self.config.risk_per_trade_pct / Decimal("100")
            risk_based_quote = risk_budget / (self.config.initial_stop_loss_pct / Decimal("100"))

        reasons: list[str] = []
        if self.config.max_total_exposure_pct > 0 and total_exposure_pct > self.config.max_total_exposure_pct:
            reasons.append(f"total exposure {format_decimal(total_exposure_pct)}% exceeds {self.config.max_total_exposure_pct}%")
        if candidate and self.config.max_symbol_exposure_pct > 0 and symbol_exposure_pct > self.config.max_symbol_exposure_pct:
            reasons.append(f"{candidate.symbol} exposure {format_decimal(symbol_exposure_pct)}% exceeds {self.config.max_symbol_exposure_pct}%")
        if self.config.max_consecutive_losses > 0 and loss_streak >= self.config.max_consecutive_losses:
            reasons.append(f"consecutive losses {loss_streak} reached {self.config.max_consecutive_losses}")
        if self.config.max_intraday_drawdown_pct > 0 and drawdown_pct >= self.config.max_intraday_drawdown_pct:
            reasons.append(f"intraday drawdown {format_decimal(drawdown_pct)}% reached {self.config.max_intraday_drawdown_pct}%")

        return stringify_decimals(
            {
                "entry_blocked": bool(reasons),
                "reason": "; ".join(reasons) if reasons else "account risk checks passed",
                "quote_asset": self.config.quote_asset,
                "equity_estimate": equity,
                "quote_balance": quote_balance,
                "total_exposure": total_exposure,
                "proposed_total_exposure": proposed_total_exposure,
                "total_exposure_pct": total_exposure_pct,
                "symbol": candidate.symbol if candidate else "",
                "proposed_symbol_exposure": proposed_symbol_exposure,
                "symbol_exposure_pct": symbol_exposure_pct,
                "realized_pnl_today": stats["realized_pnl"],
                "unrealized_pnl": unrealized_pnl,
                "intraday_drawdown": daily_drawdown,
                "intraday_drawdown_pct": drawdown_pct,
                "consecutive_losses": loss_streak,
                "fixed_order_quote": self.config.order_quote_amount,
                "risk_based_quote_suggestion": risk_based_quote,
                "limits": {
                    "max_total_exposure_pct": self.config.max_total_exposure_pct,
                    "max_symbol_exposure_pct": self.config.max_symbol_exposure_pct,
                    "max_consecutive_losses": self.config.max_consecutive_losses,
                    "max_intraday_drawdown_pct": self.config.max_intraday_drawdown_pct,
                    "risk_per_trade_pct": self.config.risk_per_trade_pct,
                },
                "checked_at": utc_now(),
            }
        )

    def _sync_open_position_with_account(self) -> None:
        if self.config.dry_run or not self.config.account_sync_enabled:
            return
        positions = self._active_positions()
        if not positions:
            return
        updated_positions: list[PositionState] = []
        changed = False
        try:
            for position in positions:
                if self._position_market_type(position) == MARKET_FUTURES:
                    updated_positions.append(position)
                    continue
                account_qty = self._account_asset_balance(position.base_asset)
                wanted_qty = Decimal(position.quantity)
                step_size = symbol_step_size(self.spot_client.exchange_info(), position.symbol)
                synced_qty = round_down_to_step(account_qty, step_size)
                if synced_qty <= 0:
                    LOGGER.warning("account sync cleared local position %s; no %s balance found", position.symbol, position.base_asset)
                    self._append_trade("ACCOUNT_SYNC_CLEAR", position.symbol, wanted_qty, Decimal(position.entry_price), None)
                    changed = True
                    continue
                if synced_qty < wanted_qty:
                    LOGGER.warning("account sync reduced %s local quantity from %s to %s", position.symbol, wanted_qty, synced_qty)
                    position.quantity = format_decimal(synced_qty)
                    position.quote_spent = format_decimal(synced_qty * Decimal(position.entry_price))
                    changed = True
                updated_positions.append(position)
            if changed:
                self._set_positions(updated_positions)
                self._touch_state()
        except Exception:
            LOGGER.exception("account sync failed; keeping local position state")

    def _account_asset_balance(self, asset: str) -> Decimal:
        account = self.spot_client.account()
        for item in account.get("balances", []):
            if item.get("asset") == asset:
                return Decimal(str(item.get("free", "0"))) + Decimal(str(item.get("locked", "0")))
        return Decimal("0")

    def _cooldown_until(self, symbol: str) -> datetime | None:
        if self.config.cooldown_minutes <= 0:
            return None
        now = datetime.now(timezone.utc)
        cooldown_seconds = self.config.cooldown_minutes * 60
        for item in reversed(self.state.trade_log):
            action = str(item.get("action", ""))
            if item.get("symbol") != symbol or "SELL" not in action:
                continue
            closed_at = parse_timestamp(item.get("ts"))
            if closed_at is None:
                continue
            until = closed_at + timedelta(seconds=cooldown_seconds)
            if until > now:
                return until
            return None
        return None

    def _daily_trade_stats(self) -> dict[str, Decimal | int]:
        today = datetime.now(timezone.utc).date()
        buy_count = 0
        realized_pnl = Decimal("0")
        open_costs: dict[str, list[dict[str, Decimal]]] = {}

        for item in self.state.trade_log:
            action = str(item.get("action", ""))
            symbol = str(item.get("symbol", ""))
            ts = parse_timestamp(item.get("ts"))
            qty = decimal_from_any(item.get("quantity"))
            price = decimal_from_any(item.get("price"))
            if not symbol or qty is None or price is None:
                continue
            amount = decimal_from_any(item.get("quote_amount")) or qty * price
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

        return {"buy_count": buy_count, "realized_pnl": realized_pnl}

    def _fixed_stop_enabled(self) -> bool:
        if self.config.fixed_stop_after_first_round_trip and self.state.completed_round_trips > 0:
            return True
        if self.config.fixed_stop_equity_usdt is None:
            return False
        try:
            account = self.spot_client.account()
            balances = {item["asset"]: Decimal(item["free"]) + Decimal(item["locked"]) for item in account["balances"]}
            quote_balance = balances.get(self.config.quote_asset, Decimal("0"))
            position_value = Decimal("0")
            for position in self._active_positions():
                last = self._market_client(self._position_market_type(position)).ticker_price(position.symbol)
                position_value += Decimal(position.quantity) * last
            return quote_balance + position_value >= self.config.fixed_stop_equity_usdt
        except Exception:
            LOGGER.exception("failed to evaluate equity threshold; keeping percent stop")
            return False

    def _safe_sell_quantity(self, symbol: str, base_asset: str, wanted_qty: Decimal, market_type: str = MARKET_SPOT) -> Decimal:
        if market_type == MARKET_FUTURES:
            return round_down_to_step(wanted_qty, symbol_step_size(self.futures_client.exchange_info(), symbol))
        account = self.spot_client.account() if not self.config.dry_run else {"balances": []}
        free_balance = wanted_qty
        if not self.config.dry_run:
            for item in account.get("balances", []):
                if item.get("asset") == base_asset:
                    free_balance = Decimal(str(item.get("free", "0")))
                    break
        qty = min(wanted_qty, free_balance)
        step_size = symbol_step_size(self.spot_client.exchange_info(), symbol)
        return round_down_to_step(qty, step_size)

    def _buy_order_error(self, symbol: str, quote_amount: Decimal, price: Decimal, market_type: str = MARKET_SPOT) -> str | None:
        rules = symbol_order_rules(self._market_client(market_type).exchange_info(), symbol)
        gross_quote = quote_amount * self.config.leverage_multiplier if market_type == MARKET_FUTURES else quote_amount
        if rules.min_notional > 0 and gross_quote < rules.min_notional:
            return f"quote amount {gross_quote} is below min notional {rules.min_notional}"
        estimate_price = price * (Decimal("1") + self.config.slippage_pct / Decimal("100")) if self.config.dry_run else price
        estimate_quote = max(Decimal("0"), gross_quote - (gross_quote * self.config.fee_rate_pct / Decimal("100")))
        estimated_qty = estimate_quote / estimate_price if estimate_price > 0 else Decimal("0")
        if rules.min_qty > 0 and estimated_qty < rules.min_qty:
            return f"estimated quantity {estimated_qty} is below min quantity {rules.min_qty}"
        return None

    def _futures_order_quantity(self, symbol: str, margin_quote: Decimal, price: Decimal) -> Decimal:
        rules = symbol_order_rules(self.futures_client.exchange_info(), symbol)
        notional = margin_quote * self.config.leverage_multiplier
        quantity = notional / price if price > 0 else Decimal("0")
        return round_down_to_step(quantity, rules.step_size)

    def _prepare_futures_symbol_for_live(self, symbol: str) -> None:
        margin_type = self.config.futures_margin_type.upper()
        try:
            self.futures_client.change_margin_type(symbol, margin_type)
        except BinanceAPIError as exc:
            text = str(exc).lower()
            if "-4046" not in text and "no need to change margin type" not in text:
                raise
            LOGGER.info("futures margin type already set for %s: %s", symbol, margin_type)
        self.futures_client.change_leverage(symbol, self.config.leverage_multiplier)

    def _sell_order_error(self, symbol: str, quantity: Decimal, price: Decimal, market_type: str = MARKET_SPOT) -> str | None:
        rules = symbol_order_rules(self._market_client(market_type).exchange_info(), symbol)
        if rules.min_qty > 0 and quantity < rules.min_qty:
            return f"quantity {quantity} is below min quantity {rules.min_qty}"
        notional = quantity * price
        if rules.min_notional > 0 and notional < rules.min_notional:
            return f"notional {notional} is below min notional {rules.min_notional}"
        return None

    def _dry_run_buy_fill(self, market_price: Decimal, market_type: str = MARKET_FUTURES) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        fill_price = market_price * (Decimal("1") + self.config.slippage_pct / Decimal("100"))
        margin_quote = self.config.order_quote_amount
        gross_quote = margin_quote * self.config.leverage_multiplier if market_type == MARKET_FUTURES else margin_quote
        fee_amount = gross_quote * self.config.fee_rate_pct / Decimal("100")
        net_quote = max(Decimal("0"), gross_quote - fee_amount)
        quantity = net_quote / fill_price if fill_price > 0 else Decimal("0")
        return fill_price, quantity, fee_amount, margin_quote

    def _dry_run_sell_fill(self, market_price: Decimal, quantity: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        fill_price = market_price * (Decimal("1") - self.config.slippage_pct / Decimal("100"))
        gross_quote = quantity * fill_price
        fee_amount = gross_quote * self.config.fee_rate_pct / Decimal("100")
        quote_received = max(Decimal("0"), gross_quote - fee_amount)
        return fill_price, fee_amount, quote_received

    def _append_trade(
        self,
        action: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        raw_order: dict[str, Any] | None,
        fee_amount: Decimal | None = None,
        quote_amount: Decimal | None = None,
    ) -> None:
        position = next((item for item in self._active_positions() if item.symbol == symbol), None)
        market_type = self._position_market_type(position) if position else MARKET_SPOT
        position_mode = position.position_mode if position else ""
        record = {
            "ts": utc_now(),
            "action": action,
            "symbol": symbol,
            "market_type": market_type,
            "position_mode": position_mode,
            "quantity": format_decimal(quantity),
            "price": format_decimal(price),
            "dry_run": self.config.dry_run,
            "order": raw_order,
        }
        if fee_amount is not None:
            record["fee_amount"] = format_decimal(fee_amount)
            record["fee_asset"] = self.config.quote_asset
        if quote_amount is not None:
            record["quote_amount"] = format_decimal(quote_amount)
        record["event_uid"] = trade_event_uid(record)
        try:
            insert_trade_event(self.config.trade_journal_file, record)
        except Exception as exc:
            LOGGER.warning("trade journal write failed: %s", exc)
        self.state.trade_log.append(record)
        self.state.trade_log = self.state.trade_log[-100:]

    def _touch_state(self) -> None:
        self.state.updated_at = utc_now()
        save_state(self.config.state_file, self.state)


def build_retry_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST", "DELETE"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def load_state(path: str) -> BotState:
    if not os.path.exists(path):
        return BotState(updated_at=utc_now())
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    position = raw.get("position")
    positions_raw = raw.get("positions") or []
    positions = [PositionState(**item) for item in positions_raw if item]
    if position and not positions:
        positions = [PositionState(**position)]
    pending_raw = raw.get("pending_order")
    pending_order = PendingOrderState(**pending_raw) if isinstance(pending_raw, dict) and pending_raw else None
    protection_orders = [
        ProtectionOrderState(**item)
        for item in raw.get("protection_orders", [])
        if isinstance(item, dict) and item
    ]
    return BotState(
        first_buy_done=bool(raw.get("first_buy_done", False)),
        completed_round_trips=int(raw.get("completed_round_trips", 0)),
        position=positions[0] if positions else None,
        positions=positions,
        updated_at=raw.get("updated_at", ""),
        trade_log=list(raw.get("trade_log", [])),
        pending_order=pending_order,
        protection_orders=protection_orders,
        last_safety_check=dict(raw.get("last_safety_check", {})),
        entry_confirmation=dict(raw.get("entry_confirmation", {})),
        square_confidence=dict(raw.get("square_confidence", {})),
        account_risk_snapshot=dict(raw.get("account_risk_snapshot", {})),
        square_seen_post_ids=list(raw.get("square_seen_post_ids", []))[-1500:],
        square_latest_post_time=str(raw.get("square_latest_post_time", "")),
        square_consecutive_failures=int(raw.get("square_consecutive_failures", 0)),
    )


def save_state(path: str, state: BotState) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(asdict(state), handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def dedupe_posts(posts: list[SquarePost], validate: bool = True) -> list[SquarePost]:
    seen: set[str] = set()
    result: list[SquarePost] = []
    for post in posts:
        if is_square_noise_post(post):
            continue
        if validate and not is_valid_square_post(post):
            continue
        key = f"id:{post.post_id}" if post.post_id else "text:" + clean_text(f"{post.title} {post.text}")[:300].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(post)
    return result


def is_candidate_square_api_url(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in ("square", "feed", "article", "content", "bapi"))


def block_square_heavy_resource(route: Any) -> None:
    if route.request.resource_type in {"image", "media", "font", "stylesheet"}:
        route.abort()
    else:
        route.continue_()


def extract_square_posts_from_api_payload(
    data: Any,
    source_url: str,
    extractor_mode: str = "network_api",
) -> list[SquarePost]:
    posts: list[SquarePost] = []
    objects = iter_json_objects(data) if isinstance(data, str) else walk_dicts(data)
    for obj in objects:
        post_id = first_string(obj, ("articleId", "postId", "id", "code", "article_id", "post_id"))
        title = first_string(obj, ("title", "subject", "headline"))
        text = first_string(obj, ("content", "text", "summary", "description", "body", "articleContent"))
        if not (title or text):
            continue
        cleaned_title = clean_text(title)
        cleaned_text = clean_text(text)
        if len(f"{cleaned_title} {cleaned_text}".strip()) < 20:
            continue
        traffic = sum_numeric_fields(
            obj,
            (
                "viewCount",
                "views",
                "readCount",
                "likeCount",
                "likes",
                "commentCount",
                "comments",
                "shareCount",
                "shares",
                "favoriteCount",
                "collectCount",
            ),
        )
        url = first_string(obj, ("url", "link", "shareUrl", "webLink")) or source_url
        created_at = first_string(obj, ("publishTime", "releaseTime", "createdAt", "createTime", "created_at"))
        author = first_string(obj, ("nickName", "nickname", "authorName", "username", "displayName"))
        posts.append(
            SquarePost(
                title=cleaned_title,
                text=cleaned_text,
                traffic_score=traffic,
                url=url,
                created_at=normalize_square_timestamp(created_at),
                post_id=post_id or None,
                author=author or None,
                extractor_mode=extractor_mode,
            )
        )
    return posts


def normalize_square_timestamp(value: Any) -> str | None:
    parsed = parse_square_timestamp(value)
    return parsed.isoformat() if parsed else str(value).strip() if value else None


def parse_square_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def latest_post_time_from_posts(posts: list[SquarePost]) -> str | None:
    parsed = [item for item in (parse_square_timestamp(post.created_at) for post in posts) if item]
    if not parsed:
        return None
    return max(parsed).isoformat()


def post_time_weight(created_at: str | None, half_life_minutes: float = 90.0) -> float:
    parsed = parse_square_timestamp(created_at)
    if not parsed:
        return 0.6
    age_minutes = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60)
    return max(0.1, math.exp(-age_minutes / half_life_minutes))


def post_signal_weight(post: SquarePost) -> float:
    traffic = max(1.0, float(post.traffic_score or 0))
    return traffic * post_time_weight(post.created_at)


def is_momentum_asset(asset: str, quote_asset: str = "USDT") -> bool:
    normalized = asset.upper()
    if normalized == quote_asset.upper():
        return False
    if normalized in EXCLUDED_MOMENTUM_ASSETS:
        return False
    if len(normalized) < 2:
        return False
    return True


def is_square_mention_asset(asset: str) -> bool:
    normalized = asset.upper()
    return is_momentum_asset(normalized) and normalized not in COMMON_FALSE_SYMBOLS


def volume_rank_score(quote_volume: Decimal, min_quote_volume: Decimal) -> Decimal:
    if min_quote_volume <= 0:
        return Decimal("0")
    return min(Decimal("80"), (quote_volume / min_quote_volume) * Decimal("8"))


def square_rank_score(mention_count: float, max_mentions: float) -> Decimal:
    if mention_count <= 0 or max_mentions <= 0:
        return Decimal("0")
    return (Decimal(str(mention_count)) / Decimal(str(max_mentions))) * Decimal("180")


def count_coin_mentions(posts: list[SquarePost], base_assets: set[str]) -> Counter[str]:
    valid_assets = {item for item in base_assets if is_square_mention_asset(item)}
    mentions: Counter[str] = Counter()
    weighted_posts = sorted(posts, key=post_signal_weight, reverse=True)
    for rank, post in enumerate(weighted_posts, start=1):
        text = f"{post.title} {post.text}".upper()
        rank_weight = max(1, len(weighted_posts) - rank + 1)
        recency_weight = post_time_weight(post.created_at)
        for asset in valid_assets:
            if asset in PREFIX_REQUIRED_SYMBOLS or (len(asset) <= 3 and asset not in STRONG_BARE_SYMBOLS):
                pattern = rf"(?<![A-Z0-9])(?:\$|#){re.escape(asset)}(?![A-Z0-9])"
            else:
                pattern = rf"(?<![A-Z0-9])(?:\$|#)?{re.escape(asset)}(?![A-Z0-9])"
            count = len(re.findall(pattern, text))
            if count:
                mentions[asset] += count * rank_weight * recency_weight
    return mentions


def extract_square_symbols(text: str) -> Counter[str]:
    upper = text.upper()
    symbols: Counter[str] = Counter()
    for asset in re.findall(r"(?<![A-Z0-9])(?:\$|#)([A-Z0-9]{2,12})(?![A-Z0-9])", upper):
        if is_square_mention_asset(asset):
            symbols[asset] += 1
    for asset in STRONG_BARE_SYMBOLS:
        if is_square_mention_asset(asset):
            count = len(re.findall(rf"(?<![A-Z0-9]){re.escape(asset)}(?![A-Z0-9])", upper))
            if count:
                symbols[asset] += count
    return symbols


def score_diagnostic_posts(posts: list[SquarePost], limit: int) -> list[dict[str, Any]]:
    scored = [score_diagnostic_post(post) for post in posts]
    scored.sort(key=lambda item: (item["score"], item.get("traffic_score") or 0), reverse=True)
    return scored[:limit]


def score_diagnostic_post(post: SquarePost) -> dict[str, Any]:
    text = clean_text(f"{post.title} {post.text}")
    symbols = extract_square_symbols(text)
    symbol_total = sum(symbols.values())
    has_symbol = contains_market_symbol_hint(text)
    has_context = contains_trading_context(text)
    long_context = is_long_only_context(text)
    valid_post = is_valid_square_post(post)
    traffic = float(post.traffic_score or 0)

    symbol_score = min(35.0, len(symbols) * 8.0 + symbol_total * 3.0)
    context_score = 25.0 if has_context else 0.0
    long_score = 20.0 if long_context else -25.0
    if traffic > 10000:
        traffic_score = min(20.0, traffic / 50000.0)
    else:
        traffic_score = min(10.0, traffic / 120.0)
    length_score = min(10.0, len(text) / 120.0)
    time_weight = post_time_weight(post.created_at)
    time_score = round(time_weight * 10.0, 1)
    total_score = max(0.0, symbol_score + context_score + long_score + traffic_score + length_score + time_score)

    filter_reasons: list[str] = []
    if is_square_noise_post(post):
        filter_reasons.append("噪音文本或内容过短")
    if not has_symbol:
        filter_reasons.append("未识别币种符号")
    if not has_context:
        filter_reasons.append("缺少交易语境")
    if not long_context:
        filter_reasons.append("含看空或做空语境")
    if not filter_reasons:
        filter_reasons.append("通过有效帖过滤")

    payload = post_to_dict(post)
    payload.update(
        {
            "score": round(total_score, 1),
            "valid_trading_post": valid_post,
            "filter_reasons": filter_reasons,
            "symbols": [{"asset": asset, "mentions": count} for asset, count in symbols.most_common(8)],
            "score_basis": {
                "symbol_score": round(symbol_score, 1),
                "context_score": round(context_score, 1),
                "long_context_score": round(long_score, 1),
                "traffic_score": round(traffic_score, 1),
                "length_score": round(length_score, 1),
                "time_decay_score": time_score,
                "time_weight": round(time_weight, 3),
                "symbol_mentions": symbol_total,
                "has_trading_context": has_context,
                "long_only_context": long_context,
                "text_length": len(text),
            },
        }
    )
    return payload


def iter_json_objects(blob: str) -> Iterable[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", blob):
        try:
            obj, _ = decoder.raw_decode(blob[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield from walk_dicts(obj)


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_string(obj: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def sum_numeric_fields(obj: dict[str, Any], keys: tuple[str, ...]) -> float:
    total = 0.0
    for key in keys:
        value = obj.get(key)
        if isinstance(value, (int, float)):
            total += float(value)
        elif isinstance(value, str) and value.replace(".", "", 1).isdigit():
            total += float(value)
    return total


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def contains_market_symbol_hint(text: str) -> bool:
    upper = text.upper()
    if re.search(r"(?<![A-Z0-9])(?:\$|#)[A-Z0-9]{2,12}(?![A-Z0-9])", upper):
        return True
    strong = "|".join(sorted(STRONG_BARE_SYMBOLS))
    return bool(re.search(rf"(?<![A-Z0-9])(?:{strong})(?![A-Z0-9])", upper))


def contains_trading_context(text: str) -> bool:
    return bool(MARKET_CONTEXT_PATTERN.search(text))


def is_long_only_context(text: str) -> bool:
    return not any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in LONG_ONLY_REJECT_PATTERNS)


def is_square_noise_post(post: SquarePost) -> bool:
    text = clean_text(f"{post.title} {post.text}")
    if len(text) < 40:
        return True
    lower = text.lower()
    return lower.startswith("ba-") or "ba-table" in lower or "ba-title" in lower


def is_valid_square_post(post: SquarePost) -> bool:
    if is_square_noise_post(post):
        return False
    text = clean_text(f"{post.title} {post.text}")
    return contains_market_symbol_hint(text) and contains_trading_context(text) and is_long_only_context(text)


def is_square_boundary_line(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "like",
        "comment",
        "comments",
        "share",
        "follow",
        "following",
        "read more",
        "translate",
    }


def post_to_dict(post: SquarePost) -> dict[str, Any]:
    return {
        "title": post.title,
        "text": post.text[:500],
        "traffic_score": post.traffic_score,
        "url": post.url,
        "created_at": post.created_at,
        "post_id": post.post_id,
        "author": post.author,
        "source": post.source,
        "extractor_mode": post.extractor_mode,
    }


def average_fill_price(order: dict[str, Any]) -> Decimal | None:
    fills = order.get("fills") or []
    total_qty = Decimal("0")
    total_quote = Decimal("0")
    for fill in fills:
        qty = Decimal(str(fill.get("qty", "0")))
        price = Decimal(str(fill.get("price", "0")))
        total_qty += qty
        total_quote += qty * price
    if total_qty > 0:
        return total_quote / total_qty

    executed = Decimal(str(order.get("executedQty", "0")))
    cummulative_quote = Decimal(str(order.get("cummulativeQuoteQty", "0")))
    if executed > 0 and cummulative_quote > 0:
        return cummulative_quote / executed
    avg_price = Decimal(str(order.get("avgPrice", "0")))
    if avg_price > 0:
        return avg_price
    return None


def kline_confirmation_snapshot(rows: list[Any]) -> dict[str, Any]:
    closes: list[Decimal] = []
    quote_volumes: list[Decimal] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        closes.append(Decimal(str(row[4])))
        quote_volumes.append(Decimal(str(row[7])))
    if len(closes) < 3:
        return {
            "roc_pct": Decimal("-999"),
            "above_ema9": False,
            "volume_expanding": False,
            "close": Decimal("0"),
            "ema9": Decimal("0"),
            "reason": "not enough kline data",
        }
    first_close = closes[0]
    last_close = closes[-1]
    roc_pct = (last_close - first_close) / first_close * Decimal("100") if first_close > 0 else Decimal("-999")
    ema9 = ema(closes[-9:] if len(closes) >= 9 else closes)
    recent_volume = sum(quote_volumes[-3:], Decimal("0")) / Decimal(min(3, len(quote_volumes)))
    previous_slice = quote_volumes[-9:-3] if len(quote_volumes) >= 9 else quote_volumes[:-3]
    previous_volume = (
        sum(previous_slice, Decimal("0")) / Decimal(len(previous_slice))
        if previous_slice
        else Decimal("0")
    )
    return {
        "roc_pct": roc_pct,
        "above_ema9": last_close >= ema9,
        "volume_expanding": previous_volume <= 0 or recent_volume >= previous_volume,
        "close": last_close,
        "ema9": ema9,
        "recent_quote_volume": recent_volume,
        "previous_quote_volume": previous_volume,
    }


def ema(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    multiplier = Decimal("2") / Decimal(len(values) + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def orderbook_liquidity_snapshot(depth: dict[str, Any]) -> dict[str, Any]:
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    if not bids or not asks:
        return {"spread_bps": None, "bid_depth_usdt": None, "ask_depth_usdt": None}
    best_bid = Decimal(str(bids[0][0]))
    best_ask = Decimal(str(asks[0][0]))
    mid = (best_bid + best_ask) / Decimal("2")
    spread_bps = (best_ask - best_bid) / mid * Decimal("10000") if mid > 0 else Decimal("999999")
    bid_depth = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty, *_ in bids)
    ask_depth = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty, *_ in asks)
    return {
        "spread_bps": spread_bps,
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


def square_confidence_snapshot(posts: list[SquarePost], diagnostics: dict[str, Any], feed_state: SquareFeedState) -> dict[str, Any]:
    post_count = len(posts)
    score = Decimal("0")
    reasons: list[str] = []
    score += min(Decimal("25"), Decimal(post_count) * Decimal("2.5"))
    if post_count == 0:
        reasons.append("no Square posts extracted")
    structured_count = sum(1 for post in posts if post.post_id or post.author or post.created_at)
    structured_ratio = Decimal(structured_count) / Decimal(post_count) if post_count else Decimal("0")
    score += structured_ratio * Decimal("20")
    if structured_ratio < Decimal("0.25"):
        reasons.append("low structured post ratio")
    fresh_count = sum(1 for post in posts if parse_square_timestamp(post.created_at))
    fresh_ratio = Decimal(fresh_count) / Decimal(post_count) if post_count else Decimal("0")
    score += fresh_ratio * Decimal("15")
    extractor_mode = str(diagnostics.get("extractor_mode") or "")
    if extractor_mode in {"browser_network", "browser_rendered", "network_api"} or diagnostics.get("api_post_count"):
        score += Decimal("20")
    elif extractor_mode and extractor_mode != "none":
        score += Decimal("10")
    else:
        reasons.append("extractor mode unavailable")
    if int(diagnostics.get("new_post_count") or 0) > 0 or fresh_count > 0:
        score += Decimal("10")
    if feed_state.consecutive_failures:
        penalty = min(Decimal("20"), Decimal(feed_state.consecutive_failures) * Decimal("5"))
        score -= penalty
        reasons.append(f"Square fetch failures={feed_state.consecutive_failures}")
    score = max(Decimal("0"), min(Decimal("100"), score))
    if not reasons:
        reasons.append("Square data confidence is acceptable")
    return stringify_decimals(
        {
            "score": score,
            "post_count": post_count,
            "structured_count": structured_count,
            "fresh_count": fresh_count,
            "extractor_mode": extractor_mode or "unknown",
            "consecutive_failures": feed_state.consecutive_failures,
            "reasons": reasons,
            "checked_at": utc_now(),
        }
    )


def current_loss_streak(trade_log: list[dict[str, Any]]) -> int:
    completed: list[Decimal] = []
    open_trades: dict[str, list[dict[str, Decimal]]] = {}
    for item in trade_log:
        action = str(item.get("action", ""))
        symbol = str(item.get("symbol", ""))
        qty = decimal_from_any(item.get("quantity"))
        price = decimal_from_any(item.get("price"))
        if not symbol or qty is None or price is None:
            continue
        amount = decimal_from_any(item.get("quote_amount")) or qty * price
        if "BUY" in action:
            open_trades.setdefault(symbol, []).append({"qty": qty, "amount": amount})
        elif "SELL" in action and open_trades.get(symbol):
            queue = open_trades[symbol]
            remaining_sell_qty = qty
            pnl = Decimal("0")
            while remaining_sell_qty > 0 and queue:
                open_trade = queue[0]
                open_qty = open_trade["qty"]
                closed_qty = min(open_qty, remaining_sell_qty)
                ratio = closed_qty / qty if qty > 0 else Decimal("1")
                open_ratio = closed_qty / open_qty if open_qty > 0 else Decimal("1")
                exit_amount = amount * ratio
                entry_amount = open_trade["amount"] * open_ratio
                pnl += exit_amount - entry_amount
                open_trade["qty"] = open_qty - closed_qty
                open_trade["amount"] = open_trade["amount"] - entry_amount
                remaining_sell_qty -= closed_qty
                if open_trade["qty"] <= 0:
                    queue.pop(0)
            completed.append(pnl)
    streak = 0
    for pnl in reversed(completed):
        if pnl < 0:
            streak += 1
            continue
        break
    return streak


def trade_journal_enabled(path: str) -> bool:
    return bool(str(path or "").strip())


def trade_journal_path(path: str) -> Path:
    return Path(str(path or DEFAULT_TRADE_JOURNAL_FILE)).expanduser()


def trade_event_uid(record: dict[str, Any]) -> str:
    raw = "|".join(
        str(record.get(key, ""))
        for key in ("ts", "action", "symbol", "quantity", "price", "quote_amount", "dry_run")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def ensure_trade_journal(path: str) -> None:
    if not trade_journal_enabled(path):
        return
    db_path = trade_journal_path(path)
    if db_path.parent and str(db_path.parent) not in {"", "."}:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uid TEXT NOT NULL UNIQUE,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL DEFAULT '',
                position_mode TEXT NOT NULL DEFAULT '',
                dry_run INTEGER NOT NULL DEFAULT 1,
                quantity TEXT NOT NULL DEFAULT '0',
                price TEXT NOT NULL DEFAULT '0',
                fee_amount TEXT NOT NULL DEFAULT '',
                fee_asset TEXT NOT NULL DEFAULT '',
                quote_amount TEXT NOT NULL DEFAULT '',
                order_json TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_round_trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_event_id INTEGER NOT NULL,
                exit_event_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL DEFAULT '',
                position_mode TEXT NOT NULL DEFAULT '',
                dry_run INTEGER NOT NULL DEFAULT 1,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                exit_price TEXT NOT NULL,
                entry_amount TEXT NOT NULL,
                exit_amount TEXT NOT NULL,
                fee_amount TEXT NOT NULL DEFAULT '',
                pnl TEXT NOT NULL,
                return_pct TEXT NOT NULL,
                exit_reason TEXT NOT NULL DEFAULT '',
                duration_seconds INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_ts ON trade_events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_symbol ON trade_events(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_round_trips_exit_time ON trade_round_trips(exit_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_round_trips_symbol ON trade_round_trips(symbol)")
        conn.commit()


def insert_trade_event(path: str, record: dict[str, Any], rebuild: bool = True) -> None:
    if not trade_journal_enabled(path):
        return
    ensure_trade_journal(path)
    db_path = trade_journal_path(path)
    event_uid = str(record.get("event_uid") or trade_event_uid(record))
    record["event_uid"] = event_uid
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO trade_events (
                event_uid, ts, action, symbol, market_type, position_mode, dry_run,
                quantity, price, fee_amount, fee_asset, quote_amount, order_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_uid,
                str(record.get("ts") or utc_now()),
                str(record.get("action") or ""),
                str(record.get("symbol") or ""),
                str(record.get("market_type") or ""),
                str(record.get("position_mode") or ""),
                1 if bool(record.get("dry_run", True)) else 0,
                str(record.get("quantity") or "0"),
                str(record.get("price") or "0"),
                str(record.get("fee_amount") or ""),
                str(record.get("fee_asset") or ""),
                str(record.get("quote_amount") or ""),
                json.dumps(record.get("order") or {}, ensure_ascii=False, default=str),
                utc_now(),
            ),
        )
        conn.commit()
    if rebuild:
        rebuild_trade_round_trips(path)


def migrate_trade_log_to_journal(path: str, trade_log: list[dict[str, Any]]) -> None:
    if not trade_journal_enabled(path) or not trade_log:
        return
    ensure_trade_journal(path)
    for item in trade_log:
        if isinstance(item, dict):
            insert_trade_event(path, dict(item), rebuild=False)
    rebuild_trade_round_trips(path)
    db_path = trade_journal_path(path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO journal_meta (key, value) VALUES ('state_trade_log_migrated', '1')"
        )
        conn.commit()


def rebuild_trade_round_trips(path: str) -> None:
    if not trade_journal_enabled(path):
        return
    ensure_trade_journal(path)
    db_path = trade_journal_path(path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        events = [dict(row) for row in conn.execute("SELECT * FROM trade_events ORDER BY ts ASC, id ASC")]
        completed = build_round_trips_from_events(events)
        conn.execute("DELETE FROM trade_round_trips")
        conn.executemany(
            """
            INSERT INTO trade_round_trips (
                entry_event_id, exit_event_id, symbol, market_type, position_mode, dry_run,
                entry_time, exit_time, quantity, entry_price, exit_price, entry_amount,
                exit_amount, fee_amount, pnl, return_pct, exit_reason, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["entry_event_id"],
                    item["exit_event_id"],
                    item["symbol"],
                    item["market_type"],
                    item["position_mode"],
                    1 if item["dry_run"] else 0,
                    item["entry_time"],
                    item["exit_time"],
                    item["quantity"],
                    item["entry_price"],
                    item["exit_price"],
                    item["entry_amount"],
                    item["exit_amount"],
                    item["fee_amount"],
                    item["pnl"],
                    item["return_pct"],
                    item["exit_reason"],
                    item["duration_seconds"],
                )
                for item in completed
            ],
        )
        conn.commit()


def build_round_trips_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    open_trades: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        action = str(event.get("action") or "")
        symbol = str(event.get("symbol") or "")
        qty = decimal_from_any(event.get("quantity"))
        price = decimal_from_any(event.get("price"))
        if not symbol or qty is None or price is None or qty <= 0:
            continue
        amount = decimal_from_any(event.get("quote_amount")) or qty * price
        if "BUY" in action:
            open_trades.setdefault(symbol, []).append(
                {
                    "event_id": int(event.get("id") or 0),
                    "symbol": symbol,
                    "qty": qty,
                    "amount": amount,
                    "price": price,
                    "ts": str(event.get("ts") or ""),
                    "dry_run": bool(event.get("dry_run", 1)),
                    "market_type": str(event.get("market_type") or ""),
                    "position_mode": str(event.get("position_mode") or ""),
                    "fee_amount": decimal_from_any(event.get("fee_amount")) or Decimal("0"),
                }
            )
            continue
        if "SELL" not in action or not open_trades.get(symbol):
            continue
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
            fee_amount = (decimal_from_any(event.get("fee_amount")) or Decimal("0")) * ratio
            pnl = exit_amount - entry_amount
            return_pct = pnl / entry_amount * Decimal("100") if entry_amount > 0 else Decimal("0")
            entry_time = str(open_trade.get("ts") or "")
            exit_time = str(event.get("ts") or "")
            opened_at = parse_timestamp(entry_time)
            closed_at = parse_timestamp(exit_time)
            duration = int((closed_at - opened_at).total_seconds()) if opened_at and closed_at else 0
            completed.append(
                {
                    "entry_event_id": int(open_trade["event_id"]),
                    "exit_event_id": int(event.get("id") or 0),
                    "symbol": symbol,
                    "market_type": open_trade.get("market_type") or str(event.get("market_type") or ""),
                    "position_mode": open_trade.get("position_mode") or str(event.get("position_mode") or ""),
                    "dry_run": bool(open_trade.get("dry_run", True)),
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "quantity": format_decimal(closed_qty),
                    "entry_price": format_decimal(open_trade["price"]),
                    "exit_price": format_decimal(price),
                    "entry_amount": format_decimal(entry_amount),
                    "exit_amount": format_decimal(exit_amount),
                    "fee_amount": format_decimal(fee_amount),
                    "pnl": format_decimal(pnl),
                    "return_pct": format_decimal(return_pct),
                    "exit_reason": action,
                    "duration_seconds": duration,
                }
            )
            open_trade["qty"] = open_qty - closed_qty
            open_trade["amount"] = open_trade["amount"] - entry_amount
            remaining_sell_qty -= closed_qty
            if open_trade["qty"] <= 0:
                queue.pop(0)
    return completed


def query_trade_journal(path: str, view: str = "round_trips", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if not trade_journal_enabled(path):
        return {"view": view, "items": [], "total": 0, "limit": limit, "offset": offset}
    ensure_trade_journal(path)
    db_path = trade_journal_path(path)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    normalized_view = "events" if view == "events" else "round_trips"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if normalized_view == "events":
            total = int(conn.execute("SELECT COUNT(*) FROM trade_events").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM trade_events ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            total = int(conn.execute("SELECT COUNT(*) FROM trade_round_trips").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM trade_round_trips ORDER BY exit_time DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return {
        "view": normalized_view,
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "db_path": str(db_path),
    }


def trade_journal_stats(path: str, quote_asset: str) -> dict[str, Any] | None:
    if not trade_journal_enabled(path):
        return None
    ensure_trade_journal(path)
    db_path = trade_journal_path(path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM trade_round_trips ORDER BY exit_time ASC, id ASC")]
        event_count = int(conn.execute("SELECT COUNT(*) FROM trade_events").fetchone()[0])
    if not rows:
        return None
    pnls = [decimal_from_any(item.get("pnl")) or Decimal("0") for item in rows]
    returns = [decimal_from_any(item.get("return_pct")) or Decimal("0") for item in rows]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    total = len(rows)
    total_pnl = sum(pnls, Decimal("0"))
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum((-pnl for pnl in losses), Decimal("0"))
    avg_pnl = total_pnl / total if total else Decimal("0")
    avg_return_pct = sum(returns, Decimal("0")) / total if total else Decimal("0")
    win_rate = Decimal(len(wins)) / Decimal(total) * Decimal("100") if total else Decimal("0")
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    current_streak = 0
    current_streak_type = ""
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if pnl > 0:
            current_streak = current_streak + 1 if current_streak_type == "win" else 1
            current_streak_type = "win"
        elif pnl < 0:
            current_streak = current_streak + 1 if current_streak_type == "loss" else 1
            current_streak_type = "loss"
    return {
        "quote_asset": quote_asset,
        "completed_trades": total,
        "trade_count": total,
        "event_count": event_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_pnl": avg_pnl,
        "avg_return_pct": avg_return_pct,
        "profit_factor": profit_factor,
        "best_trade": max(pnls, default=Decimal("0")),
        "worst_trade": min(pnls, default=Decimal("0")),
        "max_drawdown": max_drawdown,
        "current_streak": current_streak,
        "current_streak_type": current_streak_type,
        "journal_enabled": True,
        "journal_file": str(db_path),
    }


def symbol_order_rules(exchange_info: dict[str, Any], symbol: str) -> OrderRules:
    rules = OrderRules(symbol=symbol)
    for item in exchange_info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        for filt in item.get("filters", []):
            filter_type = filt.get("filterType")
            if filter_type in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
                step_size = Decimal(str(filt.get("stepSize", "0")))
                min_qty = Decimal(str(filt.get("minQty", "0")))
                if step_size > 0:
                    rules.step_size = step_size
                if min_qty > 0:
                    rules.min_qty = max(rules.min_qty, min_qty)
            elif filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
                min_notional = Decimal(str(filt.get("minNotional", filt.get("notional", "0"))))
                if min_notional > 0:
                    rules.min_notional = max(rules.min_notional, min_notional)
            elif filter_type == "PRICE_FILTER":
                tick_size = Decimal(str(filt.get("tickSize", "0")))
                if tick_size > 0:
                    rules.tick_size = tick_size
        return rules
    return rules


def symbol_step_size(exchange_info: dict[str, Any], symbol: str) -> Decimal:
    return symbol_order_rules(exchange_info, symbol).step_size


def round_down_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return quantity
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def build_client_order_id(action: str, symbol: str, timestamp: str | None = None) -> str:
    compact_ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    normalized_action = re.sub(r"[^a-z0-9]", "", action.lower())[:6] or "order"
    normalized_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper())[:11]
    seed = f"{normalized_action}|{normalized_symbol}|{compact_ts}|{time.perf_counter_ns()}"
    short_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    client_id = f"bm-{normalized_action}-{normalized_symbol}-{compact_ts}-{short_hash}"
    return client_id[:36]


def format_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def stringify_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, dict):
        return {key: stringify_decimals(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify_decimals(item) for item in value]
    if isinstance(value, tuple):
        return [stringify_decimals(item) for item in value]
    return value


def compact_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def signal_post_summary(posts: list[SquarePost], limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(posts, key=post_signal_weight, reverse=True)
    summaries: list[dict[str, Any]] = []
    for post in ranked[:limit]:
        summaries.append(
            {
                "title": compact_text(post.title, 120),
                "text": compact_text(post.text, 220),
                "traffic_score": post.traffic_score,
                "url": post.url or "",
                "created_at": post.created_at or "",
                "post_id": post.post_id or "",
                "author": compact_text(post.author, 80),
                "source": post.source,
                "extractor_mode": post.extractor_mode,
            }
        )
    return stringify_decimals(summaries)


def signal_candidate_row(candidate: TradeCandidate) -> dict[str, Any]:
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
            "market_type": candidate.market_type,
        }
    )


def build_signal_record(
    config: BotConfig,
    source: str,
    posts: list[SquarePost],
    candidates: list[TradeCandidate],
    candidate: TradeCandidate | None,
    entry_confirmation: dict[str, Any] | None,
    square_confidence: dict[str, Any] | None,
    account_risk_snapshot: dict[str, Any] | None,
    final_action: str,
    note: str = "",
) -> dict[str, Any]:
    return stringify_decimals(
        {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "source": source,
            "dry_run": config.dry_run,
            "quote_asset": config.quote_asset,
            "fixed_order_quote_usdt": config.order_quote_amount,
            "min_square_confidence_score": config.min_square_confidence_score,
            "square_confidence": square_confidence or {},
            "hot_posts": signal_post_summary(posts),
            "candidates": [signal_candidate_row(item) for item in candidates[: config.top_coin_limit]],
            "candidate": signal_candidate_row(candidate) if candidate else None,
            "entry_confirmation": entry_confirmation or {},
            "checks": dict((entry_confirmation or {}).get("checks") or {}),
            "account_risk": account_risk_snapshot or {},
            "final_action": final_action,
            "entered": final_action == "entered",
            "note": compact_text(note, 300),
        }
    )


def append_signal_record(path: str, record: dict[str, Any]) -> None:
    target = Path(path)
    if target.parent and str(target.parent) not in {"", "."}:
        target.parent.mkdir(parents=True, exist_ok=True)
    safe_record = redact_signal_record(record)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(safe_record, ensure_ascii=False, separators=(",", ":")) + "\n")


def redact_signal_record(record: dict[str, Any]) -> dict[str, Any]:
    blocked = {
        "api_key",
        "api_secret",
        "telegram_bot_token",
        "telegram_chat_id",
        "secret",
        "balances",
        "account_balances",
    }

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key not in blocked}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(record)


def read_signal_records(path: str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                LOGGER.warning("invalid signal record JSONL line skipped")
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return records


def write_signal_records(path: str, records: list[dict[str, Any]]) -> None:
    target = Path(path)
    if target.parent and str(target.parent) not in {"", "."}:
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(redact_signal_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")


def signal_record_stats(path: str) -> dict[str, Any]:
    records = read_signal_records(path)
    last_record_at = records[-1].get("recorded_at", "") if records else ""
    future_return_records = sum(1 for record in records if isinstance(record.get("future_returns"), dict) and record.get("future_returns"))
    decision_groups: dict[str, int] = {}
    for record in records:
        group = signal_record_decision_group(record)
        decision_groups[group] = decision_groups.get(group, 0) + 1
    return {
        "record_file": path,
        "record_count": len(records),
        "entered_count": sum(1 for item in records if item.get("entered") or item.get("final_action") == "entered"),
        "skipped_count": sum(1 for item in records if not (item.get("entered") or item.get("final_action") == "entered")),
        "last_record_at": last_record_at,
        "future_returns_count": future_return_records,
        "decision_groups": decision_groups,
    }


def signal_record_decision_group(record: dict[str, Any]) -> str:
    if record.get("entered") or record.get("final_action") == "entered":
        return "entered"
    square_score = decimal_from_any((record.get("square_confidence") or {}).get("score"))
    threshold = decimal_from_any(record.get("min_square_confidence_score")) or Decimal("35")
    if square_score is not None and square_score < threshold:
        return "square_low_confidence"
    checks = record.get("checks") or (record.get("entry_confirmation") or {}).get("checks") or {}
    if isinstance(checks, dict):
        if isinstance(checks.get("kline"), dict) and checks["kline"].get("passed") is False:
            return "kline_rejected"
        if isinstance(checks.get("liquidity"), dict) and checks["liquidity"].get("passed") is False:
            return "orderbook_rejected"
    account_risk = record.get("account_risk") or {}
    reason = str((record.get("entry_confirmation") or {}).get("reason") or record.get("note") or "").lower()
    if (
        isinstance(account_risk, dict)
        and account_risk.get("entry_blocked")
        or "account risk" in reason
        or "exposure" in reason
        or "drawdown" in reason
        or "consecutive losses" in reason
    ):
        return "account_risk_rejected"
    if (record.get("entry_confirmation") or {}).get("passed") is False:
        return "entry_rejected"
    return "skipped_other"


FUTURE_RETURN_INTERVALS: tuple[tuple[str, str, int], ...] = (
    ("5m", "5m", 5),
    ("15m", "15m", 15),
    ("1h", "1h", 60),
    ("4h", "4h", 240),
)


def update_signal_record_future_returns(
    config: BotConfig,
    record_file: str | None = None,
    client: BinanceSpotClient | None = None,
) -> dict[str, Any]:
    path = record_file or config.signal_record_file
    records = read_signal_records(path)
    if not records:
        return {"record_file": path, "record_count": 0, "updated_count": 0}
    market_client = client or BinanceSpotClient(config)
    updated = 0
    for record in records:
        if update_record_future_returns(record, market_client):
            updated += 1
    if updated:
        write_signal_records(path, records)
    result = signal_record_stats(path)
    result["updated_count"] = updated
    return result


def update_record_future_returns(record: dict[str, Any], client: BinanceSpotClient) -> bool:
    candidate = record.get("candidate") or {}
    symbol = str(candidate.get("symbol") or "")
    entry_price = decimal_from_any(candidate.get("last_price"))
    recorded_at = parse_timestamp(record.get("recorded_at") or record.get("checked_at"))
    if not symbol or not entry_price or entry_price <= 0 or recorded_at is None:
        return False
    returns = dict(record.get("future_returns") or {})
    changed = False
    for key, interval, minutes in FUTURE_RETURN_INTERVALS:
        if key in returns:
            continue
        target_ms = int((recorded_at + timedelta(minutes=minutes)).timestamp() * 1000)
        try:
            rows = client.klines(symbol, interval, 1, start_time=target_ms)
        except Exception as exc:
            returns[key] = {"error": str(exc)}
            changed = True
            continue
        if not rows:
            continue
        close_price = decimal_from_any(rows[0][4] if isinstance(rows[0], list) and len(rows[0]) > 4 else None)
        if close_price is None or close_price <= 0:
            continue
        returns[key] = stringify_decimals(
            {
                "close_price": close_price,
                "return_pct": (close_price - entry_price) / entry_price * Decimal("100"),
                "observed_at": datetime.fromtimestamp(int(rows[0][0]) / 1000, tz=timezone.utc).isoformat()
                if isinstance(rows[0], list) and rows[0]
                else utc_now(),
            }
        )
        changed = True
    if changed:
        record["future_returns"] = returns
        record["future_returns_updated_at"] = utc_now()
    return changed


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def account_safety_snapshot(client: BinanceSpotClient, config: BotConfig) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "api_key_loaded": bool(config.api_key),
        "api_secret_loaded": bool(config.api_secret),
        "api_key_suffix": config.api_key[-4:] if config.api_key else "",
        "manual_withdraw_permission_check_required": True,
        "manual_ip_whitelist_check_required": True,
        "checked_at": utc_now(),
    }
    if not config.api_key or not config.api_secret:
        snapshot["error"] = "API key/secret not loaded"
        return snapshot
    try:
        account = client.account()
    except Exception as exc:
        snapshot["error"] = str(exc)
        return snapshot
    permissions = account.get("permissions") or []
    can_trade = account.get("canTrade")
    can_withdraw = account.get("canWithdraw")
    snapshot.update(
        {
            "can_trade": bool(can_trade),
            "can_withdraw": bool(can_withdraw),
            "can_deposit": bool(account.get("canDeposit")),
            "permissions": permissions,
            "spot_trading_allowed": True if not permissions else "SPOT" in {str(item).upper() for item in permissions},
            "warning": "Manually verify withdraw permission is disabled and IP whitelist is enabled in Binance API settings.",
        }
    )
    return snapshot


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_from_any(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def decimal_env(name: str, default: str | None = None) -> Decimal | None:
    value = os.getenv(name, default)
    if value is None or value == "":
        return None
    return Decimal(value)


def bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def tuple_env(name: str, default: str = "") -> tuple[str, ...]:
    return parse_symbol_list(os.getenv(name, default))


def parse_symbol_list(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = value.split(",")
    else:
        raw_items = list(value)
    return tuple(item.strip().upper() for item in raw_items if str(item).strip())


def default_fixed_stop_loss_usdt(order_quote_amount: Decimal) -> Decimal:
    return max(MIN_FIXED_STOP_LOSS_USDT, order_quote_amount * DEFAULT_FIXED_STOP_LOSS_RATIO)


def effective_stop_loss_snapshot(config: BotConfig, leverage_multiplier: Decimal, contract_simulation: bool) -> dict[str, Any]:
    configured_pct = max(Decimal("0"), config.initial_stop_loss_pct)
    effective_pct = configured_pct
    margin_loss_stop_pct: Decimal | None = None
    liquidation_distance_pct: Decimal | None = None
    max_safe_stop_loss_pct: Decimal | None = None

    if contract_simulation and leverage_multiplier > 0:
        if config.contract_max_margin_loss_pct > 0:
            margin_loss_stop_pct = config.contract_max_margin_loss_pct / leverage_multiplier
            effective_pct = min(effective_pct, margin_loss_stop_pct)
        liquidation_distance_pct = Decimal("100") / leverage_multiplier
        max_safe_stop_loss_pct = max(
            Decimal("0"),
            liquidation_distance_pct - max(Decimal("0"), config.liquidation_stop_buffer_pct),
        )
        effective_pct = min(effective_pct, max_safe_stop_loss_pct)

    tightened = effective_pct < configured_pct
    warning = ""
    if tightened and contract_simulation:
        if max_safe_stop_loss_pct is not None and configured_pct > max_safe_stop_loss_pct:
            warning = "配置止损低于/接近强平价，已按强平保护规则收紧。"
        else:
            warning = "配置止损超过合约模拟保证金风险上限，已按有效止损规则收紧。"
    return {
        "configured_stop_loss_pct": configured_pct,
        "effective_stop_loss_pct": effective_pct,
        "margin_loss_stop_pct": margin_loss_stop_pct,
        "liquidation_distance_pct": liquidation_distance_pct,
        "max_safe_stop_loss_pct": max_safe_stop_loss_pct,
        "contract_max_margin_loss_pct": config.contract_max_margin_loss_pct,
        "liquidation_stop_buffer_pct": config.liquidation_stop_buffer_pct,
        "stop_guard_tightened": tightened,
        "stop_guard_warning": warning,
    }


def effective_initial_stop_price(config: BotConfig, entry_price: Decimal, leverage_multiplier: Decimal, contract_simulation: bool) -> tuple[Decimal, dict[str, Any]]:
    snapshot = effective_stop_loss_snapshot(config, leverage_multiplier, contract_simulation)
    effective_pct = Decimal(str(snapshot["effective_stop_loss_pct"]))
    return entry_price * (Decimal("1") - effective_pct / Decimal("100")), snapshot


def dynamic_stop_price(config: BotConfig, entry_price: Decimal, highest_price: Decimal, pct_stop: Decimal) -> tuple[Decimal, str]:
    stop_price = pct_stop
    stop_mode = "percent"
    highest_gain_pct = (highest_price - entry_price) / entry_price * Decimal("100")

    if config.breakeven_trigger_pct > 0 and highest_gain_pct >= config.breakeven_trigger_pct:
        breakeven_stop = entry_price * (Decimal("1") + config.breakeven_offset_pct / Decimal("100"))
        if breakeven_stop > stop_price:
            stop_price = breakeven_stop
            stop_mode = "breakeven"

    if config.trailing_stop_pct > 0 and highest_gain_pct >= config.trailing_start_pct:
        trailing_stop = highest_price * (Decimal("1") - config.trailing_stop_pct / Decimal("100"))
        if trailing_stop > stop_price:
            stop_price = trailing_stop
            stop_mode = "trailing"

    return stop_price, stop_mode


def normalize_trade_market_mode(value: str) -> str:
    mode = str(value or "futures_preferred").strip().lower().replace("-", "_")
    if mode not in TRADE_MARKET_MODES:
        raise ValueError(f"TRADE_MARKET_MODE must be one of {', '.join(sorted(TRADE_MARKET_MODES))}")
    return mode


def load_config(args: argparse.Namespace) -> BotConfig:
    square_urls = tuple(
        item.strip()
        for item in os.getenv("BINANCE_SQUARE_URLS", ",".join(DEFAULT_SQUARE_URLS)).split(",")
        if item.strip()
    )
    base_url = os.getenv("BINANCE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    futures_base_url = os.getenv("FUTURES_BASE_URL", DEFAULT_FUTURES_BASE_URL).rstrip("/")
    if args.testnet:
        base_url = "https://testnet.binance.vision"
        futures_base_url = DEFAULT_FUTURES_TESTNET_BASE_URL

    order_quote_amount = decimal_env("ORDER_QUOTE_USDT", "50") or Decimal("50")
    fixed_stop_loss_usdt = decimal_env("FIXED_STOP_LOSS_USDT")
    if fixed_stop_loss_usdt is None:
        fixed_stop_loss_usdt = default_fixed_stop_loss_usdt(order_quote_amount)
    leverage_multiplier = decimal_env("LEVERAGE_MULTIPLIER", "3") or Decimal("3")
    if leverage_multiplier <= 0:
        raise ValueError("LEVERAGE_MULTIPLIER must be greater than zero")

    return BotConfig(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        base_url=base_url,
        futures_base_url=futures_base_url,
        quote_asset=os.getenv("QUOTE_ASSET", "USDT"),
        trade_market_mode=normalize_trade_market_mode(os.getenv("TRADE_MARKET_MODE", "futures_preferred")),
        futures_margin_type=os.getenv("FUTURES_MARGIN_TYPE", "ISOLATED").strip().upper() or "ISOLATED",
        order_quote_amount=order_quote_amount,
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "15")),
        leverage_multiplier=leverage_multiplier,
        contract_simulation_enabled=bool_env("CONTRACT_SIMULATION_ENABLED", True),
        contract_max_margin_loss_pct=decimal_env("CONTRACT_MAX_MARGIN_LOSS_PCT", "20") or Decimal("20"),
        liquidation_stop_buffer_pct=decimal_env("LIQUIDATION_STOP_BUFFER_PCT", "2") or Decimal("2"),
        min_quote_volume=decimal_env("MIN_QUOTE_VOLUME_USDT", "5000000") or Decimal("5000000"),
        min_price_change_percent=decimal_env("MIN_PRICE_CHANGE_PERCENT", "3") or Decimal("3"),
        min_volatility_percent=decimal_env("MIN_VOLATILITY_PERCENT", "5") or Decimal("5"),
        top_post_limit=int(os.getenv("TOP_POST_LIMIT", "25")),
        top_coin_limit=int(os.getenv("TOP_COIN_LIMIT", "10")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
        recv_window_ms=int(os.getenv("RECV_WINDOW_MS", "5000")),
        initial_stop_loss_pct=decimal_env("INITIAL_STOP_LOSS_PCT", "4") or Decimal("4"),
        take_profit_pct=decimal_env("TAKE_PROFIT_PCT", "0"),
        breakeven_trigger_pct=decimal_env("BREAKEVEN_TRIGGER_PCT", "3"),
        breakeven_offset_pct=decimal_env("BREAKEVEN_OFFSET_PCT", "0.2"),
        trailing_start_pct=decimal_env("TRAILING_START_PCT", "6"),
        trailing_stop_pct=decimal_env("TRAILING_STOP_PCT", "3"),
        fixed_stop_loss_usdt=fixed_stop_loss_usdt,
        fixed_stop_after_first_round_trip=bool_env("FIXED_STOP_AFTER_FIRST_ROUND_TRIP", False),
        fixed_stop_equity_usdt=decimal_env("FIXED_STOP_EQUITY_USDT"),
        cooldown_minutes=int(os.getenv("COOLDOWN_MINUTES", "30")),
        max_daily_trades=int(os.getenv("MAX_DAILY_TRADES", "5")),
        max_daily_loss_usdt=decimal_env("MAX_DAILY_LOSS_USDT", "25"),
        max_total_exposure_pct=decimal_env("MAX_TOTAL_EXPOSURE_PCT", "0") or Decimal("0"),
        max_symbol_exposure_pct=decimal_env("MAX_SYMBOL_EXPOSURE_PCT", "0") or Decimal("0"),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "0")),
        max_intraday_drawdown_pct=decimal_env("MAX_INTRADAY_DRAWDOWN_PCT", "0") or Decimal("0"),
        risk_per_trade_pct=decimal_env("RISK_PER_TRADE_PCT", "0") or Decimal("0"),
        fee_rate_pct=decimal_env("FEE_RATE_PCT", "0.1"),
        slippage_pct=decimal_env("SLIPPAGE_PCT", "0.05"),
        asset_whitelist=tuple_env("ASSET_WHITELIST"),
        asset_blacklist=tuple_env("ASSET_BLACKLIST"),
        market_filter_enabled=bool_env("MARKET_FILTER_ENABLED", False),
        market_filter_assets=tuple_env("MARKET_FILTER_ASSETS", "BTC,ETH"),
        market_filter_min_change_pct=decimal_env("MARKET_FILTER_MIN_CHANGE_PCT", "-1"),
        market_filter_require_all=bool_env("MARKET_FILTER_REQUIRE_ALL", False),
        account_sync_enabled=bool_env("ACCOUNT_SYNC_ENABLED", True),
        kline_confirmation_enabled=bool_env("KLINE_CONFIRMATION_ENABLED", True),
        min_square_confidence_score=decimal_env("MIN_SQUARE_CONFIDENCE_SCORE", "35") or Decimal("35"),
        max_spread_bps=decimal_env("MAX_SPREAD_BPS", "50") or Decimal("50"),
        min_orderbook_depth_usdt=decimal_env("MIN_ORDERBOOK_DEPTH_USDT", "1000") or Decimal("1000"),
        exchange_protection_enabled=bool_env("EXCHANGE_PROTECTION_ENABLED", True),
        oco_stop_limit_slippage_pct=decimal_env("OCO_STOP_LIMIT_SLIPPAGE_PCT", "0.5") or Decimal("0.5"),
        signal_recording_enabled=bool_env("SIGNAL_RECORDING_ENABLED", True),
        signal_record_file=os.getenv("SIGNAL_RECORD_FILE", DEFAULT_SIGNAL_RECORD_FILE),
        trade_journal_file=os.getenv("TRADE_JOURNAL_FILE", DEFAULT_TRADE_JOURNAL_FILE),
        state_file=os.getenv("STATE_FILE", DEFAULT_STATE_FILE),
        dry_run=not args.live,
        square_urls=square_urls,
        square_browser_mode=args.square_browser or bool_env("SQUARE_BROWSER_MODE", True),
        square_diagnostic_limit=int(os.getenv("SQUARE_DIAGNOSTIC_LIMIT", "10")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_enabled=bool_env("TELEGRAM_ENABLED", False),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance Square long-only futures-preferred momentum bot")
    parser.add_argument("--live", action="store_true", help="place real Binance futures or Spot orders based on TRADE_MARKET_MODE")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--update-signal-returns", action="store_true", help="update future-return fields in the signal JSONL file and exit")
    parser.add_argument("--testnet", action="store_true", help="use Binance Spot and USD-M Futures testnet base URLs")
    parser.add_argument("--square-browser", action="store_true", help="scrape Binance Square with Playwright browser rendering")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_config(args)
    if config.dry_run:
        LOGGER.warning("dry-run mode is enabled; no real orders will be sent")
    else:
        LOGGER.warning("LIVE mode is enabled; real Futures or Spot orders may be sent (mode=%s)", config.trade_market_mode)

    if args.update_signal_returns:
        result = update_signal_record_future_returns(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    bot = LongOnlyMomentumBot(config)
    if args.once:
        bot.run_once()
    else:
        bot.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
