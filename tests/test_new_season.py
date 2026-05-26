import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta
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


def _seed_liquidation_db(db_path: Path, *, order_status: str = "", manual_status: str = "", sync_status: str = "", mismatch: int = 0) -> None:
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
        connection.execute("CREATE TABLE orders (code TEXT, side TEXT, quantity INTEGER, status TEXT)")
        connection.execute("CREATE TABLE positions (code TEXT, name TEXT, quantity INTEGER, current_price INTEGER, sync_status TEXT, lot_quantity_mismatch INTEGER)")
        connection.execute("CREATE TABLE lots (lot_id TEXT, code TEXT, remaining_quantity INTEGER, buy_price INTEGER, status TEXT, buy_filled_at TEXT)")
        connection.execute("INSERT INTO lots VALUES ('LOT-1', '005930', 2, 70000, 'OPEN', '2026-05-26T09:00:00')")
        connection.execute("INSERT INTO positions VALUES ('005930', 'Samsung Electronics', 2, 71000, ?, ?)", (sync_status, mismatch))
        if order_status:
            connection.execute("INSERT INTO orders VALUES ('005930', 'SELL', 2, ?)", (order_status,))
        if manual_status:
            connection.execute("INSERT INTO manual_order_requests (request_id, code, side, quantity, lot_id, status) VALUES ('MANUAL-1', '005930', 'SELL', 2, 'LOT-1', ?)", (manual_status,))


def _write_balance(
    tmp_path: Path,
    *,
    quantity: int = 2,
    sellable: int | None = 2,
    generated_at: str | None = None,
) -> Path:
    path = tmp_path / "kis_balance.json"
    row = {"code": "005930", "quantity": quantity}
    if sellable is not None:
        row["sellable_quantity"] = sellable
    payload = {"generated_at": generated_at or datetime.now().isoformat(timespec="seconds"), "positions": [row]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_expansion_100_candidates_are_unique_and_mark_event_risk() -> None:
    result = prepare_new_season.validate_candidates()

    assert result["count"] == 100
    assert result["duplicates"] == []
    assert result["invalid_format"] == []
    disabled_codes = {item["code"] for item in result["risk_disabled"]}
    assert {"005935", "001230", "020560"}.issubset(disabled_codes)
    assert [item for item in result["risk_disabled"] if item["code"] == "020560"][0]["administrative_issue"] is True
    assert [item for item in result["risk_disabled"] if item["code"] == "001230"][0]["trading_halted"] is True


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


def test_reset_blocks_defensive_pending_order_statuses(tmp_path) -> None:
    for status in prepare_new_season.PENDING_ORDER_STATUSES:
        case_dir = tmp_path / status
        case_dir.mkdir()
        db_path = case_dir / "state.sqlite3"
        config_path = _write_config(case_dir, db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE orders (status TEXT)")
            connection.execute("INSERT INTO orders VALUES (?)", (status,))

        blocked = prepare_new_season.reset_db(config_path, prepare_new_season.CONFIRM_RESET, dry_run=False)

        assert blocked["blockers"]["open_order_count"] == 1


def test_reset_allows_terminal_order_and_manual_statuses_without_lots(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE orders (status TEXT)")
        connection.execute("CREATE TABLE manual_order_requests (status TEXT)")
        connection.execute("CREATE TABLE lots (remaining_quantity INTEGER, status TEXT)")
        connection.execute("CREATE TABLE positions (sync_status TEXT, lot_quantity_mismatch INTEGER)")
        for status in prepare_new_season.TERMINAL_ORDER_STATUSES:
            connection.execute("INSERT INTO orders VALUES (?)", (status,))
        for status in prepare_new_season.TERMINAL_MANUAL_STATUSES:
            connection.execute("INSERT INTO manual_order_requests VALUES (?)", (status,))

    result = prepare_new_season.reset_db(config_path, prepare_new_season.CONFIRM_RESET, dry_run=True)

    assert result["reason"] == "dry_run"


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
    _seed_liquidation_db(db_path)

    result = prepare_new_season.liquidation_plan(config_path, tmp_path / "exports", dry_run=False)

    assert result["item_count"] == 1
    assert result["order_api_called"] is False
    assert result["manual_requests_created"] is False
    assert result["items"][0]["source"] == "db_only_dry_run"
    assert result["items"][0]["block_reason"] == "liquidation_kis_balance_fetch_required"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_order_requests").fetchone()[0] == 0


def test_liquidation_plan_preview_warns_when_snapshot_metadata_is_missing(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = tmp_path / "kis_balance.json"
    balance_path.write_text(json.dumps({"positions": [{"code": "005930", "quantity": 2}]}, ensure_ascii=False), encoding="utf-8")

    validation = prepare_new_season.validate_kis_balance_snapshot(balance_path, mode="preview")
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=True,
        kis_balances=validation["balances"],
        kis_balance_path=balance_path,
    )

    assert validation["valid"] is True
    assert "snapshot_generated_at_missing_warning" in plan["snapshot_warnings"]
    assert "snapshot_sellable_quantity_fallback_warning" in plan["snapshot_warnings"]
    assert plan["request_creation_allowed"] is False
    assert plan["request_creation_block_reason"] == "liquidation_kis_balance_snapshot_missing_generated_at"


def test_liquidation_request_requires_generated_at_and_sellable_quantity(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = tmp_path / "kis_balance.json"
    balance_path.write_text(json.dumps({"positions": [{"code": "005930", "quantity": 2}]}, ensure_ascii=False), encoding="utf-8")
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
    )

    result = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )

    assert result["reason"] == "liquidation_kis_balance_snapshot_missing_generated_at"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_order_requests").fetchone()[0] == 0


def test_liquidation_request_blocks_missing_sellable_even_when_generated_at_exists(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path, sellable=None)
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
    )

    result = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )

    assert result["reason"] == "liquidation_kis_sellable_quantity_missing"


