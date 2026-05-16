"""Lot-based variable grid strategy."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BotConfig
from .lot_manager import LotManager
from .models import AccountSnapshot, LotState, OrderSide, PositionState
from .risk_manager import RiskDecision


@dataclass(frozen=True)
class StrategyAction:
    side: OrderSide
    amount: int
    quantity: int | None
    reason: str
    lot_id: str = ""
    target_lot: LotState | None = None


class LotGridStrategy:
    def __init__(self, config: BotConfig, lot_manager: LotManager) -> None:
        self.config = config
        self.lot_manager = lot_manager

    def decide(
        self,
        position: PositionState,
        current_price: int,
        snapshot: AccountSnapshot,
        account_risk: RiskDecision,
        symbol_risk: RiskDecision,
    ) -> StrategyAction | None:
        sell = self._sell_action(position, current_price)
        if sell:
            return sell
        if not account_risk.allowed or not symbol_risk.allowed:
            return None
        if position.quantity <= 0 or not self.lot_manager.open_lots(position.code):
            return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "initial_buy")
        return self._add_buy_action(position, current_price, snapshot)

    def _add_buy_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        if exposure > self.config.strategy.auto_buy_limit:
            position.needs_review = True
            position.auto_buy_enabled = False
            return None
        last = self.lot_manager.last_buy_lot(position.code)
        plan = self.lot_manager.buy_plan(exposure)
        if not last or not plan:
            return None
        drop_pct, amount = plan
        if exposure + amount > self.config.strategy.absolute_max_investment:
            return None
        if snapshot.cash_available < amount:
            return None
        decline = (current_price - last.buy_price) / last.buy_price * 100.0
        if decline <= -drop_pct:
            return StrategyAction(OrderSide.BUY, amount, None, f"add_buy_drop_{drop_pct:g}%")
        return None

    def _sell_action(self, position: PositionState, current_price: int) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        sellable = self.lot_manager.sellable_lots(position.code, current_price, exposure)
        if not sellable:
            return None
        lot = sellable[0]
        quantity = lot.remaining_quantity
        if exposure > self.config.strategy.auto_buy_limit:
            quantity = max(1, int(lot.remaining_quantity * self.config.strategy.high_exposure_partial_sell_pct / 100.0))
        if quantity < 1:
            return None
        estimated_fee_tax = int(round(current_price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
        if (current_price - lot.buy_price) * quantity - estimated_fee_tax <= 0:
            return None
        return StrategyAction(OrderSide.SELL, 0, quantity, "sell_profitable_lot", lot.lot_id, lot)
