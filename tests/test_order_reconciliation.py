from datetime import datetime, timedelta

from kis_msj.config import BotConfig, OrderConfig
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, TradeFill
from kis_msj.order_manager import OrderManager
from kis_msj.storage import StateStore


class ReconcileClient:
    def __init__(self, fills: tuple[TradeFill, ...] = ()) -> None:
        self.fills = fills
        self.canceled: list[tuple[str, int]] = []

    def executions(self):
        return self.fills

    def cancel_order(self, order_id: str, quantity: int) -> OrderStatus:
        self.canceled.append((order_id, quantity))
        return OrderStatus.CANCELED


def order(code: str = "005930", side: OrderSide = OrderSide.BUY, quantity: int = 10, lot_id: str = "") -> OrderResult:
    request = OrderRequest(code, "Test", side, quantity, 10000, "test", lot_id)
    return OrderResult(request, "000001", OrderStatus.REQUESTED, "requested")


def test_requested_order_moves_to_filled_when_full_fill_arrives(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_order(order())
    fill = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", datetime.now(), execution_id="E1")
    manager = OrderManager(BotConfig(), ReconcileClient((fill,)), store, __import__("logging").getLogger("test"))

    fills = manager.reconcile_open_orders()

    assert fills == (fill,)
    assert not store.has_open_order("005930", OrderSide.BUY)


def test_requested_order_moves_to_partial_when_partial_fill_arrives(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_order(order(quantity=10))
    fill = TradeFill("005930", "Test", OrderSide.BUY, 4, 10000, "000001", datetime.now(), execution_id="E2")
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999)), ReconcileClient((fill,)), store, __import__("logging").getLogger("test"))

    fills = manager.reconcile_open_orders()

    assert fills == (fill,)
    assert store.has_partial_order("005930")
    assert store.has_open_order("005930", OrderSide.BUY)


def test_old_partial_order_becomes_partial_canceled_after_cancel(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_order(order(quantity=10))
    fill = TradeFill("005930", "Test", OrderSide.BUY, 4, 10000, "000001", datetime.now(), execution_id="E3")
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=0)), ReconcileClient((fill,)), store, __import__("logging").getLogger("test"))

    manager.reconcile_open_orders()

    assert not store.has_open_order("005930", OrderSide.BUY)


def test_canceled_order_no_longer_counts_as_open(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order()
    store.record_order(result)
    store.record_order(OrderResult(result.request, result.order_id, OrderStatus.CANCELED, "canceled"))

    assert not store.has_open_order("005930", OrderSide.BUY)


def test_rejected_order_no_longer_counts_as_open(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order()
    store.record_order(result)
    store.record_order(OrderResult(result.request, result.order_id, OrderStatus.REJECTED, "rejected"))

    assert not store.has_open_order("005930", OrderSide.BUY)
