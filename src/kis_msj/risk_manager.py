"""Risk gates for the lot-based strategy."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BotConfig
from .models import AccountSnapshot, PositionState


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()


class RiskManager:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.consecutive_api_errors = 0
        self.data_mismatch_detected = False

    def account_buy_allowed(self, snapshot: AccountSnapshot, positions: dict[str, PositionState]) -> RiskDecision:
        reasons: list[str] = []
        risk = self.config.risk
        if risk.market_risk_mode:
            reasons.append("market_risk_mode")
        if snapshot.daily_profit_loss_pct <= risk.daily_account_loss_limit_pct:
            reasons.append("daily_account_loss_limit")
        if snapshot.total_profit_loss_pct <= risk.total_account_loss_limit_pct:
            reasons.append("total_account_loss_limit")
        if sum(1 for item in positions.values() if item.needs_review) >= risk.max_review_positions:
            reasons.append("too_many_review_positions")
        if snapshot.cash_available < risk.min_cash_available:
            reasons.append("cash_below_minimum")
        total_invested = sum(max(0, item.cumulative_invested_amount) for item in positions.values())
        if total_invested >= risk.max_total_invested_amount:
            reasons.append("max_total_invested_amount_reached")
        if self.consecutive_api_errors >= risk.max_consecutive_api_errors:
            reasons.append("consecutive_api_errors")
        if self.data_mismatch_detected:
            reasons.append("data_mismatch")
        return RiskDecision(not reasons, tuple(reasons))

    def symbol_buy_allowed(self, position: PositionState) -> RiskDecision:
        reasons: list[str] = []
        if position.danger_state:
            reasons.append("danger_state")
        if position.trading_paused:
            reasons.append("trading_paused")
        if position.sync_status == "SYNC_REQUIRED":
            reasons.append("sync_required")
        if position.needs_review:
            reasons.append("needs_review")
        if not position.auto_buy_enabled:
            reasons.append("auto_buy_disabled")
        lot_sizing_mode = self.config.strategy.lot_sizing_mode == "cycle_locked_by_entry_price"
        if not lot_sizing_mode and position.cumulative_invested_amount > position.auto_buy_limit:
            reasons.append("auto_buy_limit_exceeded")
        if not lot_sizing_mode and position.cumulative_invested_amount >= position.absolute_max_investment:
            reasons.append("absolute_max_reached")
        if position.profit_loss_pct <= self.config.strategy.review_loss_pct:
            reasons.append("unrealized_loss_review")
        if position.lot_quantity_mismatch and self.config.risk.block_on_lot_mismatch:
            reasons.append("lot_quantity_mismatch")
        return RiskDecision(not reasons, tuple(reasons))
