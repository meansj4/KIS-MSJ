"""Grid-style strategy decisions based on fills, reference price, and risk."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BotConfig
from .models import AccountSnapshot, OrderSide, PositionState
from .risk_manager import RiskDecision


@dataclass(frozen=True)
class StrategyAction:
    side: OrderSide
    amount: int
    quantity_pct: float | None
    reason: str
    priority: int = 0


class GridStrategy:
    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def decide(
        self,
        position: PositionState,
        current_price: int,
        snapshot: AccountSnapshot,
        account_buy_risk: RiskDecision,
        symbol_buy_risk: RiskDecision,
    ) -> StrategyAction | None:
        stop_loss = self._stop_loss_action(position)
        if stop_loss is not None:
            return stop_loss

        if position.quantity <= 0:
            if account_buy_risk.allowed:
                return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "initial_buy", priority=10)
            return None

        sell_action = self._take_profit_action(position, current_price)
        if sell_action is not None:
            return sell_action

        if not account_buy_risk.allowed or not symbol_buy_risk.allowed:
            return None
        return self._add_buy_action(position, current_price, snapshot)

    def _add_buy_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        stage = position.add_buy_stage
        drops = self.config.strategy.add_buy_drop_pcts
        amounts = self.config.strategy.add_buy_amounts
        if stage >= len(drops):
            return None
        reference = position.reference_price(self.config.strategy.reference_price_mode)
        if reference <= 0:
            return None
        change_pct = (current_price - reference) / reference * 100.0
        target_drop = -abs(drops[stage])
        if change_pct <= target_drop and snapshot.cash_available >= amounts[stage]:
            return StrategyAction(OrderSide.BUY, amounts[stage], None, f"add_buy_stage_{stage + 1}", priority=20)
        return None

    def _take_profit_action(self, position: PositionState, current_price: int) -> StrategyAction | None:
        stage = position.sell_stage
        rises = self.config.strategy.sell_rise_pcts
        if stage >= len(rises):
            return None
        reference = position.reference_price(self.config.strategy.reference_price_mode)
        if reference <= 0:
            return None
        change_pct = (current_price - reference) / reference * 100.0
        if change_pct < rises[stage]:
            return None
        portion = self.config.strategy.final_sell_portion_pct if stage == len(rises) - 1 else self.config.strategy.sell_portion_pct
        return StrategyAction(OrderSide.SELL, 0, portion, f"take_profit_stage_{stage + 1}", priority=30)

    def _stop_loss_action(self, position: PositionState) -> StrategyAction | None:
        loss_pct = position.position_profit_loss_pct
        if position.quantity <= 0:
            return None
        if loss_pct <= self.config.risk.full_stop_loss_pct:
            return StrategyAction(OrderSide.SELL, 0, 100.0, "full_stop_loss", priority=100)
        if loss_pct <= self.config.risk.half_stop_loss_pct:
            return StrategyAction(OrderSide.SELL, 0, 50.0, "half_stop_loss", priority=90)
        return None
