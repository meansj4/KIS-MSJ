from kis_msj.config import BotConfig, OrderConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj.models import AccountSnapshot, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, SellReason
from kis_msj.risk_manager import RiskDecision
from kis_msj.strategy import StrategyAction


def trader(tmp_path) -> AutoTrader:
    config = BotConfig(
        order=OrderConfig(price_sample_interval_seconds=0),
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
