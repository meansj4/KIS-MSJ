"""Shared models for lot-based domestic stock automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NONE = "NONE"
    REQUESTED = "REQUESTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class LotStatus(str, Enum):
    OPEN = "OPEN"
    PARTIAL_SOLD = "PARTIAL_SOLD"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Quote:
    code: str
    price: int
    timestamp: datetime
    name: str = ""


@dataclass(frozen=True)
class BalanceItem:
    code: str
    name: str
    quantity: int
    average_price: float
    current_price: int


@dataclass(frozen=True)
class AccountSnapshot:
    cash_available: int
    total_asset: int
    total_profit_loss: int
    daily_profit_loss: int
    positions: tuple[BalanceItem, ...] = ()

    @property
    def total_profit_loss_pct(self) -> float:
        base = self.total_asset - self.total_profit_loss
        return self.total_profit_loss / base * 100.0 if base else 0.0

    @property
    def daily_profit_loss_pct(self) -> float:
        base = self.total_asset - self.daily_profit_loss
        return self.daily_profit_loss / base * 100.0 if base else 0.0


@dataclass
class PositionState:
    code: str
    name: str = ""
    quantity: int = 0
    average_price: float = 0.0
    current_price: int = 0
    cumulative_invested_amount: int = 0
    auto_buy_limit: int = 3_000_000
    absolute_max_investment: int = 5_000_000
    realized_profit_loss: int = 0
    unrealized_profit_loss: int = 0
    total_profit_loss: int = 0
    profit_loss_pct: float = 0.0
    last_buy_lot_id: str = ""
    last_fill_price: int = 0
    last_fill_side: str = "NONE"
    last_order_id: str = ""
    last_order_status: str = "NONE"
    add_buy_stage: int = 0
    needs_review: bool = False
    auto_buy_enabled: bool = True
    danger_state: bool = False
    daily_buy_amount: int = 0
    daily_sell_amount: int = 0
    last_update_time: str = ""
    last_order_time: str = ""
    lot_quantity_mismatch: bool = False


@dataclass
class LotState:
    lot_id: str
    code: str
    buy_filled_at: str
    buy_price: int
    buy_quantity: int
    buy_amount: int
    remaining_quantity: int
    target_profit_pct: float
    target_sell_price: int
    sell_completed: bool = False
    partial_sold: bool = False
    realized_profit_loss: int = 0
    estimated_fee_tax: int = 0
    status: str = LotStatus.OPEN.value

    @property
    def open_amount(self) -> int:
        return self.remaining_quantity * self.buy_price

    def profit_pct_at(self, price: int) -> float:
        return (price - self.buy_price) / self.buy_price * 100.0 if self.buy_price else 0.0


@dataclass(frozen=True)
class OrderRequest:
    code: str
    name: str
    side: OrderSide
    quantity: int
    limit_price: int
    reason: str
    lot_id: str = ""
    market_order: bool = False


@dataclass(frozen=True)
class OrderResult:
    request: OrderRequest
    order_id: str
    status: OrderStatus
    message: str = ""


@dataclass(frozen=True)
class TradeFill:
    code: str
    name: str
    side: OrderSide
    quantity: int
    price: int
    order_id: str
    filled_at: datetime
    lot_id: str = ""
