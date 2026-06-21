from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import binance_square_momentum_bot as bot
import web_dashboard as web
import analyze_signal_records
import replay_signal_records
import walk_forward_signal_records


def assert_raises(expected: type[Exception], fn) -> None:
    try:
        fn()
    except expected:
        return
    raise AssertionError(f"expected {expected.__name__}")


def test_state_migration_and_client_order_id() -> None:
    cid1 = bot.build_client_order_id("buy", "BTCUSDT", "20260608120000")
    cid2 = bot.build_client_order_id("buy", "BTCUSDT", "20260608120001")
    assert cid1.startswith("bm-buy-BTCUSDT-20260608120000-"), cid1
    assert cid1 != cid2
    assert len(cid1) <= 36

    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "bot_state.json"
        state_path.write_text('{"first_buy_done": false, "positions": [], "trade_log": []}', encoding="utf-8")
        loaded = bot.load_state(str(state_path))
        assert loaded.pending_order is None
        assert loaded.protection_orders == []
        assert loaded.entry_confirmation == {}
        assert loaded.account_risk_snapshot == {}

        loaded.pending_order = bot.PendingOrderState(symbol="BTCUSDT", side="BUY", client_order_id=cid1)
        loaded.protection_orders = [bot.ProtectionOrderState(symbol="BTCUSDT", client_order_id="bm-prot")]
        bot.save_state(str(state_path), loaded)
        reloaded = bot.load_state(str(state_path))
        assert reloaded.pending_order and reloaded.pending_order.client_order_id == cid1
        assert reloaded.protection_orders and reloaded.protection_orders[0].symbol == "BTCUSDT"

        state_path.write_text(
            json.dumps(
                {
                    "positions": [
                        {"symbol": "LEGACYUSDT", "position_mode": "contract-sim"},
                        {
                            "symbol": "EXPLICITUSDT",
                            "position_mode": "contract-sim",
                            "market_type": bot.MARKET_SPOT,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        migrated = bot.load_state(str(state_path))
        assert migrated.positions[0].market_type == bot.MARKET_FUTURES
        assert migrated.positions[1].market_type == bot.MARKET_SPOT


def test_live_confirm_and_dashboard_auth() -> None:
    config = web.config_from_payload({"live": True})
    assert_raises(RuntimeError, lambda: web.require_live_confirmation(config, {"live": True}, "run-once"))
    web.require_live_confirmation(config, {"live": True, "live_confirmed": True}, "run-once")

    assert web.dashboard_auth_error("", {}, "/api/run-once") is None
    assert "token" in (web.dashboard_auth_error("secret", {}, "/api/run-once") or "")
    assert web.dashboard_auth_error("secret", {"X-Dashboard-Token": "secret"}, "/api/run-once") is None
    assert web.dashboard_request_host_error("127.0.0.1", {"Host": "127.0.0.1:8787"}) is None
    assert web.dashboard_request_host_error("127.0.0.1", {"Host": "evil.example"}) is not None
    assert web.dashboard_request_host_error("127.0.0.1", {"Host": "127.0.0.1:8787", "Origin": "http://evil.example"}) is not None

    previous = os.environ.get("DASHBOARD_READ_ONLY")
    try:
        os.environ["DASHBOARD_READ_ONLY"] = "true"
        assert web.dashboard_read_only_enabled()
        assert web.dashboard_read_only_error("/api/run-once") is not None
        assert web.dashboard_read_only_error("/api/status") is None
        snapshot = web.dashboard_security_snapshot("127.0.0.1")
        assert snapshot["read_only"] is True
        assert snapshot["local_only_host"] is True
    finally:
        if previous is None:
            os.environ.pop("DASHBOARD_READ_ONLY", None)
        else:
            os.environ["DASHBOARD_READ_ONLY"] = previous


def test_dashboard_signal_payload_from_run_once_record() -> None:
    record = {
        "recorded_at": "2026-06-10T01:00:00+00:00",
        "source": "run_once",
        "square_confidence": {"score": "42", "post_count": 5},
        "candidates": [
            {"asset": "BTC", "symbol": "BTCUSDT", "score": "100", "price_change_percent": "8"},
            {"asset": "ETH", "symbol": "ETHUSDT", "score": "80", "price_change_percent": "5"},
        ],
        "candidate": {"asset": "BTC", "symbol": "BTCUSDT", "score": "100", "price_change_percent": "8"},
        "entry_confirmation": {"passed": True, "symbol": "BTCUSDT", "reason": "entry confirmed"},
        "final_action": "entered",
        "entered": True,
        "note": "dry-run position opened",
    }
    signal = web.signal_payload_from_record(record, checked_at="2026-06-10 09:00:00")
    assert signal["checked_at"] == "2026-06-10 09:00:00"
    assert signal["source"].startswith("自动循环")
    assert signal["candidate"]["symbol"] == "BTCUSDT"
    assert len(signal["hot_assets"]) == 2
    assert signal["entered"] is True
    assert "已开仓" in signal["note"]

    skipped = dict(record, final_action="skipped", entered=False, candidate=None, note="")
    skipped["entry_confirmation"] = {"passed": False, "reason": "Square confidence low"}
    signal = web.signal_payload_from_record(skipped, checked_at="2026-06-10 09:05:00")
    assert signal["candidate"] is None
    assert "Square confidence low" in signal["note"]
    action, note = web.loop_action_from_signal_record(skipped)
    assert action == "skipped"
    assert note == "Square confidence low"


def test_signal_reliability_filters() -> None:
    low_conf = bot.square_confidence_snapshot([], {"extractor_mode": "none"}, bot.SquareFeedState(consecutive_failures=2))
    assert Decimal(str(low_conf["score"])) < Decimal("35")

    posts = [
        bot.SquarePost(
            title="BTC breakout",
            text="BTC long setup with volume",
            post_id=str(index),
            author="analyst",
            created_at="2026-06-08T00:00:00+00:00",
        )
        for index in range(10)
    ]
    high_conf = bot.square_confidence_snapshot(
        posts,
        {"extractor_mode": "network_api", "api_post_count": 10, "new_post_count": 10},
        bot.SquareFeedState(),
    )
    assert Decimal(str(high_conf["score"])) >= Decimal("35")

    rows_down = [[0, "100", "101", "99", str(100 - i), "1", 0, "1000"] for i in range(24)]
    kline_down = bot.kline_confirmation_snapshot(rows_down)
    assert kline_down["roc_pct"] < 0
    assert not kline_down["above_ema9"]

    rows_up = [[0, "100", "101", "99", str(100 + i), "1", 0, str(1000 + i)] for i in range(24)]
    kline_up = bot.kline_confirmation_snapshot(rows_up)
    assert kline_up["roc_pct"] > 0
    assert kline_up["above_ema9"]

    bad_depth = {"bids": [["100", "1"]], "asks": [["102", "1"]]}
    assert bot.orderbook_liquidity_snapshot(bad_depth)["spread_bps"] > Decimal("50")
    good_depth = {"bids": [["100", "100"]], "asks": [["100.01", "100"]]}
    good_snapshot = bot.orderbook_liquidity_snapshot(good_depth)
    assert good_snapshot["spread_bps"] < Decimal("50")
    assert good_snapshot["ask_depth_usdt"] > Decimal("1000")


def test_symbol_scoring_rounding_and_dry_run_fill() -> None:
    posts = [
        bot.SquarePost(title="$BTC breakout", text="BTC long setup with volume", traffic_score=10),
        bot.SquarePost(title="$ETH breakout", text="ETH long setup with volume", traffic_score=5),
        bot.SquarePost(title="API update", text="not a trade", traffic_score=100),
    ]
    mentions = bot.count_coin_mentions(posts, {"BTC", "ETH", "API", "USDT"})
    assert mentions["BTC"] > mentions["ETH"] > 0
    assert "API" not in mentions
    extracted = bot.extract_square_symbols("$BTC and ETH long, API docs")
    assert extracted["BTC"] >= 1
    assert extracted["ETH"] >= 1
    assert "API" not in extracted

    candidates = [
        bot.TradeCandidate(
            symbol="ETHUSDT",
            base_asset="ETH",
            mention_count=1,
            price_change_percent=Decimal("5"),
            volatility_percent=Decimal("5"),
            quote_volume=Decimal("10000000"),
            last_price=Decimal("100"),
            market_score=Decimal("10"),
            square_score=Decimal("20"),
            combined_score=Decimal("30"),
        ),
        bot.TradeCandidate(
            symbol="BTCUSDT",
            base_asset="BTC",
            mention_count=3,
            price_change_percent=Decimal("6"),
            volatility_percent=Decimal("6"),
            quote_volume=Decimal("20000000"),
            last_price=Decimal("200"),
            market_score=Decimal("20"),
            square_score=Decimal("40"),
            combined_score=Decimal("60"),
        ),
    ]
    candidates.sort(key=lambda item: (item.combined_score, item.market_score), reverse=True)
    assert candidates[0].symbol == "BTCUSDT"
    assert bot.square_rank_score(3, 3) > bot.square_rank_score(1, 3)
    assert bot.volume_rank_score(Decimal("10000000"), Decimal("5000000")) == Decimal("16")

    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.002"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            }
        ]
    }
    rules = bot.symbol_order_rules(exchange_info, "BTCUSDT")
    assert rules.step_size == Decimal("0.001")
    assert rules.min_qty == Decimal("0.002")
    assert rules.min_notional == Decimal("10")
    assert rules.tick_size == Decimal("0.01")
    assert bot.round_down_to_step(Decimal("1.23456"), Decimal("0.001")) == Decimal("1.234")

    with tempfile.TemporaryDirectory() as tmp:
        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=str(Path(tmp) / "state.json"),
            order_quote_amount=Decimal("100"),
            leverage_multiplier=Decimal("10"),
            contract_simulation_enabled=True,
            fee_rate_pct=Decimal("0.1"),
            slippage_pct=Decimal("0.05"),
        )
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.client.market_buy_quote = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not place live buy"))
        fill_price, quantity, fee_amount, quote_spent = instance._dry_run_buy_fill(Decimal("100"))
        assert fill_price == Decimal("100.0500")
        assert fee_amount == Decimal("1.0")
        assert quote_spent == Decimal("100")
        assert quantity > Decimal("9")


def test_futures_preferred_candidate_pool_and_reduce_only_order() -> None:
    class FakeSpot:
        def tradable_quote_symbols(self, quote_asset: str):
            assert quote_asset == "USDT"
            return {
                "BOTHUSDT": {"symbol": "BOTHUSDT", "baseAsset": "BOTH", "quoteAsset": "USDT", "market_type": bot.MARKET_SPOT},
                "SPOTUSDT": {"symbol": "SPOTUSDT", "baseAsset": "SPOT", "quoteAsset": "USDT", "market_type": bot.MARKET_SPOT},
            }

        def ticker_24hr(self):
            return [
                {"symbol": "BOTHUSDT", "priceChangePercent": "5", "quoteVolume": "10000000", "highPrice": "12", "lowPrice": "10", "lastPrice": "11"},
                {"symbol": "SPOTUSDT", "priceChangePercent": "6", "quoteVolume": "10000000", "highPrice": "12", "lowPrice": "10", "lastPrice": "11"},
            ]

    class FakeFutures:
        def tradable_quote_symbols(self, quote_asset: str):
            assert quote_asset == "USDT"
            return {
                "BOTHUSDT": {"symbol": "BOTHUSDT", "baseAsset": "BOTH", "quoteAsset": "USDT", "market_type": bot.MARKET_FUTURES},
                "FUTUSDT": {"symbol": "FUTUSDT", "baseAsset": "FUT", "quoteAsset": "USDT", "market_type": bot.MARKET_FUTURES},
            }

        def ticker_24hr(self):
            return [
                {"symbol": "BOTHUSDT", "priceChangePercent": "7", "quoteVolume": "10000000", "highPrice": "12", "lowPrice": "10", "lastPrice": "11"},
                {"symbol": "FUTUSDT", "priceChangePercent": "8", "quoteVolume": "10000000", "highPrice": "12", "lowPrice": "10", "lastPrice": "11"},
            ]

    cfg = bot.BotConfig(api_key="", api_secret="", trade_market_mode="futures_preferred", min_price_change_percent=Decimal("1"), min_volatility_percent=Decimal("1"))
    instance = bot.LongOnlyMomentumBot(cfg)
    instance.spot_client = FakeSpot()
    instance.futures_client = FakeFutures()
    symbols = instance._tradable_market_symbols()
    assert symbols["BOTHUSDT"]["market_type"] == bot.MARKET_FUTURES
    assert symbols["FUTUSDT"]["market_type"] == bot.MARKET_FUTURES
    assert symbols["SPOTUSDT"]["market_type"] == bot.MARKET_SPOT
    candidates = instance._rank_trade_candidates(symbols, bot.Counter())
    assert any(item.symbol == "FUTUSDT" and item.market_type == bot.MARKET_FUTURES for item in candidates)

    cfg = bot.BotConfig(api_key="", api_secret="", trade_market_mode="futures_only", min_price_change_percent=Decimal("1"), min_volatility_percent=Decimal("1"))
    instance = bot.LongOnlyMomentumBot(cfg)
    instance.spot_client = FakeSpot()
    instance.futures_client = FakeFutures()
    symbols = instance._tradable_market_symbols()
    assert "SPOTUSDT" not in symbols

    class CaptureFutures(bot.BinanceFuturesClient):
        def __init__(self):
            super().__init__(bot.BotConfig(api_key="key", api_secret="secret"))
            self.last = {}

        def signed_request(self, method, path, params=None):
            self.last = {"method": method, "path": path, "params": params or {}}
            return {"status": "FILLED", "executedQty": params.get("quantity", "0"), "avgPrice": "10"}

    futures = CaptureFutures()
    futures.market_sell_quantity("BTCUSDT", Decimal("0.01"), client_order_id="cid")
    assert futures.last["path"] == "/fapi/v1/order"
    assert futures.last["params"]["reduceOnly"] == "true"
    assert futures.last["params"]["side"] == "SELL"


def test_contract_sim_effective_stop_loss_guard() -> None:
    entry = Decimal("0.51401088")

    assert bot.estimated_liquidation_price(Decimal("100"), Decimal("5")) == Decimal("80")
    assert bot.estimated_liquidation_price(Decimal("100"), Decimal("10")) == Decimal("90")
    assert bot.estimated_liquidation_price(Decimal("100"), Decimal("20")) == Decimal("95")

    cfg = bot.BotConfig(api_key="", api_secret="", initial_stop_loss_pct=Decimal("20"), leverage_multiplier=Decimal("10"))
    stop_price, snapshot = bot.effective_initial_stop_price(cfg, entry, Decimal("10"), True)
    assert snapshot["effective_stop_loss_pct"] == Decimal("2")
    assert snapshot["margin_loss_stop_pct"] == Decimal("2")
    assert snapshot["liquidation_distance_pct"] == Decimal("10")
    assert snapshot["max_safe_stop_loss_pct"] == Decimal("8")
    assert snapshot["stop_guard_tightened"] is True
    assert stop_price == Decimal("0.5037306624")
    assert stop_price > entry * Decimal("0.9")

    cfg = bot.BotConfig(api_key="", api_secret="", initial_stop_loss_pct=Decimal("4"), leverage_multiplier=Decimal("10"))
    stop_price, snapshot = bot.effective_initial_stop_price(cfg, Decimal("100"), Decimal("10"), True)
    assert snapshot["effective_stop_loss_pct"] == Decimal("2")
    assert stop_price == Decimal("98")

    cfg = bot.BotConfig(api_key="", api_secret="", initial_stop_loss_pct=Decimal("4"), leverage_multiplier=Decimal("5"))
    stop_price, snapshot = bot.effective_initial_stop_price(cfg, Decimal("100"), Decimal("5"), True)
    assert snapshot["effective_stop_loss_pct"] == Decimal("4")
    assert snapshot["stop_guard_tightened"] is False
    assert stop_price == Decimal("96")

    cfg = bot.BotConfig(api_key="", api_secret="", initial_stop_loss_pct=Decimal("20"), leverage_multiplier=Decimal("10"))
    stop_price, snapshot = bot.effective_initial_stop_price(cfg, Decimal("100"), Decimal("10"), False)
    assert snapshot["effective_stop_loss_pct"] == Decimal("20")
    assert snapshot["stop_guard_tightened"] is False
    assert stop_price == Decimal("80")

    cfg = bot.BotConfig(
        api_key="",
        api_secret="",
        initial_stop_loss_pct=Decimal("20"),
        leverage_multiplier=Decimal("10"),
        breakeven_trigger_pct=Decimal("3"),
        breakeven_offset_pct=Decimal("0.2"),
        trailing_start_pct=Decimal("6"),
        trailing_stop_pct=Decimal("3"),
    )
    guarded_stop, _ = bot.effective_initial_stop_price(cfg, Decimal("100"), Decimal("10"), True)
    breakeven_stop, breakeven_mode = bot.dynamic_stop_price(cfg, Decimal("100"), Decimal("104"), guarded_stop)
    assert breakeven_mode == "breakeven"
    assert breakeven_stop > guarded_stop
    trailing_stop, trailing_mode = bot.dynamic_stop_price(cfg, Decimal("100"), Decimal("110"), guarded_stop)
    assert trailing_mode == "trailing"
    assert trailing_stop > guarded_stop


def test_contract_sim_liquidation_closes_dry_run_position() -> None:
    class FakeFutures:
        def ticker_price(self, _symbol: str) -> Decimal:
            return Decimal("94")

    with tempfile.TemporaryDirectory() as tmp:
        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=str(Path(tmp) / "state.json"),
            trade_journal_file=str(Path(tmp) / "journal.sqlite3"),
            order_quote_amount=Decimal("100"),
            leverage_multiplier=Decimal("20"),
            initial_stop_loss_pct=Decimal("20"),
            contract_max_margin_loss_pct=Decimal("100"),
            liquidation_stop_buffer_pct=Decimal("0"),
        )
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.futures_client = FakeFutures()
        instance.state.positions = [
            bot.PositionState(
                symbol="ABCUSDT",
                base_asset="ABC",
                quantity="200",
                entry_price="100",
                highest_price="100",
                quote_spent="1000",
                margin_quote="1000",
                notional_quote="20000",
                leverage_multiplier="20",
                market_type=bot.MARKET_FUTURES,
                position_mode="contract-sim",
            )
        ]
        instance.state.position = instance.state.positions[0]
        instance.state.trade_log = [
            {
                "ts": bot.utc_now(),
                "action": "BUY",
                "symbol": "ABCUSDT",
                "quantity": "200",
                "price": "100",
                "quote_amount": "1000",
                "dry_run": True,
            }
        ]

        instance._manage_single_position(instance.state.positions[0])

        assert instance._active_positions() == []
        assert instance.state.completed_round_trips == 1
        assert instance.state.trade_log[-1]["action"] == "DRY_RUN_LIQUIDATION"
        assert instance.state.trade_log[-1]["price"] == "95"
        assert instance.state.trade_log[-1]["quote_amount"] == "0"
        assert instance._daily_trade_stats()["realized_pnl"] == Decimal("-1000")


def test_explicit_position_market_type_overrides_legacy_position_mode() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)

    legacy_spot_sim = bot.PositionState(
        symbol="ATMUSDT",
        market_type=bot.MARKET_SPOT,
        position_mode="contract-sim",
    )
    futures_sim = bot.PositionState(
        symbol="BTCUSDT",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    assert instance._position_market_type(legacy_spot_sim) == bot.MARKET_SPOT
    assert instance._position_market_type(futures_sim) == bot.MARKET_FUTURES


def test_position_management_error_does_not_stop_remaining_positions() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True)
    first = bot.PositionState(symbol="BADUSDT")
    second = bot.PositionState(symbol="GOODUSDT")
    instance.state = bot.BotState(position=first, positions=[first, second])
    managed: list[str] = []
    cleared: list[str] = []

    def manage(position: bot.PositionState) -> None:
        managed.append(position.symbol)
        if position.symbol == "BADUSDT":
            raise bot.BinanceAPIError("HTTP 400: {'code': -1121, 'msg': 'Invalid symbol.'}")

    instance._manage_single_position = manage
    instance._append_trade = lambda action, *_args, **_kwargs: cleared.append(action)
    instance._touch_state = lambda: None
    instance._manage_open_position()

    assert managed == ["BADUSDT", "GOODUSDT"]
    assert cleared == ["DRY_RUN_STALE_POSITION_CLEAR"]
    assert [item.symbol for item in instance._active_positions()] == ["GOODUSDT"]


