import csv
import json
import sqlite3
from datetime import datetime

from kis_msj.config import BotConfig, OrderConfig, StockConfig, config_hash
from kis_msj.main import AutoTrader
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, TradeFill
from kis_msj.risk_manager import RiskDecision
from kis_msj.storage import StateStore

from scripts.export_analysis_dataset import export_analysis_dataset


def _config_file(tmp_path, db_path):
    config_path = tmp_path / "lot_auto_trader.json"
    config_path.write_text(
        json.dumps(
            {
                "stocks": [{"code": "005930", "name": "삼성전자", "enabled": True}],
                "storage_path": str(db_path),
                "log_path": str(tmp_path / "trade.log"),
                "run_id": "cheap_random_bootstrap_v1",
                "experiment_name": "cheap_random_bootstrap",
                "order": {"live_trading": False, "price_sample_interval_seconds": 0},
                "risk": {"profile": "analysis_test"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


def test_config_snapshot_and_decision_are_persisted(tmp_path):
    config = BotConfig(
        stocks=(StockConfig("005930", "삼성전자"),),
        order=OrderConfig(price_sample_interval_seconds=0),
        run_id="RUN-1",
        experiment_name="experiment-1",
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trade.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    expected_hash = config_hash(config)

    bot.log_symbol_decision(PositionState(code="005930", name="삼성전자"), 70000, None, RiskDecision(True), RiskDecision(True), "NONE")

    with sqlite3.connect(config.storage_path) as connection:
        snapshot = connection.execute("SELECT config_hash, source, run_id, experiment_name FROM config_snapshots").fetchone()
        decision = connection.execute("SELECT config_hash, code, run_id, experiment_name, payload_json FROM decisions").fetchone()

    assert snapshot[0] == expected_hash
    assert snapshot[1] == "bot_init"
    assert snapshot[2] == "RUN-1"
    assert snapshot[3] == "experiment-1"
    assert decision[0] == expected_hash
    assert decision[1] == "005930"
    assert decision[2] == "RUN-1"
    assert decision[3] == "experiment-1"
    payload = json.loads(decision[4])
    assert payload["config_hash"] == expected_hash
    assert payload["run_id"] == "RUN-1"
    assert payload["experiment_name"] == "experiment-1"
    assert payload["risk_profile"] == "default"


def test_orders_fills_and_manual_requests_keep_config_hash(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.set_active_config("HASH-1", "VERSION-1", run_id="RUN-1", experiment_name="experiment-1")
    request = OrderRequest("005930", "삼성전자", OrderSide.BUY, 1, 70000, "initial_buy")
    store.record_order(OrderResult(request, "ORDER-1", OrderStatus.REQUESTED, "requested"))
    store.record_fill(TradeFill("005930", "삼성전자", OrderSide.BUY, 1, 70000, "ORDER-1", datetime.now(), "LOT-1", execution_id="EXEC-1"))
    store.create_manual_order_request(
        {
            "request_id": "MANUAL-1",
            "code": "005930",
            "side": "BUY",
            "quantity": 1,
            "status": "REQUESTED",
        }
    )

    with sqlite3.connect(store.path) as connection:
        order_hash = connection.execute("SELECT config_hash, config_version, run_id, experiment_name FROM orders").fetchone()
        fill_hash = connection.execute("SELECT config_hash, config_version, run_id, experiment_name FROM fills").fetchone()
        manual_hash = connection.execute("SELECT config_hash, config_version, run_id, experiment_name FROM manual_order_requests").fetchone()

    assert tuple(order_hash) == ("HASH-1", "VERSION-1", "RUN-1", "experiment-1")
    assert tuple(fill_hash) == ("HASH-1", "VERSION-1", "RUN-1", "experiment-1")
    assert tuple(manual_hash) == ("HASH-1", "VERSION-1", "RUN-1", "experiment-1")


def test_export_analysis_dataset_creates_sanitized_files_without_mutating_db(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    config_path = _config_file(tmp_path, db_path)
    store = StateStore(db_path)
    store.set_active_config("HASH-EXPORT")
    store.record_config_snapshot(
        "HASH-EXPORT",
        {
            "kis_account": {"account_number_env": "KIS_ACCOUNT_NUMBER", "appsecret": "SHOULD_NOT_LEAK"},
            "order": {"live_trading": False},
        },
        source="test",
        run_id="RUN-EXPORT",
        experiment_name="experiment-export",
    )
    store.save_position(PositionState(code="005930", name="삼성전자", quantity=1, current_price=70000, cumulative_invested_amount=70000))
    request = OrderRequest("005930", "삼성전자", OrderSide.BUY, 1, 70000, "initial_buy")
    store.record_order(OrderResult(request, "ORDER-1", OrderStatus.FILLED, "filled"))
    store.record_fill(TradeFill("005930", "삼성전자", OrderSide.BUY, 1, 70000, "ORDER-1", datetime.now(), "LOT-1", execution_id="EXEC-1"))

    before = db_path.read_bytes()
    result = export_analysis_dataset(config_path, tmp_path / "analysis")
    after = db_path.read_bytes()

    assert before == after
    output = tmp_path / "analysis"
    assert (output / "orders.csv").exists()
    assert (output / "fills.csv").exists()
    assert (output / "decisions.csv").exists()
    assert (output / "config_snapshots.jsonl").exists()
    assert (output / "skipped_actions.csv").exists()
    assert (output / "metadata.json").exists()
    assert (output / "README.md").exists()
    assert (output / "summary.json").exists()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["kis_order_api_called"] is False
    assert summary["db_mutated"] is False
    assert summary["total_buy_fills"] == 1
    assert "RUN-EXPORT" in summary["run_ids"]
    snapshots = (output / "config_snapshots.jsonl").read_text(encoding="utf-8")
    assert "SHOULD_NOT_LEAK" not in snapshots
    assert "***" in snapshots
    with (output / "orders.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["config_hash"] == "HASH-EXPORT"
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["row_counts"]["fills"] == 1
    assert "HASH-EXPORT" in metadata["config_hashes"]


def test_blocked_decision_exports_skipped_actions_with_run_id(tmp_path):
    config = BotConfig(
        stocks=(StockConfig("005930", "삼성전자"),),
        order=OrderConfig(price_sample_interval_seconds=0),
        run_id="RUN-BLOCKED",
        experiment_name="blocked-test",
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trade.log"),
    )
    bot = AutoTrader(config, use_mock_client=True)
    bot.log_symbol_decision(
        PositionState(code="005930", name="삼성전자", position_state="HOLDING"),
        70000,
        None,
        RiskDecision(True),
        RiskDecision(True),
        "add_buy",
        final_block_reason="max_total_open_lots_reached",
        action_created=True,
    )

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"storage_path": config.storage_path, "log_path": config.log_path}, ensure_ascii=False),
        encoding="utf-8",
    )
    export_analysis_dataset(config_path, tmp_path / "analysis")

    with (tmp_path / "analysis" / "skipped_actions.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "RUN-BLOCKED"
    assert rows[0]["candidate_action_type"] == "add_buy"
    assert rows[0]["block_reason"] == "max_total_open_lots_reached"
