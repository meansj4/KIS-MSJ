"""Shared data models for the domestic stock trading workflow."""

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
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


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

    @property
    def invested_amount(self) -> int:
        return int(round(self.quantity * self.average_price))


@dataclass(frozen=True)
class AccountSnapshot:
    cash_available: int
    total_asset: int
    total_evaluation: int
    total_profit_loss: int
    daily_profit_loss: int
    positions: tuple[BalanceItem, ...]

    @property
    def total_profit_loss_pct(self) -> float:
        base = self.total_asset - self.total_profit_loss
        return (self.total_profit_loss / base * 100.0) if base else 0.0

    @property
    def daily_profit_loss_pct(self) -> float:
        base = self.total_asset - self.daily_profit_loss
        return (self.daily_profit_loss / base * 100.0) if base else 0.0


@dataclass(frozen=True)
class OrderRequest:
    code: str
    name: str
    side: OrderSide
    quantity: int
    limit_price: int
    reason: str
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


@dataclass
class PositionState:
    code: str
    name: str = ""
    quantity: int = 0
    average_price: float = 0.0
    total_buy_amount: int = 0
    total_sell_amount: int = 0
    realized_profit_loss: int = 0
    unrealized_profit_loss: int = 0
    last_fill_price: int = 0
    last_fill_side: str = "NONE"
    last_order_id: str = ""
    last_order_status: str = "NONE"
    is_filled: bool = False
    min_buy_fill_price: int = 0
    max_buy_fill_price: int = 0
    min_sell_fill_price: int = 0
    max_sell_fill_price: int = 0
    add_buy_stage: int = 0
    daily_buy_amount: int = 0
    daily_sell_amount: int = 0
    cumulative_invested_amount: int = 0
    position_profit_loss_pct: float = 0.0
    account_profit_loss_pct: float = 0.0
    last_update_time: str = ""
    last_order_time: str = ""
    sell_stage: int = 0
    risk_note: str = ""

    def reference_price(self, mode: str) -> float:
        if mode == "average_price" and self.average_price > 0:
            return self.average_price
        if self.last_fill_price > 0:
            return float(self.last_fill_price)
        return self.average_price
