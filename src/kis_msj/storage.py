"""SQLite storage for positions, lots, orders, and fills."""

from __future__ import annotations

import sqlite3
import shutil
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import LotState, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, TradeFill


POSITION_COLUMNS = tuple(PositionState.__dataclass_fields__.keys())
LOT_COLUMNS = tuple(LotState.__dataclass_fields__.keys())
OPEN_ORDER_STATUSES = (OrderStatus.REQUESTED.value, OrderStatus.PARTIAL.value, OrderStatus.CANCEL_REJECTED.value)
SYNC_REQUIRED = "SYNC_REQUIRED"


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.active_config_hash = ""
        self.active_config_version = ""
        self.active_run_id = ""
        self.active_experiment_name = ""
        self.active_profile_name = ""
        self._db_existed_before_init = self.path.exists() and self.path.stat().st_size > 0
        self._migration_backup_done = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    average_price REAL NOT NULL,
                    current_price INTEGER NOT NULL,
                    cumulative_invested_amount INTEGER NOT NULL,
                    auto_buy_limit INTEGER NOT NULL,
                    absolute_max_investment INTEGER NOT NULL,
                    realized_profit_loss INTEGER NOT NULL,
                    unrealized_profit_loss INTEGER NOT NULL,
                    total_profit_loss INTEGER NOT NULL,
                    profit_loss_pct REAL NOT NULL,
                    last_buy_lot_id TEXT NOT NULL,
                    last_fill_price INTEGER NOT NULL,
                    last_fill_side TEXT NOT NULL,
                    last_order_id TEXT NOT NULL,
                    last_order_status TEXT NOT NULL,
                    add_buy_stage INTEGER NOT NULL,
                    needs_review INTEGER NOT NULL,
                    auto_buy_enabled INTEGER NOT NULL,
                    danger_state INTEGER NOT NULL,
                    daily_buy_amount INTEGER NOT NULL,
                    daily_sell_amount INTEGER NOT NULL,
                    last_update_time TEXT NOT NULL,
                    last_order_time TEXT NOT NULL,
                    lot_quantity_mismatch INTEGER NOT NULL,
                    sync_status TEXT NOT NULL DEFAULT 'OK',
                    trading_paused INTEGER NOT NULL DEFAULT 0,
                    position_state TEXT NOT NULL DEFAULT 'NEVER_BOUGHT',
                    last_sell_price INTEGER NOT NULL DEFAULT 0,
                    reentry_anchor_price INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._backup_before_migration_if_needed(connection)
            _ensure_column(connection, "positions", "sync_status", "TEXT NOT NULL DEFAULT 'OK'")
            _ensure_column(connection, "positions", "trading_paused", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "position_state", "TEXT NOT NULL DEFAULT 'NEVER_BOUGHT'")
            _ensure_column(connection, "positions", "retire_after_exit", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "retire_reason", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "trade_stop_after_exit_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "last_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "reentry_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "exit_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_highest_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_last_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_vwap_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_median_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "normal_exit_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "trailing_exit_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_fill_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_quantity", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_value_amount", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_sell_prices", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "anchor_single_fill", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "anchor_confidence", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "post_exit_high_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "exit_time", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "cleanup_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cleanup_time", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "cleanup_reentry_cooldown_until", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "cleanup_buy_cooldown_until", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "last_reentry_type", "TEXT NOT NULL DEFAULT 'NONE'")
            _ensure_column(connection, "positions", "trailing_reentry_count_today", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "trailing_reentry_count_date", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_reason", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_created_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_trigger_values", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_acknowledged_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_acknowledged_by", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "review_note", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "skip_reason", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "entry_price_for_lot_sizing", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "lot_unit_amount", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "max_symbol_amount", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "max_lots_per_symbol", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "lot_sizing_bucket", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "lot_sizing_locked_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "positions", "lot_sizing_mode", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lots (
                    lot_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    buy_filled_at TEXT NOT NULL,
                    buy_price INTEGER NOT NULL,
                    buy_quantity INTEGER NOT NULL,
                    buy_amount INTEGER NOT NULL,
                    remaining_quantity INTEGER NOT NULL,
                    target_profit_pct REAL NOT NULL,
                    target_sell_price INTEGER NOT NULL,
                    sell_completed INTEGER NOT NULL,
                    partial_sold INTEGER NOT NULL,
                    realized_profit_loss INTEGER NOT NULL,
                    estimated_fee_tax INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    cleanup_candidate INTEGER NOT NULL DEFAULT 0,
                    age_weeks REAL NOT NULL DEFAULT 0,
                    base_target_profit_rate REAL NOT NULL DEFAULT 0,
                    effective_target_profit_rate REAL NOT NULL DEFAULT 0,
                    last_sell_reason TEXT NOT NULL DEFAULT 'UNKNOWN'
                )
                """
            )
            _ensure_column(connection, "lots", "cleanup_candidate", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "age_weeks", "REAL NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "base_target_profit_rate", "REAL NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "effective_target_profit_rate", "REAL NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "last_sell_reason", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
            _ensure_column(connection, "lots", "deep_loss_started_on", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "lots", "deep_loss_last_observed_on", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "lots", "deep_loss_observation_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "deep_loss_last_close", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "lots", "deep_loss_low_price", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price INTEGER NOT NULL,
                    order_id TEXT NOT NULL,
                    filled_at TEXT NOT NULL,
                    lot_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL DEFAULT '',
                    sell_reason TEXT NOT NULL DEFAULT 'UNKNOWN',
                    reentry_type TEXT NOT NULL DEFAULT 'NONE',
                    UNIQUE(order_id, code, side, quantity, price, filled_at, lot_id)
                )
                """
            )
            _ensure_column(connection, "fills", "execution_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "fills", "sell_reason", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
            _ensure_column(connection, "fills", "reentry_type", "TEXT NOT NULL DEFAULT 'NONE'")
            _ensure_column(connection, "fills", "config_hash", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "fills", "config_version", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "fills", "run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "fills", "experiment_name", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    limit_price INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    lot_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    sell_reason TEXT NOT NULL DEFAULT 'UNKNOWN',
                    reentry_type TEXT NOT NULL DEFAULT 'NONE',
                    cleanup_flag INTEGER NOT NULL DEFAULT 0,
                    config_hash TEXT NOT NULL DEFAULT '',
                    config_version TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            _ensure_column(connection, "orders", "requested_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "sell_reason", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
            _ensure_column(connection, "orders", "reentry_type", "TEXT NOT NULL DEFAULT 'NONE'")
            _ensure_column(connection, "orders", "cleanup_flag", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "market_order", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "config_hash", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "config_version", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "experiment_name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "cancel_confirmed", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "cancel_rejected", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "filled_after_cancel_request", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "orders", "cancel_response_code", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "cancel_response_message", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "cancel_checked_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "post_cancel_execution_checked_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "cancel_retry_count", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_order_requests (
                    request_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    current_price INTEGER NOT NULL DEFAULT 0,
                    amount INTEGER NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    lot_id TEXT NOT NULL DEFAULT '',
                    order_type TEXT NOT NULL DEFAULT 'LIMIT_POLICY',
                    preview_json TEXT NOT NULL DEFAULT '{}',
                    runtime_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    live_trading INTEGER NOT NULL DEFAULT 0,
                    confirm_text_verified INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'REQUESTED',
                    block_reason TEXT NOT NULL DEFAULT '',
                    linked_order_id TEXT NOT NULL DEFAULT '',
                    processing_started_at TEXT NOT NULL DEFAULT '',
                    processing_claimed_by TEXT NOT NULL DEFAULT '',
                    claim_attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_processing_error TEXT NOT NULL DEFAULT '',
                    stale_processing_reason TEXT NOT NULL DEFAULT '',
                    config_hash TEXT NOT NULL DEFAULT '',
                    config_version TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            _ensure_column(connection, "manual_order_requests", "current_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "manual_order_requests", "processing_started_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "processing_claimed_by", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "claim_attempt_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "manual_order_requests", "last_processing_error", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "stale_processing_reason", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "config_hash", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "config_version", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "manual_order_requests", "experiment_name", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS config_snapshots (
                    config_hash TEXT PRIMARY KEY,
                    config_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    changed_keys TEXT NOT NULL DEFAULT '',
                    operator_note TEXT NOT NULL DEFAULT '',
                    full_config_json TEXT NOT NULL
                )
                """
            )
            _ensure_column(connection, "config_snapshots", "run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "config_snapshots", "experiment_name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "config_snapshots", "profile_name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "config_snapshots", "started_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "config_snapshots", "ended_at", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    config_hash TEXT NOT NULL DEFAULT '',
                    config_version TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    action_created INTEGER NOT NULL DEFAULT 0,
                    final_block_reason TEXT NOT NULL DEFAULT '',
                    skip_reason TEXT NOT NULL DEFAULT '',
                    current_price INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            _ensure_column(connection, "decisions", "run_id", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "decisions", "experiment_name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "decisions", "profile_name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "decisions", "price_snapshot_id", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    current_price INTEGER NOT NULL DEFAULT 0,
                    previous_close INTEGER,
                    day_open INTEGER,
                    day_high INTEGER,
                    day_low INTEGER,
                    volume INTEGER,
                    trading_value INTEGER,
                    bid_price INTEGER,
                    ask_price INTEGER,
                    spread INTEGER,
                    spread_rate REAL,
                    source TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    config_hash TEXT NOT NULL DEFAULT '',
                    decision_id INTEGER NOT NULL DEFAULT 0,
                    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_prices (
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open INTEGER NOT NULL DEFAULT 0,
                    high INTEGER NOT NULL DEFAULT 0,
                    low INTEGER NOT NULL DEFAULT 0,
                    close INTEGER NOT NULL DEFAULT 0,
                    volume INTEGER NOT NULL DEFAULT 0,
                    trading_value INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, date, source)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS liquidity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    best_bid INTEGER,
                    best_ask INTEGER,
                    spread INTEGER,
                    spread_rate REAL,
                    bid_size INTEGER,
                    ask_size INTEGER,
                    source TEXT NOT NULL DEFAULT '',
                    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_data_collection_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    symbols_requested INTEGER NOT NULL DEFAULT 0,
                    symbols_succeeded INTEGER NOT NULL DEFAULT 0,
                    symbols_failed INTEGER NOT NULL DEFAULT 0,
                    rows_inserted INTEGER NOT NULL DEFAULT 0,
                    rows_updated INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    config_hash TEXT NOT NULL DEFAULT '',
                    experiment_run_id TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def _backup_before_migration_if_needed(self, connection: sqlite3.Connection) -> None:
        if self._migration_backup_done or not self._db_existed_before_init:
            return
        expected_columns = {
            "positions": {
                "sync_status",
                "trading_paused",
                "position_state",
                "last_sell_price",
                "reentry_anchor_price",
                "exit_anchor_price",
                "cycle_highest_sell_price",
                "cycle_last_sell_price",
                "cycle_sell_vwap_price",
                "cycle_sell_median_price",
                "normal_exit_anchor_price",
                "trailing_exit_anchor_price",
                "cycle_sell_fill_count",
                "cycle_sell_quantity",
                "cycle_sell_value_amount",
                "cycle_sell_prices",
                "anchor_single_fill",
                "anchor_confidence",
                "post_exit_high_price",
                "exit_time",
                "cleanup_sell_price",
                "cleanup_time",
                "cleanup_reentry_cooldown_until",
                "cleanup_buy_cooldown_until",
                "last_reentry_type",
                "trailing_reentry_count_today",
                "trailing_reentry_count_date",
                "review_reason",
                "review_created_at",
                "review_trigger_values",
                "review_acknowledged_at",
                "review_acknowledged_by",
                "review_note",
                "skip_reason",
                "entry_price_for_lot_sizing",
                "lot_unit_amount",
                "max_symbol_amount",
                "max_lots_per_symbol",
                "lot_sizing_bucket",
                "lot_sizing_locked_at",
                "lot_sizing_mode",
            },
            "lots": {"cleanup_candidate", "age_weeks", "base_target_profit_rate", "effective_target_profit_rate", "last_sell_reason", "deep_loss_started_on", "deep_loss_last_observed_on", "deep_loss_observation_count", "deep_loss_last_close", "deep_loss_low_price"},
            "fills": {"execution_id", "sell_reason", "reentry_type", "config_hash", "config_version", "run_id", "experiment_name"},
            "orders": {"requested_at", "sell_reason", "reentry_type", "cleanup_flag", "config_hash", "config_version", "run_id", "experiment_name", "cancel_requested", "cancel_confirmed", "cancel_rejected", "filled_after_cancel_request", "cancel_response_code", "cancel_response_message", "cancel_checked_at", "post_cancel_execution_checked_at", "cancel_retry_count"},
            "manual_order_requests": {"request_id", "source", "requested_by", "requested_at", "code", "side", "current_price", "amount", "quantity", "lot_id", "order_type", "preview_json", "runtime_snapshot_json", "live_trading", "confirm_text_verified", "status", "block_reason", "linked_order_id", "processing_started_at", "processing_claimed_by", "claim_attempt_count", "last_processing_error", "stale_processing_reason", "config_hash", "config_version", "run_id", "experiment_name", "created_at", "updated_at"},
        }
        missing = False
        for table, columns in expected_columns.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if existing and not columns.issubset(existing):
                missing = True
                break
        if not missing:
            return
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{self.path.stem}_{timestamp}{self.path.suffix}"
        shutil.copy2(self.path, backup_path)
        self._migration_backup_done = True

    def load_positions(self) -> dict[str, PositionState]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM positions").fetchall()
        positions = {}
        for row in rows:
            data = dict(row)
            data.setdefault("sync_status", "OK")
            data.setdefault("trading_paused", 0)
            data.setdefault("position_state", "NEVER_BOUGHT")
            data.setdefault("last_sell_price", 0)
            data.setdefault("reentry_anchor_price", 0)
            data.setdefault("last_reentry_type", "NONE")
            data.setdefault("review_reason", "")
            data.setdefault("review_created_at", "")
            data.setdefault("review_trigger_values", "")
            data.setdefault("review_acknowledged_at", "")
            data.setdefault("review_acknowledged_by", "")
            data.setdefault("review_note", "")
            data.setdefault("skip_reason", "")
            for key in ("needs_review", "auto_buy_enabled", "danger_state", "lot_quantity_mismatch", "trading_paused", "retire_after_exit", "anchor_single_fill"):
                data[key] = bool(data[key])
            data = _known_model_fields(data, PositionState)
            positions[data["code"]] = PositionState(**data)
        return positions

    def load_lots(self) -> dict[str, LotState]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lots").fetchall()
        lots = {}
        for row in rows:
            data = dict(row)
            data["sell_completed"] = bool(data["sell_completed"])
            data["partial_sold"] = bool(data["partial_sold"])
            data.setdefault("cleanup_candidate", 0)
            data["cleanup_candidate"] = bool(data["cleanup_candidate"])
            if not data.get("base_target_profit_rate"):
                data["base_target_profit_rate"] = data.get("target_profit_pct", 0.0) / 100.0
            if not data.get("effective_target_profit_rate"):
                data["effective_target_profit_rate"] = data.get("base_target_profit_rate", data.get("target_profit_pct", 0.0) / 100.0)
            data.setdefault("last_sell_reason", "UNKNOWN")
            data.setdefault("deep_loss_started_on", "")
            data.setdefault("deep_loss_last_observed_on", "")
            data.setdefault("deep_loss_observation_count", 0)
            data.setdefault("deep_loss_last_close", 0)
            data.setdefault("deep_loss_low_price", 0)
            data = _known_model_fields(data, LotState)
            lots[data["lot_id"]] = LotState(**data)
        return lots

    def save_position(self, position: PositionState) -> None:
        data = _bools(asdict(position))
        updates = ", ".join(f"{column}=excluded.{column}" for column in POSITION_COLUMNS if column != "code")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO positions ({', '.join(POSITION_COLUMNS)}) VALUES ({', '.join('?' for _ in POSITION_COLUMNS)}) "
                f"ON CONFLICT(code) DO UPDATE SET {updates}",
                [data[column] for column in POSITION_COLUMNS],
            )

    def save_lot(self, lot: LotState) -> None:
        data = _bools(asdict(lot))
        updates = ", ".join(f"{column}=excluded.{column}" for column in LOT_COLUMNS if column != "lot_id")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO lots ({', '.join(LOT_COLUMNS)}) VALUES ({', '.join('?' for _ in LOT_COLUMNS)}) "
                f"ON CONFLICT(lot_id) DO UPDATE SET {updates}",
                [data[column] for column in LOT_COLUMNS],
            )

    def save_positions(self, positions: Iterable[PositionState]) -> None:
        rows = [_bools(asdict(position)) for position in positions]
        if not rows:
            return
        updates = ", ".join(f"{column}=excluded.{column}" for column in POSITION_COLUMNS if column != "code")
        sql = (
            f"INSERT INTO positions ({', '.join(POSITION_COLUMNS)}) VALUES ({', '.join('?' for _ in POSITION_COLUMNS)}) "
            f"ON CONFLICT(code) DO UPDATE SET {updates}"
        )
        with self._connect() as connection:
            connection.executemany(sql, ([row[column] for column in POSITION_COLUMNS] for row in rows))

    def save_lots(self, lots: Iterable[LotState]) -> None:
        rows = [_bools(asdict(lot)) for lot in lots]
        if not rows:
            return
        updates = ", ".join(f"{column}=excluded.{column}" for column in LOT_COLUMNS if column != "lot_id")
        sql = (
            f"INSERT INTO lots ({', '.join(LOT_COLUMNS)}) VALUES ({', '.join('?' for _ in LOT_COLUMNS)}) "
            f"ON CONFLICT(lot_id) DO UPDATE SET {updates}"
        )
        with self._connect() as connection:
            connection.executemany(sql, ([row[column] for column in LOT_COLUMNS] for row in rows))

    def latest_completed_session_price(self, code: str, before_date: str) -> tuple[str, int, str] | None:
        return self.latest_completed_session_prices((code,), before_date).get(str(code).zfill(6))

    def latest_completed_session_prices(
        self,
        codes: Iterable[str],
        before_date: str,
    ) -> dict[str, tuple[str, int, str]]:
        """Bulk-load completed-session prices with one snapshot-table scan."""
        normalized_codes = tuple(dict.fromkeys(str(code).zfill(6) for code in codes))
        if not normalized_codes:
            return {}
        placeholders = ", ".join("?" for _ in normalized_codes)
        with self._connect() as connection:
            daily_rows = connection.execute(
                f"""
                SELECT prices.code, prices.date, prices.close
                FROM daily_prices AS prices
                JOIN (
                    SELECT code, MAX(date) AS latest_date
                    FROM daily_prices
                    WHERE code IN ({placeholders}) AND date < ? AND close > 0
                    GROUP BY code
                ) AS latest
                  ON latest.code = prices.code AND latest.latest_date = prices.date
                WHERE prices.close > 0
                """,
                (*normalized_codes, before_date),
            ).fetchall()
            snapshot_rows = connection.execute(
                f"""
                SELECT snapshots.code, substr(snapshots.sampled_at, 1, 10) AS session_date,
                       snapshots.current_price
                FROM price_snapshots AS snapshots
                JOIN (
                    SELECT code, MAX(id) AS latest_id
                    FROM price_snapshots
                    WHERE code IN ({placeholders}) AND sampled_at < ? AND current_price > 0
                    GROUP BY code
                ) AS latest
                  ON latest.latest_id = snapshots.id
                """,
                (*normalized_codes, before_date),
            ).fetchall()
        result: dict[str, tuple[str, int, str]] = {}
        for row in snapshot_rows:
            code = str(row["code"]).zfill(6)
            result[code] = (str(row["session_date"]), int(row["current_price"]), "last_session_snapshot")
        for row in daily_rows:
            code = str(row["code"]).zfill(6)
            daily_date = str(row["date"])
            existing_date = result.get(code, ("", 0, ""))[0]
            if daily_date >= existing_date:
                result[code] = (daily_date, int(row["close"]), "daily_close")
        return result

    def has_today_sell_reason(self, code: str, sell_reason: str) -> bool:
        today = datetime.now().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM orders
                WHERE code = ? AND side = ? AND sell_reason = ?
                  AND substr(COALESCE(requested_at, updated_at), 1, 10) = ?
                LIMIT 1
                """,
                (code, OrderSide.SELL.value, sell_reason, today),
            ).fetchone()
        return row is not None

    def set_active_config(self, config_hash: str, config_version: str = "", *, run_id: str = "", experiment_name: str = "", profile_name: str = "") -> None:
        self.active_config_hash = config_hash
        self.active_config_version = config_version or config_hash
        self.active_run_id = run_id
        self.active_experiment_name = experiment_name
        self.active_profile_name = profile_name

    def record_config_snapshot(
        self,
        config_hash: str,
        full_config: dict[str, object],
        *,
        config_version: str = "",
        source: str = "bot_start",
        changed_keys: str = "",
        operator_note: str = "",
        run_id: str = "",
        experiment_name: str = "",
        profile_name: str = "",
        started_at: str = "",
        ended_at: str = "",
    ) -> None:
        created_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO config_snapshots (
                    config_hash, config_version, run_id, experiment_name, profile_name, started_at, ended_at,
                    created_at, source, changed_keys, operator_note, full_config_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config_hash,
                    config_version or config_hash,
                    run_id or self.active_run_id,
                    experiment_name or self.active_experiment_name,
                    profile_name or self.active_profile_name,
                    started_at or created_at,
                    ended_at,
                    created_at,
                    source,
                    changed_keys,
                    operator_note,
                    json.dumps(full_config, ensure_ascii=False, sort_keys=True),
                ),
            )

    def record_decision(self, payload: dict[str, object]) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO decisions (
                    config_hash, config_version, run_id, experiment_name, profile_name, code, action, action_created,
                    final_block_reason, skip_reason, current_price, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.active_config_hash,
                    self.active_config_version,
                    self.active_run_id,
                    self.active_experiment_name,
                    self.active_profile_name,
                    str(payload.get("code") or ""),
                    str(payload.get("action") or ""),
                    int(bool(payload.get("action_created"))),
                    str(payload.get("final_block_reason") or ""),
                    str(payload.get("skip_reason") or ""),
                    int(float(payload.get("current_price") or 0)),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def record_price_snapshot(self, snapshot: dict[str, object]) -> int:
        missing_fields = snapshot.get("missing_fields_json", snapshot.get("missing_fields", []))
        if not isinstance(missing_fields, str):
            missing_fields = json.dumps(missing_fields, ensure_ascii=False, sort_keys=True)
        raw_json = snapshot.get("raw_json", {})
        if not isinstance(raw_json, str):
            raw_json = json.dumps(raw_json, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO price_snapshots (
                    code, sampled_at, current_price, previous_close, day_open, day_high, day_low,
                    volume, trading_value, bid_price, ask_price, spread, spread_rate, source,
                    run_id, config_hash, decision_id, collected_at, missing_fields_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.get("code") or "").zfill(6),
                    str(snapshot.get("sampled_at") or datetime.now().isoformat(timespec="seconds")),
                    int(float(snapshot.get("current_price") or 0)),
                    _nullable_int(snapshot.get("previous_close")),
                    _nullable_int(snapshot.get("day_open")),
                    _nullable_int(snapshot.get("day_high")),
                    _nullable_int(snapshot.get("day_low")),
                    _nullable_int(snapshot.get("volume")),
                    _nullable_int(snapshot.get("trading_value")),
                    _nullable_int(snapshot.get("bid_price")),
                    _nullable_int(snapshot.get("ask_price")),
                    _nullable_int(snapshot.get("spread")),
                    _nullable_float(snapshot.get("spread_rate")),
                    str(snapshot.get("source") or ""),
                    str(snapshot.get("run_id") or self.active_run_id),
                    str(snapshot.get("config_hash") or self.active_config_hash),
                    int(snapshot.get("decision_id") or 0),
                    str(snapshot.get("collected_at") or datetime.now().isoformat(timespec="seconds")),
                    str(missing_fields),
                    str(raw_json),
                ),
            )
            return int(cursor.lastrowid)

    def link_decision_price_snapshot(self, decision_id: int, snapshot_id: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE decisions SET price_snapshot_id = ? WHERE id = ?", (snapshot_id, decision_id))

    def upsert_daily_price(self, row: dict[str, object]) -> str:
        with self._connect() as connection:
            code = str(row.get("code") or "").zfill(6)
            price_date = str(row.get("date") or datetime.now().date().isoformat())
            source = str(row.get("source") or "")
            existing = connection.execute(
                "SELECT 1 FROM daily_prices WHERE code = ? AND date = ? AND source = ? LIMIT 1",
                (code, price_date, source),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO daily_prices (
                    code, date, open, high, low, close, volume, trading_value, source, collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, date, source) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    trading_value=excluded.trading_value,
                    collected_at=excluded.collected_at
                """,
                (
                    code,
                    price_date,
                    int(float(row.get("open") or 0)),
                    int(float(row.get("high") or 0)),
                    int(float(row.get("low") or 0)),
                    int(float(row.get("close") or 0)),
                    int(float(row.get("volume") or 0)),
                    int(float(row.get("trading_value") or 0)),
                    source,
                    str(row.get("collected_at") or datetime.now().isoformat(timespec="seconds")),
                ),
            )
        return "updated" if existing is not None else "inserted"

    def record_market_data_collection_run(self, result: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO market_data_collection_runs (
                    run_id, started_at, ended_at, mode, symbols_requested, symbols_succeeded,
                    symbols_failed, rows_inserted, rows_updated, error_count, dry_run,
                    config_hash, experiment_run_id, log_path, result_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(result.get("collection_run_id") or ""),
                    str(result.get("started_at") or ""),
                    str(result.get("ended_at") or ""),
                    str(result.get("mode") or ""),
                    int(result.get("symbols_requested") or 0),
                    int(result.get("symbols_succeeded") or 0),
                    int(result.get("symbols_failed") or 0),
                    int(result.get("rows_inserted") or 0),
                    int(result.get("rows_updated") or 0),
                    int(result.get("error_count") or 0),
                    int(bool(result.get("dry_run", True))),
                    str(result.get("config_hash") or self.active_config_hash),
                    str(result.get("experiment_run_id") or self.active_run_id),
                    str(result.get("log_path") or ""),
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                ),
            )

    def record_order(self, result: OrderResult) -> None:
        request = result.request
        requested_at = datetime.now().isoformat(timespec="seconds")
        cancel_requested = result.status in {
            OrderStatus.CANCEL_REJECTED,
            OrderStatus.CANCELED,
            OrderStatus.CANCELED_NO_FILL,
            OrderStatus.CANCELED_AFTER_PARTIAL_FILL,
            OrderStatus.PARTIAL_CANCELED,
            OrderStatus.FILLED_AFTER_CANCEL_REQUEST,
        }
        cancel_confirmed = result.status in {
            OrderStatus.CANCELED,
            OrderStatus.CANCELED_NO_FILL,
            OrderStatus.CANCELED_AFTER_PARTIAL_FILL,
            OrderStatus.PARTIAL_CANCELED,
        }
        cancel_rejected = result.status is OrderStatus.CANCEL_REJECTED
        filled_after_cancel = result.status is OrderStatus.FILLED_AFTER_CANCEL_REQUEST
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    order_id, code, side, quantity, limit_price, status, reason, lot_id, message,
                    sell_reason, reentry_type, cleanup_flag, market_order, config_hash, config_version, run_id,
                    experiment_name, requested_at, cancel_requested, cancel_confirmed,
                    cancel_rejected, filled_after_cancel_request
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    code=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.code ELSE orders.code END,
                    side=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.side ELSE orders.side END,
                    quantity=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.quantity ELSE orders.quantity END,
                    limit_price=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.limit_price ELSE orders.limit_price END,
                    reason=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.reason ELSE orders.reason END,
                    lot_id=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.lot_id ELSE orders.lot_id END,
                    status=excluded.status,
                    message=excluded.message,
                    sell_reason=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.sell_reason ELSE orders.sell_reason END,
                    reentry_type=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.reentry_type ELSE orders.reentry_type END,
                    cleanup_flag=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.cleanup_flag ELSE orders.cleanup_flag END,
                    market_order=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.market_order ELSE orders.market_order END,
                    config_hash=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.config_hash ELSE orders.config_hash END,
                    config_version=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.config_version ELSE orders.config_version END,
                    run_id=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.run_id ELSE orders.run_id END,
                    experiment_name=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.experiment_name ELSE orders.experiment_name END,
                    requested_at=CASE WHEN excluded.status = 'REQUESTED' OR orders.code != excluded.code OR orders.side != excluded.side THEN excluded.requested_at ELSE orders.requested_at END,
                    cancel_requested=MAX(orders.cancel_requested, excluded.cancel_requested),
                    cancel_confirmed=MAX(orders.cancel_confirmed, excluded.cancel_confirmed),
                    cancel_rejected=MAX(orders.cancel_rejected, excluded.cancel_rejected),
                    filled_after_cancel_request=MAX(orders.filled_after_cancel_request, excluded.filled_after_cancel_request),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    result.order_id,
                    request.code,
                    request.side.value,
                    request.quantity,
                    request.limit_price,
                    result.status.value,
                    request.reason,
                    request.lot_id,
                    result.message,
                    request.sell_reason,
                    request.reentry_type,
                    int(request.cleanup_flag),
                    int(request.market_order),
                    self.active_config_hash,
                    self.active_config_version,
                    self.active_run_id,
                    self.active_experiment_name,
                    requested_at,
                    int(cancel_requested),
                    int(cancel_confirmed),
                    int(cancel_rejected),
                    int(filled_after_cancel),
                ),
            )

    def mark_order_cancel_check(
        self,
        order_id: str,
        *,
        cancel_requested: bool = True,
        cancel_confirmed: bool = False,
        cancel_rejected: bool = False,
        filled_after_cancel_request: bool = False,
        response_code: str = "",
        response_message: str = "",
        post_cancel_execution_checked: bool = False,
        increment_cancel_retry: bool = False,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE orders
                SET cancel_requested = MAX(cancel_requested, ?),
                    cancel_confirmed = MAX(cancel_confirmed, ?),
                    cancel_rejected = MAX(cancel_rejected, ?),
                    filled_after_cancel_request = MAX(filled_after_cancel_request, ?),
                    cancel_response_code = CASE WHEN ? != '' THEN ? ELSE cancel_response_code END,
                    cancel_response_message = CASE WHEN ? != '' THEN ? ELSE cancel_response_message END,
                    cancel_checked_at = CASE WHEN ? THEN ? ELSE cancel_checked_at END,
                    post_cancel_execution_checked_at = CASE WHEN ? THEN ? ELSE post_cancel_execution_checked_at END,
                    cancel_retry_count = cancel_retry_count + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (
                    int(cancel_requested),
                    int(cancel_confirmed),
                    int(cancel_rejected),
                    int(filled_after_cancel_request),
                    response_code,
                    response_code,
                    response_message,
                    response_message,
                    int(cancel_requested),
                    now,
                    int(post_cancel_execution_checked),
                    now,
                    int(increment_cancel_retry),
                    order_id,
                ),
            )

    def record_fill(self, fill: TradeFill) -> bool:
        with self._connect() as connection:
            if fill.execution_id:
                existing = connection.execute(
                    "SELECT 1 FROM fills WHERE execution_id = ? LIMIT 1",
                    (fill.execution_id,),
                ).fetchone()
                if existing is not None:
                    return False
            else:
                # Fallback dedupe is intentionally secondary to KIS execution_id.
                # It relies on stable order/code/side/lot/price/quantity/fill-time fields;
                # if KIS omits a stable execution time, raw execution logging should be used
                # to verify whether duplicate or split fills need manual reconciliation.
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM fills
                    WHERE order_id = ?
                      AND code = ?
                      AND side = ?
                      AND lot_id = ?
                      AND price = ?
                      AND quantity = ?
                      AND filled_at = ?
                    LIMIT 1
                    """,
                    (
                        fill.order_id,
                        fill.code,
                        fill.side.value,
                        fill.lot_id,
                        fill.price,
                        fill.quantity,
                        fill.filled_at.isoformat(timespec="seconds"),
                    ),
                ).fetchone()
                if existing is not None:
                    return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fills (code, name, side, quantity, price, order_id, filled_at, lot_id, execution_id, sell_reason, reentry_type, config_hash, config_version, run_id, experiment_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.code,
                    fill.name,
                    fill.side.value,
                    fill.quantity,
                    fill.price,
                    fill.order_id,
                    fill.filled_at.isoformat(),
                    fill.lot_id,
                    fill.execution_id,
                    fill.sell_reason,
                    fill.reentry_type,
                    self.active_config_hash,
                    self.active_config_version,
                    self.active_run_id,
                    self.active_experiment_name,
                ),
            )
        return cursor.rowcount > 0

    def open_orders(self) -> tuple[OrderResult, ...]:
        placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY requested_at ASC, updated_at ASC",
                OPEN_ORDER_STATUSES,
            ).fetchall()
        orders = []
        for row in rows:
            request = OrderRequest(
                str(row["code"]),
                "",
                OrderSide(str(row["side"])),
                int(row["quantity"]),
                int(row["limit_price"]),
                str(row["reason"]),
                str(row["lot_id"]),
                False,
                str(row["sell_reason"]),
                str(row["reentry_type"]),
                bool(row["cleanup_flag"]),
            )
            orders.append(
                OrderResult(
                    request,
                    str(row["order_id"]),
                    OrderStatus(str(row["status"])),
                    str(row["message"]),
                    str(row["requested_at"]),
                    int(row["cancel_retry_count"] or 0),
                    str(row["cancel_checked_at"] or ""),
                )
            )
        return tuple(orders)

    def closed_partial_buy_orders(self, code: str) -> tuple[OrderResult, ...]:
        statuses = (
            OrderStatus.CANCELED_AFTER_PARTIAL_FILL.value,
            OrderStatus.FILLED_AFTER_CANCEL_REQUEST.value,
            OrderStatus.PARTIAL_CANCELED.value,
        )
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM orders
                WHERE code = ?
                  AND side = ?
                  AND status IN ({placeholders})
                ORDER BY requested_at DESC, updated_at DESC
                """,
                (code, OrderSide.BUY.value, *statuses),
            ).fetchall()
        orders = []
        for row in rows:
            request = OrderRequest(
                str(row["code"]),
                "",
                OrderSide(str(row["side"])),
                int(row["quantity"]),
                int(row["limit_price"]),
                str(row["reason"]),
                str(row["lot_id"]),
                bool(row["market_order"]),
                str(row["sell_reason"]),
                str(row["reentry_type"]),
                bool(row["cleanup_flag"]),
            )
            orders.append(
                OrderResult(
                    request,
                    str(row["order_id"]),
                    OrderStatus(str(row["status"])),
                    str(row["message"]),
                    str(row["requested_at"]),
                    int(row["cancel_retry_count"] or 0),
                    str(row["cancel_checked_at"] or ""),
                )
            )
        return tuple(orders)

    def find_order(self, order_id: str, *, code: str = "", side: OrderSide | None = None) -> OrderResult | None:
        normalized = _normalize_order_id(order_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM orders").fetchall()
        for row in rows:
            if _normalize_order_id(str(row["order_id"])) != normalized:
                continue
            if code and str(row["code"]).zfill(6) != str(code).zfill(6):
                continue
            if side is not None and str(row["side"]) != side.value:
                continue
            request = OrderRequest(
                str(row["code"]),
                "",
                OrderSide(str(row["side"])),
                int(row["quantity"]),
                int(row["limit_price"]),
                str(row["reason"]),
                str(row["lot_id"]),
                False,
                str(row["sell_reason"]),
                str(row["reentry_type"]),
                bool(row["cleanup_flag"]),
            )
            return OrderResult(
                request,
                str(row["order_id"]),
                OrderStatus(str(row["status"])),
                str(row["message"]),
                str(row["requested_at"]),
                int(row["cancel_retry_count"] or 0),
                str(row["cancel_checked_at"] or ""),
            )
        return None

    def open_order_count(self, code: str | None = None) -> int:
        placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
        params: list[object] = [*OPEN_ORDER_STATUSES]
        code_filter = ""
        if code is not None:
            code_filter = " AND code = ?"
            params.append(code)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM orders WHERE status IN ({placeholders}){code_filter}",
                params,
            ).fetchone()
        return int(row["count"] or 0)

    def open_order_codes(self) -> set[str]:
        placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT code FROM orders WHERE status IN ({placeholders})",
                OPEN_ORDER_STATUSES,
            ).fetchall()
        return {str(row["code"]) for row in rows}

    def count_today_initial_buy_orders(self) -> int:
        today = datetime.now().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM orders
                WHERE side = ?
                  AND reason = 'initial_buy'
                  AND substr(requested_at, 1, 10) = ?
                """,
                (OrderSide.BUY.value, today),
            ).fetchone()
        return int(row["count"] or 0)

    def sum_today_initial_buy_order_amount(self) -> int:
        today = datetime.now().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(quantity * limit_price), 0) AS amount
                FROM orders
                WHERE side = ?
                  AND reason = 'initial_buy'
                  AND substr(requested_at, 1, 10) = ?
                """,
                (OrderSide.BUY.value, today),
            ).fetchone()
        return int(row["amount"] or 0)

    def filled_quantity_for_order(self, order_id: str, *, code: str = "", side: OrderSide | None = None, lot_id: str = "") -> int:
        filters = ["order_id = ?"]
        params: list[object] = [order_id]
        if code:
            filters.append("code = ?")
            params.append(str(code).zfill(6))
        if side is not None:
            filters.append("side = ?")
            params.append(side.value)
        if lot_id:
            filters.append("lot_id = ?")
            params.append(lot_id)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COALESCE(SUM(quantity), 0) AS quantity FROM fills WHERE {' AND '.join(filters)}",
                params,
            ).fetchone()
        return int(row["quantity"] or 0)

    def has_open_order(self, code: str, side: OrderSide, lot_id: str = "") -> bool:
        placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
        params: list[object] = [code, side.value, *OPEN_ORDER_STATUSES]
        lot_filter = ""
        if side is OrderSide.SELL:
            lot_filter = " AND lot_id = ?"
            params.append(lot_id)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM orders WHERE code = ? AND side = ? AND status IN ({placeholders}){lot_filter} LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def has_any_open_order(self, code: str) -> bool:
        placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
        params: list[object] = [code, *OPEN_ORDER_STATUSES]
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM orders WHERE code = ? AND status IN ({placeholders}) LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def has_partial_order(self, code: str, lot_id: str = "") -> bool:
        params: list[object] = [code, OrderStatus.PARTIAL.value]
        lot_filter = ""
        if lot_id:
            lot_filter = " AND lot_id = ?"
            params.append(lot_id)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM orders WHERE code = ? AND status = ?{lot_filter} LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def seconds_since_oldest_partial_order(self, code: str) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT requested_at FROM orders WHERE code = ? AND status = ? ORDER BY requested_at ASC LIMIT 1",
                (code, OrderStatus.PARTIAL.value),
            ).fetchone()
        if row is None:
            return None
        requested_at = str(row["requested_at"]).strip()
        if not requested_at:
            return None
        return (datetime.now() - _parse_timestamp(requested_at)).total_seconds()

    def seconds_since_recent_order_request(self, code: str, side: OrderSide | None = None) -> float | None:
        params: list[object] = [code]
        side_filter = ""
        if side is not None:
            side_filter = " AND side = ?"
            params.append(side.value)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT requested_at FROM orders WHERE code = ?{side_filter} ORDER BY requested_at DESC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return None
        if not str(row["requested_at"]).strip():
            return None
        requested_at = _parse_timestamp(str(row["requested_at"]))
        return (datetime.now() - requested_at).total_seconds()

    def seconds_since_recent_fill(self, code: str, side: OrderSide | None = None) -> float | None:
        params: list[object] = [code]
        side_filter = ""
        if side is not None:
            side_filter = " AND side = ?"
            params.append(side.value)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT filled_at FROM fills WHERE code = ?{side_filter} ORDER BY filled_at DESC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return None
        filled_at = _parse_timestamp(str(row["filled_at"]))
        return (datetime.now() - filled_at).total_seconds()

    def create_manual_order_request(self, request: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manual_order_requests (
                    request_id, source, requested_by, requested_at, code, side, current_price, amount, quantity, lot_id,
                    order_type, preview_json, runtime_snapshot_json, live_trading, confirm_text_verified,
                    status, block_reason, linked_order_id, config_hash, config_version, run_id, experiment_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["request_id"],
                    request.get("source", "local_ui_manual"),
                    request.get("requested_by", "local_ui"),
                    request.get("requested_at", datetime.now().isoformat(timespec="seconds")),
                    request["code"],
                    request["side"],
                    int(request.get("current_price") or 0),
                    int(request.get("amount") or 0),
                    int(request.get("quantity") or 0),
                    request.get("lot_id", ""),
                    request.get("order_type", "LIMIT_POLICY"),
                    request.get("preview_json", "{}"),
                    request.get("runtime_snapshot_json", "{}"),
                    int(bool(request.get("live_trading", False))),
                    int(bool(request.get("confirm_text_verified", False))),
                    request.get("status", "REQUESTED"),
                    request.get("block_reason", ""),
                    request.get("linked_order_id", ""),
                    request.get("config_hash", self.active_config_hash),
                    request.get("config_version", self.active_config_version),
                    request.get("run_id", self.active_run_id),
                    request.get("experiment_name", self.active_experiment_name),
                ),
            )

    def manual_order_requests(self, status: str | None = None) -> list[dict[str, object]]:
        query = "SELECT * FROM manual_order_requests"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            try:
                rows = connection.execute(query, params).fetchall()
            except sqlite3.Error:
                return []
        return [_normalize_row(dict(row)) for row in rows]

    def claim_manual_order_request(self, request_id: str, claimed_by: str = "bot_core") -> dict[str, object] | None:
        """Atomically claim one REQUESTED manual request for processing.

        This prevents duplicate order submission when loops wake up quickly or
        when more than one bot process accidentally observes the same queue.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE manual_order_requests
                SET status = 'PROCESSING',
                    processing_started_at = CURRENT_TIMESTAMP,
                    processing_claimed_by = ?,
                    claim_attempt_count = claim_attempt_count + 1,
                    last_processing_error = '',
                    stale_processing_reason = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
                  AND status = 'REQUESTED'
                  AND COALESCE(linked_order_id, '') = ''
                """,
                (claimed_by, request_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM manual_order_requests WHERE request_id = ?", (request_id,)).fetchone()
        return _normalize_row(dict(row)) if row is not None else None

    def update_manual_order_request(self, request_id: str, *, status: str, block_reason: str = "", linked_order_id: str = "", last_processing_error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE manual_order_requests
                SET status = ?,
                    block_reason = ?,
                    linked_order_id = COALESCE(NULLIF(?, ''), linked_order_id),
                    last_processing_error = COALESCE(NULLIF(?, ''), last_processing_error),
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
                """,
                (status, block_reason, linked_order_id, last_processing_error, request_id),
            )

    def requeue_stale_manual_order_request(self, request_id: str, reason: str = "operator_requeue_stale_processing") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE manual_order_requests
                SET status = 'REQUESTED',
                    block_reason = '',
                    processing_started_at = '',
                    processing_claimed_by = '',
                    stale_processing_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
                  AND status = 'PROCESSING'
                  AND COALESCE(linked_order_id, '') = ''
                """,
                (reason, request_id),
            )
            return cursor.rowcount == 1

    def cancel_stale_manual_order_request(self, request_id: str, reason: str = "operator_cancel_stale_processing") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE manual_order_requests
                SET status = 'BLOCKED',
                    block_reason = ?,
                    stale_processing_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
                  AND status = 'PROCESSING'
                  AND COALESCE(linked_order_id, '') = ''
                """,
                (reason, reason, request_id),
            )
            return cursor.rowcount == 1


def _bools(data: dict[str, object]) -> dict[str, object]:
    return {key: int(value) if isinstance(value, bool) else value for key, value in data.items()}


def _normalize_row(row: dict[str, object]) -> dict[str, object]:
    for key in ("live_trading", "confirm_text_verified"):
        if key in row:
            row[key] = bool(row[key])
    return row


def _known_model_fields(data: dict[str, object], model: type) -> dict[str, object]:
    return {key: data[key] for key in model.__dataclass_fields__ if key in data}


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _nullable_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _nullable_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _normalize_order_id(order_id: str) -> str:
    normalized = str(order_id).strip()
    return normalized.lstrip("0") or normalized
