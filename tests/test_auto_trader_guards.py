import time
from datetime import datetime, timedelta

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StockConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj import main as trader_main
from kis_msj.models import AccountSnapshot, BalanceItem, LotState, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState, Quote, ReentryType, SellReason, TradeFill
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


def test_startup_sync_reuses_recent_account_snapshot(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=60),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    snapshot = AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    calls = {"count": 0}

    def account_snapshot():
        calls["count"] += 1
        return snapshot

    bot.client.account_snapshot = account_snapshot

    assert bot.startup_sync() == snapshot
    assert bot.startup_sync() == snapshot
    assert calls["count"] == 1


def test_run_once_skips_loop_when_initial_account_snapshot_is_rate_limited(tmp_path, monkeypatch) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_rate_limit_cooldown_seconds=90),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    calls = {"count": 0}

    def account_snapshot():
        calls["count"] += 1
        raise RuntimeError('{"msg_cd":"EGW00201","msg1":"rate limit"}')

    bot.client.account_snapshot = account_snapshot
    monkeypatch.setattr(trader_main, "load_runtime_control", lambda: trader_main.RuntimeControl())

    assert bot.run_once() == "account_sync_rate_limited"
    assert bot.run_once() == "account_sync_rate_limited"
    assert calls["count"] == 1


def test_fill_application_invalidates_account_snapshot_cache(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=60),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    snapshots = [
        AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()),
        AccountSnapshot(990_000, 1_000_000, 0, 0, ()),
    ]
    calls = {"count": 0}

    def account_snapshot():
        calls["count"] += 1
        return snapshots[min(calls["count"] - 1, len(snapshots) - 1)]

    bot.client.account_snapshot = account_snapshot
    assert bot.startup_sync().cash_available == 1_000_000
    bot.apply_reconciled_fill(TradeFill("005930", "Test", OrderSide.BUY, 1, 10000, "BUY-1", datetime.now()))

    assert bot.startup_sync().cash_available == 990_000
    assert calls["count"] == 2


