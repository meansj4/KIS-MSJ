from datetime import datetime

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StockConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj import main as trader_main
from kis_msj.models import AccountSnapshot, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState, Quote, ReentryType, SellReason, TradeFill
from kis_msj.risk_manager import RiskDecision
from kis_msj.strategy import StrategyAction


def trader(tmp_path, risk: RiskConfig | None = None) -> AutoTrader:
    config = BotConfig(
        order=OrderConfig(price_sample_interval_seconds=0),
        risk=risk or RiskConfig(),
        strategy=StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    return AutoTrader(config, use_mock_client=True)


def test_log_symbol_decision_accepts_snapshot(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test")
    snapshot = AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())

    bot.log_symbol_decision(position, 10000, snapshot, RiskDecision(True), RiskDecision(True), "NONE")


def test_log_symbol_decision_handles_missing_snapshot(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test")

    bot.log_symbol_decision(position, 10000, None, RiskDecision(True), RiskDecision(True), "NONE")


def test_evaluate_clears_stale_skip_reason_when_no_current_block(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", skip_reason="data_mismatch")
    bot.position_manager.positions[position.code] = position
    bot.price_sampler.sample = lambda code, name: (Quote(code, 10000, datetime.now(), name),)
    bot.price_sampler.stable = lambda samples, max_volatility: (True, "")
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: None

    bot.evaluate(position, AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert position.skip_reason == ""
    assert bot.store.load_positions()[position.code].skip_reason == ""


def _force_trade_window(monkeypatch) -> None:
    monkeypatch.setattr(trader_main, "in_trade_window", lambda config: True)


def _block_submit(bot: AutoTrader) -> None:
    bot.order_manager.submit_and_confirm = lambda request: (_ for _ in ()).throw(AssertionError("submit should be blocked in test"))


def _fake_submit_no_fill(bot: AutoTrader) -> None:
    bot.order_manager.submit_and_confirm = lambda request: (OrderResult(request, "TEST-ORDER", OrderStatus.REQUESTED, "test"), None)


def test_evaluate_uses_single_quote_and_skips_stability_sampling_when_no_action(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    quote_calls = []
    bot.client.quote = lambda code, name="": quote_calls.append(code) or Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": (_ for _ in ()).throw(AssertionError("stability sampling should not run"))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: None

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert quote_calls == ["005930"]


def test_buy_candidate_runs_stability_sampling_only_for_candidate(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _fake_submit_no_fill(bot)
    quote_calls = []
    sample_calls = []
    bot.client.quote = lambda code, name="": quote_calls.append(code) or Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": sample_calls.append(code) or tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert sample_calls == ["005930"]
    assert quote_calls == ["005930", "005930"]


def test_sell_candidate_runs_stability_sampling_only_for_candidate(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _fake_submit_no_fill(bot)
    lot = bot.position_manager.apply_fill(TradeFill("005930", "Test", OrderSide.BUY, 1, 9000, "BUY-1", datetime.now()))
    bot.store.save_position(lot)
    target_lot = next(iter(bot.lot_manager.open_lots("005930")))
    quote_calls = []
    sample_calls = []
    bot.client.quote = lambda code, name="": quote_calls.append(code) or Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": sample_calls.append(code) or tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(
        OrderSide.SELL,
        0,
        1,
        "sell_profitable_lot",
        target_lot.lot_id,
        target_lot=target_lot,
        sell_reason=SellReason.PROFIT_TAKE.value,
    )

    bot.evaluate(bot.position_manager.get("005930", "Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert sample_calls == ["005930"]
    assert quote_calls == ["005930", "005930"]


def test_candidate_blocks_when_stability_sampling_is_volatile(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _block_submit(bot)
    bot.client.quote = lambda code, name="": Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": (
        Quote(code, 10000, datetime.now(), name),
        Quote(code, 10300, datetime.now(), name),
    )
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert bot.store.load_positions()["005930"].skip_reason.startswith("price_volatility_")


def test_final_quote_action_cleared_blocks_order(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _block_submit(bot)
    bot.client.quote = lambda code, name="": Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    calls = {"decide": 0}

    def decide(position, current_price, snapshot, account_risk, symbol_risk):
        calls["decide"] += 1
        return StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy") if calls["decide"] == 1 else None

    bot.strategy.decide = decide

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert bot.store.load_positions()["005930"].skip_reason == "final_quote_action_cleared"


def test_final_quote_recalculates_order_quantity_from_final_price(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    submitted = []
    quotes = iter([Quote("005930", 10000, datetime.now(), "Test"), Quote("005930", 15000, datetime.now(), "Test")])
    bot.client.quote = lambda code, name="": next(quotes)
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")
    bot.order_manager.submit_and_confirm = lambda request: submitted.append(request) or (OrderResult(request, "TEST-ORDER", OrderStatus.REQUESTED, "test"), None)

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert submitted
    assert submitted[0].quantity == 2
    assert submitted[0].limit_price == bot.order_manager.buy_limit_price(15000)


def test_final_quote_rechecks_open_order_guard(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _block_submit(bot)
    quote_calls = {"count": 0}

    def quote(code, name=""):
        quote_calls["count"] += 1
        if quote_calls["count"] == 2:
            request = OrderRequest(code, name, OrderSide.BUY, 1, 10000, "existing")
            bot.store.record_order(OrderResult(request, "OPEN-1", OrderStatus.REQUESTED, "requested"))
        return Quote(code, 10000, datetime.now(), name)

    bot.client.quote = quote
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert bot.store.load_positions()["005930"].skip_reason == "open_buy_order_exists"


def test_final_quote_rechecks_runtime_pause(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _block_submit(bot)
    bot.client.quote = lambda code, name="": Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")
    monkeypatch.setattr(trader_main, "load_runtime_control", lambda: trader_main.RuntimeControl(buy_paused=True))

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert bot.store.load_positions()["005930"].skip_reason == "runtime_buy_paused"


def test_final_quote_rechecks_sync_review_and_risk_block_states(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    cases = [
        (PositionState(code="005930", name="Test", sync_status=PositionLifecycle.SYNC_REQUIRED.value), "sync_required"),
        (PositionState(code="005930", name="Test", position_state=PositionLifecycle.RISK_BLOCKED.value, danger_state=True), "risk_blocked"),
    ]
    for position, expected in cases:
        bot = trader(tmp_path / expected)
        _block_submit(bot)
        bot.position_manager.positions[position.code] = position
        bot.client.quote = lambda code, name="": Quote(code, 10000, datetime.now(), name)
        bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
        bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

        bot.evaluate(position, AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

        assert bot.store.load_positions()["005930"].skip_reason == expected


def test_pre_request_allows_review_required_profit_take_sell(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.REVIEW_REQUIRED.value, needs_review=True)
    action = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", "LOT-1", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.pre_request_block_reason(position, action) == ""


def test_pre_request_blocks_review_required_buy_and_cleanup_sell(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.REVIEW_REQUIRED.value, needs_review=True)
    buy = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")
    cleanup = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)

    assert bot.pre_request_block_reason(position, buy) == "review_required"
    assert bot.pre_request_block_reason(position, cleanup) == "review_required"


def test_pre_request_blocks_risk_blocked_buy_and_sell_conservatively(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.RISK_BLOCKED.value, danger_state=True)
    buy = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")
    sell = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", "LOT-1", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.pre_request_block_reason(position, buy) == "risk_blocked"
    assert bot.pre_request_block_reason(position, sell) == "risk_blocked"


def test_pre_request_blocks_sync_required_buy_and_sell(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", sync_status=PositionLifecycle.SYNC_REQUIRED.value)
    buy = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")
    sell = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", "LOT-1", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.pre_request_block_reason(position, buy) == "sync_required"
    assert bot.pre_request_block_reason(position, sell) == "sync_required"


def test_no_fill_result_does_not_change_lots_or_positions(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    _fake_submit_no_fill(bot)
    bot.client.quote = lambda code, name="": Quote(code, 10000, datetime.now(), name)
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert bot.lot_manager.open_lots("005930") == []
    assert bot.position_manager.get("005930").quantity == 0


def test_duplicate_fill_dedupe_applies_position_once(tmp_path) -> None:
    bot = trader(tmp_path)
    fill = TradeFill("005930", "Test", OrderSide.BUY, 3, 10000, "ORDER-1", datetime.now(), execution_id="EXEC-1")

    assert bot.store.record_fill(fill) is True
    updated = bot.position_manager.apply_fill(fill)
    bot.store.save_position(updated)
    assert bot.store.record_fill(fill) is False

    assert bot.position_manager.get("005930").quantity == 3
    assert sum(lot.remaining_quantity for lot in bot.lot_manager.open_lots("005930")) == 3


def test_cleanup_sell_blocked_when_requested_buy_exists(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "test")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)

    assert bot.open_order_block_reason(PositionState(code="005930", name="Test"), action) == "open_order_exists_for_cleanup"


def test_pre_request_block_exposes_cleanup_open_order_reason(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "test")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)
    position = PositionState(code="005930", name="Test")

    assert bot.pre_request_block_reason(position, action) == "open_order_exists_for_cleanup"
    assert position.skip_reason == "open_order_exists_for_cleanup"


def test_cleanup_sell_blocked_when_partial_order_exists(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "test")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.PARTIAL, "partial"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)

    assert bot.open_order_block_reason(PositionState(code="005930", name="Test"), action) == "open_order_exists_for_cleanup"


def test_cleanup_sell_blocked_when_requested_sell_exists(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.SELL, 1, 10000, "sell", "OTHER-LOT")
    bot.store.record_order(OrderResult(request, "SELL-1", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)

    assert bot.open_order_block_reason(PositionState(code="005930", name="Test"), action) == "open_order_exists_for_cleanup"


def test_profit_take_sell_keeps_lot_level_open_order_guard(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.SELL, 1, 10000, "sell", "LOT-1")
    bot.store.record_order(OrderResult(request, "SELL-1", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", "LOT-2", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.open_order_block_reason(PositionState(code="005930", name="Test"), action) == ""


def test_max_active_symbols_blocks_new_initial_buy_only(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_active_symbols=1))
    bot.position_manager.positions["000001"] = PositionState(code="000001", position_state=PositionLifecycle.WAIT_REENTRY.value)
    action = StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), action) == "max_active_symbols_reached"


def test_max_new_buy_per_day_blocks_initial_buy_only(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_new_buy_per_day=1))
    request = OrderRequest("000001", "Test", OrderSide.BUY, 1, 10000, "initial_buy")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.FILLED, "filled"))
    initial_action = StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")
    reentry_action = StrategyAction(OrderSide.BUY, 30_000, None, "reentry_buy", reentry_type=ReentryType.NORMAL_REENTRY.value)

    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), initial_action) == "max_new_buy_per_day_reached"
    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), reentry_action) == ""


def test_max_new_buy_per_day_counts_rejected_initial_buy_orders(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_new_buy_per_day=1))
    request = OrderRequest("000001", "Test", OrderSide.BUY, 1, 10000, "initial_buy")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.REJECTED, "rejected"))
    action = StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    assert bot.store.count_today_initial_buy_orders() == 1
    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), action) == "max_new_buy_per_day_reached"


