from __future__ import annotations

import json
import os
import tempfile
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

        cfg = bot.BotConfig(api_key="", api_secret="", state_file=state_path, max_total_exposure_pct=Decimal("50"), order_quote_amount=Decimal("100"), max_open_positions=1)
        reason = bot.LongOnlyMomentumBot(cfg)._account_risk_guard_reason(candidate)
        assert reason and "total exposure" in reason

        cfg = bot.BotConfig(api_key="", api_secret="", state_file=state_path, max_symbol_exposure_pct=Decimal("50"), order_quote_amount=Decimal("100"), max_open_positions=1)
        reason = bot.LongOnlyMomentumBot(cfg)._account_risk_guard_reason(candidate)
        assert reason and "BTCUSDT exposure" in reason

        cfg = bot.BotConfig(api_key="", api_secret="", state_file=state_path, max_consecutive_losses=2)
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.state.trade_log = [
            {"action": "BUY", "symbol": "BTCUSDT", "quantity": "1", "price": "100", "quote_amount": "100", "ts": bot.utc_now()},
            {"action": "SELL", "symbol": "BTCUSDT", "quantity": "1", "price": "90", "quote_amount": "90", "ts": bot.utc_now()},
            {"action": "BUY", "symbol": "ETHUSDT", "quantity": "1", "price": "100", "quote_amount": "100", "ts": bot.utc_now()},
            {"action": "SELL", "symbol": "ETHUSDT", "quantity": "1", "price": "95", "quote_amount": "95", "ts": bot.utc_now()},
        ]
        reason = instance._account_risk_guard_reason(candidate)
        assert reason and "consecutive losses" in reason

        cfg = bot.BotConfig(api_key="", api_secret="", state_file=state_path, max_intraday_drawdown_pct=Decimal("5"), order_quote_amount=Decimal("100"), max_open_positions=1)
        instance = bot.LongOnlyMomentumBot(cfg)
        instance.state.positions = [bot.PositionState(symbol="BTCUSDT", base_asset="BTC", quantity="1", entry_price="100", quote_spent="100")]
        instance.state.position = instance.state.positions[0]
        instance.client.ticker_price = lambda _symbol: Decimal("90")
        reason = instance._account_risk_guard_reason(candidate)
        assert reason and "intraday drawdown" in reason

        cfg = bot.BotConfig(api_key="", api_secret="", state_file=state_path, risk_per_trade_pct=Decimal("1"), initial_stop_loss_pct=Decimal("4"), order_quote_amount=Decimal("100"), max_open_positions=1)
        snapshot = bot.LongOnlyMomentumBot(cfg)._account_risk_snapshot(candidate)
        assert Decimal(str(snapshot["fixed_order_quote"])) == Decimal("100")
        assert Decimal(str(snapshot["risk_based_quote_suggestion"])) > Decimal("0")


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


if __name__ == "__main__":
    test_state_migration_and_client_order_id()
    test_live_confirm_and_dashboard_auth()
    test_signal_reliability_filters()
    test_symbol_scoring_rounding_and_dry_run_fill()
    test_account_risk_guards()
    test_signal_recording_and_analysis()
    test_trade_journal_migration_stats_and_pagination()
    print("safety and risk tests passed")