def test_rate_limited_cached_account_snapshot_does_not_mark_sync_required(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    bot.apply_reconciled_fill(TradeFill("005930", "Test", OrderSide.BUY, 3, 10000, "BUY-1", datetime.now()))
    bot._last_account_snapshot = AccountSnapshot(
        1_000_000,
        1_000_000,
        0,
        0,
        (BalanceItem("005930", "Test", 4, 10000, 10000),),
    )
    bot._last_account_snapshot_at = time.monotonic() - 120

    def account_snapshot():
        raise RuntimeError('{"msg_cd":"EGW00201","msg1":"rate limit"}')

    bot.client.account_snapshot = account_snapshot

    bot.startup_sync()

    position = bot.position_manager.positions["005930"]
    assert not position.lot_quantity_mismatch
    assert position.sync_status == "OK"
    assert position.position_state == PositionLifecycle.HOLDING.value


def test_startup_sync_ignores_non_tradable_balance_items(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    bot.apply_reconciled_fill(TradeFill("005930", "Test", OrderSide.BUY, 3, 10000, "BUY-1", datetime.now()))
    stale_right = PositionState(
        code="J0047101G",
        name="+한솔테크닉스 39R",
        quantity=8,
        position_state=PositionLifecycle.SYNC_REQUIRED.value,
        sync_status=PositionLifecycle.SYNC_REQUIRED.value,
        lot_quantity_mismatch=True,
        trading_paused=True,
    )
    bot.position_manager.positions[stale_right.code] = stale_right

    bot.client.account_snapshot = lambda: AccountSnapshot(
        1_000_000,
        1_000_000,
        0,
        0,
        (
            BalanceItem("005930", "Test", 3, 10000, 10000),
            BalanceItem("J0047101G", "+한솔테크닉스 39R", 8, 0, 0),
        ),
    )

    bot.startup_sync()

    assert not bot.position_manager.account_mismatch_detected
    assert bot.risk_manager.data_mismatch_detected is False
    assert bot.position_manager.positions["005930"].sync_status == "OK"
    right = bot.position_manager.positions["J0047101G"]
    assert right.quantity == 0
    assert right.sync_status == "OK"
    assert right.lot_quantity_mismatch is False
    assert right.position_state == PositionLifecycle.NEVER_BOUGHT.value


def test_startup_sync_auto_repairs_single_closed_partial_buy_gap(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    request = OrderRequest("009070", "KCTC", OrderSide.BUY, 7, 4175, "add_buy_drop_6%")
    bot.store.record_order(OrderResult(request, "0029867200", OrderStatus.CANCELED_AFTER_PARTIAL_FILL, "cancel_rejected_no_cancelable_quantity_after_partial_fill"))
    first_fill = TradeFill("009070", "KCTC", OrderSide.BUY, 1, 4175, "0029867200", datetime.now(), execution_id="AGG:0029867200:009070:1:4175:141510")
    assert bot.store.record_fill(first_fill)
    updated = bot.position_manager.apply_fill(first_fill)
    bot.store.save_position(updated)
    bot.store.save_lots(bot.lot_manager.lots.values())
    bot.client.account_snapshot = lambda: AccountSnapshot(
        1_000_000,
        1_000_000,
        0,
        0,
        (BalanceItem("009070", "KCTC", 7, 4175, 4175),),
    )
    bot.client.open_orders = lambda: ()

    bot.startup_sync()

    position = bot.position_manager.positions["009070"]
    assert bot.position_manager.account_mismatch_detected is False
    assert bot.risk_manager.data_mismatch_detected is False
    assert position.quantity == 7
    assert position.sync_status == "OK"
    assert not position.lot_quantity_mismatch
    assert not position.trading_paused
    assert sum(lot.remaining_quantity for lot in bot.lot_manager.open_lots("009070")) == 7
    assert bot.store.filled_quantity_for_order("0029867200", code="009070", side=OrderSide.BUY) == 7
    assert bot.store.find_order("0029867200").status is OrderStatus.FILLED_AFTER_CANCEL_REQUEST


def test_startup_sync_keeps_sync_required_when_auto_repair_candidate_is_ambiguous(tmp_path) -> None:
    config = BotConfig(
        order=OrderConfig(live_trading=True, account_snapshot_min_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    for order_id in ("BUY-1", "BUY-2"):
        request = OrderRequest("009070", "KCTC", OrderSide.BUY, 7, 4175, "add_buy_drop_6%")
        bot.store.record_order(OrderResult(request, order_id, OrderStatus.CANCELED_AFTER_PARTIAL_FILL, "partial"))
        fill = TradeFill("009070", "KCTC", OrderSide.BUY, 1, 4175, order_id, datetime.now(), execution_id=f"AGG:{order_id}:009070:1:4175:141510")
        assert bot.store.record_fill(fill)
        bot.position_manager.apply_fill(fill)
    bot.store.save_position(bot.position_manager.positions["009070"])
    bot.store.save_lots(bot.lot_manager.lots.values())
    bot.client.account_snapshot = lambda: AccountSnapshot(
        1_000_000,
        1_000_000,
        0,
        0,
        (BalanceItem("009070", "KCTC", 8, 4175, 4175),),
    )
    bot.client.open_orders = lambda: ()

    bot.startup_sync()

    position = bot.position_manager.positions["009070"]
    assert bot.position_manager.account_mismatch_detected is True
    assert bot.risk_manager.data_mismatch_detected is True
    assert position.sync_status == PositionLifecycle.SYNC_REQUIRED.value
    assert position.lot_quantity_mismatch is True
    assert sum(lot.remaining_quantity for lot in bot.lot_manager.open_lots("009070")) == 2


def test_evaluate_clears_stale_skip_reason_when_no_current_block(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", skip_reason="data_mismatch")
    bot.position_manager.positions[position.code] = position
    bot.price_sampler.sample = lambda code, name: (Quote(code, 10000, datetime.now(), name),)
    bot.price_sampler.stable = lambda samples, max_volatility: (True, "")
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: None

    bot.evaluate(position, AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert position.skip_reason == ""
    assert bot.store.load_positions()[position.code].skip_reason == ""


def test_evaluate_auto_clears_review_required_when_triggers_resolved(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    position = PositionState(
        code="005930",
        name="Test",
        position_state=PositionLifecycle.REVIEW_REQUIRED.value,
        needs_review=True,
        auto_buy_enabled=False,
        review_reason="symbol_loss_review",
    )
    bot.position_manager.positions[position.code] = position
    bot.lot_manager.lots["LOT-REVIEW-CLEAR"] = LotState(
        "LOT-REVIEW-CLEAR",
        "005930",
        "2026-05-01T09:05:00",
        buy_price=10000,
        buy_quantity=1,
        buy_amount=10000,
        remaining_quantity=1,
        target_profit_pct=6.0,
        target_sell_price=10600,
    )
    seen = {}
    bot.client.quote = lambda code, name="": Quote(code, 11000, datetime.now(), name)
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: seen.setdefault("needs_review", position.needs_review) or None

    bot.evaluate(position, AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    saved = bot.store.load_positions()["005930"]
    assert seen["needs_review"] is False
    assert saved.position_state == PositionLifecycle.HOLDING.value
    assert saved.needs_review is False
    assert saved.auto_buy_enabled is True
    assert saved.review_reason == ""


def test_run_once_skips_manual_only_stock(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    config = BotConfig(
        stocks=(StockConfig("005930", "Manual", manual_only=True),),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    bot.client.quote = lambda code, name="": (_ for _ in ()).throw(AssertionError("manual_only stock should not be evaluated"))

    assert bot.run_once() == ""


def test_run_once_outside_trade_window_skips_symbol_quotes_and_lot_bulk_save(tmp_path, monkeypatch) -> None:
    config = BotConfig(
        stocks=(StockConfig("005930", "Test"),),
        order=OrderConfig(live_trading=True),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    snapshot = AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    bot.client.account_snapshot = lambda: snapshot
    bot.client.open_orders = lambda: ()
    bot.client.quote = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("quote must not be called"))
    monkeypatch.setattr(trader_main, "in_trade_window", lambda config: False)
    original_save_lots = bot.store.save_lots
    save_lot_calls = {"count": 0}

    def count_save_lots(*args, **kwargs):
        save_lot_calls["count"] += 1
        return original_save_lots(*args, **kwargs)

    monkeypatch.setattr(bot.store, "save_lots", count_save_lots)

    assert bot.run_once() == ""
    # startup_sync persists the reconciled account once; the market-idle path
    # must not perform the second end-of-loop bulk lot write.
    assert save_lot_calls["count"] == 1


def test_stock_config_retire_after_exit_defaults_false() -> None:
    stock = StockConfig("005930", "Test")

    assert stock.retire_after_exit is False
    assert stock.retire_reason == ""
    assert stock.buy_blocked is False


def test_pre_request_blocks_retire_after_exit_buy_but_not_sell(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", retire_after_exit=True)
    buy = StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")
    sell = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.pre_request_block_reason(position, buy) == "BUY_BLOCKED_RETIRE_AFTER_EXIT"
    assert bot.pre_request_block_reason(position, sell) == ""


def test_pre_request_allows_retire_after_exit_add_buy_while_holding(tmp_path) -> None:
    bot = trader(tmp_path)
    bot.position_manager.apply_fill(TradeFill("005930", "Test", OrderSide.BUY, 3, 10_000, "BUY-1", datetime.now()))
    position = bot.position_manager.refresh_from_lots("005930", 9_600)
    position.retire_after_exit = True
    add_buy = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")

    assert bot.pre_request_block_reason(position, add_buy) == ""


def test_stock_buy_block_flag_blocks_all_buys_but_allows_sell(tmp_path) -> None:
    config = BotConfig(
        stocks=(StockConfig("005930", "Test", retire_after_exit=True, buy_blocked=True),),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    bot.position_manager.apply_fill(TradeFill("005930", "Test", OrderSide.BUY, 3, 10_000, "BUY-1", datetime.now()))
    position = bot.position_manager.refresh_from_lots("005930", 9_600)
    add_buy = StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%")
    reentry = StrategyAction(OrderSide.BUY, 30_000, None, "normal_reentry", reentry_type=ReentryType.NORMAL_REENTRY.value)
    sell = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", sell_reason=SellReason.PROFIT_TAKE.value)

    assert bot.pre_request_block_reason(position, add_buy) == "BUY_BLOCKED_BY_STOCK_FLAG"
    assert bot.pre_request_block_reason(position, reentry) == "BUY_BLOCKED_BY_STOCK_FLAG"
    assert bot.pre_request_block_reason(position, sell) == ""


def test_pre_request_keeps_trade_stopped_block_when_retire_flag_removed(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value)
    position.retire_after_exit = False
    buy = StrategyAction(OrderSide.BUY, 30_000, None, "initial_buy")

    assert bot.pre_request_block_reason(position, buy) == "TRADE_STOPPED_AFTER_EXIT"


def test_apply_stock_retirement_config_copies_flags(tmp_path) -> None:
    config = BotConfig(
        stocks=(StockConfig("005930", "Test", retire_after_exit=True, retire_reason="replace"),),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    position = bot.position_manager.get("005930", "Test")

    bot.apply_stock_retirement_config(position, config.stocks[0])

    assert position.retire_after_exit is True
    assert position.retire_reason == "replace"


def test_retire_after_exit_final_fill_is_fill_driven_with_open_order_guard(tmp_path) -> None:
    bot = trader(tmp_path)
    position = bot.position_manager.get("005930", "Test")
    position.retire_after_exit = True
    bot.position_manager.apply_fill(TradeFill("005930", "Test", OrderSide.BUY, 1, 10_000, "BUY-1", datetime.now()))
    lot = bot.lot_manager.last_buy_lot("005930")
    assert lot is not None

    request = OrderRequest("005930", "Test", OrderSide.SELL, 1, 11_000, "sell_profitable_lot", lot.lot_id)
    bot.store.record_order(OrderResult(request, "SELL-OPEN", OrderStatus.REQUESTED, "requested"))
    duplicate_sell = StrategyAction(
        OrderSide.SELL,
        0,
        1,
        "sell_profitable_lot",
        lot.lot_id,
        sell_reason=SellReason.PROFIT_TAKE.value,
    )

    assert bot.open_order_block_reason(position, duplicate_sell) == "open_sell_order_exists"

    updated = bot.position_manager.apply_fill(
        TradeFill("005930", "Test", OrderSide.SELL, 1, 11_000, "SELL-OPEN", datetime.now(), lot.lot_id)
    )

    assert updated.position_state == PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value
    assert bot.lot_manager.open_lots("005930") == []


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


def test_order_request_prefers_strategy_limit_price(tmp_path, monkeypatch) -> None:
    _force_trade_window(monkeypatch)
    bot = trader(tmp_path)
    submitted = []
    quotes = iter([Quote("005930", 10000, datetime.now(), "Test"), Quote("005930", 15000, datetime.now(), "Test")])
    bot.client.quote = lambda code, name="": next(quotes)
    bot.price_sampler.sample = lambda code, name="": tuple(Quote(code, 10000, datetime.now(), name) for _ in range(5))
    bot.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: StrategyAction(OrderSide.BUY, 30_000, None, "add_buy_drop_4%", limit_price=14_900)
    bot.order_manager.submit_and_confirm = lambda request: submitted.append(request) or (OrderResult(request, "TEST-ORDER", OrderStatus.REQUESTED, "test"), None)

    bot.evaluate(PositionState(code="005930", name="Test"), AccountSnapshot(1_000_000, 1_000_000, 0, 0, ()), RiskDecision(True))

    assert submitted
    assert submitted[0].quantity == 2
    assert submitted[0].limit_price == 14_900


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


def test_review_required_recheck_saves_only_current_symbol_lots(tmp_path, monkeypatch) -> None:
    bot = trader(tmp_path)
    current_lot = LotState("CURRENT-LOT", "005930", datetime.now().isoformat(), 10_000, 1, 10_000, 1, 5.0, 10_500)
    other_lot = LotState("OTHER-LOT", "000660", datetime.now().isoformat(), 20_000, 1, 20_000, 1, 5.0, 21_000)
    bot.lot_manager.lots = {current_lot.lot_id: current_lot, other_lot.lot_id: other_lot}
    position = PositionState(
        code="005930",
        name="Test",
        quantity=1,
        average_price=10_000,
        current_price=8_000,
        position_state=PositionLifecycle.REVIEW_REQUIRED.value,
        needs_review=True,
    )
    saved_lots = []
    monkeypatch.setattr(bot.store, "save_lots", lambda lots: saved_lots.extend(lots))

    bot.auto_recheck_review_required(position, 8_000)

    assert [lot.lot_id for lot in saved_lots] == ["CURRENT-LOT"]


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


def test_pre_request_blocks_force_reentry_when_buy_order_is_open(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.WAIT_REENTRY.value)
    open_request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "FORCE_REENTRY_TIMEOUT_NEW_CYCLE")
    bot.store.record_order(OrderResult(open_request, "BUY-OPEN", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(
        OrderSide.BUY,
        30_000,
        None,
        "FORCE_REENTRY_TIMEOUT_NEW_CYCLE",
        reentry_type=ReentryType.FORCE_REENTRY_TIMEOUT_NEW_CYCLE.value,
    )

    assert bot.pre_request_block_reason(position, action) == "open_buy_order_exists"


def test_force_reentry_open_order_guard_clears_after_order_is_closed(tmp_path) -> None:
    bot = trader(tmp_path)
    position = PositionState(code="005930", name="Test", position_state=PositionLifecycle.WAIT_REENTRY.value)
    request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "FORCE_REENTRY_TIMEOUT_NEW_CYCLE")
    action = StrategyAction(
        OrderSide.BUY,
        30_000,
        None,
        "FORCE_REENTRY_TIMEOUT_NEW_CYCLE",
        reentry_type=ReentryType.FORCE_REENTRY_TIMEOUT_NEW_CYCLE.value,
    )
    bot.store.record_order(OrderResult(request, "BUY-CANCELED", OrderStatus.CANCELED, "canceled"))

    assert bot.open_order_block_reason(position, action) == ""


def test_force_reentry_is_blocked_by_sync_risk_and_review_guards(tmp_path) -> None:
    bot = trader(tmp_path)
    action = StrategyAction(
        OrderSide.BUY,
        30_000,
        None,
        "FORCE_REENTRY_TIMEOUT_NEW_CYCLE",
        reentry_type=ReentryType.FORCE_REENTRY_TIMEOUT_NEW_CYCLE.value,
    )

    assert bot.pre_request_block_reason(PositionState(code="005930", sync_status=PositionLifecycle.SYNC_REQUIRED.value), action) == "sync_required"
    assert bot.pre_request_block_reason(PositionState(code="005930", trading_paused=True), action) == "trading_paused"
    assert bot.pre_request_block_reason(PositionState(code="005930", position_state=PositionLifecycle.RISK_BLOCKED.value, danger_state=True), action) == "risk_blocked"
    assert bot.pre_request_block_reason(PositionState(code="005930", position_state=PositionLifecycle.REVIEW_REQUIRED.value, needs_review=True), action) == "review_required"


def test_force_reentry_strategy_blocks_lot_quantity_mismatch() -> None:
    config = BotConfig(order=OrderConfig(price_sample_interval_seconds=0), strategy=StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0))
    bot = AutoTrader(config, use_mock_client=True)
    snapshot = AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    position = bot.position_manager.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.exit_time = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    position.normal_exit_anchor_price = 10_000
    position.trailing_exit_anchor_price = 10_000
    position.lot_quantity_mismatch = True
    symbol_risk = bot.risk_manager.symbol_buy_allowed(position)

    action = bot.strategy.decide(position, 11_000, snapshot, bot.risk_manager.account_buy_allowed(snapshot, bot.position_manager.positions), symbol_risk)

    assert action is None
    assert position.skip_reason == "lot_quantity_mismatch"


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


def test_buy_lot_id_collision_keeps_both_partial_delta_lots(tmp_path) -> None:
    bot = trader(tmp_path)
    filled_at = datetime.now().replace(microsecond=0)
    first = TradeFill("005930", "Test", OrderSide.BUY, 3, 10000, "ORDER-1", filled_at, execution_id="AGG:ORDER-1:005930:3:10000:091000")
    delta = TradeFill("005930", "Test", OrderSide.BUY, 1, 10000, "ORDER-1", filled_at, execution_id="AGG:ORDER-1:005930:4:10000:091000:delta:3->4")

    bot.position_manager.apply_fill(first)
    bot.position_manager.apply_fill(delta)

    lots = bot.lot_manager.open_lots("005930")
    assert len(lots) == 2
    assert sorted(lot.remaining_quantity for lot in lots) == [1, 3]
    assert sum(lot.remaining_quantity for lot in lots) == 4


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