def test_adaptive_exit_esports_path_locks_profit_after_partial_take_profit() -> None:
    config = bot.BotConfig(
        api_key="",
        api_secret="",
        dry_run=True,
        leverage_multiplier=Decimal("5"),
        adaptive_exit_enabled=True,
        atr_multiplier=Decimal("3"),
        trailing_min_pct=Decimal("2"),
        trailing_max_pct=Decimal("8"),
        partial_take_profit_r=Decimal("2"),
        partial_take_profit_fraction=Decimal("0.5"),
        breakeven_trigger_r=Decimal("1"),
        breakeven_cost_buffer_pct=Decimal("0.25"),
        post_partial_profit_floor_r=Decimal("0.5"),
    )
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        quantity="757.0543827856109287916011614",
        initial_quantity="757.0543827856109287916011614",
        entry_price="0.197938224",
        highest_price="0.197938224",
        risk_per_unit="0.00791752896",
        active_stop_price="0.19002069504",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    partial = bot.adaptive_exit_snapshot(config, position, Decimal("0.253"), Decimal("0.005"))
    assert partial["action"] == "partial_take_profit"
    assert partial["r_multiple"] > Decimal("6")
    assert partial["close_fraction"] == Decimal("0.5")

    position.partial_take_profit_done = True
    position.highest_price = "0.253"
    trailed = bot.adaptive_exit_snapshot(config, position, Decimal("0.253"), Decimal("0.005"))
    assert trailed["stage"] == "atr_trailing"
    assert trailed["active_stop_price"] == Decimal("0.238")
    assert trailed["active_stop_price"] > Decimal("0.23")

    position.active_stop_price = bot.format_decimal(trailed["active_stop_price"])
    exit_decision = bot.adaptive_exit_snapshot(config, position, Decimal("0.237"), Decimal("0.005"))
    assert exit_decision["action"] == "atr_trailing_exit"
    assert exit_decision["trigger_price"] == Decimal("0.238")


