"""Final adaptive execution boundaries using an isolated ledger and fake broker."""

import json
import logging
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest

from kis_msj.config import BotConfig, OrderConfig, StrategyConfig
from kis_msj.market_session import SessionUnavailable
from kis_msj.models import (
    AccountSnapshot, LotState, OrderRequest, OrderResult, OrderSide,
    OrderStatus, PositionLifecycle, PositionState, SellReason, TradeFill,
)
from kis_msj.order_manager import OrderManager
from kis_msj.storage import StateStore
from kis_msj.strategy import StrategyAction
from kis_msj.volatility import VolatilityEstimate


CODE = "005930"


class Broker:
    def __init__(self, *, cash=100_000_000, total=200_000_000, error=None, order_id="123456"):
        self.cash = cash
        self.total = total
        self.error = error
        self.order_id = order_id
        self.placed = []
        self.snapshot_calls = 0

    def account_snapshot(self):
        self.snapshot_calls += 1
        return AccountSnapshot(self.cash, self.total, 0, 0)

    def place_order(self, request):
        self.placed.append(request)
        if self.error:
            raise self.error
        return OrderResult(request, self.order_id, OrderStatus.REJECTED, "test_terminal")

    def executions(self, **kwargs):
        return ()


def manager(tmp_path, *, broker=None, **strategy_overrides):
    settings = dict(adaptive_lot_enabled=True, lot_sizing_mode="legacy", initial_buy_amount=100_000,
                    auto_buy_limit=1_200_000, absolute_max_investment=1_200_000)
    settings.update(strategy_overrides)
    config = BotConfig(strategy=StrategyConfig(**settings),
                       order=OrderConfig(live_trading=True, buy_limit_markup_pct=0.5),
                       storage_path=str(tmp_path / "state.sqlite3"))
    store = StateStore(config.storage_path)
    client = broker or Broker()
    return OrderManager(config, client, store, logging.getLogger(__name__))


def lot(code=CODE, price=10_000, *, lot_id="L1", quantity=10):
    return LotState(lot_id, code, (datetime.now() - timedelta(days=1)).isoformat(),
                    price, quantity, price * quantity, quantity, 3, int(price * 1.03))


def persist_position(orders, *, lots=(), restricted=False, last_sale=10_000):
    lots = tuple(lots)
    quantity = sum(item.remaining_quantity for item in lots if item.code == CODE)
    position = PositionState(CODE, "Test", quantity=quantity, current_price=9_600,
                             cumulative_invested_amount=sum(item.open_amount for item in lots if item.code == CODE),
                             position_state=(PositionLifecycle.HOLDING.value if quantity else
                                             PositionLifecycle.WAIT_REENTRY.value),
                             rebuy_restriction_active=restricted, last_sell_price=last_sale)
    orders.store.save_position(position)
    orders.store.save_lots(lots)
    return position


def buy(*, price=9_600, quantity=10, reason="adaptive_add_buy", code=CODE):
    return OrderRequest(code, "Test", OrderSide.BUY, quantity, price, reason)


def test_adaptive_buy_uses_marketable_price_and_executable_budget(tmp_path):
    orders = manager(tmp_path)
    orders.config = replace(orders.config, order=replace(orders.config.order, buy_limit_markup_pct=10))
    action = StrategyAction(OrderSide.BUY, 100_000, None, "adaptive_add_buy", limit_price=12_000)

    request = orders.build_request(PositionState(CODE), action, 9_950)

    assert request.limit_price == orders.buy_limit_price(9_950)
    assert request.quantity == 100_000 // request.limit_price
    assert request.quantity * request.limit_price <= 100_000


def test_adaptive_buy_floor_never_crosses_exact_ceiling(tmp_path):
    orders = manager(tmp_path)
    action = StrategyAction(OrderSide.BUY, 100_000, None, "adaptive_add_buy", limit_price=9_999)
    request = orders.build_request(PositionState(CODE), action, 10_000)
    assert request.limit_price == 9_990
    assert request.limit_price <= action.limit_price


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_quote_never_builds_order(tmp_path, price):
    orders = manager(tmp_path)
    action = StrategyAction(OrderSide.BUY, 100_000, None, "adaptive_initial_buy", limit_price=10_000)
    assert orders.build_request(PositionState(CODE), action, price) is None


@pytest.mark.parametrize("target", [-1, float("nan"), float("inf")])
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_invalid_target_never_becomes_quote_fallback(tmp_path, target, side):
    orders = manager(tmp_path)
    action = StrategyAction(side, 100_000, 1, "adaptive_target", limit_price=target)
    assert orders.build_request(PositionState(CODE), action, 10_000) is None


