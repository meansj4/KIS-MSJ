from datetime import datetime

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj.models import AccountSnapshot, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState, ReentryType, SellReason, TradeFill
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


def test_cleanup_sell_blocked_when_requested_buy_exists(tmp_path) -> None:
    bot = trader(tmp_path)
    request = OrderRequest("005930", "Test", OrderSide.BUY, 1, 10000, "test")
    bot.store.record_order(OrderResult(request, "BUY-1", OrderStatus.REQUESTED, "requested"))
    action = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell_lot", "LOT-1", sell_reason=SellReason.CLEANUP_SELL.value)

    assert bot.open_order_block_reason(PositionState(code="005930", name="Test"), action) == "open_order_exists_for_cleanup"


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