def test_adaptive_exit_stop_is_monotonic_and_partial_is_idempotent() -> None:
    config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    position = bot.PositionState(
        symbol="TESTUSDT",
        quantity="10",
        initial_quantity="10",
        entry_price="100",
        highest_price="120",
        risk_per_unit="4",
        active_stop_price="112",
        partial_take_profit_done=True,
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    snapshot = bot.adaptive_exit_snapshot(config, position, Decimal("118"), Decimal("1"))
    assert snapshot["active_stop_price"] >= Decimal("112")
    assert snapshot["action"] != "partial_take_profit"

    lower_volatility_stop = bot.adaptive_exit_snapshot(config, position, Decimal("117"), Decimal("4"))
    assert lower_volatility_stop["active_stop_price"] >= Decimal("112")


def test_average_true_range_uses_completed_one_minute_bars() -> None:
    rows = [
        [0, "100", "103", "99", "102"],
        [1, "102", "106", "101", "105"],
        [2, "105", "108", "104", "107"],
    ]
    assert bot.average_true_range(rows, 2) == Decimal("4.5")
    assert bot.average_true_range(rows[:1], 2) is None


def test_kline_confirmation_ignores_the_open_candle() -> None:
    stable_rows = []
    for index in range(24):
        close = Decimal("100") + Decimal(index) / Decimal("23")
        stable_rows.append(
            [index, "100", "102", "99", str(close), "1", index, "1000"]
        )
    open_spike = [24, "100", "210", "99", "200", "1", 24, "100000"]

    class FakeMarket:
        def klines(self, _symbol, _interval, _limit):
            return stable_rows + [open_spike]

    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True)
    instance._market_client = lambda _market_type: FakeMarket()
    candidate = bot.TradeCandidate(
        symbol="ABCUSDT",
        base_asset="ABC",
        mention_count=0,
        price_change_percent=Decimal("5"),
        volatility_percent=Decimal("5"),
        quote_volume=Decimal("10000000"),
        last_price=Decimal("100"),
        market_type=bot.MARKET_FUTURES,
    )

    result = instance._kline_confirmation(candidate)

    assert result["passed"] is True
    assert Decimal(result["intervals"]["5m"]["close"]) == Decimal("101")
    assert Decimal(result["intervals"]["5m"]["roc_pct"]) == Decimal("1")


def test_overextended_entry_reasons_are_volatility_normalized() -> None:
    config = bot.BotConfig(
        api_key="",
        api_secret="",
        max_entry_roc_15m_pct=Decimal("12"),
        max_entry_roc_1h_pct=Decimal("20"),
        max_entry_extension_atr=Decimal("2.5"),
        max_entry_candle_range_atr=Decimal("2.5"),
    )
    snapshots = {
        "5m": {
            "roc_pct": Decimal("3"),
            "ema_distance_atr": Decimal("3"),
            "last_range_atr": Decimal("3"),
        },
        "15m": {"roc_pct": Decimal("13")},
        "1h": {"roc_pct": Decimal("21")},
    }

    reasons = bot.entry_overextension_reasons(config, snapshots)

    assert len(reasons) == 4
    assert any("15m ROC" in reason for reason in reasons)
    assert any("1h ROC" in reason for reason in reasons)
    assert any("EMA9" in reason for reason in reasons)
    assert any("candle range" in reason for reason in reasons)