def test_zero_adaptive_lot_target_never_becomes_sell_at_quote(tmp_path):
    orders = manager(tmp_path)
    held_lot = lot()
    action = StrategyAction(OrderSide.SELL, 0, 1, "profit_take_lot", target_lot=held_lot,
                            sell_reason=SellReason.PROFIT_TAKE.value, limit_price=0)
    assert orders.build_request(PositionState(CODE), action, 10_000) is None


def test_aged_sell_target_uses_validated_quote_above_trigger(tmp_path):
    orders = manager(tmp_path)
    action = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", target_lot=lot(),
                            sell_reason=SellReason.PROFIT_TAKE.value, limit_price=5_000)
    request = orders.build_request(PositionState(CODE), action, 10_000)
    assert request.limit_price == 10_000
    assert request.limit_price >= action.limit_price


def test_adaptive_sell_does_not_build_before_target_is_reached(tmp_path):
    orders = manager(tmp_path)
    action = StrategyAction(OrderSide.SELL, 0, 1, "sell_profitable_lot", target_lot=lot(),
                            sell_reason=SellReason.PROFIT_TAKE.value, limit_price=10_500)
    assert orders.build_request(PositionState(CODE), action, 10_000) is None


def test_final_guard_reloads_new_lowest_lot(tmp_path):
    orders = manager(tmp_path)
    persist_position(orders, lots=(lot(), lot(price=9_000, lot_id="NEW")))
    with pytest.raises(SessionUnavailable, match="adaptive_buy_price_not_reached"):
        orders.submit_and_confirm(buy())
    assert not orders.client.placed
    assert orders.client.snapshot_calls == 1


@pytest.mark.parametrize(("field", "value"), [
    ("position_state", PositionLifecycle.SYNC_REQUIRED.value),
    ("position_state", PositionLifecycle.RISK_BLOCKED.value),
    ("position_state", PositionLifecycle.REVIEW_REQUIRED.value),
    ("sync_status", "SYNC_REQUIRED"),
    ("lot_quantity_mismatch", True),
    ("trading_paused", True),
    ("auto_buy_enabled", False),
])
def test_final_guard_enforces_persisted_automatic_buy_blocks(tmp_path, field, value):
    orders = manager(tmp_path)
    position = persist_position(orders, lots=(lot(),))
    setattr(position, field, value)
    orders.store.save_position(position)
    with pytest.raises(SessionUnavailable, match="adaptive_persisted_buy_block"):
        orders.submit_and_confirm(buy())
    assert not orders.client.placed


def test_final_guard_reloads_persisted_volatility(tmp_path):
    orders = manager(tmp_path)
    persist_position(orders, lots=(lot(),))
    estimate = VolatilityEstimate(gap_pct=8, multiplier=2)
    orders.store.path.with_suffix(".volatility.json").write_text(json.dumps({
        "version": 1, "symbols": {CODE: {"requested_through": "2026-10-22", "estimate": asdict(estimate)}}
    }), encoding="utf-8")
    with pytest.raises(SessionUnavailable, match="adaptive_buy_price_not_reached"):
        orders.submit_and_confirm(buy())
    assert not orders.client.placed


def test_final_guard_applies_active_sale_ceiling_to_manual_buy(tmp_path):
    orders = manager(tmp_path)
    persist_position(orders, lots=(lot(),), restricted=True, last_sale=9_000)
    with pytest.raises(SessionUnavailable, match="adaptive_buy_price_not_reached"):
        orders.submit_and_confirm(buy(reason="manual_buy"))
    assert not orders.client.placed


def test_manual_first_entry_does_not_require_strategy_target(tmp_path):
    orders = manager(tmp_path)
    result, fill = orders.submit_and_confirm(buy(reason="manual_buy"))
    assert result.status is OrderStatus.REJECTED
    assert fill is None
    assert len(orders.client.placed) == 1


@pytest.mark.parametrize("cash", [95_999, float("nan"), float("inf"), -1])
def test_final_guard_uses_fresh_finite_cash(tmp_path, cash):
    orders = manager(tmp_path, broker=Broker(cash=cash))
    persist_position(orders, lots=(lot(),))
    with pytest.raises(SessionUnavailable, match="adaptive_cash_insufficient"):
        orders.submit_and_confirm(buy())
    assert not orders.client.placed


def test_final_guard_rejects_executable_notional_over_current_lot_budget(tmp_path):
    orders = manager(tmp_path)
    persist_position(orders, lots=(lot(),))
    with pytest.raises(SessionUnavailable, match="adaptive_buy_budget_exceeded"):
        orders.submit_and_confirm(buy(quantity=11))
    assert not orders.client.placed


def test_final_guard_reserves_other_symbols_future_lots(tmp_path):
    orders = manager(tmp_path, broker=Broker(cash=590_000, total=1_000_000))
    persist_position(orders, lots=(lot(code="000660"),), restricted=True)
    with pytest.raises(SessionUnavailable, match="adaptive_reentry_cash_insufficient"):
        orders.submit_and_confirm(buy(price=9_200, reason="adaptive_reentry_buy"))
    assert not orders.client.placed


