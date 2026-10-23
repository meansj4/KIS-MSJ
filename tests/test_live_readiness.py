"""Launch preparation exercises the real strategy with a broker that cannot trade."""

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StockConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj.models import AccountSnapshot, BalanceItem, OrderRequest, OrderResult, OrderSide, OrderStatus, Quote
from kis_msj.readiness import broker_read_only, run_readiness


CODE = "005930"


class ReadOnlyBroker:
    is_demo = False

    def __init__(self):
        self.snapshot = AccountSnapshot(1_000_000, 2_000_000, 0, 0)
        self.broker_orders = ()
        self.price = 10_000
        self.events = []
        self.submissions = []
        self.cancellations = []
        self.fail_at = ""
        self.mutate_on_execution = ""
        self.history_length = 61

    def _request(self, method, path, **kwargs):
        self.events.append((method, path))
        if self.fail_at == path:
            raise RuntimeError("simulated_broker_api_failure")
        return {}

    def executions(self, **kwargs):
        self._request("GET", "executions")
        if self.mutate_on_execution == "place":
            self.place_order(None)
        elif self.mutate_on_execution == "cancel":
            self.cancel_order("1", 1)
        elif self.mutate_on_execution == "post":
            self._request("POST", "trade")
        return ()

    def account_snapshot(self):
        self._request("GET", "account")
        return self.snapshot

    def open_orders(self):
        self._request("GET", "open_orders")
        return self.broker_orders

    def quote(self, code, *, name=""):
        self._request("GET", "quote")
        return Quote(code, self.price, datetime.now(), name)

    def adjusted_daily_closes(self, code, *, end_date, count):
        self._request("GET", "daily_closes")
        return [(end_date - timedelta(days=index), 10_000 + index) for index in reversed(range(self.history_length))]

    def place_order(self, request):
        self.submissions.append(request)
        raise AssertionError("broker mutation was reached")

    def cancel_order(self, number, quantity):
        self.cancellations.append((number, quantity))
        raise AssertionError("broker mutation was reached")


@pytest.fixture
def prepared(tmp_path):
    config = BotConfig(
        stocks=(StockConfig(CODE, "Test"),),
        strategy=StrategyConfig(adaptive_lot_enabled=True),
        order=OrderConfig(live_trading=True, price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "bot.log"),
    )
    trader = AutoTrader(config, use_mock_client=True)
    broker = ReadOnlyBroker()
    trader.client = broker
    trader.order_manager.client = broker
    return trader, broker, tmp_path / "readiness.json"


def check(prepared, **kwargs):
    trader, broker, path = prepared
    result = run_readiness(trader, report_path=path, **kwargs)
    assert broker.submissions == []
    assert broker.cancellations == []
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["ready"] is result.ready
    assert report["order_submission_enabled"] is False
    return result


def test_success_checks_fresh_broker_state_and_persists_strategy_without_trade_paths(prepared, monkeypatch):
    trader, broker, path = prepared

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not invoke a trading or heuristic repair path")

    for method in ("run_once", "startup_sync", "reconcile_open_orders", "auto_repair_sync_mismatch"):
        monkeypatch.setattr(trader, method, forbidden)
    original_request = broker._request
    original_place = broker.place_order
    original_cancel = broker.cancel_order

    result = check(prepared)

    assert result.ready
    assert result.reasons == ()
    assert result.report["verified_symbols"] == 1
    assert result.report["cash_available"] == 1_000_000
    assert result.report["symbols"][0]["volatility"]["reason"] == "ok"
    assert result.report["symbols"][0]["buy_target"] == 10_000
    assert {path for method, path in broker.events} == {"executions", "account", "open_orders", "daily_closes", "quote"}
    assert all(method == "GET" for method, path in broker.events)
    assert trader.store.load_positions()[CODE].current_price == 10_000
    assert trader._last_account_snapshot is broker.snapshot
    assert trader._last_account_snapshot_at > 0
    assert broker._request == original_request
    assert broker.place_order == original_place
    assert broker.cancel_order == original_cancel


