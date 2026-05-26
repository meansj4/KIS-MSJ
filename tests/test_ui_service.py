from __future__ import annotations

import http.client
import json
from dataclasses import asdict
from datetime import datetime
from unittest.mock import patch

from kis_msj.config import BotConfig, OrderConfig, StockConfig
from kis_msj.main import AutoTrader
from kis_msj.models import (
    Quote,
    LotState,
    AccountSnapshot,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    PositionLifecycle,
    PositionState,
    ReentryType,
    SellReason,
    TradeFill,
)
from kis_msj.risk_manager import RiskDecision
from kis_msj.runtime_control import RuntimeControl, runtime_block_reason, save_runtime_control
from kis_msj.storage import StateStore
from kis_msj.strategy import StrategyAction
from kis_msj.ui_server import INDEX_HTML, build_server
from kis_msj.ui_service import UIService


def _write_config(tmp_path, *, live_trading: bool = True, cleanup_enabled: bool = True, manual_enabled: bool = False):
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "bot.log"
    raw = asdict(
        BotConfig(
            stocks=(StockConfig("005930", "Samsung"),),
            storage_path=str(db_path),
            log_path=str(log_path),
            ui_manual_trading_enabled=manual_enabled,
        )
    )
    raw["order"]["live_trading"] = live_trading
    raw["strategy"]["cleanup_enabled"] = cleanup_enabled
    config_path = tmp_path / "lot_auto_trader.json"
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path, db_path, log_path


def _seed_store(db_path):
    store = StateStore(db_path)
    store.save_position(
        PositionState(
            "005930",
            "Samsung",
            quantity=2,
            current_price=11000,
            cumulative_invested_amount=20000,
            position_state=PositionLifecycle.HOLDING.value,
        )
    )
    store.save_lot(
        LotState(
            "LOT-1",
            "005930",
            "2026-05-01T09:05:00",
            buy_price=10000,
            buy_quantity=2,
            buy_amount=20000,
            remaining_quantity=2,
            target_profit_pct=6.0,
            target_sell_price=10600,
            base_target_profit_rate=0.06,
            effective_target_profit_rate=0.06,
        )
    )
    store.record_order(
        OrderResult(
            OrderRequest("005930", "Samsung", OrderSide.BUY, 1, 10000, "initial_buy"),
            "ORDER-1",
            OrderStatus.REQUESTED,
        )
    )
    store.record_fill(
        TradeFill(
            "005930",
            "Samsung",
            OrderSide.BUY,
            1,
            10000,
            "ORDER-1",
            datetime(2026, 5, 24, 9, 1, 2),
            execution_id="EXEC-1",
        )
    )
    store.record_fill(
        TradeFill(
            "005930",
            "Samsung",
            OrderSide.BUY,
            1,
            10010,
            "ORDER-1",
            datetime(2026, 5, 24, 9, 2, 2),
            lot_id="LOT-1",
        )
    )
    return store


def test_ui_status_masks_and_shows_core_tables(tmp_path):
    config_path, db_path, log_path = _write_config(tmp_path)
    _seed_store(db_path)
    log_path.write_text(
        "2026-05-24 09:00:00 INFO kis_raw_executions raw_execution_count=1 "
        "has_order_no=True has_filled_at=True has_side=True account=12345678 token=secret "
        "_field_mapping.order_no_field=odno\n"
        "2026-05-24 09:00:01 INFO record_fill_failed order_id=ORDER-1 dedupe_key_type=fallback appsecret=secret\n",
        encoding="utf-8",
    )

    service = UIService(config_path, tmp_path / "runtime.json")
    status = service.status()
    assert status["risk_banner"]["live_trading"] is True
    assert status["account_risk"]["total_open_lot_count"] == 1
    assert service.stocks()[0]["risk_block_reasons"] == ""
    assert service.lots()[0]["lot_id"] == "LOT-1"
    fill_types = {row["dedupe_key_type"] for row in service.fills()}
    assert fill_types == {"execution_id", "fallback"}
    log_text = "\n".join(service.logs_tail()["lines"])
    assert "account=***" in log_text
    assert "token=***" in log_text
    assert "appsecret=***" in log_text
    assert "token=secret" not in log_text
    assert "appsecret=secret" not in log_text