def test_fresh_buying_power_is_not_charged_for_pending_sells_twice(tmp_path):
    orders = manager(tmp_path, broker=Broker(cash=600_000, total=1_000_000))
    persist_position(orders, lots=(lot(code="000660"),), restricted=True)
    pending_sell = OrderRequest("000660", "Other", OrderSide.SELL, 10, 11_000, "profit_take_lot", "L1")
    orders.store.record_order(OrderResult(pending_sell, "PENDING-SELL", OrderStatus.REQUESTED))
    result, _ = orders.submit_and_confirm(buy(price=9_200, reason="adaptive_reentry_buy"))
    assert result.status is OrderStatus.REJECTED
    assert len(orders.client.placed) == 1


@pytest.mark.parametrize("pending_side", [OrderSide.BUY, OrderSide.SELL])
@pytest.mark.parametrize("new_side", [OrderSide.BUY, OrderSide.SELL])
def test_final_guard_blocks_symbol_orders_in_both_directions(tmp_path, pending_side, new_side):
    orders = manager(tmp_path)
    persist_position(orders, lots=(lot(),))
    pending = replace(buy(), side=pending_side, lot_id="L1" if pending_side is OrderSide.SELL else "")
    orders.store.record_order(OrderResult(pending, "PENDING", OrderStatus.REQUESTED))
    with pytest.raises(SessionUnavailable, match="existing_order_any_venue"):
        orders.submit_and_confirm(replace(buy(), side=new_side, lot_id="L1" if new_side is OrderSide.SELL else ""))
    assert not orders.client.placed


def test_reentry_waits_for_unknown_buy_in_other_symbol(tmp_path):
    orders = manager(tmp_path)
    persist_position(orders, restricted=True)
    orders.store.record_order(OrderResult(buy(code="000660"), "INTENT-unknown", OrderStatus.REQUESTED))
    with pytest.raises(SessionUnavailable, match="adaptive_reentry_wait_pending_buy"):
        orders.submit_and_confirm(buy(reason="adaptive_reentry_buy"))
    assert not orders.client.placed


def test_uncertain_submission_blocks_retry_after_restart(tmp_path):
    broker = Broker(error=TimeoutError("unknown broker outcome"))
    orders = manager(tmp_path, broker=broker)
    persist_position(orders, lots=(lot(),))
    with pytest.raises(TimeoutError):
        orders.submit_and_confirm(buy())
    pending = orders.store.open_orders()
    assert len(pending) == 1
    assert pending[0].message == "submission_outcome_unknown:TimeoutError"
    assert broker.durable_order_submission is True

    restarted = OrderManager(orders.config, broker, StateStore(orders.store.path), orders.logger)
    with pytest.raises(SessionUnavailable, match="existing_order_any_venue"):
        restarted.submit_and_confirm(buy())
    assert len(broker.placed) == 1


def test_acknowledged_adaptive_order_confirms_broker_fill_once(tmp_path):
    broker = Broker()
    broker.place_order = lambda request: OrderResult(request, "00123456", OrderStatus.REQUESTED)
    broker.executions = lambda **kwargs: (TradeFill(CODE, "Test", OrderSide.BUY, 10, 9_600,
                                                   "00123456", datetime.now(), execution_id="F1"),)
    orders = manager(tmp_path, broker=broker)
    persist_position(orders, lots=(lot(),))

    result, fill = orders.submit_and_confirm(buy())

    assert result.status is OrderStatus.FILLED
    assert result.order_id.startswith("SESSION:")
    assert fill.order_id == result.order_id
    assert fill.quantity == 10
    assert not orders.store.open_orders()
    assert orders.store.filled_quantity_for_order(result.order_id) == 10


def test_missing_broker_acknowledgement_keeps_reservation(tmp_path):
    broker = Broker(order_id="")
    broker.place_order = lambda request: OrderResult(request, "", OrderStatus.REQUESTED)
    orders = manager(tmp_path, broker=broker)
    persist_position(orders, lots=(lot(),))
    with pytest.raises(SessionUnavailable, match="submission_requires_reconciliation"):
        orders.submit_and_confirm(buy())
    pending = orders.store.open_orders()
    assert len(pending) == 1
    assert pending[0].message == "submission_outcome_unknown:missing_broker_order_id"


def test_cancel_retry_limit_keeps_adaptive_cash_reserved(tmp_path):
    orders = manager(tmp_path)
    pending = OrderResult(buy(), "PENDING", OrderStatus.CANCEL_REJECTED,
                          cancel_retry_count=orders.config.order.cancel_rejected_max_retries)
    orders.store.record_order(pending)
    assert orders._close_or_throttle_cancel_rejected(pending, 0)
    assert len(orders.store.open_orders()) == 1
