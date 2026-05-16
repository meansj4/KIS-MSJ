"""Position state reconciliation and fill-based updates."""

from __future__ import annotations

from datetime import datetime

from .models import AccountSnapshot, OrderSide, PositionState, TradeFill


class PositionManager:
    def __init__(self, positions: dict[str, PositionState] | None = None) -> None:
        self.positions = positions or {}
        self.state_mismatch_detected = False

    def get(self, code: str, name: str = "") -> PositionState:
        if code not in self.positions:
            self.positions[code] = PositionState(code=code, name=name)
        if name and not self.positions[code].name:
            self.positions[code].name = name
        return self.positions[code]

    def sync_account(self, snapshot: AccountSnapshot) -> None:
        actual_codes = {item.code for item in snapshot.positions}
        for item in snapshot.positions:
            position = self.get(item.code, item.name)
            if position.quantity != item.quantity or round(position.average_price) != round(item.average_price):
                self.state_mismatch_detected = True
            position.quantity = item.quantity
            position.average_price = item.average_price
            position.cumulative_invested_amount = item.invested_amount
            position.unrealized_profit_loss = int(round((item.current_price - item.average_price) * item.quantity))
            position.position_profit_loss_pct = _profit_pct(item.current_price, item.average_price)
            position.account_profit_loss_pct = snapshot.total_profit_loss_pct
            position.last_update_time = datetime.now().isoformat(timespec="seconds")

        for code, position in self.positions.items():
            if code not in actual_codes and position.quantity > 0:
                self.state_mismatch_detected = True
                position.quantity = 0
                position.average_price = 0.0
                position.cumulative_invested_amount = 0
                position.unrealized_profit_loss = 0
                position.last_update_time = datetime.now().isoformat(timespec="seconds")

    def apply_fill(self, fill: TradeFill, account_profit_loss_pct: float = 0.0) -> PositionState:
        position = self.get(fill.code, fill.name)
        before_quantity = position.quantity
        before_cost = position.average_price * before_quantity
        amount = fill.quantity * fill.price

        if fill.side is OrderSide.BUY:
            new_quantity = before_quantity + fill.quantity
            position.average_price = (before_cost + amount) / new_quantity if new_quantity else 0.0
            position.quantity = new_quantity
            position.total_buy_amount += amount
            position.daily_buy_amount += amount
            position.cumulative_invested_amount += amount
            position.min_buy_fill_price = _min_nonzero(position.min_buy_fill_price, fill.price)
            position.max_buy_fill_price = max(position.max_buy_fill_price, fill.price)
        else:
            sell_quantity = min(fill.quantity, before_quantity)
            realized = int(round((fill.price - position.average_price) * sell_quantity))
            position.quantity = max(0, before_quantity - sell_quantity)
            position.total_sell_amount += amount
            position.daily_sell_amount += amount
            position.realized_profit_loss += realized
            position.cumulative_invested_amount = int(round(position.quantity * position.average_price))
            if position.quantity == 0:
                position.average_price = 0.0
                position.add_buy_stage = 0
                position.sell_stage = 0
            position.min_sell_fill_price = _min_nonzero(position.min_sell_fill_price, fill.price)
            position.max_sell_fill_price = max(position.max_sell_fill_price, fill.price)

        position.last_fill_price = fill.price
        position.last_fill_side = fill.side.value
        position.last_order_id = fill.order_id
        position.last_order_status = "FILLED"
        position.is_filled = True
        position.unrealized_profit_loss = int(round((fill.price - position.average_price) * position.quantity))
        position.position_profit_loss_pct = _profit_pct(fill.price, position.average_price)
        position.account_profit_loss_pct = account_profit_loss_pct
        position.last_update_time = datetime.now().isoformat(timespec="seconds")
        position.last_order_time = position.last_update_time
        return position

    def mark_order_requested(self, code: str, order_id: str, status: str) -> None:
        position = self.get(code)
        position.last_order_id = order_id
        position.last_order_status = status
        position.is_filled = False
        position.last_order_time = datetime.now().isoformat(timespec="seconds")


def _min_nonzero(current: int, value: int) -> int:
    return value if current <= 0 else min(current, value)


def _profit_pct(current_price: float, average_price: float) -> float:
    return ((current_price - average_price) / average_price * 100.0) if average_price else 0.0