def test_early_failure_exit_requires_time_price_and_peak_conditions() -> None:
    config = bot.BotConfig(
        api_key="",
        api_secret="",
        dry_run=True,
        early_failure_minutes=15,
        early_failure_min_r=Decimal("0.5"),
    )
    position = bot.PositionState(
        symbol="ABCUSDT",
        entry_price="100",
        highest_price="101",
        risk_per_unit="4",
        opened_at="2026-06-21T00:00:00+00:00",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    result = bot.early_failure_exit_snapshot(
        config,
        position,
        current_price=Decimal("99"),
        ema9=Decimal("99.5"),
        now=datetime(2026, 6, 21, 0, 16, tzinfo=timezone.utc),
    )

    assert result["action"] == "early_failure_exit"
    assert result["peak_r_multiple"] == Decimal("0.25")


def test_adaptive_exit_is_disabled_for_live_positions() -> None:
    config = bot.BotConfig(api_key="", api_secret="", dry_run=False, adaptive_exit_enabled=True)
    position = bot.PositionState(
        symbol="BTCUSDT",
        quantity="1",
        entry_price="100",
        highest_price="120",
        risk_per_unit="4",
        active_stop_price="110",
        partial_take_profit_done=True,
        market_type=bot.MARKET_FUTURES,
        position_mode="futures-live",
    )
    assert bot.adaptive_exit_snapshot(config, position, Decimal("100"), Decimal("1"))["action"] == "disabled"


def test_adaptive_position_management_executes_partial_only_once() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(
        api_key="",
        api_secret="",
        dry_run=True,
        adaptive_exit_enabled=True,
        partial_take_profit_fraction=Decimal("0.5"),
    )
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        quantity="100",
        initial_quantity="100",
        entry_price="100",
        highest_price="100",
        risk_per_unit="4",
        active_stop_price="96",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    instance.state = bot.BotState(position=position, positions=[position])
    closes: list[dict[str, object]] = []
    instance._touch_state = lambda: None

    def close_position(
        managed_position,
        last_price,
        dry_action,
        live_action,
        exit_label,
        close_quantity=None,
        trigger_price=None,
        decision_metadata=None,
    ):
        closes.append(
            {
                "action": dry_action,
                "quantity": close_quantity,
                "trigger_price": trigger_price,
                "metadata": decision_metadata,
            }
        )
        return True

    instance._close_position = close_position
    instance._manage_adaptive_dry_run_position(position, Decimal("120"), Decimal("1"))
    instance._manage_adaptive_dry_run_position(position, Decimal("121"), Decimal("1"))

    assert position.partial_take_profit_done is True
    assert position.exit_stage == "atr_trailing"
    assert len(closes) == 1
    assert closes[0]["action"] == "DRY_RUN_PARTIAL_TAKE_PROFIT"
    assert closes[0]["quantity"] == Decimal("50")
    assert closes[0]["trigger_price"] == Decimal("108")


def test_adaptive_partial_flag_is_set_before_close_persists_state() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        quantity="100",
        initial_quantity="100",
        entry_price="100",
        highest_price="108",
        risk_per_unit="4",
        active_stop_price="96",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    instance.state = bot.BotState(position=position, positions=[position])
    instance._touch_state = lambda: None
    close_state: dict[str, object] = {}

    def close_position(*_args, **_kwargs):
        close_state["partial_take_profit_done"] = position.partial_take_profit_done
        close_state["exit_stage"] = position.exit_stage
        return True

    instance._close_position = close_position
    instance._manage_adaptive_dry_run_position(position, Decimal("108"), Decimal("1"))

    assert close_state == {
        "partial_take_profit_done": True,
        "exit_stage": "atr_trailing",
    }


def test_adaptive_partial_close_updates_quantity_margin_fees_and_realized_pnl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
        instance.config = bot.BotConfig(
            api_key="",
            api_secret="",
            dry_run=True,
            state_file=str(Path(tmp) / "state.json"),
            trade_journal_file=str(Path(tmp) / "journal.sqlite3"),
            fee_rate_pct=Decimal("0.1"),
            slippage_pct=Decimal("0"),
        )
        position = bot.PositionState(
            symbol="ESPORTSUSDT",
            base_asset="ESPORTS",
            quantity="100",
            initial_quantity="100",
            entry_price="100",
            highest_price="120",
            quote_spent="50",
            margin_quote="50",
            notional_quote="10000",
            leverage_multiplier="5",
            market_type=bot.MARKET_FUTURES,
            position_mode="contract-sim",
        )
        instance.state = bot.BotState(position=position, positions=[position])
        instance._state_lock = None
        instance.notifier = None
        instance._safe_sell_quantity = lambda _symbol, _base, wanted, _market: wanted
        instance._sell_order_error = lambda *_args: None

        closed = instance._close_position(
            position,
            Decimal("110"),
            "DRY_RUN_PARTIAL_TAKE_PROFIT",
            "TAKE_PROFIT_SELL",
            "adaptive partial take profit",
            close_quantity=Decimal("50"),
            trigger_price=Decimal("108"),
            decision_metadata={"exit_reason": "partial_take_profit", "r_multiple": Decimal("2.5")},
        )

        assert closed is True
        assert Decimal(position.quantity) == Decimal("50")
        assert Decimal(position.margin_quote) == Decimal("25")
        assert Decimal(position.realized_pnl) == Decimal("494.5")
        event = instance.state.trade_log[-1]
        assert event["fee_amount"] == "5.5"
        assert event["quote_amount"] == "519.5"
        assert event["trigger_price"] == "108"
        assert event["exit_reason"] == "partial_take_profit"


def test_adaptive_gap_exit_records_trigger_separately_from_fill() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        quantity="50",
        initial_quantity="100",
        entry_price="100",
        highest_price="120",
        risk_per_unit="4",
        active_stop_price="115",
        exit_stage="atr_trailing",
        partial_take_profit_done=True,
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    instance.state = bot.BotState(position=position, positions=[position])
    captured: dict[str, object] = {}
    instance._touch_state = lambda: None

    def close_position(
        managed_position,
        last_price,
        dry_action,
        live_action,
        exit_label,
        close_quantity=None,
        trigger_price=None,
        decision_metadata=None,
    ):
        captured.update(
            action=dry_action,
            last_price=last_price,
            trigger_price=trigger_price,
            metadata=decision_metadata,
        )
        return True

    instance._close_position = close_position
    instance._manage_adaptive_dry_run_position(position, Decimal("110"), Decimal("2"))

    assert captured["action"] == "DRY_RUN_GAP_EXIT"
    assert captured["last_price"] == Decimal("110")
    assert captured["trigger_price"] == Decimal("115")
    assert captured["metadata"]["exit_reason"] == "gap_exit"


def test_small_stop_cross_keeps_the_strategy_exit_reason() -> None:
    config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        quantity="50",
        entry_price="100",
        highest_price="120",
        risk_per_unit="4",
        active_stop_price="115",
        exit_stage="atr_trailing",
        partial_take_profit_done=True,
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    decision = bot.adaptive_exit_snapshot(config, position, Decimal("114.9"), Decimal("2"))

    assert decision["action"] == "atr_trailing_exit"
    assert decision["trigger_price"] == Decimal("115")


def test_adaptive_trade_events_preserve_exit_reason_and_trigger_price() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        journal_path = str(Path(tmp) / "adaptive.sqlite3")
        buy = {
            "ts": "2026-06-18T00:00:00+00:00",
            "action": "BUY",
            "symbol": "ESPORTSUSDT",
            "quantity": "100",
            "price": "100",
            "quote_amount": "100",
            "dry_run": True,
            "market_type": bot.MARKET_FUTURES,
            "position_mode": "contract-sim",
        }
        partial = {
            "ts": "2026-06-18T00:10:00+00:00",
            "action": "DRY_RUN_PARTIAL_TAKE_PROFIT",
            "symbol": "ESPORTSUSDT",
            "quantity": "50",
            "price": "108.5",
            "trigger_price": "108",
            "quote_amount": "54.25",
            "exit_reason": "partial_take_profit",
            "r_multiple": "2.125",
            "atr_value": "1",
            "dry_run": True,
            "market_type": bot.MARKET_FUTURES,
            "position_mode": "contract-sim",
        }
        bot.insert_trade_event(journal_path, buy)
        bot.insert_trade_event(journal_path, partial)

        events = bot.query_trade_journal(journal_path, "events", 10, 0)["items"]
        saved = next(item for item in events if item["action"] == "DRY_RUN_PARTIAL_TAKE_PROFIT")
        assert saved["trigger_price"] == "108"
        assert saved["exit_reason"] == "partial_take_profit"
        assert saved["r_multiple"] == "2.125"
        rounds = bot.query_trade_journal(journal_path, "round_trips", 10, 0)["items"]
        assert rounds[0]["exit_reason"] == "DRY_RUN_PARTIAL_TAKE_PROFIT"
        assert rounds[0]["quantity"] == "50"


def test_complete_trade_lifecycle_groups_partial_exit_and_excludes_synthetic_events() -> None:
    events = [
        {
            "id": 1,
            "ts": "2026-06-21T00:00:00+00:00",
            "action": "BUY",
            "symbol": "ABCUSDT",
            "trade_id": "trade-abc",
            "strategy_version": "adaptive-v2",
            "quantity": "100",
            "price": "1",
            "quote_amount": "50",
            "fee_amount": "0.5",
            "market_type": bot.MARKET_FUTURES,
            "position_mode": "contract-sim",
            "dry_run": 1,
        },
        {
            "id": 2,
            "ts": "2026-06-21T00:20:00+00:00",
            "action": "DRY_RUN_PARTIAL_TAKE_PROFIT",
            "symbol": "ABCUSDT",
            "trade_id": "trade-abc",
            "quantity": "50",
            "price": "1.2",
            "quote_amount": "35",
            "fee_amount": "0.1",
            "market_type": bot.MARKET_FUTURES,
            "position_mode": "contract-sim",
            "dry_run": 1,
        },
        {
            "id": 3,
            "ts": "2026-06-21T01:00:00+00:00",
            "action": "DRY_RUN_ATR_TRAILING_EXIT",
            "symbol": "ABCUSDT",
            "trade_id": "trade-abc",
            "quantity": "50",
            "price": "1.1",
            "quote_amount": "30",
            "fee_amount": "0.1",
            "market_type": bot.MARKET_FUTURES,
            "position_mode": "contract-sim",
            "dry_run": 1,
        },
        {
            "id": 4,
            "ts": "2026-06-21T02:00:00+00:00",
            "action": "BUY",
            "symbol": "BTCUSDT",
            "quantity": "1",
            "price": "100",
            "quote_amount": "100",
            "market_type": "",
            "position_mode": "",
            "is_synthetic": 1,
            "dry_run": 1,
        },
        {
            "id": 5,
            "ts": "2026-06-21T02:00:01+00:00",
            "action": "SELL",
            "symbol": "BTCUSDT",
            "quantity": "1",
            "price": "90",
            "quote_amount": "90",
            "market_type": "",
            "position_mode": "",
            "is_synthetic": 1,
            "dry_run": 1,
        },
    ]

    trades = bot.build_complete_trades_from_events(events)

    assert len(trades) == 1
    assert trades[0]["trade_id"] == "trade-abc"
    assert trades[0]["exit_reason"] == "DRY_RUN_ATR_TRAILING_EXIT"
    assert trades[0]["partial_exit_count"] == 1
    assert trades[0]["pnl"] == "15"
    assert trades[0]["strategy_version"] == "adaptive-v2"


def test_complete_trade_lifecycle_ignores_legacy_dust_from_previous_trade() -> None:
    events = [
        {"id": 1, "ts": "2026-06-21T00:00:00+00:00", "action": "BUY", "symbol": "ABCUSDT", "quantity": "100", "price": "1", "quote_amount": "50"},
        {"id": 2, "ts": "2026-06-21T00:10:00+00:00", "action": "DRY_RUN_HARD_STOP", "symbol": "ABCUSDT", "quantity": "99.5", "price": "0.9", "quote_amount": "44"},
        {"id": 3, "ts": "2026-06-21T00:20:00+00:00", "action": "BUY", "symbol": "ABCUSDT", "quantity": "100", "price": "1", "quote_amount": "50"},
        {"id": 4, "ts": "2026-06-21T00:21:00+00:00", "action": "DRY_RUN_HARD_STOP", "symbol": "ABCUSDT", "quantity": "0.5", "price": "0.9", "quote_amount": "0.2"},
        {"id": 5, "ts": "2026-06-21T01:00:00+00:00", "action": "DRY_RUN_ATR_TRAILING_EXIT", "symbol": "ABCUSDT", "quantity": "100", "price": "1.2", "quote_amount": "60"},
    ]

    trades = bot.build_complete_trades_from_events(events)

    assert len(trades) == 2
    assert trades[1]["exit_event_id"] == 5
    assert trades[1]["pnl"] == "10"


def test_dry_run_full_close_uses_complete_local_quantity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
        instance.config = bot.BotConfig(
            api_key="",
            api_secret="",
            dry_run=True,
            state_file=str(Path(tmp) / "state.json"),
            trade_journal_file="",
            fee_rate_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        position = bot.PositionState(
            symbol="ABCUSDT",
            base_asset="ABC",
            quantity="100.75",
            entry_price="1",
            quote_spent="50",
            margin_quote="50",
            market_type=bot.MARKET_FUTURES,
            position_mode="contract-sim",
            trade_id="trade-abc",
        )
        instance.state = bot.BotState(position=position, positions=[position])
        instance._state_lock = None
        instance.notifier = None
        instance._safe_sell_quantity = lambda *_args: Decimal("100")
        instance._sell_order_error = lambda *_args: None

        assert instance._close_position(
            position,
            Decimal("1.1"),
            "DRY_RUN_ATR_TRAILING_EXIT",
            "STOP_SELL",
            "test full close",
        )

        assert instance.state.positions == []
        assert instance.state.trade_log[-1]["quantity"] == "100.75"


def test_mark_price_cache_parses_stream_and_rest_fallback_is_rate_limited() -> None:
    cache = bot.MarkPriceCache()
    bot.update_mark_price_cache_from_message(
        cache,
        json.dumps(
            [
                {"s": "ESPORTSUSDT", "p": "0.250"},
                {"s": "BTCUSDT", "p": "65000"},
            ]
        ),
        received_monotonic=100,
    )
    assert cache.snapshot("ESPORTSUSDT", now_monotonic=101)["price"] == Decimal("0.250")
    assert cache.snapshot("ESPORTSUSDT", now_monotonic=101)["age_seconds"] == 1

    class FakeFutures:
        def __init__(self) -> None:
            self.calls = 0

        def ticker_price(self, _symbol: str) -> Decimal:
            self.calls += 1
            return Decimal("0.240")

    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True)
    instance.futures_client = FakeFutures()
    instance.spot_client = FakeFutures()
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )

    fresh = instance._risk_price_for_position(position, cache, now_monotonic=104)
    assert fresh == (Decimal("0.250"), "websocket", Decimal("4"))
    fallback = instance._risk_price_for_position(position, cache, now_monotonic=106)
    assert fallback == (Decimal("0.240"), "rest", Decimal("0"))
    cached_rest = instance._risk_price_for_position(position, cache, now_monotonic=108)
    assert cached_rest == (Decimal("0.240"), "rest-cache", Decimal("2"))
    assert instance.futures_client.calls == 1


