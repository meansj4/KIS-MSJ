from datetime import datetime, timedelta
import sqlite3
import logging

from kis_msj.config import BotConfig, OrderConfig
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, TradeFill
from kis_msj.order_manager import OrderManager
from kis_msj.storage import StateStore


class ReconcileClient:
    def __init__(self, fills: tuple[TradeFill, ...] = ()) -> None:
        self.fills = fills
        self.canceled: list[tuple[str, int]] = []
        self.execution_since = None

    def executions(self, *, since=None):
        self.execution_since = since
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
    second = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", first.filled_at)

    assert store.record_fill(first)
    assert not store.record_fill(second)


def test_fill_without_execution_id_distinguishes_different_fill_times(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    first = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", datetime.now().replace(microsecond=0))
    second = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", first.filled_at + timedelta(seconds=3))

    assert store.record_fill(first)
    assert store.record_fill(second)


def test_existing_db_is_backed_up_before_schema_migration(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE positions (code TEXT PRIMARY KEY, name TEXT NOT NULL)")

    StateStore(db_path)

    backups = list((tmp_path / "backups").glob("state_*.sqlite3"))
    assert backups


def test_reconcile_queries_from_today_for_today_open_order(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_order(order())
    client = ReconcileClient(())
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999, include_previous_day_for_open_orders=False)), client, store, __import__("logging").getLogger("test"))

    manager.reconcile_open_orders()

    assert client.execution_since == datetime.now().date()


def test_reconcile_query_includes_previous_day_for_old_open_order(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order()
    store.record_order(result)
    old_requested_at = (datetime.now() - timedelta(days=1, hours=2)).isoformat(timespec="seconds")
    with store._connect() as connection:
        connection.execute("UPDATE orders SET requested_at = ? WHERE order_id = ?", (old_requested_at, result.order_id))
    client = ReconcileClient(())
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999)), client, store, __import__("logging").getLogger("test"))

    manager.reconcile_open_orders()

    assert client.execution_since <= (datetime.now() - timedelta(days=1)).date()


def test_reconcile_query_can_disable_previous_day_floor(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order()
    store.record_order(result)
    old_requested_at = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    with store._connect() as connection:
        connection.execute("UPDATE orders SET requested_at = ? WHERE order_id = ?", (old_requested_at, result.order_id))
    client = ReconcileClient(())
    config = BotConfig(order=OrderConfig(limit_order_timeout_seconds=999, include_previous_day_for_open_orders=False))
    manager = OrderManager(config, client, store, __import__("logging").getLogger("test"))

    manager.reconcile_open_orders()

    assert client.execution_since == datetime.now().date()


def test_no_open_orders_do_not_query_executions(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    client = ReconcileClient(())
    manager = OrderManager(BotConfig(), client, store, __import__("logging").getLogger("test"))

    manager.reconcile_open_orders()

    assert client.execution_since is None


def test_duplicate_fill_count_is_logged_for_repeated_execution(tmp_path, caplog) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order(quantity=10)
    store.record_order(result)
    fill = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", datetime.now().replace(microsecond=0), execution_id="E-DUP")
    store.record_fill(fill)
    client = ReconcileClient((fill,))
    logger = logging.getLogger("test_reconcile_duplicate")
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999)), client, store, logger)

    with caplog.at_level(logging.INFO, logger="test_reconcile_duplicate"):
        fills = manager.reconcile_open_orders()

    assert fills == ()
    assert any("duplicate_fill_count=1" in message for message in caplog.messages)
    assert any("dedupe_key_type=execution_id" in message for message in caplog.messages)


def test_record_filled_returns_no_fill_when_record_fill_is_duplicate(tmp_path, caplog) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order(quantity=10)
    fill = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", datetime.now().replace(microsecond=0), execution_id="E-RECORDED")
    assert store.record_fill(fill)
    logger = logging.getLogger("test_record_filled_duplicate")
    manager = OrderManager(BotConfig(), ReconcileClient(()), store, logger)

    with caplog.at_level(logging.WARNING, logger="test_record_filled_duplicate"):
        recorded, returned_fill = manager._record_filled(result, fill)

    assert recorded.status is OrderStatus.FILLED
    assert returned_fill is None
    assert any("record_fill_failed" in message and "dedupe_key_type=execution_id" in message for message in caplog.messages)


def test_fallback_dedupe_key_type_is_logged_for_duplicate_without_execution_id(tmp_path, caplog) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order(quantity=10)
    store.record_order(result)
    fill_time = datetime.now().replace(microsecond=0)
    fill = TradeFill("005930", "Test", OrderSide.BUY, 10, 10000, "000001", fill_time)
    assert store.record_fill(fill)
    client = ReconcileClient((fill,))
    logger = logging.getLogger("test_reconcile_fallback_duplicate")
    manager = OrderManager(BotConfig(order=OrderConfig(limit_order_timeout_seconds=999)), client, store, logger)

    with caplog.at_level(logging.WARNING, logger="test_reconcile_fallback_duplicate"):
        fills = manager.reconcile_open_orders()

    assert fills == ()
    assert any("record_fill_failed" in message and "dedupe_key_type=fallback" in message for message in caplog.messages)


def test_startup_recent_reconcile_applies_known_order_fill(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    result = order(quantity=1)
    store.record_order(OrderResult(result.request, result.order_id, OrderStatus.CANCELED, "stale_status"))
    fill = TradeFill("005930", "Test", OrderSide.BUY, 1, 10000, "000001", datetime.now(), execution_id="E-START")
    client = ReconcileClient((fill,))
    manager = OrderManager(BotConfig(order=OrderConfig(startup_execution_lookup_days=1)), client, store, __import__("logging").getLogger("test"))

    fills = manager.reconcile_recent_executions()

    assert fills
    assert fills[0].execution_id == "E-START"


def test_startup_recent_reconcile_ignores_unmatched_manual_fill(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    fill = TradeFill("005930", "Test", OrderSide.BUY, 1, 10000, "MANUAL-1", datetime.now(), execution_id="E-MANUAL")
    client = ReconcileClient((fill,))
    manager = OrderManager(BotConfig(order=OrderConfig(startup_execution_lookup_days=1)), client, store, __import__("logging").getLogger("test"))

    fills = manager.reconcile_recent_executions()

    assert fills == ()
