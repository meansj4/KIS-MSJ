"""SQLite storage for positions, lots, orders, and fills."""

from __future__ import annotations

import sqlite3
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import LotState, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, TradeFill


POSITION_COLUMNS = tuple(PositionState.__dataclass_fields__.keys())
LOT_COLUMNS = tuple(LotState.__dataclass_fields__.keys())
OPEN_ORDER_STATUSES = (OrderStatus.REQUESTED.value, OrderStatus.PARTIAL.value)
SYNC_REQUIRED = "SYNC_REQUIRED"


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._db_existed_before_init = self.path.exists() and self.path.stat().st_size > 0
        self._migration_backup_done = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
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
            _ensure_column(connection, "positions", "last_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "reentry_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "exit_anchor_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_highest_sell_price", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "positions", "cycle_last_sell_price", "INTEGER NOT NULL DEFAULT 0")
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
            _ensure_column(connection, "positions", "skip_reason", "TEXT NOT NULL DEFAULT ''")
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
                    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            _ensure_column(connection, "orders", "requested_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "orders", "sell_reason", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
            _ensure_column(connection, "orders", "reentry_type", "TEXT NOT NULL DEFAULT 'NONE'")
            _ensure_column(connection, "orders", "cleanup_flag", "INTEGER NOT NULL DEFAULT 0")

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
                "skip_reason",
            },
            "lots": {"cleanup_candidate", "age_weeks", "base_target_profit_rate", "effective_target_profit_rate", "last_sell_reason"},
            "fills": {"execution_id", "sell_reason", "reentry_type"},
            "orders": {"requested_at", "sell_reason", "reentry_type", "cleanup_flag"},
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
            data.setdefault("skip_reason", "")
            for key in ("needs_review", "auto_buy_enabled", "danger_state", "lot_quantity_mismatch", "trading_paused"):
                data[key] = bool(data[key])
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
        for position in positions:
            self.save_position(position)

    def save_lots(self, lots: Iterable[LotState]) -> None:
        for lot in lots:
            self.save_lot(lot)

    def record_order(self, result: OrderResult) -> None:
        request = result.request
        requested_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (order_id, code, side, quantity, limit_price, status, reason, lot_id, message, sell_reason, reentry_type, cleanup_flag, requested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET status=excluded.status, message=excluded.message, updated_at=CURRENT_TIMESTAMP
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
                    requested_at,
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
                INSERT OR IGNORE INTO fills (code, name, side, quantity, price, order_id, filled_at, lot_id, execution_id, sell_reason, reentry_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            orders.append(OrderResult(request, str(row["order_id"]), OrderStatus(str(row["status"])), str(row["message"]), str(row["requested_at"])))
        return tuple(orders)

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

    def filled_quantity_for_order(self, order_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS quantity FROM fills WHERE order_id = ?",
                (order_id,),
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


def _bools(data: dict[str, object]) -> dict[str, object]:
    return {key: int(value) if isinstance(value, bool) else value for key, value in data.items()}


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
