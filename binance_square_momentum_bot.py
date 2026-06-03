#!/usr/bin/env python3
"""
Long-only Binance Spot momentum bot driven by Binance Square mentions.

Default mode is dry-run. Use --live only after you have reviewed the logic,
tested with small order sizes, and accepted the risk of automated trading.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
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
DEFAULT_BASE_URL = "https://api.binance.com"
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
    quote_asset: str = "USDT"
    order_quote_amount: Decimal = Decimal("50")
    min_quote_volume: Decimal = Decimal("5000000")
    min_price_change_percent: Decimal = Decimal("3")
    min_volatility_percent: Decimal = Decimal("5")
    top_post_limit: int = 25
    top_coin_limit: int = 10
    poll_seconds: int = 300
    recv_window_ms: int = 5000
    initial_stop_loss_pct: Decimal = Decimal("20")
    fixed_stop_loss_usdt: Decimal = Decimal("200")
    fixed_stop_after_first_round_trip: bool = True
    fixed_stop_equity_usdt: Decimal | None = None
    state_file: str = DEFAULT_STATE_FILE
    dry_run: bool = True
    square_urls: tuple[str, ...] = DEFAULT_SQUARE_URLS
    square_browser_mode: bool = False


@dataclass
class SquarePost:
    title: str
    text: str
    traffic_score: float = 0.0
    url: str | None = None
    created_at: str | None = None


@dataclass
class TradeCandidate:
    symbol: str
    base_asset: str
    mention_count: int
    price_change_percent: Decimal
    volatility_percent: Decimal
    quote_volume: Decimal
    last_price: Decimal
    market_score: Decimal = Decimal("0")
    square_score: Decimal = Decimal("0")
    combined_score: Decimal = Decimal("0")


@dataclass
class PositionState:
    symbol: str = ""
    base_asset: str = ""
    quantity: str = "0"
    entry_price: str = "0"
    quote_spent: str = "0"
    opened_at: str = ""
    order_id: int | None = None


@dataclass
class BotState:
    first_buy_done: bool = False
    completed_round_trips: int = 0
    position: PositionState | None = None
    updated_at: str = ""
    trade_log: list[dict[str, Any]] = field(default_factory=list)


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
                result[item["symbol"]] = item
        return result

    def ticker_24hr(self) -> list[dict[str, Any]]:
        return self.public_get("/api/v3/ticker/24hr")

    def ticker_price(self, symbol: str) -> Decimal:
        data = self.public_get("/api/v3/ticker/price", {"symbol": symbol})
        return Decimal(str(data["price"]))

    def market_buy_quote(self, symbol: str, quote_order_qty: Decimal) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": format_decimal(quote_order_qty),
            "newOrderRespType": "FULL",
        }
        return self.signed_request("POST", "/api/v3/order", params)

    def market_sell_quantity(self, symbol: str, quantity: Decimal) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": format_decimal(quantity),
            "newOrderRespType": "FULL",
        }
        return self.signed_request("POST", "/api/v3/order", params)

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


class BinanceSquareScraper:
    def __init__(self, session: requests.Session, urls: Iterable[str]) -> None:
        self.session = session
        self.urls = tuple(urls)

    def fetch_top_posts(self, limit: int, browser_mode: bool = False) -> list[SquarePost]:
        if browser_mode:
            try:
                posts = self._fetch_top_posts_with_browser(limit)
                if posts:
                    LOGGER.info("Binance Square browser mode extracted %s posts", len(posts))
                    return posts[:limit]
            except Exception as exc:
                LOGGER.warning("Binance Square browser mode failed: %s", exc)

        posts: list[SquarePost] = []
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
                posts.extend(self._extract_posts(response.text, url))
            except requests.RequestException as exc:
                LOGGER.warning("Binance Square fetch failed for %s: %s", url, exc)

        deduped = dedupe_posts(posts)
        deduped.sort(key=lambda item: item.traffic_score, reverse=True)
        return deduped[:limit]

    def diagnose(self, limit: int, browser_mode: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "checked_at": utc_now(),
            "browser_mode": browser_mode,
            "urls": [],
            "total_posts": 0,
            "samples": [],
            "browser_hint": "",
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
                item["script_count"] = len(re.findall(r"<script[^>]*>", response.text, flags=re.IGNORECASE))
                json_posts = self._extract_posts_from_json_blobs(response.text, url)
                html_posts = self._extract_posts_from_html(response.text, url)
                item["json_posts"] = len(json_posts)
                item["html_posts"] = len(html_posts)
                all_posts.extend(json_posts or html_posts)
            except requests.RequestException as exc:
                item["error"] = str(exc)
            result["urls"].append(item)

        if browser_mode:
            try:
                browser_posts = self._fetch_top_posts_with_browser(limit, validate=False)
                result["browser_posts_raw"] = len(browser_posts)
                all_posts.extend(browser_posts)
            except Exception as exc:
                result["browser_error"] = str(exc)
                result["browser_hint"] = (
                    "Install or repair browser scraping support by running fix_playwright_browser.bat, "
                    "or run: python -m pip install playwright && python -m playwright install chromium"
                )

        raw_deduped = dedupe_posts(all_posts, validate=False)
        deduped = dedupe_posts(all_posts)
        deduped.sort(key=lambda item: item.traffic_score, reverse=True)
        result["raw_posts"] = len(raw_deduped)
        result["filtered_out_posts"] = max(0, len(raw_deduped) - len(deduped))
        result["total_posts"] = len(deduped)
        result["samples"] = [post_to_dict(post) for post in deduped[: min(limit, 8)]]
        return result

    def _fetch_top_posts_with_browser(self, limit: int, validate: bool = True) -> list[SquarePost]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: python -m pip install playwright && "
                "python -m playwright install chromium"
            ) from exc

        posts: list[SquarePost] = []
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
                for url in self.urls:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
                    for _ in range(3):
                        page.mouse.wheel(0, 1400)
                        page.wait_for_timeout(1200)
                    html = page.content()
                    posts.extend(self._extract_posts(html, url))
                    if len(posts) < limit:
                        try:
                            body_text = page.locator("body").inner_text(timeout=5000)
                            posts.extend(self._extract_posts_from_rendered_text(body_text, url))
                        except Exception:
                            LOGGER.debug("failed to extract rendered Square text", exc_info=True)
            finally:
                browser.close()

        deduped = dedupe_posts(posts, validate=validate)
        deduped.sort(key=lambda item: item.traffic_score, reverse=True)
        return deduped[:limit]

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
            for obj in iter_json_objects(blob):
                title = first_string(obj, ("title", "subject", "headline"))
                content = first_string(obj, ("content", "text", "summary", "description", "body"))
                if not (title or content):
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
                    ),
                )
                url = first_string(obj, ("url", "link", "shareUrl")) or source_url
                created_at = first_string(obj, ("createdAt", "publishTime", "releaseTime"))
                posts.append(
                    SquarePost(
                        title=clean_text(title),
                        text=clean_text(content),
                        traffic_score=traffic,
                        url=url,
                        created_at=created_at,
                    )
                )
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
            posts.append(SquarePost(title=block[:120], text=block, traffic_score=float(len(block)), url=source_url))
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
                    posts.append(SquarePost(title=block[:120], text=block, traffic_score=float(len(block)), url=source_url))
                buffer = []
            buffer.append(line)
        block = clean_text(" ".join(buffer))
        if len(block) >= 80 and contains_market_symbol_hint(block):
            posts.append(SquarePost(title=block[:120], text=block, traffic_score=float(len(block)), url=source_url))
        return posts


class LongOnlyMomentumBot:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.client = BinanceSpotClient(config)
        self.square = BinanceSquareScraper(build_retry_session(), config.square_urls)
        self.state = load_state(config.state_file)
        self.stop_requested = False

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

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
            self.client.sync_time()
            self._manage_open_position()
            if self.state.position is None:
                self._scan_and_enter()
            save_state(self.config.state_file, self.state)
        except Exception:
            LOGGER.exception("cycle failed")

    def _manage_open_position(self) -> None:
        position = self.state.position
        if position is None or not position.symbol:
            return

        symbol = position.symbol
        qty = Decimal(position.quantity)
        entry_price = Decimal(position.entry_price)
        last_price = self.client.ticker_price(symbol)
        unrealized_loss = max(Decimal("0"), (entry_price - last_price) * qty)
        pct_stop = entry_price * (Decimal("1") - self.config.initial_stop_loss_pct / Decimal("100"))
        fixed_mode = self._fixed_stop_enabled()

        LOGGER.info(
            "position %s qty=%s entry=%s last=%s loss=%s stop_mode=%s",
            symbol,
            qty,
            entry_price,
            last_price,
            unrealized_loss,
            "fixed-usdt" if fixed_mode else "percent",
        )

        should_stop = unrealized_loss >= self.config.fixed_stop_loss_usdt if fixed_mode else last_price <= pct_stop
        if not should_stop:
            return

        LOGGER.warning("stop loss triggered for %s", symbol)
        sell_qty = self._safe_sell_quantity(symbol, position.base_asset, qty)
        if sell_qty <= 0:
            LOGGER.error("no sellable balance for %s; clearing local position is unsafe, keeping state", symbol)
            return

        if self.config.dry_run:
            LOGGER.warning("[dry-run] would SELL %s %s at market", sell_qty, symbol)
            self._append_trade("DRY_RUN_STOP_SELL", symbol, sell_qty, last_price, None)
            self.state.completed_round_trips += 1
            self.state.position = None
            self._touch_state()
            return

        order = self.client.market_sell_quantity(symbol, sell_qty)
        avg_price = average_fill_price(order) or last_price
        self._append_trade("STOP_SELL", symbol, sell_qty, avg_price, order)
        self.state.completed_round_trips += 1
        self.state.position = None
        self._touch_state()

    def _scan_and_enter(self) -> None:
        symbols = self.client.tradable_quote_symbols(self.config.quote_asset)
        base_assets = {data["baseAsset"] for data in symbols.values()}
        posts = self.square.fetch_top_posts(self.config.top_post_limit, browser_mode=self.config.square_browser_mode)
        mentions = count_coin_mentions(posts, base_assets)
        source = "Binance Square browser" if self.config.square_browser_mode else "Binance Square"
        if not mentions:
            LOGGER.warning("no valid long-only Binance Square mentions found; market momentum will drive ranking")

        candidates = self._rank_trade_candidates(symbols, mentions)
        ranked_assets = [item.base_asset for item in candidates[: self.config.top_coin_limit]]
        LOGGER.info("ranked assets from %s + 24h market movers: %s", source, ranked_assets)

        if not candidates:
            LOGGER.info("no candidate passed momentum filters")
            return
        candidate = candidates[0]

        LOGGER.info("selected candidate: %s", asdict(candidate))
        if self.config.dry_run:
            LOGGER.warning(
                "[dry-run] would BUY %s with %s %s",
                candidate.symbol,
                self.config.order_quote_amount,
                self.config.quote_asset,
            )
            qty = self.config.order_quote_amount / candidate.last_price
            self._open_position(candidate, qty, candidate.last_price, None)
            return

        order = self.client.market_buy_quote(candidate.symbol, self.config.order_quote_amount)
        qty = Decimal(str(order.get("executedQty", "0")))
        avg_price = average_fill_price(order) or candidate.last_price
        if qty <= 0:
            raise BinanceAPIError(f"market buy returned zero executedQty: {order}")
        self._open_position(candidate, qty, avg_price, order)

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
        tickers = self.client.ticker_24hr()
        candidates: list[TradeCandidate] = []
        max_mentions = max(mentions.values(), default=0)

        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            symbol_info = symbols.get(symbol)
            if symbol_info is None:
                continue
            base_asset = symbol_info["baseAsset"]
            if not is_momentum_asset(base_asset, self.config.quote_asset):
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
        tickers = self.client.ticker_24hr()
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
    ) -> None:
        self.state.position = PositionState(
            symbol=candidate.symbol,
            base_asset=candidate.base_asset,
            quantity=format_decimal(quantity),
            entry_price=format_decimal(entry_price),
            quote_spent=format_decimal(quantity * entry_price),
            opened_at=utc_now(),
            order_id=int(order["orderId"]) if order and "orderId" in order else None,
        )
        self.state.first_buy_done = True
        self._append_trade("BUY", candidate.symbol, quantity, entry_price, order)
        self._touch_state()

    def _fixed_stop_enabled(self) -> bool:
        if self.config.fixed_stop_after_first_round_trip and self.state.completed_round_trips > 0:
            return True
        if self.config.fixed_stop_equity_usdt is None:
            return False
        try:
            account = self.client.account()
            balances = {item["asset"]: Decimal(item["free"]) + Decimal(item["locked"]) for item in account["balances"]}
            quote_balance = balances.get(self.config.quote_asset, Decimal("0"))
            position_value = Decimal("0")
            if self.state.position:
                last = self.client.ticker_price(self.state.position.symbol)
                position_value = Decimal(self.state.position.quantity) * last
            return quote_balance + position_value >= self.config.fixed_stop_equity_usdt
        except Exception:
            LOGGER.exception("failed to evaluate equity threshold; keeping percent stop")
            return False

    def _safe_sell_quantity(self, symbol: str, base_asset: str, wanted_qty: Decimal) -> Decimal:
        account = self.client.account() if not self.config.dry_run else {"balances": []}
        free_balance = wanted_qty
        if not self.config.dry_run:
            for item in account.get("balances", []):
                if item.get("asset") == base_asset:
                    free_balance = Decimal(str(item.get("free", "0")))
                    break
        qty = min(wanted_qty, free_balance)
        step_size = symbol_step_size(self.client.exchange_info(), symbol)
        return round_down_to_step(qty, step_size)

    def _append_trade(
        self,
        action: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        raw_order: dict[str, Any] | None,
    ) -> None:
        self.state.trade_log.append(
            {
                "ts": utc_now(),
                "action": action,
                "symbol": symbol,
                "quantity": format_decimal(quantity),
                "price": format_decimal(price),
                "dry_run": self.config.dry_run,
                "order": raw_order,
            }
        )
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
    return BotState(
        first_buy_done=bool(raw.get("first_buy_done", False)),
        completed_round_trips=int(raw.get("completed_round_trips", 0)),
        position=PositionState(**position) if position else None,
        updated_at=raw.get("updated_at", ""),
        trade_log=list(raw.get("trade_log", [])),
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
        key = clean_text(f"{post.title} {post.text}")[:300].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(post)
    return result


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


def square_rank_score(mention_count: int, max_mentions: int) -> Decimal:
    if mention_count <= 0 or max_mentions <= 0:
        return Decimal("0")
    return (Decimal(mention_count) / Decimal(max_mentions)) * Decimal("180")


def count_coin_mentions(posts: list[SquarePost], base_assets: set[str]) -> Counter[str]:
    valid_assets = {item for item in base_assets if is_square_mention_asset(item)}
    mentions: Counter[str] = Counter()
    weighted_posts = sorted(posts, key=lambda item: item.traffic_score, reverse=True)
    for rank, post in enumerate(weighted_posts, start=1):
        text = f"{post.title} {post.text}".upper()
        rank_weight = max(1, len(weighted_posts) - rank + 1)
        for asset in valid_assets:
            if asset in PREFIX_REQUIRED_SYMBOLS or (len(asset) <= 3 and asset not in STRONG_BARE_SYMBOLS):
                pattern = rf"(?<![A-Z0-9])(?:\$|#){re.escape(asset)}(?![A-Z0-9])"
            else:
                pattern = rf"(?<![A-Z0-9])(?:\$|#)?{re.escape(asset)}(?![A-Z0-9])"
            count = len(re.findall(pattern, text))
            if count:
                mentions[asset] += count * rank_weight
    return mentions


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
    return None


def symbol_step_size(exchange_info: dict[str, Any], symbol: str) -> Decimal:
    for item in exchange_info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        for filt in item.get("filters", []):
            if filt.get("filterType") == "LOT_SIZE":
                return Decimal(str(filt["stepSize"]))
    return Decimal("0.00000001")


def round_down_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return quantity
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def format_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def load_config(args: argparse.Namespace) -> BotConfig:
    square_urls = tuple(
        item.strip()
        for item in os.getenv("BINANCE_SQUARE_URLS", ",".join(DEFAULT_SQUARE_URLS)).split(",")
        if item.strip()
    )
    base_url = os.getenv("BINANCE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if args.testnet:
        base_url = "https://testnet.binance.vision"

    return BotConfig(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        base_url=base_url,
        quote_asset=os.getenv("QUOTE_ASSET", "USDT"),
        order_quote_amount=decimal_env("ORDER_QUOTE_USDT", "50") or Decimal("50"),
        min_quote_volume=decimal_env("MIN_QUOTE_VOLUME_USDT", "5000000") or Decimal("5000000"),
        min_price_change_percent=decimal_env("MIN_PRICE_CHANGE_PERCENT", "3") or Decimal("3"),
        min_volatility_percent=decimal_env("MIN_VOLATILITY_PERCENT", "5") or Decimal("5"),
        top_post_limit=int(os.getenv("TOP_POST_LIMIT", "25")),
        top_coin_limit=int(os.getenv("TOP_COIN_LIMIT", "10")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
        recv_window_ms=int(os.getenv("RECV_WINDOW_MS", "5000")),
        initial_stop_loss_pct=decimal_env("INITIAL_STOP_LOSS_PCT", "20") or Decimal("20"),
        fixed_stop_loss_usdt=decimal_env("FIXED_STOP_LOSS_USDT", "200") or Decimal("200"),
        fixed_stop_after_first_round_trip=bool_env("FIXED_STOP_AFTER_FIRST_ROUND_TRIP", True),
        fixed_stop_equity_usdt=decimal_env("FIXED_STOP_EQUITY_USDT"),
        state_file=os.getenv("STATE_FILE", DEFAULT_STATE_FILE),
        dry_run=not args.live,
        square_urls=square_urls,
        square_browser_mode=args.square_browser or bool_env("SQUARE_BROWSER_MODE", False),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance Square long-only spot momentum bot")
    parser.add_argument("--live", action="store_true", help="place real Binance Spot orders")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--testnet", action="store_true", help="use Binance Spot testnet base URL")
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
        LOGGER.warning("LIVE mode is enabled; real Spot BUY/SELL orders may be sent")

    bot = LongOnlyMomentumBot(config)
    if args.once:
        bot.run_once()
    else:
        bot.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
