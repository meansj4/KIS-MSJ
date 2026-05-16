"""SQLite storage for positions, lots, orders, and fills."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import LotState, OrderResult, PositionState, TradeFill


POSITION_COLUMNS = tuple(PositionState.__dataclass_fields__.keys())
LOT_COLUMNS = tuple(LotState.__dataclass_fields__.keys())


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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
                    lot_quantity_mismatch INTEGER NOT NULL
                )
                """
            )
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
                    status TEXT NOT NULL
                )
                """
            )
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
                    UNIQUE(order_id, code, side, quantity, price, filled_at, lot_id)
                )
                """
            )
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
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def load_positions(self) -> dict[str, PositionState]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM positions").fetchall()
        positions = {}
        for row in rows:
            data = dict(row)
            for key in ("needs_review", "auto_buy_enabled", "danger_state", "lot_quantity_mismatch"):
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
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (order_id, code, side, quantity, limit_price, status, reason, lot_id, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

    def record_fill(self, fill: TradeFill) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO fills (code, name, side, quantity, price, order_id, filled_at, lot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fill.code, fill.name, fill.side.value, fill.quantity, fill.price, fill.order_id, fill.filled_at.isoformat(), fill.lot_id),
            )


def _bools(data: dict[str, object]) -> dict[str, object]:
    return {key: int(value) if isinstance(value, bool) else value for key, value in data.items()}