def test_config_validation_backup_atomic_save_and_stock_patch(tmp_path):
    config_path, _, _ = _write_config(tmp_path)
    service = UIService(config_path, tmp_path / "runtime.json")

    valid, errors = service.validate_config_data(service.raw_config())
    assert valid is True
    assert errors == []
    invalid = service.save_config_patch({"strategy": {"initial_buy_amount": -1}})
    assert invalid["saved"] is False

    saved = service.save_config_patch({"risk": {"max_active_symbols": 7}})
    assert saved["saved"] is True
    assert (config_path.parent / "backups").exists()
    assert service.config.risk.max_active_symbols == 7

    patched = service.patch_stock("005930", {"enabled": False, "investment_alert": True})
    assert patched["saved"] is True
    stock = service.raw_config()["stocks"][0]
    assert stock["enabled"] is False
    assert stock["investment_alert"] is True


def test_config_schema_metadata_and_danger_flags(tmp_path):
    config_path, _, _ = _write_config(tmp_path)
    service = UIService(config_path, tmp_path / "runtime.json")

    schema = service.config_schema()
    by_key = {item["key"]: item for item in schema["metadata"]}
    for key in (
        "strategy.initial_buy_amount",
        "strategy.pnl_minus_threshold",
        "strategy.cleanup_profit_offset_ratio",
        "risk.max_total_open_lots",
        "order.live_trading",
        "order.enable_execution_raw_log",
        "market_hours.open_time",
        "storage_path",
    ):
        assert by_key[key]["label_ko"]
        assert by_key[key]["description_ko"]
        assert by_key[key]["unit"]
    assert by_key["strategy.pnl_minus_threshold"]["display_format"] == "decimal_percent"
    assert by_key["strategy.pnl_minus_threshold"]["config_format"] == "decimal_rate"
    assert by_key["strategy.exposure_buy_bands"]["config_format"] == "json"
    assert "strategy.reentry_drop_rate" not in by_key
    assert "normal_exit_anchor_price" in by_key["strategy.normal_reentry_drop_rate"]["description_ko"]
    assert "cycle_sell_vwap_price" in by_key["strategy.normal_reentry_drop_rate"]["description_ko"]
    assert "trailing_exit_anchor_price" in by_key["strategy.trailing_activation_gain"]["description_ko"]
    assert "reference_buy_price" in by_key["strategy.exposure_buy_bands"]["description_ko"]
    assert "manual_order_requests" in by_key["ui_manual_trading_enabled"]["description_ko"]
    assert "몇 번 확인" in by_key["order.price_sample_count"]["description_ko"]
    assert "각 읽기 사이" in by_key["order.price_sample_interval_seconds"]["description_ko"]
    assert "지정가" in by_key["order.buy_limit_markup_pct"]["description_ko"]
    assert "order.live_trading" in schema["danger_confirm_keys"]
    assert "order.enable_execution_raw_log" in schema["danger_confirm_keys"]


def test_config_form_and_table_sorting_scripts_are_present():
    assert "function sortRows" in INDEX_HTML
    assert "function sortValue" in INDEX_HTML
    assert "table(rows, 'stocks', {actions:true})" in INDEX_HTML
    assert "table(o, 'orders')" in INDEX_HTML
    assert "table(f, 'fills')" in INDEX_HTML
    assert "function renderConfigField" in INDEX_HTML
    assert "decimal_rate" in INDEX_HTML
    assert "danger_confirm_required" in INDEX_HTML
    assert "고급 / 원본 JSON 보기" in INDEX_HTML
    assert "보유 상태" in INDEX_HTML
    assert "position_state" in INDEX_HTML
    assert "전체 주문 일시정지" in INDEX_HTML
    assert "Emergency Stop 비상정지" in INDEX_HTML
    assert "수동 주문 요청" in INDEX_HTML
    assert "UI는 KIS 주문 API를 직접 호출하지" in INDEX_HTML
    assert "manual order request" in INDEX_HTML
    assert "종목코드" in INDEX_HTML
    assert "잔여 수량" in INDEX_HTML
    assert "중복방지 키" in INDEX_HTML
    assert "trading_halted:'거래정지'" in INDEX_HTML
    assert "administrative_issue:'관리종목 이슈'" in INDEX_HTML
    assert "investment_alert:'투자주의/경고'" in INDEX_HTML
    assert "audit_opinion_issue:'감사의견 이슈'" in INDEX_HTML
    assert "delisting_risk:'상장폐지 위험'" in INDEX_HTML
    assert "accounting_issue:'회계 이슈'" in INDEX_HTML
    assert "liquidity_warning:'유동성 경고'" in INDEX_HTML
    assert "openStockLots" in INDEX_HTML
    assert "LOT 보기" in INDEX_HTML
    assert "openManualSell" in INDEX_HTML
    assert "tableWrap" in INDEX_HTML
    assert "DEFAULT_COLUMNS" in INDEX_HTML
    assert "columnControls" in INDEX_HTML
    assert "showAllColumns" in INDEX_HTML
    assert "전체보기" in INDEX_HTML
    assert "핵심 컬럼" in INDEX_HTML
    assert "function setupAutoRefresh" in INDEX_HTML
    assert "function manualRefresh" in INDEX_HTML
    assert "새로고침" in INDEX_HTML
    assert "자동 갱신" in INDEX_HTML
    assert "Start / 루프 재개" in INDEX_HTML
    assert "Reset / Config 다시 읽기" in INDEX_HTML
    assert "/api/runtime/reload-config" in INDEX_HTML
    assert "manualBuyPrice" in INDEX_HTML
    assert "manualSellPrice" in INDEX_HTML
    assert "Execution Check" not in INDEX_HTML