def test_max_new_buy_amount_per_day_blocks_initial_buy_only(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_new_buy_amount_per_day=2_000_000))
    request = OrderRequest("000001", "Test", OrderSide.BUY, 1, 1_500_000, "initial_buy")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.REJECTED, "rejected"))
    initial_action = StrategyAction(OrderSide.BUY, 600_000, None, "initial_buy")
    reentry_action = StrategyAction(OrderSide.BUY, 600_000, None, "reentry_buy", reentry_type=ReentryType.NORMAL_REENTRY.value)

    assert bot.store.sum_today_initial_buy_order_amount() == 1_500_000
    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), initial_action) == "max_new_buy_amount_per_day_reached"
    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), reentry_action) == ""


def test_max_total_open_lots_blocks_buy_but_not_sell(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_total_open_lots=1))
    bot.position_manager.apply_fill(TradeFill("000001", "Test", OrderSide.BUY, 1, 10000, "BUY-1", datetime.now()))
    buy_action = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")
    sell_action = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", "LOT-1", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), buy_action) == "max_total_open_lots_reached"
    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), sell_action) == ""


def test_max_total_invested_blocks_buy(tmp_path) -> None:
    bot = trader(tmp_path, RiskConfig(max_total_invested_amount=10_000))
    bot.position_manager.apply_fill(TradeFill("000001", "Test", OrderSide.BUY, 1, 10000, "BUY-1", datetime.now()))
    action = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")

    assert bot.portfolio_buy_block_reason(PositionState(code="005930", name="Test"), action) == "max_total_invested_amount_reached"


def test_risk_block_reasons_reports_stock_config_flags(tmp_path) -> None:
    config = BotConfig(
        stocks=(StockConfig("005930", "Test", trading_halted=True, investment_alert=True),),
        order=OrderConfig(price_sample_interval_seconds=0),
        strategy=StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)

    assert bot.risk_block_reasons(PositionState(code="005930", name="Test", danger_state=True)) == "trading_halted,investment_alert"
