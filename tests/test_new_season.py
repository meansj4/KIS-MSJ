import importlib.util
import json
import sqlite3
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("prepare_new_season", Path("scripts/prepare_new_season.py"))
prepare_new_season = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(prepare_new_season)


def _write_config(tmp_path: Path, db_path: Path | None = None, log_path: Path | None = None) -> Path:
    config_path = tmp_path / "lot_auto_trader.json"
    config_path.write_text(
        json.dumps(
            {
                "stocks": [],
                "strategy": {"cleanup_enabled": True},
                "risk": {},
                "order": {"live_trading": True, "enable_execution_raw_log": False},
                "storage_path": str(db_path or tmp_path / "state.sqlite3"),
                "log_path": str(log_path or tmp_path / "lot_auto_trader.log"),
                "ui_manual_trading_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


def test_expansion_100_candidates_are_unique_and_mark_event_risk() -> None:
    result = prepare_new_season.validate_candidates()

    assert result["count"] == 100
    assert result["duplicates"] == []
    assert result["invalid_format"] == []
    disabled_codes = {item["code"] for item in result["risk_disabled"]}
    assert {"005935", "001230", "020560"}.issubset(disabled_codes)
    asiana = [item for item in result["risk_disabled"] if item["code"] == "020560"][0]
    assert asiana["enabled"] is False
    assert asiana["manual_only"] is True
    assert asiana["administrative_issue"] is True
    dongkuk = [item for item in result["risk_disabled"] if item["code"] == "001230"][0]
    assert dongkuk["trading_halted"] is True


def test_apply_expansion_config_safe_profile(tmp_path) -> None:
    config_path = _write_config(tmp_path)

    dry_run = prepare_new_season.apply_expansion_config(config_path, "expansion_100_safe", dry_run=True)
    assert dry_run["dry_run"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["stocks"] == []

    result = prepare_new_season.apply_expansion_config(config_path, "expansion_100_safe", dry_run=False)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["stock_count"] == 100
    assert result["enabled_count"] == 97
    assert config["risk"]["profile"] == "expansion_100_safe"
    assert config["risk"]["max_active_symbols"] == 100
    assert config["risk"]["max_new_buy_per_day"] == 10
    assert config["risk"]["max_new_buy_amount_per_day"] == 2_000_000
    assert config["risk"]["max_total_initial_buy_amount_per_day"] == 2_000_000
    assert config["risk"]["max_total_open_lots"] == 300
    assert config["risk"]["max_total_invested_amount"] == 20_000_000
    assert config["strategy"]["cleanup_enabled"] is False
    assert config["order"]["live_trading"] is False
    assert config["order"]["enable_execution_raw_log"] is True
    assert config["ui_manual_trading_enabled"] is False


def test_archive_dry_run_does_not_create_archive(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "lot_auto_trader.log"
    db_path.write_bytes(b"")
    log_path.write_text("log", encoding="utf-8")
    config_path = _write_config(tmp_path, db_path, log_path)
    archive_root = tmp_path / "archive"

    result = prepare_new_season.archive_current_state(config_path, archive_root, dry_run=True)

    assert result["dry_run"] is True
    assert not archive_root.exists()


def test_archive_exports_existing_tables(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "lot_auto_trader.log"
    config_path = _write_config(tmp_path, db_path, log_path)
    log_path.write_text("log", encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE orders (order_id TEXT, status TEXT)")
        connection.execute("INSERT INTO orders VALUES ('ORDER-1', 'FILLED')")
    archive_root = tmp_path / "archive"

    result = prepare_new_season.archive_current_state(config_path, archive_root, dry_run=False)
    root = Path(result["archive_root"])

    assert (root / "config" / config_path.name).exists()
    assert (root / "db" / db_path.name).exists()
    assert (root / "logs" / log_path.name).exists()
    assert (root / "exports" / "orders.csv").exists()


def test_reset_requires_confirm_and_blocks_open_orders(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE orders (status TEXT)")
        connection.execute("INSERT INTO orders VALUES ('REQUESTED')")

    assert prepare_new_season.reset_db(config_path, "", dry_run=False)["reason"] == "confirm_required"
    blocked = prepare_new_season.reset_db(config_path, prepare_new_season.CONFIRM_RESET, dry_run=False)
    assert blocked["reason"] == "reset_blocked_by_open_order_or_sync_mismatch"
    assert db_path.exists()


def test_reset_blocks_pending_manual_requests_and_open_lots(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE manual_order_requests (status TEXT)")
        connection.execute("CREATE TABLE lots (remaining_quantity INTEGER, status TEXT)")
        connection.execute("INSERT INTO manual_order_requests VALUES ('SUBMITTED')")
        connection.execute("INSERT INTO lots VALUES (1, 'OPEN')")

    blocked = prepare_new_season.reset_db(config_path, prepare_new_season.CONFIRM_RESET, dry_run=False)

    assert blocked["reason"] == "reset_blocked_by_open_order_or_sync_mismatch"
    assert blocked["blockers"]["pending_manual_request_count"] == 1
    assert blocked["blockers"]["open_lot_count"] == 1
    assert db_path.exists()


def test_reset_dry_run_with_confirm_keeps_db(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE orders (status TEXT)")

    result = prepare_new_season.reset_db(config_path, prepare_new_season.CONFIRM_RESET, dry_run=True)

    assert result["reason"] == "dry_run"
    assert db_path.exists()


def test_liquidation_plan_does_not_create_orders_or_requests(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE lots (lot_id TEXT, code TEXT, remaining_quantity INTEGER, buy_price INTEGER, status TEXT, buy_filled_at TEXT)")
        connection.execute("CREATE TABLE positions (code TEXT, name TEXT, current_price INTEGER)")
        connection.execute("CREATE TABLE manual_order_requests (request_id TEXT)")
        connection.execute("INSERT INTO lots VALUES ('LOT-1', '005930', 2, 70000, 'OPEN', '2026-05-26T09:00:00')")
        connection.execute("INSERT INTO positions VALUES ('005930', '삼성전자', 71000)")

    result = prepare_new_season.liquidation_plan(config_path, tmp_path / "exports", dry_run=False)

    assert result["item_count"] == 1
    assert result["order_api_called"] is False
    assert result["manual_requests_created"] is False
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_order_requests").fetchone()[0] == 0


def test_liquidation_manual_requests_require_confirm_and_use_queue_only(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE manual_order_requests (
                request_id TEXT, source TEXT, requested_by TEXT, requested_at TEXT, code TEXT, side TEXT,
                current_price INTEGER, amount INTEGER, quantity INTEGER, lot_id TEXT, order_type TEXT,
                preview_json TEXT, runtime_snapshot_json TEXT, live_trading INTEGER, confirm_text_verified INTEGER,
                status TEXT, block_reason TEXT, linked_order_id TEXT, created_at TEXT, updated_at TEXT
            )
            """
        )
        connection.execute("CREATE TABLE orders (status TEXT)")
        connection.execute("CREATE TABLE positions (sync_status TEXT, lot_quantity_mismatch INTEGER)")
        connection.execute("CREATE TABLE lots (lot_id TEXT, code TEXT, remaining_quantity INTEGER, buy_price INTEGER, status TEXT, buy_filled_at TEXT)")
        connection.execute("CREATE TABLE positions_extra (code TEXT)")
        connection.execute("CREATE TABLE fills (id INTEGER)")
        connection.execute("DROP TABLE positions_extra")
        connection.execute("DROP TABLE fills")
        connection.execute("CREATE TABLE positions_tmp (code TEXT)")
        connection.execute("DROP TABLE positions_tmp")
        connection.execute("INSERT INTO lots VALUES ('LOT-1', '005930', 2, 70000, 'OPEN', '2026-05-26T09:00:00')")
        connection.execute("ALTER TABLE positions ADD COLUMN code TEXT")
        connection.execute("ALTER TABLE positions ADD COLUMN name TEXT")
        connection.execute("ALTER TABLE positions ADD COLUMN current_price INTEGER")
        connection.execute("INSERT INTO positions (sync_status, lot_quantity_mismatch, code, name, current_price) VALUES ('', 0, '005930', '삼성전자', 71000)")

    no_confirm = prepare_new_season.create_liquidation_manual_requests(config_path, "", dry_run=False)
    assert no_confirm["reason"] == "confirm_required"
    dry_run = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=True)
    assert dry_run["reason"] == "dry_run"
    created = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False)

    assert created["created"] is True
    assert created["request_count"] == 1
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT side, status, lot_id, quantity FROM manual_order_requests").fetchone()
    assert row == ("SELL", "REQUESTED", "LOT-1", 2)
