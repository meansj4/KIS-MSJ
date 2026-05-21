"""Position aggregation from account balance and open lots."""

from __future__ import annotations

from datetime import datetime

from .config import StrategyConfig
from .lot_manager import LotManager
from .models import AccountSnapshot, OrderSide, PositionLifecycle, PositionState, TradeFill


class PositionManager:
    def __init__(self, config: StrategyConfig, lot_manager: LotManager, positions: dict[str, PositionState] | None = None) -> None:
        self.config = config
        self.lot_manager = lot_manager
        self.positions = positions or {}
        self.account_mismatch_detected = False

    def get(self, code: str, name: str = "") -> PositionState:
        if code not in self.positions:
            self.positions[code] = PositionState(
                code=code,
                name=name,
                auto_buy_limit=self.config.auto_buy_limit,
                absolute_max_investment=self.config.absolute_max_investment,
            )
        else:
            self.positions[code].auto_buy_limit = self.config.auto_buy_limit
            self.positions[code].absolute_max_investment = self.config.absolute_max_investment
        if name and not self.positions[code].name:
            self.positions[code].name = name
        return self.positions[code]

    def refresh_from_lots(self, code: str, current_price: int = 0) -> PositionState:
        position = self.get(code)
        lots = self.lot_manager.open_lots(code)
        quantity = sum(lot.remaining_quantity for lot in lots)
        exposure = sum(lot.open_amount for lot in lots)
        realized = sum(lot.realized_profit_loss for lot in self.lot_manager.lots.values() if lot.code == code)
        average_price = exposure / quantity if quantity else 0.0
        unrealized = sum((current_price - lot.buy_price) * lot.remaining_quantity for lot in lots) if current_price else 0
        position.quantity = quantity
        position.average_price = average_price
        position.current_price = current_price
        position.cumulative_invested_amount = exposure
        position.realized_profit_loss = int(realized)
        position.unrealized_profit_loss = int(unrealized)
        position.total_profit_loss = position.realized_profit_loss + position.unrealized_profit_loss
        position.profit_loss_pct = position.unrealized_profit_loss / exposure * 100.0 if exposure else 0.0
        last = self.lot_manager.last_buy_lot(code)
        position.last_buy_lot_id = last.lot_id if last else ""
        position.add_buy_stage = _stage_for_exposure(exposure)
        if exposure > self.config.auto_buy_limit or position.profit_loss_pct <= self.config.review_loss_pct or len(lots) > self.config.max_open_lots_before_review:
            position.needs_review = True
            position.auto_buy_enabled = False
        position.position_state = self._lifecycle_for(position, bool(lots))
        position.last_update_time = datetime.now().isoformat(timespec="seconds")
        return position

    def _lifecycle_for(self, position: PositionState, has_open_lots: bool) -> str:
        if position.sync_status == PositionLifecycle.SYNC_REQUIRED.value or position.trading_paused:
            return PositionLifecycle.SYNC_REQUIRED.value
        if position.danger_state:
            return PositionLifecycle.RISK_BLOCKED.value
        if position.needs_review:
            return PositionLifecycle.REVIEW_REQUIRED.value
        if has_open_lots:
            return PositionLifecycle.HOLDING.value
        if position.last_fill_side == OrderSide.SELL.value or any(lot.code == position.code for lot in self.lot_manager.lots.values()):
            return PositionLifecycle.WAIT_REENTRY.value
        return PositionLifecycle.NEVER_BOUGHT.value

    def sync_account(self, snapshot: AccountSnapshot) -> None:
        actual = {item.code: item for item in snapshot.positions}
        for code, item in actual.items():
            position = self.refresh_from_lots(code, item.current_price)
            position.name = item.name or position.name
            if position.quantity != item.quantity:
                position.lot_quantity_mismatch = True
                position.sync_status = PositionLifecycle.SYNC_REQUIRED.value
                position.position_state = PositionLifecycle.SYNC_REQUIRED.value
                position.trading_paused = True
                position.auto_buy_enabled = False
                self.account_mismatch_detected = True
                position.quantity = item.quantity
                position.average_price = item.average_price
        for code, position in self.positions.items():
            if code not in actual and position.quantity > 0:
                position.lot_quantity_mismatch = True
                position.sync_status = PositionLifecycle.SYNC_REQUIRED.value
                position.position_state = PositionLifecycle.SYNC_REQUIRED.value
                position.trading_paused = True
                position.auto_buy_enabled = False
                self.account_mismatch_detected = True

    def apply_fill(self, fill: TradeFill) -> PositionState:
        position = self.get(fill.code, fill.name)
        if fill.side is OrderSide.BUY:
            lot = self.lot_manager.create_buy_lot(fill)
            position.last_buy_lot_id = lot.lot_id
            position.daily_buy_amount += lot.buy_amount
        else:
            lot = self.lot_manager.apply_sell_fill(fill)
            position.daily_sell_amount += fill.quantity * fill.price
            position.last_sell_price = fill.price
            if not self.lot_manager.open_lots(fill.code):
                position.reentry_anchor_price = fill.price
        position.last_fill_price = fill.price
        position.last_fill_side = fill.side.value
        position.last_order_id = fill.order_id
        position.last_order_status = "FILLED"
        position.last_order_time = fill.filled_at.isoformat(timespec="seconds")
        return self.refresh_from_lots(fill.code, fill.price)

    def mark_order_requested(self, code: str, order_id: str, status: str) -> None:
        position = self.get(code)
        position.last_order_id = order_id
        position.last_order_status = status
        position.last_order_time = datetime.now().isoformat(timespec="seconds")


def _stage_for_exposure(exposure: int) -> int:
    if exposure <= 0:
        return 0
    if exposure <= 60_000:
        return 1
    if exposure <= 120_000:
        return 2
    if exposure <= 200_000:
        return 3
    if exposure <= 300_000:
        return 4
    return 5