@pytest.mark.parametrize("source", ["broker", "local"])
def test_pending_orders_block_readiness_without_cancellation(prepared, source):
    trader, broker, path = prepared
    if source == "broker":
        broker.broker_orders = ({"odno": "1"},)
    else:
        request = OrderRequest(CODE, "Test", OrderSide.BUY, 1, 10_000, "adaptive_initial_buy")
        trader.store.record_order(OrderResult(request, "INTENT-unknown", OrderStatus.REQUESTED, "submission_outcome_unknown"))

    result = check(prepared)

    assert not result.ready
    assert f"{source}_open_orders_require_reconciliation" in result.reasons


def test_unproven_balance_difference_stays_blocked_and_is_never_repaired(prepared):
    trader, broker, path = prepared
    broker.snapshot = replace(broker.snapshot, positions=(BalanceItem(CODE, "Test", 5, 10_000, 10_000),))

    result = check(prepared)

    assert not result.ready
    assert "account_lot_quantity_mismatch" in result.reasons
    assert result.report["quantity_mismatch_codes"] == [CODE]
    assert trader.store.load_lots() == {}
    assert trader.store.load_positions()[CODE].sync_status == "SYNC_REQUIRED"


@pytest.mark.parametrize("field,value", [
    ("cash_available", -1), ("cash_available", float("nan")), ("cash_available", float("inf")),
    ("cash_available", None), ("cash_available", True), ("total_asset", 0),
    ("total_asset", float("nan")), ("total_asset", float("inf")),
])
def test_invalid_account_values_return_failed_persisted_report(prepared, field, value):
    trader, broker, path = prepared
    broker.snapshot = replace(broker.snapshot, **{field: value})

    result = check(prepared)

    assert not result.ready
    assert "invalid_account_cash" in result.reasons if field == "cash_available" else "invalid_account_assets" in result.reasons


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None, True])
def test_invalid_quotes_block_readiness(prepared, price):
    trader, broker, path = prepared
    broker.price = price

    result = check(prepared)

    assert not result.ready
    assert any(reason.startswith("symbol_check_failed:" + CODE) for reason in result.reasons)


@pytest.mark.parametrize("failure", ["executions", "account", "open_orders", "quote", "daily_closes"])
def test_broker_read_failures_block_readiness(prepared, failure):
    trader, broker, path = prepared
    broker.fail_at = failure

    result = check(prepared)

    assert not result.ready
    assert result.reasons


@pytest.mark.parametrize("mutation", ["place", "cancel", "post"])
def test_even_reconciliation_cannot_reach_remote_mutation(prepared, mutation):
    trader, broker, path = prepared
    broker.mutate_on_execution = mutation

    result = check(prepared)

    assert not result.ready
    assert all(method == "GET" for method, path in broker.events)


def test_demo_broker_is_rejected_before_any_broker_request(prepared):
    trader, broker, path = prepared
    broker.is_demo = True

    result = check(prepared)

    assert not result.ready
    assert "real_account_required" in result.reasons
    assert broker.events == []


def test_existing_account_buy_limits_remain_visible_without_becoming_launch_failures(prepared):
    trader, broker, path = prepared
    trader.risk_manager.config = replace(trader.config, risk=RiskConfig(min_cash_available=2_000_000))

    result = check(prepared)

    assert result.ready
    assert result.report["account_buy_allowed"] is False
    assert "cash_below_minimum" in result.report["account_buy_block_reasons"]


def test_short_confirmed_history_uses_documented_fallback(prepared):
    trader, broker, path = prepared
    broker.history_length = 20

    result = check(prepared)

    assert result.ready
    assert result.report["symbols"][0]["volatility"]["reason"] == "insufficient_data"


def test_broker_guard_restores_methods_after_exception():
    broker = ReadOnlyBroker()
    original = broker._request
    with pytest.raises(RuntimeError, match="live_readiness_non_get_forbidden"):
        with broker_read_only(broker):
            broker._request("POST", "trade")
    assert broker._request == original
    assert broker.events == []


@pytest.mark.parametrize("status", ["REQUESTED", "PROCESSING"])
def test_queued_manual_requests_block_launch_without_executing_or_removing_them(prepared, status):
    trader, _, _ = prepared
    trader.store.create_manual_order_request({"request_id": "queued-manual", "code": CODE,
        "side": "BUY", "quantity": 1, "current_price": 10_000, "status": status})
    result = check(prepared)
    assert not result.ready
    assert "pending_manual_requests_require_review" in result.reasons
    assert trader.store.manual_order_requests(status)[0]["request_id"] == "queued-manual"
