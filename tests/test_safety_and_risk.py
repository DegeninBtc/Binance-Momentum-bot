from __future__ import annotations

import json
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


if __name__ == "__main__":
    test_state_migration_and_client_order_id()
    test_live_confirm_and_dashboard_auth()
    test_signal_reliability_filters()
    test_symbol_scoring_rounding_and_dry_run_fill()
    test_account_risk_guards()
    test_signal_recording_and_analysis()
    print("safety and risk tests passed")
