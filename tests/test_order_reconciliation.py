from datetime import datetime, timedelta
import sqlite3

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


def test_side_mismatch_fill_is_not_matched_to_order(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_order(order(side=OrderSide.BUY))
    fill = TradeFill("005930", "Test", OrderSide.SELL, 10, 10000, "000001", datetime.now(), execution_id="E4")
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999)), ReconcileClient((fill,)), store, __import__("logging").getLogger("test"))

    fills = manager.reconcile_open_orders()

    assert fills == ()
    assert store.has_open_order("005930", OrderSide.BUY)


def test_fill_without_execution_id_is_deduped_by_stable_trade_fields(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    first = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", datetime.now().replace(microsecond=0))
    second = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", first.filled_at + timedelta(seconds=3))

    assert store.record_fill(first)
    assert not store.record_fill(second)


def test_existing_db_is_backed_up_before_schema_migration(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE positions (code TEXT PRIMARY KEY, name TEXT NOT NULL)")

    StateStore(db_path)

    backups = list((tmp_path / "backups").glob("state_*.sqlite3"))
    assert backups