def test_runtime_controls_are_readable_and_block_actions(tmp_path):
    config_path, _, _ = _write_config(tmp_path)
    runtime_path = tmp_path / "runtime.json"
    service = UIService(config_path, runtime_path)

    assert service.runtime_status()["all_orders_paused"] is False
    paused = service.runtime_set(all_orders_paused=True, reason="test")
    assert paused["all_orders_paused"] is True
    action = StrategyAction(OrderSide.BUY, 30000, None, "initial_buy")
    assert runtime_block_reason(RuntimeControl(all_orders_paused=True), action) == "runtime_all_orders_paused"
    assert runtime_block_reason(RuntimeControl(buy_paused=True), action) == "runtime_buy_paused"
    sell = StrategyAction(OrderSide.SELL, 0, 1, "profit_take", sell_reason=SellReason.PROFIT_TAKE.value)
    assert runtime_block_reason(RuntimeControl(sell_paused=True), sell) == "runtime_sell_paused"
    cleanup = StrategyAction(OrderSide.SELL, 0, 1, "cleanup_sell", cleanup_flag=True)
    assert runtime_block_reason(RuntimeControl(cleanup_paused=True), cleanup) == "runtime_cleanup_paused"
    reentry = StrategyAction(OrderSide.BUY, 30000, None, "reentry_buy", reentry_type=ReentryType.NORMAL_REENTRY.value)
    assert runtime_block_reason(RuntimeControl(reentry_paused=True), reentry) == "runtime_reentry_paused"


def test_runtime_loop_pause_and_config_reload_flags(tmp_path):
    config_path, _, _ = _write_config(tmp_path)
    runtime_path = tmp_path / "runtime.json"
    service = UIService(config_path, runtime_path)

    paused = service.runtime_set(bot_paused=True, reason="ui_pause_loop")
    assert paused["bot_paused"] is True
    assert paused["reason"] == "ui_pause_loop"

    reload_requested = service.runtime_set(config_reload_requested=True, reason="ui_reload_config")
    assert reload_requested["config_reload_requested"] is True
    assert reload_requested["config_reload_requested_at"]

    resumed = service.runtime_set(bot_paused=False, reason="ui_start_loop")
    assert resumed["bot_paused"] is False
    assert resumed["config_reload_requested"] is True


def test_execution_mapping_and_reconciliation_logs(tmp_path):
    config_path, _, log_path = _write_config(tmp_path)
    log_path.write_text(
        "2026-05-24 09:00:00 INFO startup_execution_reconcile fetched_execution_count=2 "
        "new_fill_count=1 duplicate_fill_count=1 ignored_unmatched_execution_count=0\n"
        "2026-05-24 09:00:01 INFO kis_raw_executions raw_execution_count=1 "
        "has_order_no=True has_filled_at=True has_side=True has_execution_id=False "
        "fallback_dedupe_fields=order_id,code,side,lot_id,price,quantity,filled_at\n",
        encoding="utf-8",
    )
    service = UIService(config_path, tmp_path / "runtime.json")
    mapping = service.execution_mapping_status()
    assert mapping["status"] == "PASS"
    assert mapping["has_order_no"] == "True"
    reconciliation = service.status()["reconciliation"]
    assert reconciliation["fetched_execution_count"] == "2"
    assert reconciliation["new_fill_count"] == "1"
    assert reconciliation["duplicate_fill_count"] == "1"


