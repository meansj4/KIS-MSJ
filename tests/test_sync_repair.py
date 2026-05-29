from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

from kis_msj.config import BotConfig, StockConfig
from kis_msj.models import LotState, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState
from kis_msj.storage import StateStore

from scripts.repair_missing_fill import CONFIRM_EXECUTE, main as repair_main


def _write_config(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    log_path = tmp_path / "bot.log"
    raw = asdict(BotConfig(stocks=(StockConfig("092790", "넥스틸"),), storage_path=str(db_path), log_path=str(log_path)))
    raw["order"]["live_trading"] = True
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return config_path, db_path


def _seed_mismatch(db_path):
    store = StateStore(db_path)
    store.save_position(
        PositionState(
            "092790",
            "넥스틸",
            quantity=23,
            average_price=15498.7826,
            current_price=14750,
            cumulative_invested_amount=270160,
            lot_quantity_mismatch=True,
            sync_status=PositionLifecycle.SYNC_REQUIRED.value,
            position_state=PositionLifecycle.SYNC_REQUIRED.value,
            trading_paused=True,
            auto_buy_enabled=False,
        )
    )
    store.save_lot(LotState("LOT-1", "092790", "2026-05-27T11:02:14", 16700, 5, 83500, 5, 5.0, 17550))
    store.save_lot(LotState("LOT-2", "092790", "2026-05-28T10:37:50", 15910, 6, 95460, 6, 5.0, 16550))
    store.save_lot(LotState("LOT-3", "092790", "2026-05-29T09:26:00", 15200, 6, 91200, 6, 5.0, 15650))
    request = OrderRequest("092790", "넥스틸", OrderSide.BUY, 6, 14850, "add_buy_drop_6%")
    store.record_order(OrderResult(request, "0018769500", OrderStatus.CANCELED, "unfilled_limit_order_timeout", "2026-05-29T10:34:55"))
    return store


def _open_qty(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT COALESCE(SUM(remaining_quantity), 0) FROM lots WHERE code='092790' AND status!='CLOSED'").fetchone()[0]


def test_missing_fill_repair_executes_through_fill_and_apply_path(tmp_path):
    config_path, db_path = _write_config(tmp_path)
    _seed_mismatch(db_path)

    rc = repair_main(
        [
            "--config",
            str(config_path),
            "--code",
            "092790",
            "--order-no",
            "0018769500",
            "--output-dir",
            str(tmp_path / "exports"),
            "--manual-filled-at",
            "2026-05-29T10:35:20",
            "--manual-price",
            "14850",
            "--manual-quantity",
            "6",
            "--manual-execution-id",
            "EXEC-0018769500",
            "--kis-quantity",
            "23",
            "--execute",
            "--confirm",
            CONFIRM_EXECUTE,
        ]
    )

    assert rc == 0
    assert _open_qty(db_path) == 23
    store = StateStore(db_path)
    position = store.load_positions()["092790"]
    assert position.quantity == 23
    assert position.sync_status == "OK"
    assert not position.lot_quantity_mismatch
    assert not position.trading_paused
    assert position.auto_buy_enabled
    with sqlite3.connect(db_path) as connection:
        fill_count = connection.execute("SELECT COUNT(*) FROM fills WHERE order_id='0018769500'").fetchone()[0]
    assert fill_count == 1


def test_missing_fill_repair_is_deduped_on_same_execution(tmp_path):
    config_path, db_path = _write_config(tmp_path)
    _seed_mismatch(db_path)
    args = [
        "--config",
        str(config_path),
        "--code",
        "092790",
        "--order-no",
        "0018769500",
        "--output-dir",
        str(tmp_path / "exports"),
        "--manual-filled-at",
        "2026-05-29T10:35:20",
        "--manual-price",
        "14850",
        "--manual-quantity",
        "6",
        "--manual-execution-id",
        "EXEC-0018769500",
        "--kis-quantity",
        "23",
        "--execute",
        "--confirm",
        CONFIRM_EXECUTE,
    ]

    assert repair_main(args) == 0
    assert repair_main(args) == 0

    assert _open_qty(db_path) == 23
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fills WHERE order_id='0018769500'").fetchone()[0] == 1


def test_missing_execution_creates_manual_plan_without_db_repair(tmp_path):
    config_path, db_path = _write_config(tmp_path)
    _seed_mismatch(db_path)

    rc = repair_main(["--config", str(config_path), "--code", "092790", "--order-no", "0018769500", "--output-dir", str(tmp_path / "exports")])

    assert rc == 0
    assert _open_qty(db_path) == 17
    assert next((tmp_path / "exports").glob("sync_repair_nexteel_*"))
    store = StateStore(db_path)
    assert store.load_positions()["092790"].sync_status == PositionLifecycle.SYNC_REQUIRED.value


def test_execute_requires_confirm_text(tmp_path):
    config_path, db_path = _write_config(tmp_path)
    _seed_mismatch(db_path)

    rc = repair_main(
        [
            "--config",
            str(config_path),
            "--code",
            "092790",
            "--order-no",
            "0018769500",
            "--output-dir",
            str(tmp_path / "exports"),
            "--manual-filled-at",
            "2026-05-29T10:35:20",
            "--manual-price",
            "14850",
            "--manual-quantity",
            "6",
            "--execute",
        ]
    )

    assert rc == 2
    assert _open_qty(db_path) == 17