def test_stale_market_data_does_not_fabricate_an_exit() -> None:
    class BrokenFutures:
        def ticker_price(self, _symbol: str) -> Decimal:
            raise bot.BinanceAPIError("offline")

    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True)
    instance.futures_client = BrokenFutures()
    instance.spot_client = BrokenFutures()
    position = bot.PositionState(
        symbol="ESPORTSUSDT",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    cache = bot.MarkPriceCache()
    cache.update("ESPORTSUSDT", Decimal("0.250"), received_monotonic=100)

    assert instance._risk_price_for_position(position, cache, now_monotonic=116) == (
        None,
        "stale",
        Decimal("16"),
    )


def test_risk_monitor_manages_each_position_with_fresh_prices() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    first = bot.PositionState(
        symbol="AAAUSDT",
        entry_price="100",
        quantity="1",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    second = bot.PositionState(
        symbol="BBBUSDT",
        entry_price="10",
        quantity="2",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    instance.state = bot.BotState(position=first, positions=[first, second])
    cache = bot.MarkPriceCache()
    cache.update("AAAUSDT", Decimal("101"), received_monotonic=100)
    cache.update("BBBUSDT", Decimal("11"), received_monotonic=100)
    managed: list[tuple[str, Decimal]] = []
    instance._latest_position_atr = lambda _position: Decimal("1")
    instance._manage_adaptive_dry_run_position = (
        lambda position, price, _atr: managed.append((position.symbol, price))
    )

    instance.monitor_open_positions_once(cache, now_monotonic=101)
    assert managed == [("AAAUSDT", Decimal("101")), ("BBBUSDT", Decimal("11"))]


def test_risk_monitor_fetches_atr_only_for_trailing_positions() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    initial = bot.PositionState(
        symbol="AAAUSDT",
        entry_price="100",
        quantity="1",
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    trailing = bot.PositionState(
        symbol="BBBUSDT",
        entry_price="10",
        quantity="2",
        partial_take_profit_done=True,
        market_type=bot.MARKET_FUTURES,
        position_mode="contract-sim",
    )
    instance.state = bot.BotState(position=initial, positions=[initial, trailing])
    cache = bot.MarkPriceCache()
    cache.update("AAAUSDT", Decimal("101"), received_monotonic=100)
    cache.update("BBBUSDT", Decimal("11"), received_monotonic=100)
    atr_requests: list[str] = []
    managed: list[tuple[str, Decimal | None]] = []
    instance._latest_position_atr = lambda position: (
        atr_requests.append(position.symbol) or Decimal("1")
    )
    instance._manage_adaptive_dry_run_position = (
        lambda position, _price, atr: managed.append((position.symbol, atr))
    )

    instance.monitor_open_positions_once(cache, now_monotonic=101)

    assert atr_requests == ["BBBUSDT"]
    assert managed == [("AAAUSDT", None), ("BBBUSDT", Decimal("1"))]


def test_run_once_uses_serialized_state_persistence() -> None:
    instance = bot.LongOnlyMomentumBot.__new__(bot.LongOnlyMomentumBot)
    instance.config = bot.BotConfig(api_key="", api_secret="", dry_run=True)
    instance.state = bot.BotState()
    instance.external_risk_monitor_active = True
    instance.last_signal_record = None
    instance.notifier = None
    instance.spot_client = type("Client", (), {"sync_time": lambda self: None})()
    instance.futures_client = type("Client", (), {"sync_time": lambda self: None})()
    instance._ensure_live_account_safety = lambda: None
    instance._recover_pending_order = lambda: None
    instance._sync_open_position_with_account = lambda: None
    instance._scan_and_enter = lambda: None
    instance._sync_square_feed_state = lambda: None
    persisted: list[bool] = []
    instance._touch_state = lambda: persisted.append(True)
    original_save_state = bot.save_state
    bot.save_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("run_once bypassed serialized persistence")
    )
    try:
        instance.run_once()
    finally:
        bot.save_state = original_save_state

    assert persisted == [True]


def test_dashboard_loop_failure_stops_the_risk_monitor() -> None:
    class FakeFeed:
        def __init__(self, _cache):
            pass

        def start(self):
            return True

        def stop(self):
            pass

    class FakeBot:
        def __init__(self, _config):
            self.external_risk_monitor_active = False

        def run_once(self):
            raise RuntimeError("scan failed")

    class FakeModule:
        MarkPriceCache = dict
        MarkPriceWebSocketFeed = FakeFeed
        LongOnlyMomentumBot = FakeBot

    config = type(
        "Config",
        (),
        {
            "dry_run": True,
            "adaptive_exit_enabled": True,
            "risk_monitor_interval_seconds": 1,
            "poll_seconds": 300,
        },
    )()
    runner = web.BotRunner(web.MemoryLogHandler())
    runner.running = True
    observed_stop_state: list[bool] = []

    def risk_worker(_bot, _cache, _config):
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline and not runner.stop_event.is_set():
            time.sleep(0.01)
        observed_stop_state.append(runner.stop_event.is_set())

    runner._risk_monitor_worker = risk_worker
    original_bot_module = web.bot_module
    original_logger_disabled = web.LOGGER.disabled
    web.bot_module = lambda: FakeModule
    web.LOGGER.disabled = True
    try:
        runner._loop_worker(config)
    finally:
        web.bot_module = original_bot_module
        web.LOGGER.disabled = original_logger_disabled

    assert observed_stop_state == [True]


def test_dashboard_snapshot_exposes_adaptive_exit_state() -> None:
    class FakeRunner:
        def ticker_price_for_status(self, _config, _symbol, _market_type="spot"):
            return Decimal("118"), ""

    cfg = bot.BotConfig(api_key="", api_secret="", dry_run=True, adaptive_exit_enabled=True)
    position = {
        "symbol": "ESPORTSUSDT",
        "base_asset": "ESPORTS",
        "quantity": "50",
        "initial_quantity": "100",
        "entry_price": "100",
        "highest_price": "120",
        "quote_spent": "50",
        "margin_quote": "25",
        "notional_quote": "5000",
        "leverage_multiplier": "5",
        "market_type": bot.MARKET_FUTURES,
        "position_mode": "contract-sim",
        "trade_id": "trade-1",
        "risk_per_unit": "4",
        "active_stop_price": "115",
        "exit_stage": "atr_trailing",
        "partial_take_profit_done": True,
        "atr_value": "1",
        "realized_pnl": "20",
        "last_market_price_at": bot.utc_now(),
    }
    snapshot = web.build_position_snapshot(
        position,
        {"completed_round_trips": 0, "trade_log": []},
        cfg,
        FakeRunner(),
    )
    assert snapshot is not None
    assert snapshot["trade_id"] == "trade-1"
    assert snapshot["r_multiple"] == Decimal("4.5")
    assert snapshot["adaptive_active_stop_price"] == Decimal("117")
    assert snapshot["exit_stage"] == "atr_trailing"
    assert snapshot["partial_take_profit_done"] is True
    assert snapshot["peak_drawdown_pct"] == Decimal("1.666666666666666666666666667")


def test_dashboard_position_snapshot_marks_liquidation_risk() -> None:
    class FakeRunner:
        def ticker_price_for_status(self, _config, _symbol, _market_type="spot"):
            return Decimal("94"), ""

    cfg = bot.BotConfig(api_key="", api_secret="", leverage_multiplier=Decimal("20"))
    state = {"trade_log": [{"symbol": "ABCUSDT", "action": "BUY", "dry_run": True}]}
    position = {
        "symbol": "ABCUSDT",
        "base_asset": "ABC",
        "quantity": "200",
        "entry_price": "100",
        "highest_price": "100",
        "quote_spent": "1000",
        "margin_quote": "1000",
        "notional_quote": "20000",
        "leverage_multiplier": "20",
        "market_type": bot.MARKET_FUTURES,
        "position_mode": "contract-sim",
    }

    snapshot = web.build_position_snapshot(position, state, cfg, FakeRunner())

    assert snapshot["liquidation_price"] == Decimal("95.00")
    assert snapshot["liquidation_triggered"] is True
    assert snapshot["liquidation_distance_pct"] < Decimal("0")
    assert snapshot["equity_at_risk"] == Decimal("1000")


def test_account_risk_guards() -> None:
    candidate = bot.TradeCandidate(
        symbol="BTCUSDT",
        base_asset="BTC",
        mention_count=1,
        price_change_percent=Decimal("5"),
        volatility_percent=Decimal("5"),
        quote_volume=Decimal("10000000"),
        last_price=Decimal("100"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        state_path = str(Path(tmp) / "state.json")

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            max_total_exposure_pct=Decimal("50"),
            max_symbol_exposure_pct=Decimal("0"),
            risk_per_trade_pct=Decimal("0"),
            order_quote_amount=Decimal("100"),
            max_open_positions=1,
        )
        reason = bot.LongOnlyMomentumBot(cfg)._account_risk_guard_reason(candidate)
        assert reason and "total exposure" in reason

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            max_total_exposure_pct=Decimal("0"),
            max_symbol_exposure_pct=Decimal("50"),
            risk_per_trade_pct=Decimal("0"),
            order_quote_amount=Decimal("100"),
            max_open_positions=1,
        )
        reason = bot.LongOnlyMomentumBot(cfg)._account_risk_guard_reason(candidate)
        assert reason and "BTCUSDT exposure" in reason

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            max_total_exposure_pct=Decimal("0"),
            max_symbol_exposure_pct=Decimal("0"),
            max_consecutive_losses=2,
        )
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.state.trade_log = [
            {"action": "BUY", "symbol": "LOSSAUSDT", "quantity": "1", "price": "100", "quote_amount": "100", "ts": bot.utc_now()},
            {"action": "SELL", "symbol": "LOSSAUSDT", "quantity": "1", "price": "90", "quote_amount": "90", "ts": bot.utc_now()},
            {"action": "BUY", "symbol": "LOSSBUSDT", "quantity": "1", "price": "100", "quote_amount": "100", "ts": bot.utc_now()},
            {"action": "SELL", "symbol": "LOSSBUSDT", "quantity": "1", "price": "95", "quote_amount": "95", "ts": bot.utc_now()},
        ]
        reason = instance._account_risk_guard_reason(candidate)
        assert reason and "consecutive losses" in reason

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            max_intraday_drawdown_pct=Decimal("5"),
            max_total_exposure_pct=Decimal("0"),
            max_symbol_exposure_pct=Decimal("0"),
            order_quote_amount=Decimal("100"),
            max_open_positions=1,
        )
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.state.positions = [bot.PositionState(symbol="BTCUSDT", base_asset="BTC", quantity="1", entry_price="100", quote_spent="100")]
        instance.state.position = instance.state.positions[0]
        instance.client.ticker_price = lambda _symbol: Decimal("90")
        reason = instance._account_risk_guard_reason(candidate)
        assert reason and "intraday drawdown" in reason

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            risk_per_trade_pct=Decimal("1"),
            initial_stop_loss_pct=Decimal("4"),
            order_quote_amount=Decimal("100"),
            max_open_positions=1,
        )
        snapshot = bot.LongOnlyMomentumBot(cfg)._account_risk_snapshot(candidate)
        assert Decimal(str(snapshot["fixed_order_quote"])) == Decimal("100")
        assert Decimal(str(snapshot["risk_based_quote_suggestion"])) > Decimal("0")

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=state_path,
            trade_journal_file="",
            dry_run_initial_equity_usdt=Decimal("1000"),
            order_quote_amount=Decimal("600"),
            leverage_multiplier=Decimal("10"),
            risk_per_trade_pct=Decimal("0"),
            max_open_positions=3,
        )
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.state.positions = [
            bot.PositionState(
                symbol="ETHUSDT",
                base_asset="ETH",
                quantity="50",
                entry_price="100",
                quote_spent="500",
                margin_quote="500",
                notional_quote="5000",
                leverage_multiplier="10",
                market_type=bot.MARKET_FUTURES,
                position_mode="contract-sim",
            )
        ]
        futures_candidate = bot.TradeCandidate(
            symbol="BTCUSDT",
            base_asset="BTC",
            mention_count=1,
            price_change_percent=Decimal("5"),
            volatility_percent=Decimal("5"),
            quote_volume=Decimal("10000000"),
            last_price=Decimal("100"),
            market_type=bot.MARKET_FUTURES,
        )
        reason = instance._account_risk_guard_reason(futures_candidate)
        assert reason and "dry-run simulated equity" in reason
        snapshot = instance.state.account_risk_snapshot
        assert snapshot["dry_run_max_notional_quote"] == "10000"
        assert snapshot["proposed_notional_quote"] == "6000"
        assert snapshot["available_notional_quote"] == "5000"

        cfg = bot.BotConfig(
            api_key="",
            api_secret="",
            state_file=str(Path(tmp) / "empty_state.json"),
            trade_journal_file="",
            dry_run_initial_equity_usdt=Decimal("1000"),
            order_quote_amount=Decimal("1000"),
            leverage_multiplier=Decimal("3"),
            risk_per_trade_pct=Decimal("0"),
        )
        snapshot = bot.LongOnlyMomentumBot(cfg)._account_risk_snapshot(futures_candidate)
        assert snapshot["dry_run_max_notional_quote"] == "3000"
        assert snapshot["proposed_notional_quote"] == "3000"
        assert snapshot["available_notional_quote"] == "3000"

        parsed = web.config_from_payload({"order_quote_amount": "250", "max_open_positions": "4"})
        assert parsed.dry_run_initial_equity_usdt == Decimal("1000")
        parsed = web.config_from_payload({"dry_run_initial_equity_usdt": "2500"})
        assert parsed.dry_run_initial_equity_usdt == Decimal("2500")