def test_http_api_status_config_runtime_and_no_order_endpoint(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path)
    _seed_store(db_path)
    server = build_server(config_path, "127.0.0.1", 0, tmp_path / "runtime.json")
    try:
        host, port = server.server_address
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["risk_banner"]["live_trading"] is True

        connection.request("GET", "/api/runtime")
        runtime = json.loads(connection.getresponse().read().decode("utf-8"))
        assert runtime["all_orders_paused"] is False

        connection.request("GET", "/api/config/schema")
        schema = json.loads(connection.getresponse().read().decode("utf-8"))
        assert schema["sections"]["Order"]
        assert "order.live_trading" in schema["danger_confirm_keys"]

        connection.request("POST", "/api/runtime/pause-cleanup", body="{}", headers={"content-type": "application/json"})
        paused = json.loads(connection.getresponse().read().decode("utf-8"))
        assert paused["cleanup_paused"] is True

        connection.request("POST", "/api/runtime/pause-loop", body="{}", headers={"content-type": "application/json"})
        loop_paused = json.loads(connection.getresponse().read().decode("utf-8"))
        assert loop_paused["bot_paused"] is True

        connection.request("POST", "/api/runtime/start-loop", body="{}", headers={"content-type": "application/json"})
        loop_started = json.loads(connection.getresponse().read().decode("utf-8"))
        assert loop_started["bot_paused"] is False

        connection.request("POST", "/api/runtime/reload-config", body="{}", headers={"content-type": "application/json"})
        reload_requested = json.loads(connection.getresponse().read().decode("utf-8"))
        assert reload_requested["config_reload_requested"] is True
        assert reload_requested["config_reload_requested_at"]

        connection.request("GET", "/api/place-order")
        missing = connection.getresponse()
        assert missing.status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_decision_preview_is_dry_run_and_reports_runtime_block(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path)
    _seed_store(db_path)
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(RuntimeControl(all_orders_paused=True), runtime_path)
    service = UIService(config_path, runtime_path)

    preview = service.decision_preview("005930", current_price=10600)
    assert preview["dry_run"] is True
    assert preview["order_api_called"] is False
    assert preview["previews"]
    if preview["previews"][0]["action_created"]:
        assert preview["previews"][0]["final_block_reason"] == "runtime_all_orders_paused"
        assert preview["previews"][0]["action_execution_state"] == "BLOCKED"


def test_manual_order_disabled_blocks_ui_api_request(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path, manual_enabled=False)
    _seed_store(db_path)
    service = UIService(config_path, tmp_path / "runtime.json")

    preview = service.manual_order_preview({"side": "BUY", "code": "005930", "amount": 30000, "confirm_text": "수동주문 확인"})
    assert preview["can_create"] is False
    assert "ui_manual_trading_disabled" in preview["block_reasons"]
    created = service.create_manual_order_request({"side": "BUY", "code": "005930", "amount": 30000, "confirm_text": "수동주문 확인"})
    assert created["created"] is False
    assert service.manual_order_requests() == []


def test_manual_buy_request_created_when_enabled_and_confirmed(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path, manual_enabled=True, live_trading=True)
    store = StateStore(db_path)
    store.save_position(PositionState("005930", "Samsung", current_price=10000))
    service = UIService(config_path, tmp_path / "runtime.json")

    blocked = service.manual_order_preview({"side": "BUY", "code": "005930", "amount": 30000})
    assert "confirm_text_required" in blocked["block_reasons"]
    created = service.create_manual_order_request({"side": "BUY", "code": "005930", "amount": 30000, "confirm_text": "수동주문 확인"})
    assert created["created"] is True
    requests = service.manual_order_requests()
    assert requests[0]["request_id"] == created["request_id"]
    assert requests[0]["status"] == "REQUESTED"
    assert requests[0]["confirm_text_verified"] is True
    assert requests[0]["current_price"] == 10000


def test_manual_buy_preview_reports_lot_sizing_and_blocks_disabled_band(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path, manual_enabled=True, live_trading=False)
    store = StateStore(db_path)
    store.save_position(PositionState("005930", "Samsung", current_price=10100))
    service = UIService(config_path, tmp_path / "runtime.json")

    preview = service.manual_order_preview({"side": "BUY", "code": "005930", "current_price": 10100})

    assert preview["can_create"] is True
    assert preview["lot_unit_amount"] == 30000
    assert preview["max_symbol_amount"] == 300000
    assert preview["price_lot_band"] == "10001-30000"

    blocked = service.manual_order_preview({"side": "BUY", "code": "005930", "current_price": 250})
    assert "lot_sizing_band_disabled" in blocked["block_reasons"]


