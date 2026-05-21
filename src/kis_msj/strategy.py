"""Lot-based variable grid strategy."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BotConfig
from .lot_manager import LotManager
from .models import AccountSnapshot, LotState, OrderSide, PositionLifecycle, PositionState
from .risk_manager import RiskDecision


@dataclass(frozen=True)
class StrategyAction:
    side: OrderSide
    amount: int
    quantity: int | None
    reason: str
    lot_id: str = ""
    target_lot: LotState | None = None


@dataclass(frozen=True)
class StrategyContext:
    position_state: str
    pnl_mode: str
    position_pnl_rate: float
    lowest_open_buy_lot_price: int = 0
    highest_open_buy_lot_price: int = 0
    reference_buy_price: int = 0
    reference_sell_price: int = 0
    target_buy_drop_rate: float = 0.0
    target_profit_rate: float = 0.0
    buy_condition_met: bool = False
    sell_signal_met: bool = False
    profitable_lots: str = "NONE"
    selected_sell_lot_id: str = ""
    reentry_condition_met: bool = False
    skip_reason: str = ""


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
        lifecycle = self._position_state(position)
        if position.quantity <= 0 or not self.lot_manager.open_lots(position.code):
            if lifecycle == PositionLifecycle.NEVER_BOUGHT.value:
                if current_price <= self.config.strategy.initial_buy_amount:
                    return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "initial_buy")
                return None
            if lifecycle == PositionLifecycle.WAIT_REENTRY.value and self._reentry_condition_met(position, current_price):
                return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "reentry_buy")
            return None
        return self._add_buy_action(position, current_price, snapshot)

    def _add_buy_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        if exposure > self.config.strategy.auto_buy_limit:
            position.needs_review = True
            position.auto_buy_enabled = False
            position.position_state = PositionLifecycle.REVIEW_REQUIRED.value
            return None
        reference_lot = self._reference_buy_lot(position)
        plan = self.lot_manager.buy_plan(exposure)
        if not reference_lot or not plan:
            return None
        drop_pct, amount = plan
        if exposure + amount > self.config.strategy.auto_buy_limit:
            return None
        if exposure + amount > self.config.strategy.absolute_max_investment:
            return None
        if snapshot.cash_available < amount:
            return None
        decline = (current_price - reference_lot.buy_price) / reference_lot.buy_price * 100.0
        if decline <= -drop_pct:
            return StrategyAction(OrderSide.BUY, amount, None, f"add_buy_drop_{drop_pct:g}%")
        return None

    def _sell_action(self, position: PositionState, current_price: int) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        target_pct = self.lot_manager.target_profit_pct(exposure)
        sellable = self.lot_manager.sellable_lots(position.code, current_price, exposure, target_pct)
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

    def context(self, position: PositionState, current_price: int) -> StrategyContext:
        exposure = position.cumulative_invested_amount
        lowest = self.lot_manager.lowest_open_buy_lot(position.code)
        highest = self.lot_manager.highest_open_buy_lot(position.code)
        target_profit = self.lot_manager.target_profit_pct(exposure) if exposure > 0 else 0.0
        buy_plan = self.lot_manager.buy_plan(exposure)
        reference_buy = self._reference_buy_lot(position)
        reference_sell = self._reference_sell_lot(position)
        sellable = self.lot_manager.sellable_lots(position.code, current_price, exposure, target_profit) if exposure > 0 else []
        buy_condition = False
        if reference_buy and buy_plan:
            buy_condition = current_price <= reference_buy.buy_price * (1.0 - buy_plan[0] / 100.0)
        return StrategyContext(
            position_state=self._position_state(position),
            pnl_mode=self._pnl_mode(position),
            position_pnl_rate=position.profit_loss_pct / 100.0,
            lowest_open_buy_lot_price=lowest.buy_price if lowest else 0,
            highest_open_buy_lot_price=highest.buy_price if highest else 0,
            reference_buy_price=reference_buy.buy_price if reference_buy else 0,
            reference_sell_price=reference_sell.buy_price if reference_sell else 0,
            target_buy_drop_rate=(buy_plan[0] / 100.0) if buy_plan else 0.0,
            target_profit_rate=target_profit / 100.0,
            buy_condition_met=buy_condition,
            sell_signal_met=self._sell_signal_met(position, current_price, target_profit),
            profitable_lots=";".join(lot.lot_id for lot in sellable) or "NONE",
            selected_sell_lot_id=sellable[0].lot_id if sellable else "",
            reentry_condition_met=self._reentry_condition_met(position, current_price),
            skip_reason=self._skip_reason(position, current_price),
        )

    def _position_state(self, position: PositionState) -> str:
        if position.sync_status == PositionLifecycle.SYNC_REQUIRED.value or position.trading_paused:
            return PositionLifecycle.SYNC_REQUIRED.value
        if position.danger_state:
            return PositionLifecycle.RISK_BLOCKED.value
        if position.needs_review:
            return PositionLifecycle.REVIEW_REQUIRED.value
        if self.lot_manager.open_lots(position.code):
            return PositionLifecycle.HOLDING.value
        if position.position_state == PositionLifecycle.WAIT_REENTRY.value or position.last_fill_side == OrderSide.SELL.value:
            return PositionLifecycle.WAIT_REENTRY.value
        return PositionLifecycle.NEVER_BOUGHT.value

    def _pnl_mode(self, position: PositionState) -> str:
        pnl_rate = position.profit_loss_pct / 100.0
        if pnl_rate <= self.config.strategy.pnl_minus_threshold:
            return "MINUS"
        if pnl_rate >= self.config.strategy.pnl_plus_threshold:
            return "PLUS"
        return "NEUTRAL"

    def _reference_buy_lot(self, position: PositionState) -> LotState | None:
        mode = self._pnl_mode(position)
        if mode == "PLUS":
            return self.lot_manager.highest_open_buy_lot(position.code)
        return self.lot_manager.lowest_open_buy_lot(position.code)

    def _reference_sell_lot(self, position: PositionState) -> LotState | None:
        mode = self._pnl_mode(position)
        if mode == "PLUS":
            return self.lot_manager.highest_open_buy_lot(position.code)
        return self.lot_manager.lowest_open_buy_lot(position.code)

    def _sell_signal_met(self, position: PositionState, current_price: int, target_pct: float) -> bool:
        if position.cumulative_invested_amount <= 0:
            return False
        return bool(self.lot_manager.sellable_lots(position.code, current_price, position.cumulative_invested_amount, target_pct))

    def _reentry_condition_met(self, position: PositionState, current_price: int) -> bool:
        anchor = position.reentry_anchor_price or position.last_sell_price
        if self._position_state(position) != PositionLifecycle.WAIT_REENTRY.value or anchor <= 0:
            return False
        return current_price <= anchor * (1.0 - self.config.strategy.reentry_drop_rate)

    def _skip_reason(self, position: PositionState, current_price: int) -> str:
        state = self._position_state(position)
        if state in {PositionLifecycle.SYNC_REQUIRED.value, PositionLifecycle.REVIEW_REQUIRED.value, PositionLifecycle.RISK_BLOCKED.value}:
            return state.lower()
        if state == PositionLifecycle.WAIT_REENTRY.value and not self._reentry_condition_met(position, current_price):
            return "wait_reentry"
        if state == PositionLifecycle.NEVER_BOUGHT.value and current_price > self.config.strategy.initial_buy_amount:
            return "initial_buy_amount_below_price"
        return ""