def test_b_route_risk_sizing_and_default_limits() -> None:
    sizing = bot.risk_based_order_size(
        equity=Decimal("1000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("96"),
        leverage=Decimal("5"),
        risk_pct=Decimal("0.75"),
        max_margin_quote=Decimal("50"),
    )
    assert sizing["risk_budget"] == Decimal("7.5")
    assert sizing["quantity"] == Decimal("1.875")
    assert sizing["notional_quote"] == Decimal("187.5")
    assert sizing["margin_quote"] == Decimal("37.5")

    defaults = bot.BotConfig(api_key="", api_secret="")
    assert defaults.max_open_positions == 4
    assert defaults.max_daily_trades == 12
    assert defaults.max_daily_loss_pct == Decimal("2")
    assert defaults.max_total_exposure_pct == Decimal("100")
    assert defaults.max_symbol_exposure_pct == Decimal("25")
    assert defaults.max_consecutive_losses == 3
    assert defaults.consecutive_loss_pause_minutes == 240
    assert defaults.risk_per_trade_pct == Decimal("0.75")
    assert defaults.cooldown_minutes == 60


def test_consecutive_loss_pause_expires() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    trades = [
        {"pnl": "-1", "exit_time": "2026-06-21T08:00:00+00:00"},
        {"pnl": "-1", "exit_time": "2026-06-21T08:30:00+00:00"},
        {"pnl": "-1", "exit_time": "2026-06-21T09:00:00+00:00"},
    ]
    assert bot.consecutive_loss_pause_until(trades, 3, 240, now=now) == datetime(
        2026, 6, 21, 13, 0, tzinfo=timezone.utc
    )
    assert bot.consecutive_loss_pause_until(
        trades,
        3,
        240,
        now=datetime(2026, 6, 21, 13, 1, tzinfo=timezone.utc),
    ) is None


def test_signal_recording_and_analysis() -> None:
    candidate = bot.TradeCandidate(
        symbol="BTCUSDT",
        base_asset="BTC",
        mention_count=2,
        price_change_percent=Decimal("6"),
        volatility_percent=Decimal("7"),
        quote_volume=Decimal("10000000"),
        last_price=Decimal("100"),
        market_score=Decimal("10"),
        square_score=Decimal("5"),
        combined_score=Decimal("15"),
    )
    post = bot.SquarePost(
        title="BTC breakout",
        text="BTC long setup with volume",
        post_id="p1",
        author="analyst",
        created_at="2026-06-08T00:00:00+00:00",
    )
    with tempfile.TemporaryDirectory() as tmp:
        record_path = Path(tmp) / "signal_records.jsonl"
        state_path = Path(tmp) / "bot_state.json"
        state_path.write_text('{"positions": [{"symbol": "BTCUSDT"}]}', encoding="utf-8")
        cfg = bot.BotConfig(
            api_key="secret-key",
            api_secret="secret-value",
            telegram_bot_token="telegram-secret",
            telegram_chat_id="chat-secret",
            signal_record_file=str(record_path),
            state_file=str(state_path),
        )
        record = bot.build_signal_record(
            cfg,
            source="preview",
            posts=[post],
            candidates=[candidate],
            candidate=candidate,
            entry_confirmation={"passed": False, "reason": "15m ROC is not positive", "checks": {"kline": {"passed": False}}},
            square_confidence={"score": "20"},
            account_risk_snapshot={"entry_blocked": False},
            final_action="skipped",
            note="test",
        )
        record["api_key"] = cfg.api_key
        record["api_secret"] = cfg.api_secret
        record["telegram_bot_token"] = cfg.telegram_bot_token
        bot.append_signal_record(str(record_path), record)
        text = record_path.read_text(encoding="utf-8")
        assert "secret-key" not in text
        assert "secret-value" not in text
        assert "telegram-secret" not in text

        loaded = json.loads(text)
        assert loaded["candidate"]["symbol"] == "BTCUSDT"
        summary = analyze_signal_records.summarize([loaded])
        assert summary["record_count"] == 1
        assert summary["skipped_count"] == 1
        assert summary["square_low_confidence_count"] == 1
        assert summary["kline_block_count"] == 1
        assert summary["decision_groups"]["square_low_confidence"] == 1

        class FakeClient:
            def klines(self, symbol: str, interval: str, limit: int, start_time: int | None = None):
                assert symbol == "BTCUSDT"
                assert start_time is not None
                return [[start_time, "100", "102", "99", "101", "1"]]

        before_state = state_path.read_text(encoding="utf-8")
        stats = bot.update_signal_record_future_returns(cfg, client=FakeClient())
        after_state = state_path.read_text(encoding="utf-8")
        assert before_state == after_state
        assert stats["updated_count"] == 1
        updated = json.loads(record_path.read_text(encoding="utf-8"))
        assert updated["future_returns"]["5m"]["return_pct"] == "1"

        csv_path = Path(tmp) / "records.csv"
        analyze_signal_records.write_csv([updated], csv_path)
        csv_text = csv_path.read_text(encoding="utf-8")
        assert "recorded_at,source,symbol,decision_group" in csv_text
        assert "secret-key" not in csv_text
        assert "telegram-secret" not in csv_text

        entered_record = dict(updated)
        entered_record["entered"] = True
        entered_record["final_action"] = "entered"
        entered_record["entry_confirmation"] = {"passed": True, "reason": "entry confirmation passed", "checks": {}}
        entered_record["future_returns"] = {"1h": {"return_pct": "-2"}}
        multi_summary = analyze_signal_records.summarize([updated, entered_record])
        assert multi_summary["entered_count"] == 1
        assert multi_summary["skipped_count"] == 1
        assert multi_summary["future_returns_by_decision"]["entered"]["1h"]["count"] == 1

        before_state = state_path.read_text(encoding="utf-8")
        replay = replay_signal_records.replay([updated, entered_record], "1h")
        after_state = state_path.read_text(encoding="utf-8")
        assert before_state == after_state
        assert replay["trade_count"] == 1
        assert replay["max_consecutive_losses"] == 1
        assert replay["group_opportunity"]["square_low_confidence"]["missed_upside_count"] == 1

        empty_walk = walk_forward_signal_records.walk_forward([])
        assert empty_walk["record_count"] == 0
        assert empty_walk["split_count"] == {"train": 0, "validation": 0, "test": 0}

        records = []
        for index in range(5):
            item = dict(updated if index % 2 == 0 else entered_record)
            item["recorded_at"] = f"2026-06-08T00:0{index}:00+00:00"
            item["api_secret"] = "should-not-appear"
            records.append(item)
        before_state = state_path.read_text(encoding="utf-8")
        walk = walk_forward_signal_records.walk_forward(records)
        after_state = state_path.read_text(encoding="utf-8")
        assert before_state == after_state
        assert walk["split_count"] == {"train": 3, "validation": 1, "test": 1}
        assert walk["phases"]["train"]["record_count"] == 3
        assert "should-not-appear" not in json.dumps(walk)


def test_future_return_updater_routes_futures_candidates_to_futures_client() -> None:
    class FakeClient:
        def __init__(self, close_price: str) -> None:
            self.close_price = close_price
            self.calls: list[str] = []

        def klines(self, symbol, _interval, _limit, start_time=None):
            self.calls.append(symbol)
            return [[start_time or 0, "0", "0", "0", self.close_price]]

    spot = FakeClient("90")
    futures = FakeClient("110")
    record = {
        "recorded_at": "2026-06-20T00:00:00+00:00",
        "candidate": {
            "symbol": "ABCUSDT",
            "last_price": "100",
            "market_type": bot.MARKET_FUTURES,
        },
    }

    assert bot.update_record_future_returns(record, spot, futures) is True
    assert spot.calls == []
    assert futures.calls == ["ABCUSDT"] * 4
    assert record["future_returns"]["1h"]["return_pct"] == "10"


def test_trade_journal_migration_stats_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        journal_path = str(Path(tmp) / "trade_journal.sqlite3")
        trade_log = [
            {
                "ts": "2026-06-10T00:00:00+00:00",
                "action": "BUY",
                "symbol": "ABCUSDT",
                "quantity": "10",
                "price": "5",
                "quote_amount": "50",
                "dry_run": True,
                "market_type": bot.MARKET_FUTURES,
                "position_mode": "contract-sim",
            },
            {
                "ts": "2026-06-10T00:10:00+00:00",
                "action": "DRY_RUN_STOP_SELL",
                "symbol": "ABCUSDT",
                "quantity": "10",
                "price": "4.8",
                "quote_amount": "48",
                "dry_run": True,
                "market_type": bot.MARKET_FUTURES,
                "position_mode": "contract-sim",
            },
        ]
        bot.migrate_trade_log_to_journal(journal_path, trade_log)
        stats = bot.trade_journal_stats(journal_path, "USDT")
        assert stats is not None
        assert stats["trade_count"] == 1
        assert stats["event_count"] == 2
        assert str(stats["total_pnl"]) == "-2"

        rounds = bot.query_trade_journal(journal_path, "round_trips", 10, 0)
        assert rounds["total"] == 1
        assert rounds["items"][0]["symbol"] == "ABCUSDT"
        assert rounds["items"][0]["market_type"] == bot.MARKET_FUTURES
        assert rounds["items"][0]["exit_reason"] == "DRY_RUN_STOP_SELL"

        events = bot.query_trade_journal(journal_path, "events", 1, 0)
        assert events["total"] == 2
        assert len(events["items"]) == 1

        manual_log = [
            {
                "ts": "2026-06-10T01:00:00+00:00",
                "action": "BUY",
                "symbol": "XYZUSDT",
                "quantity": "5",
                "price": "10",
                "quote_amount": "50",
                "dry_run": True,
            },
            {
                "ts": "2026-06-10T01:05:00+00:00",
                "action": "DRY_RUN_MANUAL_SELL",
                "symbol": "XYZUSDT",
                "quantity": "5",
                "price": "11",
                "quote_amount": "55",
                "dry_run": True,
            },
        ]
        bot.migrate_trade_log_to_journal(journal_path, manual_log)
        stats = bot.trade_journal_stats(journal_path, "USDT")
        assert stats is not None
        assert stats["trade_count"] == 2
        rounds = bot.query_trade_journal(journal_path, "round_trips", 10, 0)
        assert any(item["exit_reason"] == "DRY_RUN_MANUAL_SELL" for item in rounds["items"])

        liquidation_log = [
            {
                "ts": "2026-06-10T02:00:00+00:00",
                "action": "BUY",
                "symbol": "LIQUSDT",
                "quantity": "2",
                "price": "100",
                "quote_amount": "50",
                "dry_run": True,
                "market_type": bot.MARKET_FUTURES,
                "position_mode": "contract-sim",
            },
            {
                "ts": "2026-06-10T02:03:00+00:00",
                "action": "DRY_RUN_LIQUIDATION",
                "symbol": "LIQUSDT",
                "quantity": "2",
                "price": "95",
                "quote_amount": "0",
                "dry_run": True,
                "market_type": bot.MARKET_FUTURES,
                "position_mode": "contract-sim",
            },
        ]
        bot.migrate_trade_log_to_journal(journal_path, liquidation_log)
        rounds = bot.query_trade_journal(journal_path, "round_trips", 10, 0)
        assert any(item["symbol"] == "LIQUSDT" and item["exit_reason"] == "DRY_RUN_LIQUIDATION" for item in rounds["items"])
        liq_round = next(item for item in rounds["items"] if item["symbol"] == "LIQUSDT")
        assert liq_round["exit_amount"] == "0"
        assert liq_round["pnl"] == "-50"
        assert bot.current_loss_streak(liquidation_log) == 1


if __name__ == "__main__":
    test_state_migration_and_client_order_id()
    test_live_confirm_and_dashboard_auth()
    test_signal_reliability_filters()
    test_symbol_scoring_rounding_and_dry_run_fill()
    test_contract_sim_effective_stop_loss_guard()
    test_contract_sim_liquidation_closes_dry_run_position()
    test_explicit_position_market_type_overrides_legacy_position_mode()
    test_position_management_error_does_not_stop_remaining_positions()
    test_adaptive_exit_esports_path_locks_profit_after_partial_take_profit()
    test_adaptive_exit_stop_is_monotonic_and_partial_is_idempotent()
    test_average_true_range_uses_completed_one_minute_bars()
    test_kline_confirmation_ignores_the_open_candle()
    test_overextended_entry_reasons_are_volatility_normalized()
    test_early_failure_exit_requires_time_price_and_peak_conditions()
    test_adaptive_exit_is_disabled_for_live_positions()
    test_adaptive_position_management_executes_partial_only_once()
    test_adaptive_partial_flag_is_set_before_close_persists_state()
    test_adaptive_partial_close_updates_quantity_margin_fees_and_realized_pnl()
    test_adaptive_gap_exit_records_trigger_separately_from_fill()
    test_small_stop_cross_keeps_the_strategy_exit_reason()
    test_adaptive_trade_events_preserve_exit_reason_and_trigger_price()
    test_complete_trade_lifecycle_groups_partial_exit_and_excludes_synthetic_events()
    test_complete_trade_lifecycle_ignores_legacy_dust_from_previous_trade()
    test_dry_run_full_close_uses_complete_local_quantity()
    test_mark_price_cache_parses_stream_and_rest_fallback_is_rate_limited()
    test_stale_market_data_does_not_fabricate_an_exit()
    test_risk_monitor_manages_each_position_with_fresh_prices()
    test_risk_monitor_fetches_atr_only_for_trailing_positions()
    test_run_once_uses_serialized_state_persistence()
    test_dashboard_loop_failure_stops_the_risk_monitor()
    test_dashboard_snapshot_exposes_adaptive_exit_state()
    test_dashboard_position_snapshot_marks_liquidation_risk()
    test_account_risk_guards()
    test_b_route_risk_sizing_and_default_limits()
    test_consecutive_loss_pause_expires()
    test_signal_recording_and_analysis()
    test_future_return_updater_routes_futures_candidates_to_futures_client()
    test_trade_journal_migration_stats_and_pagination()
    print("safety and risk tests passed")
