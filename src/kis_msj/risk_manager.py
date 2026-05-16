"""Risk checks for per-symbol and account-level trading limits."""

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
        self.state_mismatch_detected = False

    def account_buy_allowed(self, snapshot: AccountSnapshot, positions: dict[str, PositionState]) -> RiskDecision:
        reasons: list[str] = []
        risk = self.config.risk
        if risk.market_risk_mode:
            reasons.append("market_risk_mode")
        if snapshot.daily_profit_loss_pct <= risk.daily_account_loss_limit_pct:
            reasons.append("daily_account_loss_limit")
        if snapshot.total_profit_loss_pct <= risk.total_account_loss_limit_pct:
            reasons.append("total_account_loss_limit")
        stop_waiting = sum(1 for position in positions.values() if position.position_profit_loss_pct <= risk.block_add_buy_loss_pct)
        if stop_waiting >= risk.max_stop_waiting_positions:
            reasons.append("too_many_stop_waiting_positions")
        if self.consecutive_api_errors >= risk.max_consecutive_api_errors:
            reasons.append("consecutive_api_errors")
        if risk.block_on_state_mismatch and self.state_mismatch_detected:
            reasons.append("state_mismatch")
        total_exposure = sum(position.cumulative_invested_amount for position in positions.values())
        if total_exposure >= risk.max_total_exposure:
            reasons.append("max_total_exposure")
        if snapshot.total_asset > 0 and total_exposure / snapshot.total_asset * 100.0 >= risk.max_total_exposure_pct:
            reasons.append("max_total_exposure_pct")
        return RiskDecision(not reasons, tuple(reasons))

    def symbol_buy_allowed(self, position: PositionState) -> RiskDecision:
        reasons: list[str] = []
        if position.cumulative_invested_amount >= self.config.strategy.max_position_amount:
            reasons.append("max_position_amount")
        if position.position_profit_loss_pct <= self.config.risk.block_add_buy_loss_pct:
            reasons.append("symbol_add_buy_blocked_by_loss")
        return RiskDecision(not reasons, tuple(reasons))
