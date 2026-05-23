"""Lot-based variable grid strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import BotConfig
from .lot_manager import LotManager
from .models import AccountSnapshot, LotState, OrderSide, PositionLifecycle, PositionState, ReentryType, SellReason
from .risk_manager import RiskDecision


EPSILON = 1e-9


@dataclass(frozen=True)
class StrategyAction:
    side: OrderSide
    amount: int
    quantity: int | None
    reason: str
    lot_id: str = ""
    target_lot: LotState | None = None
    sell_reason: str = SellReason.UNKNOWN.value
    reentry_type: str = ReentryType.NONE.value
    cleanup_flag: bool = False


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
    normal_reentry_condition_met: bool = False
    trailing_reentry_condition_met: bool = False
    sell_reason: str = SellReason.UNKNOWN.value
    realized_pnl_rate: float = 0.0
    net_realized_pnl: int = 0
    reentry_type: str = ReentryType.NONE.value
    exit_anchor_price: int = 0
    cycle_highest_sell_price: int = 0
    cycle_last_sell_price: int = 0
    post_exit_high_price: int = 0
    cleanup_candidate: bool = False
    cleanup_loss_budget: int = 0
    expected_cleanup_loss: int = 0
    cleanup_allowed: bool = False
    cleanup_buy_cooldown_until: str = ""
    cleanup_reentry_cooldown_until: str = ""
    profit_take_lot_count: int = 0
    cleanup_candidate_lot_count: int = 0
    stale_lot_count: int = 0
    stale_lot_ids: str = "NONE"
    review_required_condition_met: bool = False
    review_reason: str = ""
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
        lifecycle = self._position_state(position)
        if lifecycle == PositionLifecycle.SYNC_REQUIRED.value:
            position.skip_reason = "sync_required"
            return None
        if lifecycle == PositionLifecycle.RISK_BLOCKED.value:
            # Conservative policy: risk-blocked symbols are held for manual review,
            # so both BUY and SELL are blocked until risk reasons are classified.
            position.skip_reason = "risk_blocked_buy_sell_blocked"
            return None
        sell = self._sell_action(position, current_price, snapshot)
        if sell:
            return sell
        if not account_risk.allowed or not symbol_risk.allowed:
            position.skip_reason = "|".join(account_risk.reasons + symbol_risk.reasons)
            return None
        buy_block = self._buy_block_reason(position)
        if buy_block:
            position.skip_reason = buy_block
            return None
        if position.quantity <= 0 or not self.lot_manager.open_lots(position.code):
            if lifecycle == PositionLifecycle.NEVER_BOUGHT.value:
                if current_price <= self.config.strategy.initial_buy_amount:
                    return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "initial_buy")
                return None
            if lifecycle == PositionLifecycle.WAIT_REENTRY.value:
                normal, trailing = self.check_reentry_conditions(position, current_price)
                if normal:
                    return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "reentry_buy", reentry_type=ReentryType.NORMAL_REENTRY.value)
                if trailing:
                    return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "reentry_buy", reentry_type=ReentryType.TRAILING_REENTRY.value)
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

    def _sell_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        candidate = self._sell_candidate(position, current_price, snapshot)
        if candidate is None:
            return None
        lot, sell_reason, expected_cleanup_loss, cleanup_allowed = candidate
        quantity = lot.remaining_quantity
        if sell_reason == SellReason.PROFIT_TAKE.value and exposure > self.config.strategy.auto_buy_limit:
            quantity = max(1, int(lot.remaining_quantity * self.config.strategy.high_exposure_partial_sell_pct / 100.0))
        if quantity < 1:
            return None
        estimated_fee_tax = int(round(current_price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
        net_pnl = (current_price - lot.buy_price) * quantity - estimated_fee_tax
        if sell_reason == SellReason.PROFIT_TAKE.value and net_pnl < 0:
            return None
        if sell_reason == SellReason.CLEANUP_SELL.value and not cleanup_allowed:
            return None
        reason = "cleanup_sell_lot" if sell_reason == SellReason.CLEANUP_SELL.value else "sell_profitable_lot"
        return StrategyAction(OrderSide.SELL, 0, quantity, reason, lot.lot_id, lot, sell_reason, cleanup_flag=expected_cleanup_loss > 0)

    def _sell_candidate(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> tuple[LotState, str, int, bool] | None:
        profit_candidates: list[LotState] = []
        cleanup_candidates: list[tuple[LotState, int, bool]] = []
        budget = self.cleanup_loss_budget(snapshot)
        for lot in self.lot_manager.open_lots(position.code):
            self.lot_manager.update_lot_target_metadata(lot, current_price)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            quantity = lot.remaining_quantity
            net_pnl = self.calculate_expected_realized_pnl(lot, current_price, quantity)
            if realized_rate + EPSILON < lot.effective_target_profit_rate:
                continue
            sell_reason = self.classify_sell_reason(lot, current_price, quantity)
            if sell_reason == SellReason.PROFIT_TAKE.value:
                profit_candidates.append(lot)
                continue
            if not self.config.strategy.cleanup_enabled:
                continue
            expected_loss = max(0, -net_pnl)
            cleanup_allowed = (
                self._position_state(position) == PositionLifecycle.HOLDING.value
                and lot.age_weeks >= self.config.strategy.cleanup_min_age_weeks
                and lot.effective_target_profit_rate < -EPSILON
                and lot.effective_target_profit_rate + EPSILON >= self.config.strategy.cleanup_min_target_rate
                and realized_rate < 0
                and realized_rate + EPSILON >= self.config.strategy.cleanup_min_target_rate
                and budget > 0
                and expected_loss <= budget
            )
            if sell_reason == SellReason.CLEANUP_SELL.value and lot.effective_target_profit_rate < -EPSILON and realized_rate < 0:
                lot.cleanup_candidate = True
                cleanup_candidates.append((lot, expected_loss, cleanup_allowed))
        if profit_candidates:
            return (self._sort_sell_lots(profit_candidates, current_price)[0], SellReason.PROFIT_TAKE.value, 0, True)
        allowed_cleanup = [item for item in cleanup_candidates if item[2]]
        if allowed_cleanup:
            lot, expected_loss, cleanup_allowed = sorted(allowed_cleanup, key=lambda item: (item[1], -item[0].age_weeks))[0]
            return (lot, SellReason.CLEANUP_SELL.value, expected_loss, cleanup_allowed)
        return None

    def calculate_expected_realized_pnl(self, lot: LotState, price: int, quantity: int | None = None) -> int:
        quantity = quantity or lot.remaining_quantity
        return self._net_pnl(lot, price, quantity)

    def classify_sell_reason(self, lot: LotState, price: int, quantity: int | None = None) -> str:
        net_pnl = self.calculate_expected_realized_pnl(lot, price, quantity)
        return SellReason.PROFIT_TAKE.value if net_pnl >= 0 else SellReason.CLEANUP_SELL.value

    def _sort_sell_lots(self, lots: list[LotState], current_price: int) -> list[LotState]:
        return sorted(
            lots,
            key=lambda lot: (
                lot.profit_pct_at(current_price),
                lot.open_amount,
                -datetime.fromisoformat(lot.buy_filled_at).timestamp(),
                -lot.remaining_quantity,
            ),
            reverse=True,
        )

    def context(self, position: PositionState, current_price: int, snapshot: AccountSnapshot | None = None) -> StrategyContext:
        snapshot = snapshot or AccountSnapshot(0, 0, 0, 0, ())
        exposure = position.cumulative_invested_amount
        lowest = self.lot_manager.lowest_open_buy_lot(position.code)
        highest = self.lot_manager.highest_open_buy_lot(position.code)
        target_profit = self.lot_manager.target_profit_pct(exposure) if exposure > 0 else 0.0
        buy_plan = self.lot_manager.buy_plan(exposure)
        reference_buy = self._reference_buy_lot(position)
        reference_sell = self._reference_sell_lot(position)
        profit_take_lots = self.lot_manager.profit_take_lots(position.code, current_price, exposure, target_profit) if exposure > 0 else []
        cleanup_candidate_lots = self.lot_manager.cleanup_candidate_lots(position.code, current_price) if exposure > 0 else []
        stale_lots = self.lot_manager.stale_lots(position.code, current_price) if exposure > 0 else []
        normal_reentry, trailing_reentry = self.check_reentry_conditions(position, current_price)
        sell_candidate = self._sell_candidate(position, current_price, snapshot) if exposure > 0 else None
        cleanup_budget = self.cleanup_loss_budget(snapshot)
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
            profitable_lots=";".join(lot.lot_id for lot in profit_take_lots) or "NONE",
            selected_sell_lot_id=profit_take_lots[0].lot_id if profit_take_lots else "",
            reentry_condition_met=normal_reentry or trailing_reentry,
            normal_reentry_condition_met=normal_reentry,
            trailing_reentry_condition_met=trailing_reentry,
            sell_reason=sell_candidate[1] if sell_candidate else SellReason.UNKNOWN.value,
            realized_pnl_rate=(sell_candidate[0].profit_pct_at(current_price) / 100.0) if sell_candidate else 0.0,
            net_realized_pnl=self._net_pnl(sell_candidate[0], current_price, sell_candidate[0].remaining_quantity) if sell_candidate else 0,
            reentry_type=ReentryType.TRAILING_REENTRY.value if trailing_reentry else (ReentryType.NORMAL_REENTRY.value if normal_reentry else ReentryType.NONE.value),
            exit_anchor_price=position.exit_anchor_price,
            cycle_highest_sell_price=position.cycle_highest_sell_price,
            cycle_last_sell_price=position.cycle_last_sell_price,
            post_exit_high_price=position.post_exit_high_price,
            cleanup_candidate=bool(cleanup_candidate_lots),
            cleanup_loss_budget=cleanup_budget,
            expected_cleanup_loss=sell_candidate[2] if sell_candidate else 0,
            cleanup_allowed=bool(sell_candidate and sell_candidate[1] == SellReason.CLEANUP_SELL.value and sell_candidate[3]),
            cleanup_buy_cooldown_until=position.cleanup_buy_cooldown_until,
            cleanup_reentry_cooldown_until=position.cleanup_reentry_cooldown_until,
            profit_take_lot_count=len(profit_take_lots),
            cleanup_candidate_lot_count=len(cleanup_candidate_lots),
            stale_lot_count=len(stale_lots),
            stale_lot_ids=";".join(lot.lot_id for lot in stale_lots) or "NONE",
            review_required_condition_met=position.needs_review,
            review_reason=position.review_reason,
            skip_reason=self._skip_reason(position, current_price),
        )

    def _position_state(self, position: PositionState) -> str:
        if position.sync_status == PositionLifecycle.SYNC_REQUIRED.value or position.trading_paused:
            return PositionLifecycle.SYNC_REQUIRED.value
        if position.danger_state:
            return PositionLifecycle.RISK_BLOCKED.value
        if position.needs_review:
            return PositionLifecycle.REVIEW_REQUIRED.value
        if position.position_state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value:
            return PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
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
        return bool(self.lot_manager.profit_take_lots(position.code, current_price, position.cumulative_invested_amount, target_pct))

    def update_reentry_tracking(self, position: PositionState, current_price: int, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        anchor = position.exit_anchor_price or position.reentry_anchor_price or position.last_sell_price
        if self._position_state(position) != PositionLifecycle.WAIT_REENTRY.value or anchor <= 0:
            return False
        previous_high = position.post_exit_high_price
        if position.post_exit_high_price <= 0:
            position.post_exit_high_price = anchor
        position.post_exit_high_price = max(position.post_exit_high_price, current_price)
        today = now.date().isoformat()
        previous_count_date = position.trailing_reentry_count_date
        if position.trailing_reentry_count_date != today:
            position.trailing_reentry_count_today = 0
            position.trailing_reentry_count_date = today
        return previous_high != position.post_exit_high_price or previous_count_date != position.trailing_reentry_count_date

    def check_reentry_conditions(self, position: PositionState, current_price: int, now: datetime | None = None) -> tuple[bool, bool]:
        now = now or datetime.now()
        anchor = position.exit_anchor_price or position.reentry_anchor_price or position.last_sell_price
        if self._position_state(position) != PositionLifecycle.WAIT_REENTRY.value or anchor <= 0:
            return False, False
        post_exit_high = position.post_exit_high_price if position.post_exit_high_price > 0 else anchor
        normal = current_price <= anchor * (1.0 - self.config.strategy.normal_reentry_drop_rate)
        exit_time = _parse_time(position.exit_time)
        waited = exit_time is not None and now - exit_time >= timedelta(minutes=self.config.strategy.min_reentry_wait_minutes)
        count_today = position.trailing_reentry_count_today if position.trailing_reentry_count_date == now.date().isoformat() else 0
        trailing = (
            post_exit_high >= anchor * (1.0 + self.config.strategy.trailing_activation_gain)
            and current_price <= post_exit_high * (1.0 - self.config.strategy.trailing_reentry_drop_rate)
            and waited
            and count_today < self.config.strategy.max_trailing_reentry_per_day
        )
        return normal, trailing

    def _skip_reason(self, position: PositionState, current_price: int) -> str:
        if position.skip_reason:
            return position.skip_reason
        state = self._position_state(position)
        buy_block = self._buy_block_reason(position)
        if buy_block:
            return buy_block
        if state in {PositionLifecycle.SYNC_REQUIRED.value, PositionLifecycle.REVIEW_REQUIRED.value, PositionLifecycle.RISK_BLOCKED.value}:
            return state.lower()
        if state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value:
            return "cleanup_cooldown"
        if state == PositionLifecycle.WAIT_REENTRY.value and not any(self.check_reentry_conditions(position, current_price)):
            return "wait_reentry"
        if state == PositionLifecycle.NEVER_BOUGHT.value and current_price > self.config.strategy.initial_buy_amount:
            return "initial_buy_amount_below_price"
        return ""

    def _buy_block_reason(self, position: PositionState) -> str:
        state = self._position_state(position)
        if state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value:
            return "cleanup_cooldown"
        cooldown_until = _parse_time(position.cleanup_buy_cooldown_until)
        if cooldown_until is not None and datetime.now() < cooldown_until:
            return "cleanup_buy_cooldown"
        if position.last_reentry_type != ReentryType.NONE.value:
            elapsed = None
            last_order_time = _parse_time(position.last_order_time)
            if last_order_time is not None:
                elapsed = datetime.now() - last_order_time
            if elapsed is not None and elapsed < timedelta(minutes=self.config.strategy.reentry_buy_cooldown_minutes):
                return "reentry_buy_cooldown"
        return ""

    def cleanup_loss_budget(self, snapshot: AccountSnapshot) -> int:
        return int(max(0, snapshot.daily_profit_loss) * self.config.strategy.cleanup_profit_offset_ratio)

    def _net_pnl(self, lot: LotState, price: int, quantity: int) -> int:
        fee_tax = int(round(price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
        return (price - lot.buy_price) * quantity - fee_tax


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
