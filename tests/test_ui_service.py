from __future__ import annotations

import http.client
import json
from dataclasses import asdict
from datetime import datetime

from kis_msj.config import BotConfig, StockConfig
from kis_msj.models import (
    LotState,
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
from kis_msj.runtime_control import RuntimeControl, runtime_block_reason, save_runtime_control
from kis_msj.storage import StateStore
from kis_msj.strategy import StrategyAction
from kis_msj.ui_server import INDEX_HTML, build_server
from kis_msj.ui_service import UIService


def _write_config(tmp_path, *, live_trading: bool = True, cleanup_enabled: bool = True):
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "bot.log"
    raw = asdict(
        BotConfig(
            stocks=(StockConfig("005930", "Samsung"),),
            storage_path=str(db_path),
            log_path=str(log_path),
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
    assert "order.live_trading" in schema["danger_confirm_keys"]
    assert "order.enable_execution_raw_log" in schema["danger_confirm_keys"]


def test_config_form_and_table_sorting_scripts_are_present():
    assert "function sortRows" in INDEX_HTML
    assert "function sortValue" in INDEX_HTML
    assert "table(rows, 'stocks')" in INDEX_HTML
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
    assert "수동 주문 요청 구조 검토" in INDEX_HTML
    assert "UI 서버는 KIS 주문 API를 직접 호출하지" in INDEX_HTML
    assert "manual order request" in INDEX_HTML


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
    server = build_server(config_path, "127.0.0.1", 0)
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
