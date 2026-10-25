"""Lot-based variable grid strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import NamedTuple

from .config import BotConfig
from .lot_manager import LotManager, ceil_price, lot_buy_timestamp, round_price
from .models import AccountSnapshot, LotState, OrderSide, PositionLifecycle, PositionState, ReentryType, SellReason
from .risk_manager import RiskDecision
from .adaptive_policy import cash_multipliers, trend_multiplier, lowest_lot_buy_target, reentry_buy_target, floor_order_price


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
    limit_price: int = 0


class SellCandidate(NamedTuple):
    lot: LotState
    reason: str
    expected_loss: int
    allowed: bool
    trigger_lot: LotState
    limit_price: int


@dataclass(frozen=True)
class StrategyContext:
    position_state: str
    pnl_mode: str
    position_pnl_rate: float
    lowest_open_buy_lot_price: int = 0
    highest_open_buy_lot_price: int = 0
    open_lot_vwap_buy_price: int = 0
    median_open_buy_price: int = 0
    reference_buy_price: int = 0
    reference_buy_source: str = ""
    reference_exclusion_enabled: bool = False
    reference_exclusion_threshold: float = 0.0
    reference_total_open_lot_count: int = 0
    reference_excluded_lot_count: int = 0
    reference_eligible_lot_count: int = 0
    excluded_lot_ids: str = "NONE"
    excluded_lot_return_rates: str = "NONE"
    open_lot_vwap_raw_all_lots: int = 0
    median_open_buy_price_raw_all_lots: int = 0
    open_lot_vwap_reference_eligible_only: int = 0
    median_open_buy_price_reference_eligible_only: int = 0
    reference_buy_price_before_exclusion: int = 0
    reference_buy_price_after_exclusion: int = 0
    reference_fallback_to_current_price: bool = False
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
    reentry_mode: str = ReentryType.NONE.value
    wait_reentry_started_at: str = ""
    days_since_wait_reentry_started: int = 0
    base_reentry_rate: float = 0.0
    daily_reentry_decay_rate: float = 0.0
    decay_duration_days: int = 0
    effective_reentry_rate: float = 0.0
    reentry_trigger_price: int = 0
    force_reentry_timeout_days: int = 0
    force_reentry_eligible: bool = False
    force_reentry_reason: str = ""
    force_reentry_starts_new_cycle: bool = False
    old_cycle_id: str = ""
    new_cycle_id: str = ""
    retire_after_exit: bool = False
    retire_reason: str = ""
    trade_stop_after_exit_eligible: bool = False
    trade_stop_after_exit_state: str = ""
    buy_blocked_by_retire_after_exit: bool = False
    reentry_blocked_by_retire_after_exit: bool = False
    sell_reason: str = SellReason.UNKNOWN.value
    realized_pnl_rate: float = 0.0
    net_realized_pnl: int = 0
    reentry_type: str = ReentryType.NONE.value
    exit_anchor_price: int = 0
    cycle_highest_sell_price: int = 0
    cycle_last_sell_price: int = 0
    cycle_sell_vwap_price: int = 0
    cycle_sell_median_price: int = 0
    normal_exit_anchor_price: int = 0
    trailing_exit_anchor_price: int = 0
    cycle_sell_fill_count: int = 0
    anchor_single_fill: bool = False
    anchor_confidence: str = ""
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
    entry_price_for_lot_sizing: int = 0
    lot_unit_amount: int = 0
    max_symbol_amount: int = 0
    max_lots_per_symbol: int = 0
    lot_sizing_bucket: str = ""
    lot_sizing_locked: bool = False
    lot_sizing_locked_at: str = ""
    lot_sizing_mode: str = ""
    lot_sizing_skip_reason: str = ""
    add_buy_lot_band: str = ""
    current_open_lot_count: int = 0
    original_lot_base_target_profit_rate: float = 0.0
    current_base_target_profit_rate: float = 0.0
    target_profit_source: str = ""
    target_profit_lot_band: str = ""
    active_lot_count_band: str = ""
    max_lots_reached: bool = False
    max_lots_buy_blocked: bool = False
    position_state_after_max_lots_check: str = ""
    effective_target_profit_rate: float = 0.0
    lot_age_weeks: float = 0.0
    age_decay_rate: float = 0.0
    base_target_profit_rate: float = 0.0
    age_decay_applied: float = 0.0
    lot_unrealized_pnl_rate: float = 0.0
    decay_cleanup_eligible: bool = False
    cash_target_profit_active: bool = False
    cash_available_for_target: int = 0
    low_cash_high_cost_sell: bool = False
    low_cash_oldest_lot_sell: bool = False
    negative_target_high_cost_sell: bool = False
    sell_trigger_lot_id: str = ""
    sell_trigger_price: int = 0
    sell_limit_price: int = 0
    reentry_policy: str = "legacy_price"
    reentry_lot_unit_amount: int = 0
    reentry_required_cash: int = 0
    reentry_cash_available: int | None = None
    reentry_cash_condition_met: bool = False
    wait_reentry_timestamp_valid: bool = False
    cash_buy_drop_enabled: bool = False
    cash_buy_reference_price: float = 0.0
    cash_buy_reference_source: str = ""
    cash_buy_drop_rate: float | None = None
    cash_buy_limit_price: int = 0
    cash_buy_price_condition_met: bool = False
    cash_buy_skip_reason: str = ""
    pre_exit_average_price: float = 0.0
    pre_exit_average_captured_at: str = ""
    reentry_price_condition_met: bool = False
    volatility_multiplier: float = 1.0
    consecutive_buy_count: int = 0
    consecutive_sell_count: int = 0
    trend_score: int = 0
    effective_buy_pnl_threshold: float | None = None
    rebuy_restriction_active: bool = False
    adaptive_buy_target_price: int = 0
    cash_buy_multiplier: float | None = None
    cash_sell_multiplier: float | None = None
    cash_adjustment_known: bool = False
    pre_cash_buy_gap_pct: float = 0.0
    pre_cash_sell_target_rate: float = 0.0


@dataclass(frozen=True)
class ReferencePriceContext:
    enabled: bool
    threshold: float
    total_open_lot_count: int
    excluded_lot_count: int
    eligible_lot_count: int
    excluded_lot_ids: str
    excluded_lot_return_rates: str
    raw_vwap: int
    raw_median: int
    eligible_vwap: int
    eligible_median: int
    before_price: int
    after_price: int
    source: str
    fallback_to_current_price: bool


class LotGridStrategy:
    def __init__(self, config: BotConfig, lot_manager: LotManager) -> None:
        self.config = config
        self.lot_manager = lot_manager
        self.positions: dict[str, PositionState] = {}

    def adaptive_reentry_cash_details(self, position: PositionState, snapshot: AccountSnapshot | None,
                                     new_order_cost: int) -> dict[str, object]:
        """Broker buying power already reserves acknowledged pending orders."""
        cash = snapshot.cash_available if snapshot else None
        assets = snapshot.total_asset if snapshot else None
        valid = (type(cash) in (int, float) and math.isfinite(cash) and cash >= 0
                 and type(assets) in (int, float) and math.isfinite(assets) and assets > 0)
        future = 0
        codes = {lot.code for lot in self.lot_manager.lots.values() if lot.remaining_quantity > 0}
        codes.update(code for code, held in self.positions.items() if held.quantity > 0)
        broker_held = {held.code for held in snapshot.positions if held.quantity > 0} if snapshot else set()
        codes.update(broker_held)
        codes.add(position.code)
        for code in codes:
            lots = self.lot_manager.open_lots(code)
            held = self.positions.get(code) or (position if code == position.code else PositionState(code))
            if not lots and (held.quantity > 0 or code in broker_held):
                valid = False
            price = min((lot.buy_price for lot in lots), default=position.current_price)
            sizing = (self.lot_sizing_from_position(held) if held.lot_unit_amount
                      else self.lot_sizing_for_new_cycle(price))
            unit = int(sizing.get("lot_unit_amount", 0)) if self._lot_sizing_enabled() else self.config.strategy.initial_buy_amount
            maximum = int(sizing.get("max_lots_per_symbol", 0)) or self.config.strategy.max_lots_per_symbol_default
            max_amount = int(sizing.get("max_symbol_amount", 0)) if self._lot_sizing_enabled() else self.config.strategy.absolute_max_investment
            proposed = int(code == position.code)
            exposure = sum(lot.open_amount for lot in lots) + (new_order_cost if proposed else 0)
            slots = min(self.config.strategy.reentry_reserve_lots_per_symbol,
                        max(0, maximum - len(lots) - proposed))
            if unit <= 0:
                valid = False
                continue
            slots = min(slots, max(0, (max_amount - exposure) // unit)) if max_amount else slots
            future += slots * unit
        buffer = math.ceil(assets * self.config.strategy.reentry_cash_buffer_ratio) if valid else 0
        required = new_order_cost + future + buffer
        allowed = bool(valid and cash >= required)
        return {"reentry_required_cash": required, "reentry_future_lot_reserve": future,
                "reentry_safety_cash_buffer": buffer, "reentry_cash_available": cash,
                "reentry_cash_condition_met": allowed,
                "skip_reason": "" if allowed else "adaptive_reentry_cash_insufficient"}

    def adaptive_buy_details(self, position: PositionState, current_price: int,
                             snapshot: AccountSnapshot | None) -> dict[str, object]:
        cash = snapshot.cash_available if snapshot else None
        try:
            buy_cash_multiplier, sell_cash_multiplier = cash_multipliers(cash)
        except ValueError:
            buy_cash_multiplier = sell_cash_multiplier = None
        valid_cash = buy_cash_multiplier is not None
        lots = self.lot_manager.open_lots(position.code)
        self.lot_manager.set_symbol_trade_state(position.code, position.consecutive_sell_count)
        multiplier = self.lot_manager.volatility_multiplier_for(position.code)
        score = self.lot_manager.trend_score_for(position.code)
        pre_cash_gap = (4.0 + 2.0 * (len(lots) - 1)) * multiplier * trend_multiplier(score) if lots else 0.0
        gap = pre_cash_gap * buy_cash_multiplier if valid_cash else 0.0
        quantity = sum(lot.remaining_quantity for lot in lots)
        cost = sum(lot.buy_price * lot.remaining_quantity for lot in lots)
        vwap = cost / quantity if quantity > 0 else 0.0
        valid_position = not lots or (quantity > 0 and math.isfinite(vwap) and vwap > 0 and current_price > 0)
        normal = vwap * (1.0 - gap / 100.0) if lots and valid_position and gap < 100 else 0.0
        restricted = position.rebuy_restriction_active
        rebuy_gap = 4.0 * multiplier * buy_cash_multiplier if valid_cash else 0.0
        rebuy = reentry_buy_target(position.last_sell_price, rebuy_gap) if restricted and valid_cash else 0.0
        ceilings = ([normal] if lots else []) + ([rebuy] if restricted else [])
        target = floor_order_price(min(ceilings) if ceilings else current_price) if valid_cash else 0
        sizing = (self.ensure_lot_sizing(position, current_price) if lots else
                  self.lot_sizing_for_new_cycle(current_price)) if self._lot_sizing_enabled() else {}
        amount = int(sizing.get("lot_unit_amount", 0)) if self._lot_sizing_enabled() else self.config.strategy.initial_buy_amount
        block = self._retire_buy_block_reason(position, self._position_state(position), current_price)
        block = block or ("adaptive_invalid_position_pnl" if not valid_position else
                          "adaptive_pnl_threshold_unreachable" if lots and gap >= 100 else "")
        block = block or self._buy_block_reason(position)
        if self._lot_sizing_enabled():
            block = block or self.lot_sizing_buy_block_reason(position, current_price, sizing,
                next_buy_amount=amount, open_lot_count=len(lots))
        elif len(lots) >= self.config.strategy.max_lots_per_symbol_default:
            block = block or "max_lots_per_symbol_reached"
        elif (position.cumulative_invested_amount + amount > self.config.strategy.absolute_max_investment
              or position.cumulative_invested_amount + amount > self.config.strategy.auto_buy_limit):
            block = block or "max_symbol_amount_reached"
        if not valid_cash:
            block = block or "adaptive_cash_snapshot_unavailable"
        elif cash < amount:
            block = block or "cash_not_enough"
        condition = current_price > 0 and target > 0 and current_price <= target
        if not lots and not self._lot_sizing_enabled() and current_price > self.config.strategy.initial_buy_amount:
            condition = False
        reserve = self.adaptive_reentry_cash_details(position, snapshot, amount) if restricted else {
            "reentry_required_cash": amount, "reentry_cash_condition_met": bool(valid_cash and cash >= amount),
            "reentry_cash_available": cash, "skip_reason": ""}
        block = block or str(reserve["skip_reason"])
        if not condition:
            block = block or "adaptive_buy_price_not_reached"
        return {**reserve, "buy_amount": amount, "buy_target_price": target,
                "position_vwap": vwap, "position_pnl_pct": (current_price / vwap - 1) * 100 if valid_position and vwap else None,
                "active_lot_count": len(lots), "trend_score": score,
                "effective_buy_pnl_threshold": -gap if lots else None,
                "normal_buy_target": normal, "reentry_target": rebuy,
                "effective_buy_gap_pct": gap, "effective_rebuy_gap_pct": rebuy_gap,
                "pre_cash_buy_gap_pct": pre_cash_gap,
                "cash_buy_multiplier": buy_cash_multiplier, "cash_sell_multiplier": sell_cash_multiplier,
                "cash_adjustment_known": valid_cash,
                "buy_condition_met": condition, "skip_reason": block}

    def _adaptive_decide(self, position, current_price, snapshot, account_risk, symbol_risk):
        try:
            cash_multipliers(snapshot.cash_available if snapshot else None)
        except ValueError:
            position.skip_reason = "adaptive_cash_snapshot_unavailable"
            return None
        from .trading_calendar import CalendarUnavailable
        try:
            self.lot_manager.trend_score_for(position.code)
            for lot in self.lot_manager.open_lots(position.code):
                self.lot_manager.calendar.completed_since(lot.buy_filled_at, datetime.now())
        except CalendarUnavailable as error:
            position.skip_reason = str(error)
            return None
        self.lot_manager.set_symbol_trade_state(position.code, position.consecutive_sell_count)
        lifecycle = self._position_state(position)
        if lifecycle in {PositionLifecycle.SYNC_REQUIRED.value, PositionLifecycle.RISK_BLOCKED.value}:
            position.skip_reason = lifecycle.lower()
            return None
        sell = self._sell_action(position, current_price, snapshot)
        if sell:
            return sell
        if not account_risk.allowed or not symbol_risk.allowed:
            position.skip_reason = "|".join(account_risk.reasons + symbol_risk.reasons)
            return None
        details = self.adaptive_buy_details(position, current_price, snapshot)
        position.skip_reason = str(details["skip_reason"])
        if position.skip_reason:
            return None
        restricted = position.rebuy_restriction_active
        reason = "adaptive_reentry_buy" if restricted else ("adaptive_add_buy" if self.lot_manager.open_lots(position.code) else "adaptive_initial_buy")
        return StrategyAction(OrderSide.BUY, int(details["buy_amount"]), None, reason,
            reentry_type=ReentryType.NORMAL_REENTRY.value if restricted else ReentryType.NONE.value,
            limit_price=int(details["buy_target_price"]))

    def decide(
        self,
        position: PositionState,
        current_price: int,
        snapshot: AccountSnapshot,
        account_risk: RiskDecision,
        symbol_risk: RiskDecision,
    ) -> StrategyAction | None:
        if self.config.strategy.adaptive_lot_enabled:
            return self._adaptive_decide(position, current_price, snapshot, account_risk, symbol_risk)
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
        retire_block = self._retire_buy_block_reason(position, lifecycle, current_price)
        if retire_block:
            position.skip_reason = retire_block
            return None
        if not account_risk.allowed or not symbol_risk.allowed:
            position.skip_reason = "|".join(account_risk.reasons + symbol_risk.reasons)
            return None
        if self.config.strategy.cash_buy_drop_enabled:
            cash = snapshot.cash_available if snapshot is not None else None
            if type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0:
                position.skip_reason = "cash_buy_snapshot_unavailable"
                return None
        buy_block = self._buy_block_reason(position)
        if buy_block:
            position.skip_reason = buy_block
            return None
        if position.quantity <= 0 or not self.lot_manager.open_lots(position.code):
            if lifecycle == PositionLifecycle.NEVER_BOUGHT.value:
                if self._lot_sizing_enabled():
                    sizing = self.lot_sizing_for_new_cycle(current_price)
                    block = self.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=sizing.get("lot_unit_amount", 0), open_lot_count=0)
                    if block:
                        position.skip_reason = block
                        return None
                    return StrategyAction(OrderSide.BUY, int(sizing["lot_unit_amount"]), None, "initial_buy")
                if current_price <= self.config.strategy.initial_buy_amount:
                    return StrategyAction(OrderSide.BUY, self.config.strategy.initial_buy_amount, None, "initial_buy")
                return None
            if lifecycle == PositionLifecycle.WAIT_REENTRY.value:
                if self.config.strategy.cash_capacity_reentry_enabled():
                    details = self.cash_capacity_reentry_details(position, current_price, snapshot)
                    if details["skip_reason"]:
                        position.skip_reason = str(details["skip_reason"])
                        return None
                    return StrategyAction(
                        OrderSide.BUY, int(details["reentry_lot_unit_amount"]), None,
                        "cash_capacity_reentry", reentry_type=ReentryType.CASH_CAPACITY_REENTRY.value,
                        limit_price=(int(details.get("cash_buy_limit_price", 0))
                                     if self.config.strategy.cash_buy_drop_enabled else 0),
                    )
                normal, trailing = self.check_reentry_conditions(position, current_price)
                if normal:
                    if self._lot_sizing_enabled():
                        sizing = self.lot_sizing_for_new_cycle(current_price)
                        block = self.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=sizing.get("lot_unit_amount", 0), open_lot_count=0)
                        if block:
                            position.skip_reason = block
                            return None
                        amount = int(sizing["lot_unit_amount"])
                    else:
                        amount = self.config.strategy.initial_buy_amount
                    limit_price = round_price(self.reentry_details(position, current_price)["reentry_trigger_price"])
                    return StrategyAction(OrderSide.BUY, amount, None, "reentry_buy", reentry_type=ReentryType.NORMAL_REENTRY.value, limit_price=limit_price)
                if trailing:
                    if self._lot_sizing_enabled():
                        sizing = self.lot_sizing_for_new_cycle(current_price)
                        block = self.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=sizing.get("lot_unit_amount", 0), open_lot_count=0)
                        if block:
                            position.skip_reason = block
                            return None
                        amount = int(sizing["lot_unit_amount"])
                    else:
                        amount = self.config.strategy.initial_buy_amount
                    limit_price = round_price(self.reentry_details(position, current_price)["reentry_trigger_price"])
                    return StrategyAction(OrderSide.BUY, amount, None, "reentry_buy", reentry_type=ReentryType.TRAILING_REENTRY.value, limit_price=limit_price)
                if self.force_reentry_eligible(position, current_price):
                    if self._lot_sizing_enabled():
                        sizing = self.lot_sizing_for_new_cycle(current_price)
                        block = self.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=sizing.get("lot_unit_amount", 0), open_lot_count=0)
                        if block:
                            position.skip_reason = block
                            return None
                        amount = int(sizing["lot_unit_amount"])
                    else:
                        amount = self.config.strategy.initial_buy_amount
                    return StrategyAction(
                        OrderSide.BUY,
                        amount,
                        None,
                        "FORCE_REENTRY_TIMEOUT_NEW_CYCLE",
                        reentry_type=ReentryType.FORCE_REENTRY_TIMEOUT_NEW_CYCLE.value,
                    )
            return None
        return self._add_buy_action(position, current_price, snapshot)

    def _add_buy_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        cash_buy = self.cash_buy_details(position, current_price, snapshot)
        if self.config.strategy.cash_buy_drop_enabled and cash_buy["skip_reason"]:
            position.skip_reason = str(cash_buy["skip_reason"])
            return None
        if self._lot_sizing_enabled():
            self.ensure_lot_sizing(position, current_price)
            open_lot_count = len(self.lot_manager.open_lots(position.code))
            plan = self.add_buy_lot_plan(open_lot_count)
            reference_price, _ = self._reference_buy_price(position, current_price)
            if not reference_price or not plan:
                return None
            drop_rate, add_lot_count = plan
            if self.config.strategy.cash_buy_drop_enabled:
                drop_rate = float(cash_buy["cash_buy_drop_rate"])
                reference_price = float(cash_buy["cash_buy_reference_price"])
            amount = position.lot_unit_amount * add_lot_count
            block = self.lot_sizing_buy_block_reason(position, current_price, self.lot_sizing_from_position(position), next_buy_amount=amount, open_lot_count=open_lot_count)
            if block:
                position.skip_reason = block
                if block in {"max_symbol_amount_reached", "lot_sizing_missing"}:
                    position.needs_review = True
                    position.review_reason = block
                return None
            if snapshot.cash_available < amount:
                position.skip_reason = "cash_not_enough"
                return None
            decline = (current_price - reference_price) / reference_price
            if (cash_buy["cash_buy_price_condition_met"] if self.config.strategy.cash_buy_drop_enabled
                    else decline <= -drop_rate):
                limit_price = (int(cash_buy["cash_buy_limit_price"]) if self.config.strategy.cash_buy_drop_enabled
                               else round_price(reference_price * (1.0 - drop_rate)))
                return StrategyAction(OrderSide.BUY, amount, None, f"add_buy_drop_{drop_rate * 100:g}%", limit_price=limit_price)
            return None
        exposure = position.cumulative_invested_amount
        if exposure > self.config.strategy.auto_buy_limit:
            position.needs_review = True
            position.auto_buy_enabled = False
            position.position_state = PositionLifecycle.REVIEW_REQUIRED.value
            return None
        plan = self.lot_manager.buy_plan(exposure)
        reference_price, _ = self._reference_buy_price(position, current_price)
        if not reference_price or not plan:
            return None
        drop_pct, amount = plan
        if self.config.strategy.cash_buy_drop_enabled:
            drop_pct = float(cash_buy["cash_buy_drop_rate"]) * 100.0
            reference_price = float(cash_buy["cash_buy_reference_price"])
        if exposure + amount > self.config.strategy.auto_buy_limit:
            return None
        if exposure + amount > self.config.strategy.absolute_max_investment:
            return None
        if snapshot.cash_available < amount:
            return None
        decline = (current_price - reference_price) / reference_price * 100.0
        if (cash_buy["cash_buy_price_condition_met"] if self.config.strategy.cash_buy_drop_enabled
                else decline <= -drop_pct):
            limit_price = (int(cash_buy["cash_buy_limit_price"]) if self.config.strategy.cash_buy_drop_enabled
                           else round_price(reference_price * (1.0 - drop_pct / 100.0)))
            return StrategyAction(OrderSide.BUY, amount, None, f"add_buy_drop_{drop_pct:g}%", limit_price=limit_price)
        return None

    def _sell_action(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> StrategyAction | None:
        exposure = position.cumulative_invested_amount
        candidate = self._sell_candidate(position, current_price, snapshot)
        if candidate is None:
            return None
        lot, sell_reason, expected_cleanup_loss, cleanup_allowed = candidate[:4]
        quantity = lot.remaining_quantity
        partial_threshold = self.config.strategy.profit_partial_sell_exposure_threshold
        if partial_threshold is None:
            partial_threshold = self.config.strategy.auto_buy_limit
        if sell_reason == SellReason.PROFIT_TAKE.value and exposure > partial_threshold:
            quantity = max(1, int(lot.remaining_quantity * self.config.strategy.high_exposure_partial_sell_pct / 100.0))
        if quantity < 1:
            return None
        estimated_fee_tax = int(round(current_price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
        net_pnl = (current_price - lot.buy_price) * quantity - estimated_fee_tax
        if sell_reason == SellReason.PROFIT_TAKE.value and net_pnl < 0 and not self.config.strategy.adaptive_lot_enabled:
            return None
        if sell_reason == SellReason.CLEANUP_SELL.value and not cleanup_allowed:
            return None
        reason = (
            "low_cash_oldest_lot_sell"
            if sell_reason == SellReason.LOW_CASH_OLDEST_LOT_SELL.value
            else
            "negative_target_high_cost_sell_lot"
            if sell_reason == SellReason.NEGATIVE_TARGET_HIGH_COST_SELL.value
            else
            "low_cash_high_cost_sell_lot"
            if sell_reason == SellReason.LOW_CASH_HIGH_COST_SELL.value
            else "decay_cleanup_sell_lot"
            if sell_reason == SellReason.AUTO_DECAY_CLEANUP_SELL.value
            else (
                "deep_loss_recovery_sell_lot"
                if sell_reason == SellReason.DEEP_LOSS_RECOVERY_SELL.value
                else ("cleanup_sell_lot" if sell_reason == SellReason.CLEANUP_SELL.value else "sell_profitable_lot")
            )
        )
        # A zero limit delegates pricing to OrderManager, which applies the
        # configured sell_limit_markdown_pct to the latest validated quote.
        limit_price = candidate.limit_price
        if sell_reason in {SellReason.LOW_CASH_HIGH_COST_SELL.value, SellReason.LOW_CASH_OLDEST_LOT_SELL.value,
                           SellReason.NEGATIVE_TARGET_HIGH_COST_SELL.value}:
            expected_cleanup_loss = max(0, -self.calculate_expected_realized_pnl(lot, limit_price, quantity))
        return StrategyAction(OrderSide.SELL, 0, quantity, reason, lot.lot_id, lot, sell_reason, cleanup_flag=expected_cleanup_loss > 0, limit_price=limit_price)

    def _target_sell_price(self, lot: LotState) -> int:
        raw_price = lot.buy_price * (1.0 + lot.effective_target_profit_rate)
        if self.config.strategy.adaptive_lot_enabled:
            return ceil_price(raw_price) if math.isfinite(raw_price) and raw_price > 0 else 0
        return ceil_price(raw_price) if self.config.strategy.cash_target_profit_active() else round_price(raw_price)

    def _sell_candidate(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> SellCandidate | None:
        if self.config.strategy.adaptive_lot_enabled:
            return self._adaptive_sell_candidate(position, current_price, snapshot)
        if not self.lot_manager.open_lots(position.code):
            return None
        if self.config.strategy.cash_target_profit_active():
            cash = snapshot.cash_available if snapshot is not None else None
            if type(cash) not in (int, float) or not math.isfinite(cash):
                return None
        candidate = self._base_sell_candidate(position, current_price, snapshot)
        if candidate is None:
            return None
        lot, reason, loss, allowed = candidate
        trigger_lot = self.lot_manager.lowest_open_buy_lot(position.code) if reason == SellReason.LOW_CASH_HIGH_COST_SELL.value else lot
        if reason == SellReason.DEEP_LOSS_RECOVERY_SELL.value:
            limit_price = 0
        elif reason == SellReason.LOW_CASH_OLDEST_LOT_SELL.value:
            limit_price = round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))
        else:
            limit_price = self._target_sell_price(trigger_lot)
        if (self.config.strategy.age_decay_mode == "current_open_lot_count"
                and self.config.strategy.cash_target_profit_active()
                and reason in {SellReason.PROFIT_TAKE.value, SellReason.AUTO_DECAY_CLEANUP_SELL.value, SellReason.CLEANUP_SELL.value}
                and trigger_lot.effective_target_profit_rate < -EPSILON
                and self.calculate_expected_realized_pnl(trigger_lot, limit_price) < 0):
            lot = self.lot_manager.highest_open_buy_lot(position.code)
            reason = SellReason.NEGATIVE_TARGET_HIGH_COST_SELL.value
            loss = max(0, -self.calculate_expected_realized_pnl(lot, limit_price))
            allowed = True
        return SellCandidate(lot, reason, loss, allowed, trigger_lot, limit_price)

    def _adaptive_sell_candidate(self, position, current_price, snapshot):
        try:
            cash_multipliers(snapshot.cash_available if snapshot else None)
        except ValueError:
            position.skip_reason = "adaptive_cash_snapshot_unavailable"
            return None
        self.lot_manager.set_symbol_trade_state(position.code, position.consecutive_sell_count)
        base, _, _ = self.lot_manager.current_target_profit_info(position.code, cash_available=snapshot.cash_available)
        profits, recoveries, decayed = [], [], []
        for lot in self.lot_manager.open_lots(position.code):
            self.lot_manager.update_lot_target_metadata(lot, current_price,
                                                       current_base_target_profit_rate=base)
            target = self._target_sell_price(lot)
            if self.deep_loss_recovery_triggered(lot, current_price):
                recoveries.append(lot)
            elif target > 0 and current_price >= target:
                (decayed if lot.effective_target_profit_rate < 0 else profits).append(lot)
        # Keep the existing profit, independent recovery, then decay priority.
        pool = profits or recoveries or decayed
        if not pool:
            return None
        lot = self._sort_sell_lots(pool, current_price)[0]
        reason = (SellReason.PROFIT_TAKE.value if profits else
                  SellReason.DEEP_LOSS_RECOVERY_SELL.value if recoveries else
                  SellReason.AUTO_DECAY_CLEANUP_SELL.value)
        target = 0 if recoveries and not profits else self._target_sell_price(lot)
        loss = max(0, -self._net_pnl(lot, current_price, lot.remaining_quantity))
        return SellCandidate(lot, reason, loss, True, lot, target)

    def _base_sell_candidate(self, position: PositionState, current_price: int, snapshot: AccountSnapshot) -> tuple[LotState, str, int, bool] | None:
        profit_candidates: list[LotState] = []
        deep_loss_candidates: list[tuple[LotState, int]] = []
        decay_cleanup_candidates: list[tuple[LotState, int]] = []
        cleanup_candidates: list[tuple[LotState, int, bool]] = []
        budget = self.cleanup_loss_budget(snapshot)
        current_base_rate, _, _ = self.lot_manager.current_target_profit_info(
            position.code, position.cumulative_invested_amount, cash_available=snapshot.cash_available,
        )
        open_lots = self.lot_manager.open_lots(position.code)
        for lot in open_lots:
            self.lot_manager.update_lot_target_metadata(lot, current_price, current_base_target_profit_rate=current_base_rate)
        cash_target_active = self.config.strategy.cash_target_profit_active()
        low_cash = cash_target_active and snapshot.cash_available <= self.config.strategy.cash_target_low_cash_threshold
        if low_cash and open_lots:
            # The oldest policy funds cash immediately. The legacy policy
            # waits for the lowest-cost target and sells the highest-cost LOT.
            oldest_policy = self.config.strategy.cash_target_low_cash_lot_policy == "oldest"
            trigger_lot = (min(open_lots, key=lambda lot: (lot_buy_timestamp(lot), lot.lot_id))
                           if oldest_policy else self.lot_manager.lowest_open_buy_lot(position.code))
            sell_lot = trigger_lot if oldest_policy else self.lot_manager.highest_open_buy_lot(position.code)
            if oldest_policy:
                # Funding is immediate: no profit/decay target may delay the
                # oldest LOT sale. Order pricing follows the normal markdown.
                if current_price <= 0:
                    return None
                limit = round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))
                loss = max(0, -self.calculate_expected_realized_pnl(sell_lot, limit))
                return (sell_lot, SellReason.LOW_CASH_OLDEST_LOT_SELL.value, loss, True)
            trigger_price = self._target_sell_price(trigger_lot)
            if current_price >= trigger_price:
                loss = max(0, -self.calculate_expected_realized_pnl(sell_lot, trigger_price))
                return (sell_lot, SellReason.LOW_CASH_HIGH_COST_SELL.value, loss, True)
        for lot in open_lots:
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            quantity = lot.remaining_quantity
            net_pnl = self.calculate_expected_realized_pnl(lot, current_price, quantity)
            if self.deep_loss_recovery_triggered(lot, current_price):
                deep_loss_candidates.append((lot, max(0, -net_pnl)))
                continue
            if low_cash:
                # Other LOTs' ages must not bypass the lowest-cost trigger.
                # The existing independent deep-loss recovery rule stays live.
                continue
            if cash_target_active and current_price < self._target_sell_price(lot):
                continue
            if lot.effective_target_profit_rate < -EPSILON and realized_rate + EPSILON >= lot.effective_target_profit_rate and net_pnl < 0:
                lot.cleanup_candidate = True
                decay_cleanup_candidates.append((lot, max(0, -net_pnl)))
                continue
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
        if deep_loss_candidates:
            lot, expected_loss = sorted(
                deep_loss_candidates,
                key=lambda item: (
                    lot_buy_timestamp(item[0]),
                    item[0].profit_pct_at(current_price),
                    item[0].lot_id,
                ),
            )[0]
            return (lot, SellReason.DEEP_LOSS_RECOVERY_SELL.value, expected_loss, True)
        if decay_cleanup_candidates:
            lot, expected_loss = sorted(
                decay_cleanup_candidates,
                key=lambda item: (
                    lot_buy_timestamp(item[0]),
                    item[1],
                    item[0].profit_pct_at(current_price),
                    item[0].lot_id,
                ),
            )[0]
            return (lot, SellReason.AUTO_DECAY_CLEANUP_SELL.value, expected_loss, True)
        allowed_cleanup = [item for item in cleanup_candidates if item[2]]
        if allowed_cleanup:
            lot, expected_loss, cleanup_allowed = sorted(
                allowed_cleanup,
                key=lambda item: (
                    lot_buy_timestamp(item[0]),
                    item[1],
                    item[0].profit_pct_at(current_price),
                    item[0].lot_id,
                ),
            )[0]
            return (lot, SellReason.CLEANUP_SELL.value, expected_loss, cleanup_allowed)
        return None

    def observe_completed_session(self, code: str, session_date: str, close_price: int) -> bool:
        """Apply one completed-session close to every open lot exactly once."""
        if not self.config.strategy.deep_loss_recovery_enabled or not session_date or close_price <= 0:
            return False
        changed = False
        threshold = self.config.strategy.deep_loss_threshold_rate
        for lot in self.lot_manager.open_lots(code):
            if lot.deep_loss_last_observed_on >= session_date:
                continue
            rate = lot.profit_pct_at(close_price) / 100.0
            lot.deep_loss_last_observed_on = session_date
            if lot.deep_loss_started_on:
                previous_low = lot.deep_loss_low_price or lot.deep_loss_last_close or close_price
                lot.deep_loss_low_price = min(previous_low, close_price)
                lot.deep_loss_observation_count += 1
            elif rate <= threshold:
                lot.deep_loss_started_on = session_date
                lot.deep_loss_low_price = close_price
                lot.deep_loss_observation_count = 1
            lot.deep_loss_last_close = close_price
            changed = True
        return changed

    def deep_loss_rebound_rate(self, lot: LotState, today: str | None = None) -> float:
        max_days = max(1, self.config.strategy.deep_loss_max_recovery_days)
        elapsed_days = self.deep_loss_elapsed_days(lot, today)
        remaining_ratio = max(0.0, 1.0 - elapsed_days / max_days)
        return max(0.0, self.config.strategy.deep_loss_initial_rebound_rate * remaining_ratio)

    def deep_loss_recovery_triggered(self, lot: LotState, current_price: int, today: str | None = None) -> bool:
        if not self.config.strategy.deep_loss_recovery_enabled or not lot.deep_loss_started_on:
            return False
        elapsed_days = self.deep_loss_elapsed_days(lot, today)
        if elapsed_days >= self.config.strategy.deep_loss_max_recovery_days:
            return True
        low_price = lot.deep_loss_low_price or lot.deep_loss_last_close
        if low_price <= 0 or current_price <= 0:
            return False
        trigger_price = low_price * (1.0 + self.deep_loss_rebound_rate(lot, today))
        return current_price + EPSILON >= trigger_price

    @staticmethod
    def deep_loss_elapsed_days(lot: LotState, today: str | None = None) -> int:
        if not lot.deep_loss_started_on:
            return 0
        try:
            start = datetime.fromisoformat(lot.deep_loss_started_on).date()
            end = datetime.fromisoformat(today).date() if today else datetime.now().date()
        except ValueError:
            return 0
        return max(0, (end - start).days)

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
                lot_buy_timestamp(lot),
                -lot.profit_pct_at(current_price),
                -lot.open_amount,
                -lot.remaining_quantity,
                lot.lot_id,
            ),
        )

    def context(self, position: PositionState, current_price: int, snapshot: AccountSnapshot | None = None) -> StrategyContext:
        if self.config.strategy.adaptive_lot_enabled:
            from .trading_calendar import CalendarUnavailable
            try:
                return self._adaptive_context(position, current_price, snapshot)
            except CalendarUnavailable as error:
                return StrategyContext(position_state=self._position_state(position), pnl_mode=self._pnl_mode(position),
                    position_pnl_rate=position.profit_loss_pct/100, skip_reason=str(error),
                    buy_condition_met=False, sell_signal_met=False)
        cash_buy = self.cash_buy_details(position, current_price, snapshot)
        snapshot = snapshot or AccountSnapshot(0, 0, 0, 0, ())
        exposure = position.cumulative_invested_amount
        lowest = self.lot_manager.lowest_open_buy_lot(position.code)
        highest = self.lot_manager.highest_open_buy_lot(position.code)
        reference_context = self._reference_buy_context(position, current_price)
        open_lot_vwap = reference_context.eligible_vwap
        median_open = reference_context.eligible_median
        current_base_rate, target_profit_lot_band, target_profit_source = self.lot_manager.current_target_profit_info(
            position.code, exposure, cash_available=snapshot.cash_available,
        )
        target_profit = current_base_rate * 100.0 if exposure > 0 else 0.0
        buy_plan = self.lot_manager.buy_plan(exposure)
        reference_buy_price, reference_buy_source = reference_context.after_price, reference_context.source
        reference_sell = self._reference_sell_lot(position)
        profit_take_lots = self.lot_manager.profit_take_lots(position.code, current_price, exposure, target_profit) if exposure > 0 else []
        cleanup_candidate_lots = self.lot_manager.cleanup_candidate_lots(position.code, current_price, current_base_rate) if exposure > 0 else []
        stale_lots = self.lot_manager.stale_lots(position.code, current_price, current_base_rate) if exposure > 0 else []
        normal_reentry, trailing_reentry = self.check_reentry_conditions(position, current_price)
        reentry_details = self.reentry_details(position, current_price, snapshot=snapshot)
        sell_candidate = self._sell_candidate(position, current_price, snapshot) if exposure > 0 else None
        selected_target_lot = sell_candidate[0] if sell_candidate else (profit_take_lots[0] if profit_take_lots else (self.lot_manager.open_lots(position.code)[0] if self.lot_manager.open_lots(position.code) else None))
        cash_target_active = self.config.strategy.cash_target_profit_active()
        low_cash = cash_target_active and snapshot.cash_available <= self.config.strategy.cash_target_low_cash_threshold
        low_cash_trigger = lowest
        if low_cash and self.config.strategy.cash_target_low_cash_lot_policy == "oldest":
            low_cash_trigger = min(self.lot_manager.open_lots(position.code),
                                   key=lambda lot: (lot_buy_timestamp(lot), lot.lot_id), default=None)
        trigger_lot = sell_candidate.trigger_lot if sell_candidate else (low_cash_trigger if low_cash else selected_target_lot)
        trigger_price = sell_candidate.limit_price if sell_candidate else (self._target_sell_price(trigger_lot) if trigger_lot else 0)
        cleanup_budget = self.cleanup_loss_budget(snapshot)
        buy_condition = False
        active_buy_drop_rate = 0.0
        if reference_buy_price and buy_plan:
            active_buy_drop_rate = buy_plan[0] / 100.0
            buy_condition = current_price <= reference_buy_price * (1.0 - active_buy_drop_rate)
        sizing = self.lot_sizing_from_position(position)
        if self._lot_sizing_enabled() and not sizing.get("lot_unit_amount") and self.lot_manager.open_lots(position.code):
            sizing = self.ensure_lot_sizing(position, current_price)
        open_lot_count = len(self.lot_manager.open_lots(position.code))
        add_buy_lot_band = self.add_buy_lot_band_label(open_lot_count) if self._lot_sizing_enabled() else ""
        add_buy_plan = self.add_buy_lot_plan(open_lot_count) if self._lot_sizing_enabled() else None
        if self._lot_sizing_enabled() and reference_buy_price and add_buy_plan:
            active_buy_drop_rate = add_buy_plan[0]
            buy_condition = current_price <= reference_buy_price * (1.0 - active_buy_drop_rate)
        if self.config.strategy.cash_buy_drop_enabled:
            active_buy_drop_rate = cash_buy["cash_buy_drop_rate"] or 0.0
            buy_condition = bool(cash_buy["cash_buy_price_condition_met"])
        max_lots = int(sizing.get("max_lots_per_symbol") or position.max_lots_per_symbol or 0) if isinstance(sizing, dict) else int(position.max_lots_per_symbol or 0)
        max_lots_reached = bool(max_lots and open_lot_count >= max_lots)
        lot_sizing_skip_reason = self.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=position.lot_unit_amount or int(sizing.get("lot_unit_amount", 0)), open_lot_count=open_lot_count) if self._lot_sizing_enabled() else ""
        selected_pnl_rate = (selected_target_lot.profit_pct_at(current_price) / 100.0) if selected_target_lot else 0.0
        selected_effective_target = trigger_lot.effective_target_profit_rate if trigger_lot else 0.0
        selected_base_target = (current_base_rate if cash_target_active else trigger_lot.base_target_profit_rate) if trigger_lot else 0.0
        selected_age_decay = (
            trigger_lot.age_weeks * self.lot_manager.age_decay_rate_for(position.code)
            if cash_target_active and trigger_lot else max(0.0, selected_base_target - selected_effective_target)
        )
        return StrategyContext(
            position_state=self._position_state(position),
            pnl_mode=self._pnl_mode(position),
            position_pnl_rate=position.profit_loss_pct / 100.0,
            lowest_open_buy_lot_price=lowest.buy_price if lowest else 0,
            highest_open_buy_lot_price=highest.buy_price if highest else 0,
            open_lot_vwap_buy_price=open_lot_vwap,
            median_open_buy_price=median_open,
            reference_buy_price=reference_buy_price,
            reference_buy_source=reference_buy_source,
            reference_exclusion_enabled=reference_context.enabled,
            reference_exclusion_threshold=reference_context.threshold,
            reference_total_open_lot_count=reference_context.total_open_lot_count,
            reference_excluded_lot_count=reference_context.excluded_lot_count,
            reference_eligible_lot_count=reference_context.eligible_lot_count,
            excluded_lot_ids=reference_context.excluded_lot_ids,
            excluded_lot_return_rates=reference_context.excluded_lot_return_rates,
            open_lot_vwap_raw_all_lots=reference_context.raw_vwap,
            median_open_buy_price_raw_all_lots=reference_context.raw_median,
            open_lot_vwap_reference_eligible_only=reference_context.eligible_vwap,
            median_open_buy_price_reference_eligible_only=reference_context.eligible_median,
            reference_buy_price_before_exclusion=reference_context.before_price,
            reference_buy_price_after_exclusion=reference_context.after_price,
            reference_fallback_to_current_price=reference_context.fallback_to_current_price,
            reference_sell_price=reference_sell.buy_price if reference_sell else 0,
            target_buy_drop_rate=active_buy_drop_rate,
            target_profit_rate=current_base_rate if exposure > 0 else 0.0,
            buy_condition_met=buy_condition,
            sell_signal_met=bool(sell_candidate) if cash_target_active else self._sell_signal_met(position, current_price, target_profit),
            profitable_lots=";".join(lot.lot_id for lot in profit_take_lots) or "NONE",
            selected_sell_lot_id=sell_candidate[0].lot_id if sell_candidate else (profit_take_lots[0].lot_id if profit_take_lots else ""),
            reentry_condition_met=(bool(reentry_details.get("reentry_condition_met", reentry_details.get("reentry_cash_condition_met")))
                                  if self.config.strategy.cash_capacity_reentry_enabled() else normal_reentry or trailing_reentry),
            normal_reentry_condition_met=normal_reentry,
            trailing_reentry_condition_met=trailing_reentry,
            reentry_mode=reentry_details["reentry_mode"],
            wait_reentry_started_at=reentry_details["wait_reentry_started_at"],
            days_since_wait_reentry_started=int(reentry_details["days_since_wait_reentry_started"]),
            base_reentry_rate=float(reentry_details["base_reentry_rate"]),
            daily_reentry_decay_rate=float(reentry_details["daily_reentry_decay_rate"]),
            decay_duration_days=int(reentry_details["decay_duration_days"]),
            effective_reentry_rate=float(reentry_details["effective_reentry_rate"]),
            reentry_trigger_price=int(reentry_details["reentry_trigger_price"]),
            force_reentry_timeout_days=int(reentry_details["force_reentry_timeout_days"]),
            force_reentry_eligible=bool(reentry_details["force_reentry_eligible"]),
            force_reentry_reason=str(reentry_details["force_reentry_reason"]),
            force_reentry_starts_new_cycle=bool(reentry_details["force_reentry_starts_new_cycle"]),
            old_cycle_id=str(reentry_details["old_cycle_id"]),
            new_cycle_id=str(reentry_details["new_cycle_id"]),
            retire_after_exit=position.retire_after_exit,
            retire_reason=position.retire_reason,
            trade_stop_after_exit_eligible=position.retire_after_exit and not bool(self.lot_manager.open_lots(position.code)),
            trade_stop_after_exit_state=PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value if self._position_state(position) == PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value else "",
            buy_blocked_by_retire_after_exit=self._retire_buy_block_reason(position, self._position_state(position), current_price) != "",
            reentry_blocked_by_retire_after_exit=position.retire_after_exit and self._position_state(position) == PositionLifecycle.WAIT_REENTRY.value,
            sell_reason=sell_candidate[1] if sell_candidate else SellReason.UNKNOWN.value,
            realized_pnl_rate=(sell_candidate[0].profit_pct_at(current_price) / 100.0) if sell_candidate else 0.0,
            net_realized_pnl=self._net_pnl(sell_candidate[0], current_price, sell_candidate[0].remaining_quantity) if sell_candidate else 0,
            reentry_type=(ReentryType.CASH_CAPACITY_REENTRY.value if self.config.strategy.cash_capacity_reentry_enabled()
                          and reentry_details.get("reentry_condition_met", reentry_details.get("reentry_cash_condition_met")) else
                          (ReentryType.TRAILING_REENTRY.value if trailing_reentry else (ReentryType.NORMAL_REENTRY.value if normal_reentry else ReentryType.NONE.value))),
            exit_anchor_price=position.exit_anchor_price,
            cycle_highest_sell_price=position.cycle_highest_sell_price,
            cycle_last_sell_price=position.cycle_last_sell_price,
            cycle_sell_vwap_price=position.cycle_sell_vwap_price,
            cycle_sell_median_price=position.cycle_sell_median_price,
            normal_exit_anchor_price=position.normal_exit_anchor_price,
            trailing_exit_anchor_price=position.trailing_exit_anchor_price,
            cycle_sell_fill_count=position.cycle_sell_fill_count,
            anchor_single_fill=position.anchor_single_fill,
            anchor_confidence=position.anchor_confidence,
            post_exit_high_price=position.post_exit_high_price,
            cleanup_candidate=bool(cleanup_candidate_lots),
            cleanup_loss_budget=cleanup_budget,
            expected_cleanup_loss=sell_candidate[2] if sell_candidate else 0,
            cleanup_allowed=bool(sell_candidate and sell_candidate[1] in {SellReason.CLEANUP_SELL.value, SellReason.AUTO_DECAY_CLEANUP_SELL.value, SellReason.LOW_CASH_HIGH_COST_SELL.value, SellReason.LOW_CASH_OLDEST_LOT_SELL.value, SellReason.NEGATIVE_TARGET_HIGH_COST_SELL.value} and sell_candidate[3]),
            cleanup_buy_cooldown_until=position.cleanup_buy_cooldown_until,
            cleanup_reentry_cooldown_until=position.cleanup_reentry_cooldown_until,
            profit_take_lot_count=len(profit_take_lots),
            cleanup_candidate_lot_count=len(cleanup_candidate_lots),
            stale_lot_count=len(stale_lots),
            stale_lot_ids=";".join(lot.lot_id for lot in stale_lots) or "NONE",
            review_required_condition_met=position.needs_review,
            review_reason=position.review_reason,
            skip_reason=self._skip_reason(position, current_price, snapshot),
            entry_price_for_lot_sizing=position.entry_price_for_lot_sizing,
            lot_unit_amount=position.lot_unit_amount,
            max_symbol_amount=position.max_symbol_amount,
            max_lots_per_symbol=position.max_lots_per_symbol,
            lot_sizing_bucket=position.lot_sizing_bucket,
            lot_sizing_locked=bool(position.lot_unit_amount),
            lot_sizing_locked_at=position.lot_sizing_locked_at,
            lot_sizing_mode=position.lot_sizing_mode,
            lot_sizing_skip_reason=lot_sizing_skip_reason,
            add_buy_lot_band=add_buy_lot_band,
            current_open_lot_count=open_lot_count,
            original_lot_base_target_profit_rate=selected_target_lot.base_target_profit_rate if selected_target_lot else 0.0,
            current_base_target_profit_rate=current_base_rate if exposure > 0 else 0.0,
            target_profit_source=target_profit_source if exposure > 0 else "",
            target_profit_lot_band=target_profit_lot_band if exposure > 0 else "",
            active_lot_count_band=target_profit_lot_band if exposure > 0 else add_buy_lot_band,
            max_lots_reached=max_lots_reached,
            max_lots_buy_blocked=lot_sizing_skip_reason == "max_lots_per_symbol_reached",
            position_state_after_max_lots_check=PositionLifecycle.HOLDING.value if max_lots_reached and self.lot_manager.open_lots(position.code) and not position.needs_review else self._position_state(position),
            effective_target_profit_rate=selected_effective_target,
            lot_age_weeks=trigger_lot.age_weeks if trigger_lot else 0.0,
            age_decay_rate=self.lot_manager.age_decay_rate_for(position.code),
            base_target_profit_rate=selected_base_target,
            age_decay_applied=selected_age_decay,
            lot_unrealized_pnl_rate=selected_pnl_rate,
            decay_cleanup_eligible=bool(sell_candidate and sell_candidate[1] == SellReason.AUTO_DECAY_CLEANUP_SELL.value),
            cash_target_profit_active=cash_target_active,
            cash_available_for_target=snapshot.cash_available,
            low_cash_high_cost_sell=bool(sell_candidate and sell_candidate[1] == SellReason.LOW_CASH_HIGH_COST_SELL.value),
            low_cash_oldest_lot_sell=bool(sell_candidate and sell_candidate[1] == SellReason.LOW_CASH_OLDEST_LOT_SELL.value),
            negative_target_high_cost_sell=bool(sell_candidate and sell_candidate[1] == SellReason.NEGATIVE_TARGET_HIGH_COST_SELL.value),
            sell_trigger_lot_id=trigger_lot.lot_id if trigger_lot else "",
            sell_trigger_price=trigger_price,
            sell_limit_price=trigger_price if sell_candidate else 0,
            reentry_policy=self.config.strategy.reentry_policy,
            reentry_lot_unit_amount=int(reentry_details.get("reentry_lot_unit_amount", 0)),
            reentry_required_cash=int(reentry_details.get("reentry_required_cash", 0)),
            reentry_cash_available=reentry_details.get("reentry_cash_available"),
            reentry_cash_condition_met=bool(reentry_details.get("reentry_cash_condition_met", False)),
            wait_reentry_timestamp_valid=bool(reentry_details.get("wait_reentry_timestamp_valid", False)),
            cash_buy_drop_enabled=self.config.strategy.cash_buy_drop_enabled,
            cash_buy_reference_price=float(cash_buy["cash_buy_reference_price"]),
            cash_buy_reference_source=str(cash_buy["cash_buy_reference_source"]),
            cash_buy_drop_rate=cash_buy["cash_buy_drop_rate"],
            cash_buy_limit_price=int(cash_buy["cash_buy_limit_price"]),
            cash_buy_price_condition_met=bool(cash_buy["cash_buy_price_condition_met"]),
            cash_buy_skip_reason=str(cash_buy["skip_reason"]),
            pre_exit_average_price=getattr(position, "pre_exit_average_price", 0.0),
            pre_exit_average_captured_at=getattr(position, "pre_exit_average_captured_at", ""),
            reentry_price_condition_met=bool(reentry_details.get("reentry_price_condition_met", False)),
        )

    def _adaptive_context(self, position, current_price, snapshot):
        details = self.adaptive_buy_details(position, current_price, snapshot)
        lots = self.lot_manager.open_lots(position.code)
        candidate = self._adaptive_sell_candidate(position, current_price, snapshot)
        selected = candidate.lot if candidate else (self._sort_sell_lots(lots, current_price)[0] if lots else None)
        known = bool(details["cash_adjustment_known"])
        base, _, source = self.lot_manager.current_target_profit_info(position.code, cash_available=snapshot.cash_available if known else None)
        if not known:
            base = 0.0
        elif selected is not None and candidate is None:
            self.lot_manager.update_lot_target_metadata(selected, current_price, current_base_target_profit_rate=base)
        effective = selected.effective_target_profit_rate if selected and known else 0.0
        return StrategyContext(
            trend_score=details['trend_score'], effective_buy_pnl_threshold=details['effective_buy_pnl_threshold'],
            position_state=self._position_state(position), pnl_mode=self._pnl_mode(position),
            position_pnl_rate=position.profit_loss_pct / 100.0,
            lowest_open_buy_lot_price=min((lot.buy_price for lot in lots), default=0),
            highest_open_buy_lot_price=max((lot.buy_price for lot in lots), default=0),
            reference_buy_price=details['position_vwap'],
            reference_buy_source="position_quantity_weighted_vwap", target_buy_drop_rate=details["effective_buy_gap_pct"] / 100.0,
            target_profit_rate=base, buy_condition_met=bool(details["buy_condition_met"]),
            sell_signal_met=bool(candidate), selected_sell_lot_id=candidate.lot.lot_id if candidate else "",
            sell_reason=candidate.reason if candidate else SellReason.UNKNOWN.value,
            sell_trigger_lot_id=candidate.lot.lot_id if candidate else "",
            sell_trigger_price=candidate.limit_price if candidate else (self._target_sell_price(selected) if selected and known else 0),
            sell_limit_price=current_price if candidate and candidate.limit_price > 0 else 0,
            current_open_lot_count=len(lots), current_base_target_profit_rate=base,
            base_target_profit_rate=base, effective_target_profit_rate=effective, target_profit_source=source,
            age_decay_rate=self.lot_manager.age_decay_rate_for(position.code),
            lot_age_weeks=selected.age_weeks if selected else 0.0,
            age_decay_applied=base - effective if selected else 0.0,
            lot_unit_amount=int(details["buy_amount"]), max_lots_per_symbol=position.max_lots_per_symbol,
            max_symbol_amount=position.max_symbol_amount, lot_sizing_bucket=position.lot_sizing_bucket,
            lot_sizing_locked=bool(position.lot_unit_amount), lot_sizing_mode=position.lot_sizing_mode,
            entry_price_for_lot_sizing=position.entry_price_for_lot_sizing,
            reentry_policy="adaptive_price_reserve", reentry_trigger_price=floor_order_price(details["reentry_target"]),
            reentry_required_cash=int(details["reentry_required_cash"]),
            reentry_cash_available=details["reentry_cash_available"],
            reentry_cash_condition_met=bool(details["reentry_cash_condition_met"]),
            reentry_price_condition_met=bool(details["buy_condition_met"]),
            reentry_condition_met=bool(position.rebuy_restriction_active and not details["skip_reason"]),
            reentry_type=ReentryType.NORMAL_REENTRY.value if position.rebuy_restriction_active else ReentryType.NONE.value,
            retire_after_exit=position.retire_after_exit, retire_reason=position.retire_reason,
            skip_reason=position.skip_reason or str(details["skip_reason"]),
            volatility_multiplier=self.lot_manager.volatility_multiplier_for(position.code),
            consecutive_buy_count=position.consecutive_buy_count, consecutive_sell_count=position.consecutive_sell_count,
            rebuy_restriction_active=position.rebuy_restriction_active,
            adaptive_buy_target_price=int(details["buy_target_price"]),
            cash_available_for_target=snapshot.cash_available if known else 0,
            cash_buy_multiplier=details["cash_buy_multiplier"],
            cash_sell_multiplier=details["cash_sell_multiplier"], cash_adjustment_known=known,
            pre_cash_buy_gap_pct=details["pre_cash_buy_gap_pct"],
            pre_cash_sell_target_rate=(base / details["cash_sell_multiplier"]) if known else 0.0,
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
        if position.position_state == PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value or position.retire_after_exit:
            return PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value
        if position.position_state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value:
            return PositionLifecycle.WAIT_REENTRY.value
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

    def _reference_buy_price(self, position: PositionState, current_price: int = 0) -> tuple[int, str]:
        context = self._reference_buy_context(position, current_price)
        return context.after_price, context.source

    def _reference_buy_context(self, position: PositionState, current_price: int = 0) -> ReferencePriceContext:
        lots = self.lot_manager.open_lots(position.code)
        raw_vwap = _vwap_buy_price(lots)
        raw_median = _median_buy_price(lots)
        mode = self._pnl_mode(position)
        after_price, source = _reference_from_vwap_median(mode, raw_vwap, raw_median)
        if self.config.strategy.cash_buy_drop_enabled:
            after_price, source = self._cash_buy_reference(position)
        return ReferencePriceContext(
            enabled=False,
            threshold=0.0,
            total_open_lot_count=len(lots),
            excluded_lot_count=0,
            eligible_lot_count=len(lots),
            excluded_lot_ids="NONE",
            excluded_lot_return_rates="NONE",
            raw_vwap=raw_vwap,
            raw_median=raw_median,
            eligible_vwap=raw_vwap,
            eligible_median=raw_median,
            before_price=after_price,
            after_price=after_price,
            source=source,
            fallback_to_current_price=False,
        )

    def _cash_buy_reference(self, position: PositionState) -> tuple[float, str]:
        lots = self.lot_manager.open_lots(position.code)
        quantity = sum(lot.remaining_quantity for lot in lots)
        if quantity > 0:
            return sum(lot.buy_price * lot.remaining_quantity for lot in lots) / quantity, "remaining_quantity_vwap"
        if self._position_state(position) == PositionLifecycle.WAIT_REENTRY.value:
            reference = getattr(position, "pre_exit_average_price", 0.0)
            if type(reference) in (int, float) and math.isfinite(reference) and reference > 0:
                return float(reference), "pre_exit_remaining_quantity_vwap"
        return 0.0, "missing_cash_buy_reference"

    def cash_buy_details(self, position: PositionState, current_price: int,
                         snapshot: AccountSnapshot | None) -> dict[str, object]:
        """One shared price test for additional buys, reentry and UI previews."""
        reference, source = self._cash_buy_reference(position)
        cash = snapshot.cash_available if snapshot is not None else None
        drop = None
        limit = 0
        condition = False
        block = ""
        if self.config.strategy.cash_buy_drop_enabled:
            if type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0:
                block = "cash_buy_snapshot_unavailable"
            else:
                drop = self.config.strategy.cash_buy_drop_rate(cash)
            if not block and reference <= 0:
                block = "cash_buy_reference_missing"
            if not block and (type(current_price) not in (int, float)
                              or not math.isfinite(current_price) or current_price <= 0):
                block = "current_price_lookup_failed"
            if not block:
                limit = _floor_cash_buy_price(reference * (1.0 - drop))
                condition = limit > 0 and current_price <= limit
                if not condition:
                    block = "cash_buy_price_not_reached"
        return {
            "cash_buy_drop_enabled": self.config.strategy.cash_buy_drop_enabled,
            "cash_buy_reference_price": reference,
            "cash_buy_reference_source": source,
            "cash_buy_drop_rate": drop,
            "cash_buy_limit_price": limit,
            "cash_buy_price_condition_met": condition,
            "cash_buy_cash_available": cash,
            "skip_reason": block,
        }

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
        if self.config.strategy.adaptive_lot_enabled:
            return False
        if self.config.strategy.cash_capacity_reentry_enabled():
            return False
        now = now or datetime.now()
        anchor = self._trailing_exit_anchor(position)
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
        if self.config.strategy.cash_capacity_reentry_enabled():
            return False, False
        now = now or datetime.now()
        normal_anchor = self._normal_exit_anchor(position)
        trailing_anchor = self._trailing_exit_anchor(position)
        if self._position_state(position) != PositionLifecycle.WAIT_REENTRY.value or normal_anchor <= 0 or trailing_anchor <= 0:
            return False, False
        post_exit_high = position.post_exit_high_price if position.post_exit_high_price > 0 else trailing_anchor
        normal_rate = self.effective_reentry_rate(position, ReentryType.NORMAL_REENTRY.value, now)
        trailing_rate = self.effective_reentry_rate(position, ReentryType.TRAILING_REENTRY.value, now)
        exit_time = _parse_time(position.exit_time)
        if exit_time is None:
            return False, False
        normal = current_price <= normal_anchor * (1.0 + normal_rate)
        waited = exit_time is not None and now - exit_time >= timedelta(minutes=self.config.strategy.min_reentry_wait_minutes)
        count_today = position.trailing_reentry_count_today if position.trailing_reentry_count_date == now.date().isoformat() else 0
        trailing = (
            post_exit_high >= trailing_anchor * (1.0 + self.config.strategy.trailing_activation_gain)
            and current_price <= post_exit_high * (1.0 + trailing_rate)
            and waited
            and count_today < self.config.strategy.max_trailing_reentry_per_day
        )
        return normal, trailing

    def effective_reentry_rate(self, position: PositionState, reentry_type: str, now: datetime | None = None) -> float:
        if self.config.strategy.cash_capacity_reentry_enabled():
            return 0.0
        now = now or datetime.now()
        base, daily_decay, duration_days = self._reentry_decay_params(reentry_type)
        if not self.config.strategy.reentry_decay_enabled:
            return base
        elapsed_days = min(self.days_since_wait_reentry_started(position, now), duration_days)
        return min(0.0, self.config.strategy.reentry_decay_cap_rate, base + elapsed_days * daily_decay)

    def days_since_wait_reentry_started(self, position: PositionState, now: datetime | None = None) -> int:
        now = now or datetime.now()
        started = _parse_time(position.exit_time)
        if started is None:
            return 0
        return max(0, (now.date() - started.date()).days)

    def force_reentry_eligible(self, position: PositionState, current_price: int, now: datetime | None = None) -> bool:
        if self.config.strategy.adaptive_lot_enabled:
            return False
        if self.config.strategy.cash_capacity_reentry_enabled():
            return False
        now = now or datetime.now()
        if not self.config.strategy.force_reentry_after_timeout_enabled:
            return False
        if self._position_state(position) != PositionLifecycle.WAIT_REENTRY.value:
            return False
        if self.lot_manager.open_lots(position.code):
            return False
        if _parse_time(position.exit_time) is None:
            return False
        normal, trailing = self.check_reentry_conditions(position, current_price, now)
        if normal or trailing:
            return False
        return self.days_since_wait_reentry_started(position, now) >= self.config.strategy.force_reentry_timeout_days

    def cash_capacity_reentry_details(
        self, position: PositionState, current_price: int,
        snapshot: AccountSnapshot | None = None, now: datetime | None = None,
    ) -> dict[str, object]:
        """Check funding for one fresh-cycle LOT, without old price signals."""
        now = now or datetime.now(timezone(timedelta(hours=9)))
        if now.tzinfo is not None:
            now = now.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
        started = _parse_time(position.exit_time)
        waiting = (self._position_state(position) == PositionLifecycle.WAIT_REENTRY.value
                   and not self.lot_manager.open_lots(position.code) and position.quantity <= 0)
        timestamp_valid = started is not None and started <= now
        sizing = self.lot_sizing_for_new_cycle(current_price) if self._lot_sizing_enabled() else {}
        amount = int(sizing.get("lot_unit_amount") or 0) if self._lot_sizing_enabled() else self.config.strategy.initial_buy_amount
        required_cash = amount * self.config.strategy.reentry_cash_lot_multiple
        available = snapshot.cash_available if snapshot is not None else None
        block = ""
        if not waiting:
            block = "not_waiting_reentry"
        elif not self.config.strategy.cash_capacity_reentry_active(now):
            block = "reentry_not_started_until_start_date"
        elif not timestamp_valid:
            block = "REENTRY_BLOCKED_MISSING_EXIT_TIME"
        elif current_price <= 0:
            block = "current_price_lookup_failed"
        elif self._lot_sizing_enabled():
            block = self.lot_sizing_buy_block_reason(
                position, current_price, sizing, next_buy_amount=amount, open_lot_count=0,
            )
        elif amount <= 0 or current_price > amount:
            block = "initial_buy_amount_below_price"
        if not block and (available is None or available < required_cash):
            block = "reentry_cash_capacity_insufficient"
        cash_condition_met = not block
        cash_buy = self.cash_buy_details(position, current_price, snapshot)
        if self.config.strategy.cash_buy_drop_enabled and not block:
            block = str(cash_buy["skip_reason"])
        return {
            "reentry_policy": self.config.strategy.reentry_policy,
            "reentry_lot_unit_amount": amount,
            "reentry_required_cash": required_cash,
            "reentry_cash_available": available,
            "reentry_cash_condition_met": cash_condition_met,
            "reentry_price_condition_met": (bool(cash_buy["cash_buy_price_condition_met"])
                                             if self.config.strategy.cash_buy_drop_enabled else True),
            "reentry_condition_met": not block,
            "wait_reentry_timestamp_valid": timestamp_valid,
            "skip_reason": block,
            **{key: value for key, value in cash_buy.items() if key != "skip_reason"},
        }

    def reentry_details(self, position: PositionState, current_price: int, now: datetime | None = None, snapshot: AccountSnapshot | None = None) -> dict[str, object]:
        now = now or datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)
        if self.config.strategy.cash_capacity_reentry_enabled():
            cash_details = self.cash_capacity_reentry_details(position, current_price, snapshot, now)
            waiting = self._position_state(position) == PositionLifecycle.WAIT_REENTRY.value
            return {
                **cash_details,
                "reentry_mode": ReentryType.CASH_CAPACITY_REENTRY.value if waiting else ReentryType.NONE.value,
                "wait_reentry_started_at": position.exit_time,
                "days_since_wait_reentry_started": self.days_since_wait_reentry_started(position, now),
                "base_reentry_rate": (-self.config.strategy.cash_buy_drop_rate_at_zero
                                      if self.config.strategy.cash_buy_drop_enabled else 0.0),
                "daily_reentry_decay_rate": 0.0,
                "decay_duration_days": 0,
                "effective_reentry_rate": -(cash_details.get("cash_buy_drop_rate") or 0.0),
                "reentry_anchor_price": (cash_details.get("cash_buy_reference_price", 0)
                                         if self.config.strategy.cash_buy_drop_enabled else 0),
                "reentry_trigger_price": (cash_details.get("cash_buy_limit_price", 0)
                                          if self.config.strategy.cash_buy_drop_enabled else 0),
                "force_reentry_timeout_days": 0, "force_reentry_eligible": False,
                "force_reentry_reason": "", "force_reentry_starts_new_cycle": False,
                "old_cycle_id": self._cycle_id(position),
                "new_cycle_id": f"{position.code}:{current_price}:pending_new_cycle" if cash_details["reentry_condition_met"] else "",
            }
        normal, trailing = self.check_reentry_conditions(position, current_price, now)
        mode = ReentryType.TRAILING_REENTRY.value if trailing else (ReentryType.NORMAL_REENTRY.value if normal else ReentryType.NONE.value)
        anchor = 0
        trigger_base_price = 0
        rate_type = ReentryType.NORMAL_REENTRY.value
        if trailing:
            trigger_base_price = position.post_exit_high_price if position.post_exit_high_price > 0 else self._trailing_exit_anchor(position)
            anchor = self._trailing_exit_anchor(position)
            rate_type = ReentryType.TRAILING_REENTRY.value
        elif normal or self._position_state(position) == PositionLifecycle.WAIT_REENTRY.value:
            trigger_base_price = self._normal_exit_anchor(position)
            anchor = trigger_base_price
            rate_type = ReentryType.NORMAL_REENTRY.value
        base, daily_decay, duration_days = self._reentry_decay_params(rate_type)
        effective = self.effective_reentry_rate(position, rate_type, now)
        force = self.force_reentry_eligible(position, current_price, now)
        old_cycle_id = self._cycle_id(position)
        return {
            "reentry_mode": mode,
            "wait_reentry_started_at": position.exit_time,
            "days_since_wait_reentry_started": self.days_since_wait_reentry_started(position, now),
            "base_reentry_rate": base,
            "daily_reentry_decay_rate": daily_decay,
            "decay_duration_days": duration_days,
            "effective_reentry_rate": effective,
            "reentry_anchor_price": anchor,
            "reentry_trigger_price": int(round(trigger_base_price * (1.0 + effective))) if trigger_base_price else 0,
            "force_reentry_timeout_days": self.config.strategy.force_reentry_timeout_days,
            "force_reentry_eligible": force,
            "force_reentry_reason": "FORCE_REENTRY_TIMEOUT_NEW_CYCLE" if force else "",
            "force_reentry_starts_new_cycle": self.config.strategy.force_reentry_starts_new_cycle,
            "old_cycle_id": old_cycle_id,
            "new_cycle_id": f"{position.code}:{current_price}:pending_new_cycle" if force else "",
        }

    def _reentry_decay_params(self, reentry_type: str) -> tuple[float, float, int]:
        if reentry_type == ReentryType.TRAILING_REENTRY.value:
            base = -abs(self.config.strategy.trailing_reentry_drop_rate)
            duration_days = self.config.strategy.trailing_reentry_decay_days
            return (base, self._daily_reentry_decay_rate(base, duration_days), duration_days)
        base = -abs(self.config.strategy.normal_reentry_drop_rate)
        duration_days = self.config.strategy.normal_reentry_decay_days
        return (base, self._daily_reentry_decay_rate(base, duration_days), duration_days)

    def _daily_reentry_decay_rate(self, base_reentry_rate: float, duration_days: int) -> float:
        if duration_days <= 0:
            return 0.0
        return abs(base_reentry_rate) / duration_days

    def _cycle_id(self, position: PositionState) -> str:
        if not position.exit_time and not position.lot_sizing_locked_at:
            return ""
        return f"{position.code}:{position.lot_sizing_locked_at or 'unlocked'}:{position.exit_time or 'open'}"

    def _normal_exit_anchor(self, position: PositionState) -> int:
        return position.normal_exit_anchor_price or position.exit_anchor_price or position.reentry_anchor_price or position.last_sell_price

    def _trailing_exit_anchor(self, position: PositionState) -> int:
        return position.trailing_exit_anchor_price or position.exit_anchor_price or position.reentry_anchor_price or position.last_sell_price

    def _skip_reason(self, position: PositionState, current_price: int, snapshot: AccountSnapshot | None = None) -> str:
        if position.skip_reason:
            return position.skip_reason
        state = self._position_state(position)
        buy_block = self._buy_block_reason(position)
        if buy_block:
            return buy_block
        if state in {PositionLifecycle.SYNC_REQUIRED.value, PositionLifecycle.REVIEW_REQUIRED.value, PositionLifecycle.RISK_BLOCKED.value}:
            return state.lower()
        retire_block = self._retire_buy_block_reason(position, state, current_price)
        if retire_block:
            return retire_block
        if state == PositionLifecycle.WAIT_REENTRY.value and self.config.strategy.cash_capacity_reentry_enabled():
            return str(self.cash_capacity_reentry_details(position, current_price, snapshot)["skip_reason"])
        if state == PositionLifecycle.WAIT_REENTRY.value and _parse_time(position.exit_time) is None:
            return "REENTRY_BLOCKED_MISSING_EXIT_TIME"
        if state == PositionLifecycle.WAIT_REENTRY.value and self.force_reentry_eligible(position, current_price):
            return "FORCE_REENTRY_TIMEOUT_NEW_CYCLE"
        if state == PositionLifecycle.WAIT_REENTRY.value and not any(self.check_reentry_conditions(position, current_price)):
            return "wait_reentry"
        if self._lot_sizing_enabled() and state == PositionLifecycle.NEVER_BOUGHT.value:
            return self.lot_sizing_buy_block_reason(position, current_price, self.lot_sizing_for_new_cycle(current_price), next_buy_amount=0, open_lot_count=0)
        if state == PositionLifecycle.NEVER_BOUGHT.value and current_price > self.config.strategy.initial_buy_amount:
            return "initial_buy_amount_below_price"
        return ""

    def _buy_block_reason(self, position: PositionState) -> str:
        if self.config.strategy.cash_capacity_reentry_enabled():
            return ""
        state = self._position_state(position)
        if position.last_reentry_type != ReentryType.NONE.value:
            elapsed = None
            last_order_time = _parse_time(position.last_order_time)
            if last_order_time is not None:
                elapsed = datetime.now() - last_order_time
            if elapsed is not None and elapsed < timedelta(minutes=self.config.strategy.reentry_buy_cooldown_minutes):
                return "reentry_buy_cooldown"
        return ""

    def _retire_buy_block_reason(self, position: PositionState, lifecycle: str, current_price: int = 0) -> str:
        if lifecycle == PositionLifecycle.TRADE_STOPPED_AFTER_EXIT.value:
            return "TRADE_STOPPED_AFTER_EXIT"
        if not position.retire_after_exit:
            return ""
        if lifecycle == PositionLifecycle.WAIT_REENTRY.value:
            if current_price and self.force_reentry_eligible(position, current_price):
                return "FORCE_REENTRY_BLOCKED_RETIRE_AFTER_EXIT"
            return "REENTRY_BLOCKED_RETIRE_AFTER_EXIT"
        return ""

    def cleanup_loss_budget(self, snapshot: AccountSnapshot) -> int:
        return int(max(0, snapshot.daily_profit_loss) * self.config.strategy.cleanup_profit_offset_ratio)

    def _net_pnl(self, lot: LotState, price: int, quantity: int) -> int:
        fee_tax = int(round(price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
        return (price - lot.buy_price) * quantity - fee_tax

    def _lot_sizing_enabled(self) -> bool:
        return self.config.strategy.lot_sizing_mode == "cycle_locked_by_entry_price"

    def lot_sizing_for_new_cycle(self, current_price: int) -> dict[str, object]:
        for band in self.config.strategy.price_lot_bands:
            if band.min_price <= current_price <= band.max_price:
                max_lots = band.max_lots or self.config.strategy.max_lots_per_symbol_default
                return {
                    "enabled": band.enabled,
                    "entry_price_for_lot_sizing": current_price,
                    "lot_unit_amount": band.lot_unit_amount,
                    "max_symbol_amount": band.max_symbol_amount,
                    "max_lots_per_symbol": max_lots,
                    "lot_sizing_bucket": f"{band.min_price}-{band.max_price}",
                    "lot_sizing_mode": self.config.strategy.lot_sizing_mode,
                }
        return {"enabled": False, "lot_sizing_bucket": "", "lot_unit_amount": 0, "max_symbol_amount": 0, "max_lots_per_symbol": 0}

    def lot_sizing_from_position(self, position: PositionState) -> dict[str, object]:
        return {
            "enabled": bool(position.lot_unit_amount and position.max_symbol_amount),
            "entry_price_for_lot_sizing": position.entry_price_for_lot_sizing,
            "lot_unit_amount": position.lot_unit_amount,
            "max_symbol_amount": position.max_symbol_amount,
            "max_lots_per_symbol": position.max_lots_per_symbol,
            "lot_sizing_bucket": position.lot_sizing_bucket,
            "lot_sizing_mode": position.lot_sizing_mode,
        }

    def lock_lot_sizing(self, position: PositionState, sizing: dict[str, object]) -> None:
        position.entry_price_for_lot_sizing = int(sizing.get("entry_price_for_lot_sizing") or 0)
        position.lot_unit_amount = int(sizing.get("lot_unit_amount") or 0)
        position.max_symbol_amount = int(sizing.get("max_symbol_amount") or 0)
        position.max_lots_per_symbol = int(sizing.get("max_lots_per_symbol") or 0)
        position.lot_sizing_bucket = str(sizing.get("lot_sizing_bucket") or "")
        position.lot_sizing_mode = str(sizing.get("lot_sizing_mode") or self.config.strategy.lot_sizing_mode)
        position.lot_sizing_locked_at = datetime.now().isoformat(timespec="seconds")

    def ensure_lot_sizing(self, position: PositionState, current_price: int = 0) -> dict[str, object]:
        if not self._lot_sizing_enabled():
            return {}
        if position.lot_unit_amount and position.max_symbol_amount:
            return self.lot_sizing_from_position(position)
        open_lots = self.lot_manager.open_lots(position.code)
        if open_lots:
            entry_price = open_lots[0].buy_price
            sizing = self.lot_sizing_for_new_cycle(entry_price)
            if sizing.get("enabled"):
                self.lock_lot_sizing(position, sizing)
                position.skip_reason = "lot_sizing_migrated"
            return sizing
        sizing = self.lot_sizing_for_new_cycle(current_price)
        if sizing.get("enabled"):
            self.lock_lot_sizing(position, sizing)
        return sizing

    def add_buy_lot_plan(self, open_lot_count: int) -> tuple[float, int] | None:
        for band in self.config.strategy.add_buy_lot_bands:
            if band.min_lots <= open_lot_count <= band.max_lots:
                return band.drop_rate, band.add_lot_count
        return None

    def add_buy_lot_band_label(self, open_lot_count: int) -> str:
        for band in self.config.strategy.add_buy_lot_bands:
            if band.min_lots <= open_lot_count <= band.max_lots:
                return f"{band.min_lots}-{band.max_lots}"
        return ""

    def lot_sizing_buy_block_reason(self, position: PositionState, current_price: int, sizing: dict[str, object], *, next_buy_amount: int, open_lot_count: int) -> str:
        if not sizing or not sizing.get("lot_sizing_bucket"):
            return "price_out_of_lot_sizing_range"
        if not sizing.get("enabled"):
            return "lot_sizing_band_disabled"
        lot_unit = int(sizing.get("lot_unit_amount") or 0)
        max_amount = int(sizing.get("max_symbol_amount") or 0)
        max_lots = int(sizing.get("max_lots_per_symbol") or 0)
        if lot_unit <= 0 or max_amount <= 0:
            return "lot_sizing_band_disabled"
        if current_price > lot_unit:
            return "lot_unit_amount_below_price"
        if max_lots and open_lot_count >= max_lots:
            return "max_lots_per_symbol_reached"
        amount = next_buy_amount or lot_unit
        if max_amount and position.cumulative_invested_amount + amount > max_amount:
            return "max_symbol_amount_reached"
        return ""


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone(timedelta(hours=9)))
        return parsed.replace(tzinfo=None)
    except (AttributeError, TypeError, ValueError):
        return None


def _vwap_buy_price(lots: list[LotState]) -> int:
    quantity = sum(lot.remaining_quantity for lot in lots)
    if quantity <= 0:
        return 0
    return int(round(sum(lot.buy_price * lot.remaining_quantity for lot in lots) / quantity))


def _floor_cash_buy_price(price: float) -> int:
    """A cash-derived BUY limit never exceeds its unrounded price target."""
    if not math.isfinite(price) or price <= 0:
        return 0
    unit = (1 if price < 1_000 else 5 if price < 5_000 else 10 if price < 10_000 else
            50 if price < 50_000 else 100 if price < 100_000 else 500 if price < 500_000 else 1_000)
    return max(0, int(math.floor((price + EPSILON) / unit)) * unit)


def _median_buy_price(lots: list[LotState]) -> int:
    if not lots:
        return 0
    return int(round(median(lot.buy_price for lot in lots)))


def _reference_from_vwap_median(mode: str, vwap: int, median_price: int) -> tuple[int, str]:
    if not vwap or not median_price:
        return 0, ""
    if mode == "PLUS":
        return max(vwap, median_price), "max_vwap_median_for_plus"
    if mode == "NEUTRAL":
        return min(vwap, median_price), "min_vwap_median_for_neutral"
    return min(vwap, median_price), "min_vwap_median_for_minus"