def test_manual_sell_preview_blocks_closed_and_excess_quantity(tmp_path):
    config_path, db_path, _ = _write_config(tmp_path, manual_enabled=True, live_trading=False)
    store = _seed_store(db_path)
    lot = store.load_lots()["LOT-1"]
    lot.remaining_quantity = 0
    lot.status = "CLOSED"
    store.save_lot(lot)
    service = UIService(config_path, tmp_path / "runtime.json")

    closed = service.manual_order_preview({"side": "SELL", "code": "005930", "lot_id": "LOT-1", "quantity": 1})
    assert "closed_lot" in closed["block_reasons"]
    lot.remaining_quantity = 2
    lot.status = "OPEN"
    store.save_lot(lot)
    excess = service.manual_order_preview({"side": "SELL", "code": "005930", "lot_id": "LOT-1", "quantity": 3})
    assert "quantity_exceeds_remaining" in excess["block_reasons"]


class _StableSampler:
    def sample(self, code, name):
        return (Quote(code, 10000, datetime.now(), name),)

    def stable(self, samples, limit):
        return True, ""


class _PriceSampler:
    def __init__(self, price: int) -> None:
        self.price = price

    def sample(self, code, name):
        return (Quote(code, self.price, datetime.now(), name),)

    def stable(self, samples, limit):
        return True, ""


def test_bot_core_consumes_manual_request_through_order_manager_paper_path(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "bot.log"
    config = BotConfig(
        stocks=(StockConfig("005930", "Samsung"),),
        order=OrderConfig(live_trading=False),
        storage_path=str(db_path),
        log_path=str(log_path),
        ui_manual_trading_enabled=True,
    )
    trader = AutoTrader(config, use_mock_client=True)
    trader.price_sampler = _StableSampler()
    trader.store.create_manual_order_request(
        {
            "request_id": "MANUAL-1",
            "source": "local_ui_manual",
            "requested_by": "test",
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "code": "005930",
            "side": "BUY",
            "amount": 30000,
            "quantity": 0,
            "preview_json": "{}",
            "runtime_snapshot_json": "{}",
            "live_trading": False,
            "confirm_text_verified": True,
            "status": "REQUESTED",
        }
    )

    trader.process_manual_order_requests(AccountSnapshot(10_000_000, 10_000_000, 0, 0), RiskDecision(True))

    request = trader.store.manual_order_requests()[0]
    assert request["status"] == "FILLED"
    assert request["linked_order_id"]
    assert trader.store.load_lots()
    assert trader.store.load_positions()["005930"].quantity > 0


def test_manual_buy_blocks_when_lot_sizing_bucket_changes_after_preview(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "bot.log"
    config = BotConfig(
        stocks=(StockConfig("005930", "Samsung"),),
        order=OrderConfig(live_trading=False),
        storage_path=str(db_path),
        log_path=str(log_path),
        ui_manual_trading_enabled=True,
    )
    trader = AutoTrader(config, use_mock_client=True)
    trader.price_sampler = _PriceSampler(9000)
    trader.store.create_manual_order_request(
        {
            "request_id": "MANUAL-LOT-SIZING-CHANGED",
            "source": "local_ui_manual",
            "requested_by": "test",
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "code": "005930",
            "side": "BUY",
            "current_price": 10100,
            "amount": 30000,
            "quantity": 0,
            "preview_json": json.dumps(
                {
                    "price_lot_band": "10001-30000",
                    "lot_unit_amount": 30000,
                    "max_symbol_amount": 300000,
                }
            ),
            "runtime_snapshot_json": "{}",
            "live_trading": False,
            "confirm_text_verified": True,
            "status": "REQUESTED",
        }
    )

    trader.process_manual_order_requests(AccountSnapshot(10_000_000, 10_000_000, 0, 0), RiskDecision(True))

    request = trader.store.manual_order_requests()[0]
    assert request["status"] == "BLOCKED"
    assert request["block_reason"] == "lot_sizing_changed_after_preview"
    assert not trader.store.load_lots()


def test_bot_loop_interrupts_promptly_for_runtime_pause(tmp_path):
    config = BotConfig(
        stocks=(StockConfig("005930", "Samsung"),),
        order=OrderConfig(live_trading=False),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "bot.log"),
    )
    trader = AutoTrader(config, use_mock_client=True)

    with patch("kis_msj.main.load_runtime_control", return_value=RuntimeControl(bot_paused=True, reason="test_pause")):
        assert trader.run_once() == "bot_paused"
    assert trader.store.open_order_count() == 0