def test_liquidation_request_blocks_invalid_or_stale_generated_at(tmp_path) -> None:
    for name, generated_at, expected in [
        ("invalid", "not-a-time", "liquidation_kis_balance_snapshot_invalid_generated_at"),
        ("stale", (datetime.now() - timedelta(minutes=120)).isoformat(timespec="seconds"), "liquidation_kis_balance_snapshot_stale"),
    ]:
        case_dir = tmp_path / name
        case_dir.mkdir()
        db_path = case_dir / "state.sqlite3"
        config_path = _write_config(case_dir, db_path)
        _seed_liquidation_db(db_path)
        balance_path = _write_balance(case_dir, generated_at=generated_at)
        plan = prepare_new_season.liquidation_plan(
            config_path,
            case_dir / "exports",
            dry_run=False,
            kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
            kis_balance_path=balance_path,
            max_age_minutes=60,
        )

        result = prepare_new_season.create_liquidation_manual_requests(
            config_path,
            prepare_new_season.CONFIRM_LIQUIDATION,
            dry_run=False,
            kis_balance_path=balance_path,
            plan_path=Path(plan["plan_path"]),
        )

        assert result["reason"] == expected


def test_liquidation_manual_requests_require_confirm_and_use_queue_only(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path)

    no_confirm = prepare_new_season.create_liquidation_manual_requests(config_path, "", dry_run=False)
    assert no_confirm["reason"] == "confirm_required"
    no_balance = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False)
    assert no_balance["reason"] == "liquidation_plan_missing"
    plan = prepare_new_season.liquidation_plan(config_path, tmp_path / "exports", dry_run=False, kis_balances=prepare_new_season.load_kis_balance_json(balance_path), kis_balance_path=balance_path)
    plan_path = Path(plan["plan_path"])
    dry_run = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=True, kis_balance_path=balance_path, plan_path=plan_path)
    assert dry_run["reason"] == "dry_run"
    created = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False, kis_balance_path=balance_path, plan_path=plan_path)

    assert created["created"] is True
    assert created["request_count"] == 1
    assert json.loads(plan_path.read_text(encoding="utf-8"))["status"] == "USED"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT side, status, lot_id, quantity FROM manual_order_requests").fetchone()
    assert row == ("SELL", "REQUESTED", "LOT-1", 2)


def test_liquidation_request_blocks_db_kis_mismatch_and_sellable_shortage(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)

    mismatch_balance = _write_balance(tmp_path, quantity=1, sellable=1)
    mismatch_plan = prepare_new_season.liquidation_plan(config_path, tmp_path / "exports_mismatch", dry_run=False, kis_balances=prepare_new_season.load_kis_balance_json(mismatch_balance), kis_balance_path=mismatch_balance)
    mismatch = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False, kis_balance_path=mismatch_balance, plan_path=Path(mismatch_plan["plan_path"]))
    assert mismatch["reason"] == "liquidation_plan_not_active"

    shortage_balance = _write_balance(tmp_path, quantity=2, sellable=1)
    shortage_plan = prepare_new_season.liquidation_plan(config_path, tmp_path / "exports_shortage", dry_run=False, kis_balances=prepare_new_season.load_kis_balance_json(shortage_balance), kis_balance_path=shortage_balance)
    shortage = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False, kis_balance_path=shortage_balance, plan_path=Path(shortage_plan["plan_path"]))
    assert shortage["reason"] == "liquidation_plan_not_active"


