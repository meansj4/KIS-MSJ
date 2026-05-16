"""SQLite persistence for restart-safe trading state."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import OrderResult, PositionState, TradeFill


POSITION_COLUMNS = tuple(PositionState.__dataclass_fields__.keys())


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
                    total_buy_amount INTEGER NOT NULL,
                    total_sell_amount INTEGER NOT NULL,
                    realized_profit_loss INTEGER NOT NULL,
                    unrealized_profit_loss INTEGER NOT NULL,
                    last_fill_price INTEGER NOT NULL,
                    last_fill_side TEXT NOT NULL,
                    last_order_id TEXT NOT NULL,
                    last_order_status TEXT NOT NULL,
                    is_filled INTEGER NOT NULL,
                    min_buy_fill_price INTEGER NOT NULL,
                    max_buy_fill_price INTEGER NOT NULL,
                    min_sell_fill_price INTEGER NOT NULL,
                    max_sell_fill_price INTEGER NOT NULL,
                    add_buy_stage INTEGER NOT NULL,
                    daily_buy_amount INTEGER NOT NULL,
                    daily_sell_amount INTEGER NOT NULL,
                    cumulative_invested_amount INTEGER NOT NULL,
                    position_profit_loss_pct REAL NOT NULL,
                    account_profit_loss_pct REAL NOT NULL,
                    last_update_time TEXT NOT NULL,
                    last_order_time TEXT NOT NULL,
                    sell_stage INTEGER NOT NULL,
                    risk_note TEXT NOT NULL
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
                    UNIQUE(order_id, code, side, quantity, price, filled_at)
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
                    message TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def load_positions(self) -> dict[str, PositionState]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM positions").fetchall()
        positions: dict[str, PositionState] = {}
        for row in rows:
            data = dict(row)
            data["is_filled"] = bool(data["is_filled"])
            positions[data["code"]] = PositionState(**data)
        return positions

    def save_position(self, position: PositionState) -> None:
        data = asdict(position)
        data["is_filled"] = 1 if position.is_filled else 0
        placeholders = ", ".join("?" for _ in POSITION_COLUMNS)
        updates = ", ".join(f"{column}=excluded.{column}" for column in POSITION_COLUMNS if column != "code")
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO positions ({", ".join(POSITION_COLUMNS)})
                VALUES ({placeholders})
                ON CONFLICT(code) DO UPDATE SET {updates}
                """,
                [data[column] for column in POSITION_COLUMNS],
            )

    def save_positions(self, positions: Iterable[PositionState]) -> None:
        for position in positions:
            self.save_position(position)

    def record_fill(self, fill: TradeFill) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO fills (code, name, side, quantity, price, order_id, filled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (fill.code, fill.name, fill.side.value, fill.quantity, fill.price, fill.order_id, fill.filled_at.isoformat()),
            )

    def record_order(self, result: OrderResult) -> None:
        request = result.request
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (order_id, code, side, quantity, limit_price, status, reason, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status=excluded.status,
                    message=excluded.message,
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
                    result.message,
                ),
            )
