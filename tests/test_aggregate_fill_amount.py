"""Cumulative broker inquiry rows must yield incremental quantity and value."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from kis_msj.config import BotConfig, OrderConfig, StrategyConfig
from kis_msj.kis_client import KisClient
from kis_msj.lot_manager import LotManager
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, TradeFill
from kis_msj.order_manager import OrderManager
from kis_msj.position_manager import PositionManager
from kis_msj.storage import StateStore


CODE = "005930"
NOW = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)


def aggregate(quantity, average, amount=None, *, side=OrderSide.BUY, at=NOW):
    row = {"pdno": CODE, "prdt_name": "Test", "odno": "00123", "tot_ccld_qty": str(quantity),
           "avg_prvs": str(average), "ord_dt": at.strftime("%Y%m%d"), "ord_tmd": at.strftime("%H%M%S"),
           "sll_buy_dvsn_cd": "02" if side is OrderSide.BUY else "01"}
    if amount is not None:
        row["tot_ccld_amt"] = str(amount)
    return KisClient.__new__(KisClient)._execution_fills([row])[0]


def setup(tmp_path, side=OrderSide.BUY, *, quantity=4, lot_id=""):
    config = BotConfig(strategy=StrategyConfig(adaptive_lot_enabled=True, estimated_fee_tax_pct=0),
                       order=OrderConfig(limit_order_timeout_seconds=999_999))
    store = StateStore(tmp_path / "state.sqlite3")
    client = SimpleNamespace(fills=())
    client.executions = lambda **kwargs: client.fills
    orders = OrderManager(config, client, store, logging.getLogger(__name__))
    request = OrderRequest(CODE, "Test", side, quantity, 10_000, "test", lot_id)
    store.record_order(OrderResult(request, "00123", OrderStatus.REQUESTED))
    lots = LotManager(config.strategy)
    positions = PositionManager(config.strategy, lots)
    return orders, client, lots, positions


def reconcile(orders, client, positions, fill):
    client.fills = (fill,)
    delta = orders.reconcile_open_orders()
    for execution in delta:
        positions.apply_fill(execution)
    return delta


def test_broker_parser_preserves_exact_total_and_fractional_average():
    fill = aggregate(4, "10000.2", 40_001)
    assert fill.price == 10_000.25
    assert fill.filled_amount == 40_001
    assert fill.execution_id.startswith("AGG:")
    assert fill.timestamp_source == "order_time"


@pytest.mark.parametrize("amount", [0, -1, "nan", "Infinity", "invalid", "1.5"])
def test_invalid_reported_total_is_not_used_as_a_fill(amount):
    row = {"pdno": CODE, "odno": "123", "tot_ccld_qty": "1", "avg_prvs": "10000",
           "tot_ccld_amt": str(amount)}
    assert KisClient.__new__(KisClient)._execution_fills([row]) == []


def test_cumulative_partial_buy_uses_incremental_value_and_one_lot(tmp_path):
    orders, client, lots, positions = setup(tmp_path)
    first = reconcile(orders, client, positions, aggregate(1, 10_000, 10_000))
    second = reconcile(orders, client, positions, aggregate(4, 9_700, 38_800))

    assert first[0].quantity == 1
    assert second[0].quantity == 3
    assert second[0].price == 9_600
    assert second[0].filled_amount == 28_800
    held = lots.open_lots(CODE)
    assert len(held) == 1
    assert held[0].buy_quantity == 4
    assert held[0].buy_amount == 38_800
    assert held[0].buy_price == 9_700
    assert positions.get(CODE).daily_buy_amount == 38_800
    assert positions.get(CODE).consecutive_buy_count == 1
    assert reconcile(orders, client, positions, aggregate(4, 9_700, 38_800)) == ()
    assert held[0].buy_quantity == 4
    assert positions.get(CODE).consecutive_buy_count == 1


def test_cumulative_partial_sell_renews_anchor_from_new_fill_price(tmp_path):
    orders, client, lots, positions = setup(tmp_path, OrderSide.SELL, lot_id="LOT")
    positions.apply_fill(TradeFill(CODE, "Test", OrderSide.BUY, 4, 10_000, "seed",
                                   NOW - timedelta(minutes=1), "LOT", "seed"))
    reconcile(orders, client, positions, aggregate(1, 10_500, 10_500, side=OrderSide.SELL))
    second = reconcile(orders, client, positions, aggregate(3, "10566.66", 31_700, side=OrderSide.SELL))

    assert second[0].quantity == 2
    assert second[0].price == 10_600
    assert second[0].filled_amount == 21_200
    position = positions.get(CODE)
    assert position.last_sell_price == 10_600
    assert position.daily_sell_amount == 31_700
    assert position.consecutive_sell_count == 1
    assert position.rebuy_restriction_active
    assert lots.open_lots(CODE)[0].remaining_quantity == 1
    assert reconcile(orders, client, positions, aggregate(3, "10566.66", 31_700, side=OrderSide.SELL)) == ()
    assert position.last_sell_price == 10_600
    assert position.consecutive_sell_count == 1


def test_fractional_incremental_average_preserves_exact_buy_cost_after_restart(tmp_path):
    orders, client, lots, positions = setup(tmp_path)
    reconcile(orders, client, positions, aggregate(1, 10_000, 10_000))
    second = reconcile(orders, client, positions, aggregate(4, "10000.2", 40_001))
    held = lots.open_lots(CODE)[0]
    assert second[0].price == pytest.approx(30_001 / 3)
    assert second[0].filled_amount == 30_001
    assert held.buy_price == 10_000.25
    assert held.buy_amount == positions.get(CODE).daily_buy_amount == 40_001

    orders.store.save_lots(lots.lots.values())
    reloaded = orders.store.load_lots()[held.lot_id]
    assert reloaded.buy_price == 10_000.25
    assert reloaded.buy_amount == 40_001
    assert orders.store.recorded_fill_amount(CODE, "00123", OrderSide.BUY, filled_at=NOW) == 40_001


def test_fractional_sell_slice_average_and_exact_proceeds_survive_restart(tmp_path):
    orders, client, lots, positions = setup(tmp_path, OrderSide.SELL, quantity=5, lot_id="LOT")
    positions.apply_fill(TradeFill(CODE, "Test", OrderSide.BUY, 5, 10_000, "seed",
                                   NOW - timedelta(minutes=1), "LOT", "seed"))
    reconcile(orders, client, positions, aggregate(1, 10_500, 10_500, side=OrderSide.SELL))
    second = reconcile(orders, client, positions, aggregate(4, "10575.2", 42_301, side=OrderSide.SELL))

    assert second[0].price == pytest.approx(31_801 / 3)
    assert second[0].filled_amount == 31_801
    position = positions.get(CODE)
    assert position.last_sell_price == pytest.approx(31_801 / 3)
    assert position.daily_sell_amount == 42_301
    assert position.consecutive_sell_count == 1
    orders.store.save_position(position)
    assert orders.store.load_positions()[CODE].last_sell_price == pytest.approx(31_801 / 3)


@pytest.mark.parametrize("cumulative_amount", [9_000, 10_000])
def test_nonpositive_incremental_value_is_rejected_without_mutation(tmp_path, cumulative_amount):
    orders, client, lots, positions = setup(tmp_path)
    reconcile(orders, client, positions, aggregate(1, 10_000, 10_000))
    assert reconcile(orders, client, positions, aggregate(2, 10_000, cumulative_amount)) == ()
    assert lots.open_lots(CODE)[0].buy_quantity == 1
    assert orders.store.filled_quantity_for_order("00123") == 1
    assert positions.get(CODE).daily_buy_amount == 10_000


def test_missing_total_uses_incremental_implied_value_as_available_fallback(tmp_path):
    orders, client, lots, positions = setup(tmp_path)
    reconcile(orders, client, positions, aggregate(1, 10_000))
    second = reconcile(orders, client, positions, aggregate(3, 9_800))
    assert second[0].quantity == 2
    assert second[0].price == 9_700
    assert second[0].filled_amount == 0
    assert lots.open_lots(CODE)[0].buy_amount == 29_400


def test_startup_reconciliation_preserves_exact_amount_through_request_attribution(tmp_path):
    orders, client, lots, positions = setup(tmp_path)
    client.fills = (aggregate(4, "10000.2", 40_001),)
    fills = orders.reconcile_recent_executions()
    assert fills[0].filled_amount == 40_001
    assert fills[0].price == 10_000.25
    positions.apply_fill(fills[0])
    assert lots.open_lots(CODE)[0].buy_amount == 40_001
