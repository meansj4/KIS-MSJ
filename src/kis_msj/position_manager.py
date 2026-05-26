"""Position aggregation from account balance and open lots."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from statistics import median

from .config import StrategyConfig
from .lot_manager import LotManager
from .models import AccountSnapshot, OrderSide, PositionLifecycle, PositionState, ReentryType, SellReason, TradeFill


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
        stale_lots = self.lot_manager.stale_lots(code, current_price) if current_price else []
        review_reason = ""
        lot_sizing_mode = self.config.lot_sizing_mode == "cycle_locked_by_entry_price"
        if not lot_sizing_mode and exposure > self.config.auto_buy_limit:
            review_reason = "auto_buy_limit_exceeded"
        elif position.profit_loss_pct <= self.config.review_symbol_loss_rate * 100.0:
            review_reason = "symbol_loss_review"
        elif not lot_sizing_mode and len(lots) > self.config.max_open_lots_before_review:
            review_reason = "too_many_open_lots"
        elif any(lot.age_weeks >= self.config.stale_lot_review_age_weeks for lot in stale_lots):
            review_reason = "stale_lot_review_age"
        if review_reason:
            now = datetime.now().isoformat(timespec="seconds")
            position.needs_review = True
            position.auto_buy_enabled = False
            position.review_reason = review_reason
            position.review_created_at = position.review_created_at or now
            position.review_trigger_values = json.dumps(
                {
                    "position_pnl_rate": position.profit_loss_pct / 100.0,
                    "open_lot_count": len(lots),
                    "exposure": exposure,
                    "stale_lot_ids": [lot.lot_id for lot in stale_lots],
                    "current_price": current_price,
                },
                ensure_ascii=False,
            )
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
        if position.position_state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value:
            cooldown_until = _parse_time(position.cleanup_reentry_cooldown_until)
            if cooldown_until is not None and datetime.now() < cooldown_until:
                return PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
            if self.config.cleanup_auto_return_to_wait_reentry:
                return PositionLifecycle.WAIT_REENTRY.value
            position.needs_review = True
            position.review_reason = "cleanup_cooldown_complete"
            position.auto_buy_enabled = False
            return PositionLifecycle.REVIEW_REQUIRED.value
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
            starts_new_cycle = not self.lot_manager.open_lots(fill.code)
            lot = self.lot_manager.create_buy_lot(fill)
            position.last_buy_lot_id = lot.lot_id
            position.daily_buy_amount += lot.buy_amount
            position.position_state = PositionLifecycle.HOLDING.value
            if starts_new_cycle:
                self._lock_lot_sizing_for_new_cycle(position, fill.price, fill.filled_at)
                position.cycle_highest_sell_price = 0
                position.cycle_last_sell_price = 0
                position.cycle_sell_vwap_price = 0
                position.cycle_sell_median_price = 0
                position.normal_exit_anchor_price = 0
                position.trailing_exit_anchor_price = 0
                position.cycle_sell_fill_count = 0
                position.cycle_sell_quantity = 0
                position.cycle_sell_value_amount = 0
                position.cycle_sell_prices = ""
                position.anchor_single_fill = False
                position.anchor_confidence = ""
                position.exit_anchor_price = 0
                position.reentry_anchor_price = 0
                position.exit_time = ""
                position.cleanup_sell_price = 0
                position.cleanup_time = ""
                position.cleanup_reentry_cooldown_until = ""
            position.last_reentry_type = fill.reentry_type
            if fill.reentry_type == ReentryType.TRAILING_REENTRY.value:
                today = fill.filled_at.date().isoformat()
                if position.trailing_reentry_count_date != today:
                    position.trailing_reentry_count_today = 0
                position.trailing_reentry_count_today += 1
                position.trailing_reentry_count_date = today
            position.post_exit_high_price = 0
            position.skip_reason = ""
        else:
            lot = self.lot_manager.apply_sell_fill(fill)
            position.daily_sell_amount += fill.quantity * fill.price
            position.last_sell_price = fill.price
            sell_reason = lot.last_sell_reason or fill.sell_reason or SellReason.UNKNOWN.value
            position.cycle_last_sell_price = fill.price
            position.cycle_highest_sell_price = max(position.cycle_highest_sell_price, fill.price)
            sell_prices = _append_sell_price(position.cycle_sell_prices, fill.price)
            position.cycle_sell_prices = ",".join(str(price) for price in sell_prices)
            position.cycle_sell_fill_count = len(sell_prices)
            position.cycle_sell_quantity += fill.quantity
            position.cycle_sell_value_amount += fill.price * fill.quantity
            position.cycle_sell_vwap_price = int(round(position.cycle_sell_value_amount / position.cycle_sell_quantity)) if position.cycle_sell_quantity else fill.price
            position.cycle_sell_median_price = int(round(median(sell_prices)))
            position.anchor_single_fill = position.cycle_sell_fill_count == 1
            position.anchor_confidence = "LOW" if position.anchor_single_fill else "NORMAL"
            if sell_reason == SellReason.CLEANUP_SELL.value:
                # Cleanup cooldowns are intentionally calendar-day based. A trading-day
                # calendar can replace this later if holiday/weekend precision matters.
                position.cleanup_buy_cooldown_until = (fill.filled_at + timedelta(days=self.config.cleanup_buy_cooldown_days)).isoformat(timespec="seconds")
                position.cleanup_sell_price = fill.price
                position.cleanup_time = fill.filled_at.isoformat(timespec="seconds")
            if not self.lot_manager.open_lots(fill.code):
                if sell_reason == SellReason.CLEANUP_SELL.value:
                    position.position_state = PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
                    # Calendar-day cooldown; see cleanup_buy_cooldown_until above.
                    position.cleanup_reentry_cooldown_until = (fill.filled_at + timedelta(days=self.config.cleanup_reentry_cooldown_days)).isoformat(timespec="seconds")
                    position.skip_reason = "cleanup_cooldown"
                else:
                    position.position_state = PositionLifecycle.WAIT_REENTRY.value
                    normal_anchor, trailing_anchor = _cycle_reentry_anchors(position, fill.price)
                    position.normal_exit_anchor_price = normal_anchor
                    position.trailing_exit_anchor_price = trailing_anchor
                    # Deprecated compatibility field: keep it equal to the conservative
                    # normal reentry anchor, but do not use it as the primary condition.
                    position.exit_anchor_price = normal_anchor
                    position.reentry_anchor_price = normal_anchor
                    position.cycle_last_sell_price = fill.price
                    position.post_exit_high_price = trailing_anchor
                    position.exit_time = fill.filled_at.isoformat(timespec="seconds")
                    position.skip_reason = "wait_reentry"
        position.last_fill_price = fill.price
        position.last_fill_side = fill.side.value
        position.last_order_id = fill.order_id
        position.last_order_status = "FILLED"
        position.last_order_time = fill.filled_at.isoformat(timespec="seconds")
        return self.refresh_from_lots(fill.code, fill.price)

    def _lock_lot_sizing_for_new_cycle(self, position: PositionState, entry_price: int, locked_at: datetime) -> None:
        if self.config.lot_sizing_mode != "cycle_locked_by_entry_price":
            return
        for band in self.config.price_lot_bands:
            if band.min_price <= entry_price <= band.max_price and band.enabled and band.lot_unit_amount > 0 and band.max_symbol_amount > 0:
                position.entry_price_for_lot_sizing = entry_price
                position.lot_unit_amount = band.lot_unit_amount
                position.max_symbol_amount = band.max_symbol_amount
                position.max_lots_per_symbol = band.max_lots or self.config.max_lots_per_symbol_default
                position.lot_sizing_bucket = f"{band.min_price}-{band.max_price}"
                position.lot_sizing_mode = self.config.lot_sizing_mode
                position.lot_sizing_locked_at = locked_at.isoformat(timespec="seconds")
                return
        position.entry_price_for_lot_sizing = entry_price
        position.lot_unit_amount = 0
        position.max_symbol_amount = 0
        position.max_lots_per_symbol = 0
        position.lot_sizing_bucket = ""
        position.lot_sizing_mode = self.config.lot_sizing_mode
        position.lot_sizing_locked_at = locked_at.isoformat(timespec="seconds")

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


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _append_sell_price(raw: str, price: int) -> list[int]:
    prices = [int(value) for value in raw.split(",") if value.strip()]
    prices.append(price)
    return prices


def _cycle_reentry_anchors(position: PositionState, fallback_price: int) -> tuple[int, int]:
    vwap = position.cycle_sell_vwap_price or fallback_price
    median_price = position.cycle_sell_median_price or fallback_price
    return min(vwap, median_price), max(vwap, median_price)