def test_liquidation_request_blocks_open_order_pending_manual_sync_and_mismatch(tmp_path) -> None:
    cases = [
        ("open_order", {"order_status": "REQUESTED"}),
        ("pending_manual", {"manual_status": "REQUESTED"}),
        ("sync", {"sync_status": "SYNC_REQUIRED"}),
        ("mismatch", {"mismatch": 1}),
    ]
    for name, kwargs in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        db_path = case_dir / "state.sqlite3"
        config_path = _write_config(case_dir, db_path)
        _seed_liquidation_db(db_path, **kwargs)

        balance_path = _write_balance(case_dir)
        plan = prepare_new_season.liquidation_plan(config_path, case_dir / "exports", dry_run=False, kis_balances=prepare_new_season.load_kis_balance_json(balance_path), kis_balance_path=balance_path)
        result = prepare_new_season.create_liquidation_manual_requests(config_path, prepare_new_season.CONFIRM_LIQUIDATION, dry_run=False, kis_balance_path=balance_path, plan_path=Path(plan["plan_path"]))

        assert result["reason"] in {"liquidation_plan_not_active", "liquidation_request_blocked_by_pending_work"}


def test_new_liquidation_plan_supersedes_existing_active_plan(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path)
    balances = prepare_new_season.load_kis_balance_json(balance_path)
    output_dir = tmp_path / "exports"

    first = prepare_new_season.liquidation_plan(config_path, output_dir, dry_run=False, kis_balances=balances, kis_balance_path=balance_path)
    second = prepare_new_season.liquidation_plan(config_path, output_dir, dry_run=False, kis_balances=balances, kis_balance_path=balance_path)

    assert json.loads(Path(first["plan_path"]).read_text(encoding="utf-8"))["status"] == "SUPERSEDED"
    assert json.loads(Path(second["plan_path"]).read_text(encoding="utf-8"))["status"] == "ACTIVE"
    assert first["db_open_lot_hash"]
    assert first["kis_snapshot_hash"]


def test_liquidation_request_blocks_stale_plan_after_db_change(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path)
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE lots SET remaining_quantity = 1 WHERE lot_id = 'LOT-1'")

    result = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )

    assert result["reason"] == "liquidation_plan_db_changed"


def test_liquidation_request_blocks_pending_work_created_after_plan(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path)
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO orders VALUES ('005930', 'SELL', 2, 'REQUESTED')")

    result = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )

    assert result["reason"] == "liquidation_plan_pending_work_created"


def test_liquidation_request_blocks_expired_or_non_active_plan(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    balance_path = _write_balance(tmp_path)
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
        max_age_minutes=0,
    )

    expired = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )
    assert expired["reason"] == "liquidation_plan_snapshot_expired"

    prepare_new_season._update_plan_status(Path(plan["plan_path"]), "SUPERSEDED", "test")
    not_active = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )
    assert not_active["reason"] == "liquidation_plan_not_active"


def test_liquidation_request_does_not_touch_lots_positions_or_fills(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    config_path = _write_config(tmp_path, db_path)
    _seed_liquidation_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fills (fill_id TEXT)")
    balance_path = _write_balance(tmp_path)
    plan = prepare_new_season.liquidation_plan(
        config_path,
        tmp_path / "exports",
        dry_run=False,
        kis_balances=prepare_new_season.load_kis_balance_json(balance_path),
        kis_balance_path=balance_path,
    )

    created = prepare_new_season.create_liquidation_manual_requests(
        config_path,
        prepare_new_season.CONFIRM_LIQUIDATION,
        dry_run=False,
        kis_balance_path=balance_path,
        plan_path=Path(plan["plan_path"]),
    )

    assert created["created"] is True
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT remaining_quantity FROM lots WHERE lot_id = 'LOT-1'").fetchone()[0] == 2
        assert connection.execute("SELECT quantity FROM positions WHERE code = '005930'").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
