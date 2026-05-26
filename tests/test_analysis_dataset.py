import csv
import json
import sqlite3
from datetime import datetime

from kis_msj.config import BotConfig, OrderConfig, StockConfig, config_hash
from kis_msj.main import AutoTrader
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, Quote, TradeFill
from kis_msj.risk_manager import RiskDecision
from kis_msj.storage import StateStore

from scripts.collect_market_data import collect_market_data
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
        decision = connection.execute("SELECT config_hash, code, run_id, experiment_name, price_snapshot_id, payload_json FROM decisions").fetchone()
        price_snapshot = connection.execute("SELECT code, current_price, source, missing_fields_json FROM price_snapshots").fetchone()

    assert snapshot[0] == expected_hash
    assert snapshot[1] == "bot_init"
    assert snapshot[2] == "RUN-1"
    assert snapshot[3] == "experiment-1"
    assert decision[0] == expected_hash
    assert decision[1] == "005930"
    assert decision[2] == "RUN-1"
    assert decision[3] == "experiment-1"
    assert decision[4] > 0
    assert tuple(price_snapshot[:3]) == ("005930", 70000, "MockKisClient")
    assert "volume" in price_snapshot[3]
    payload = json.loads(decision[5])
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
    assert rows[0]["decision_id"]
    assert rows[0]["price_snapshot_id"]
    assert rows[0]["candidate_action_type"] == "add_buy"
    assert rows[0]["block_reason"] == "max_total_open_lots_reached"


def test_experiment_section_overrides_legacy_top_level_run_metadata(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    config_path = tmp_path / "lot_auto_trader.json"
    config_path.write_text(
        json.dumps(
            {
                "stocks": [{"code": "005930", "name": "Samsung", "enabled": True}],
                "storage_path": str(db_path),
                "log_path": str(tmp_path / "trade.log"),
                "run_id": "legacy-run",
                "experiment_name": "legacy-experiment",
                "experiment": {
                    "run_id": "explicit-run",
                    "experiment_name": "explicit-experiment",
                    "operator_note": "note",
                    "purpose": "logic_validation",
                },
                "order": {"live_trading": False, "price_sample_interval_seconds": 0},
            }
        ),
        encoding="utf-8",
    )
    from kis_msj.config import load_config

    config = load_config(config_path)
    bot = AutoTrader(config, use_mock_client=True)

    assert bot.run_id == "explicit-run"
    assert bot.experiment_name == "explicit-experiment"
    with sqlite3.connect(config.storage_path) as connection:
        row = connection.execute("SELECT run_id, experiment_name, operator_note FROM config_snapshots").fetchone()
    assert tuple(row) == ("explicit-run", "explicit-experiment", "note")


def test_collect_market_data_dry_run_does_not_mutate_db(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    config_path = _config_file(tmp_path, db_path)
    StateStore(db_path)
    before = db_path.read_bytes()

    result = collect_market_data(
        config_path,
        codes=("005930",),
        snapshot=True,
        daily=True,
        execute=False,
        sleep_seconds=0,
        fetcher=_fake_quote,
    )

    assert result["dry_run"] is True
    assert db_path.read_bytes() == before
    assert result["kis_order_api_called"] is False


def test_collect_market_data_execute_stores_snapshot_and_daily_upsert(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    config_path = _config_file(tmp_path, db_path)

    for _ in range(2):
        collect_market_data(
            config_path,
            codes=("005930",),
            snapshot=True,
            daily=True,
            execute=True,
            sleep_seconds=0,
            fetcher=_fake_quote,
        )

    with sqlite3.connect(db_path) as connection:
        price_count = connection.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
        daily_count = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        daily = connection.execute("SELECT close, volume, trading_value FROM daily_prices").fetchone()
    assert price_count == 2
    assert daily_count == 1
    assert tuple(daily) == (70100, 123456, 987654321)


def test_export_analysis_dataset_includes_market_data_counts(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    config_path = _config_file(tmp_path, db_path)
    store = StateStore(db_path)
    store.set_active_config("HASH-MARKET", run_id="RUN-MARKET", experiment_name="market-test")
    snapshot_id = store.record_price_snapshot(
        {
            "code": "005930",
            "sampled_at": "2026-05-27T09:00:00",
            "current_price": 70100,
            "source": "test",
            "missing_fields": ["bid_price"],
        }
    )
    store.upsert_daily_price({"code": "005930", "date": "2026-05-27", "open": 70000, "high": 71000, "low": 69900, "close": 70100, "volume": 10, "trading_value": 701000, "source": "test"})
    assert snapshot_id > 0

    export_analysis_dataset(config_path, tmp_path / "analysis")

    assert (tmp_path / "analysis" / "price_snapshots.csv").exists()
    assert (tmp_path / "analysis" / "daily_prices.csv").exists()
    summary = json.loads((tmp_path / "analysis" / "summary.json").read_text(encoding="utf-8"))
    assert summary["price_snapshots_count"] == 1
    assert summary["daily_prices_count"] == 1
    assert summary["symbols_with_price_data_count"] == 1
    metadata = json.loads((tmp_path / "analysis" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["row_counts"]["price_snapshots"] == 1


def _fake_quote(code: str) -> dict[str, object]:
    return {
        "short_code": code,
        "korean_name": "Samsung",
        "current_price": 70100,
        "accumulated_volume": 123456,
        "accumulated_trade_amount": 987654321,
        "open_price": 70000,
        "high_price": 71000,
        "low_price": 69900,
        "previous_day_diff": 100,
    }
