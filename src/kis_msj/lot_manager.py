"""Lot creation, sell-target calculation, and fill-based lot updates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from statistics import median

from .config import StrategyConfig
from .adaptive_policy import trend_multiplier, cash_multipliers
from .trading_calendar import TradingCalendar, CalendarUnavailable, trend_score
from .fill_state import fill_order_key
from .models import LotState, LotStatus, SellReason, TradeFill


def lot_buy_timestamp(lot: LotState) -> float:
    try:
        return _local_datetime(datetime.fromisoformat(lot.buy_filled_at.replace("Z", "+00:00"))).timestamp()
    except (TypeError, ValueError):
        return float("inf")


class LotManager:
    def __init__(self, config: StrategyConfig, lots: dict[str, LotState] | None = None) -> None:
        self.config = config
        self.lots = lots or {}
        self.volatility_multipliers: dict[str, float] = {}
        self.consecutive_sell_counts: dict[str, int] = {}
        self.symbol_positions = {}
        self.calendar = TradingCalendar()

    def trend_score_for(self, code: str, now=None) -> int:
        from .fill_state import actual_fill_events
        position = self.symbol_positions.get(code)
        events = actual_fill_events(position) if position is not None else []
        return trend_score(events, self.calendar, now or datetime.now())

    def set_symbol_trade_state(self, code: str, consecutive_sell_count: int) -> None:
        self.consecutive_sell_counts[code] = max(0, consecutive_sell_count)

    def set_volatility_multiplier(self, code: str, value: float) -> None:
        self.volatility_multipliers[code] = value if math.isfinite(value) and value > 0 else 1.0

    def volatility_multiplier_for(self, code: str) -> float:
        return self.volatility_multipliers.get(code, 1.0)

    def recalculate_symbol_targets(self, code: str, current_price: int, now: datetime | None = None) -> None:
        base_rate = self.current_target_profit_info(code)[0]
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price, now, base_rate)

    def open_lots(self, code: str) -> list[LotState]:
        return [lot for lot in self.lots.values() if lot.code == code and lot.remaining_quantity > 0 and lot.status != LotStatus.CLOSED.value]

    def cumulative_invested_amount(self, code: str) -> int:
        return sum(lot.open_amount for lot in self.open_lots(code))

    def last_buy_lot(self, code: str) -> LotState | None:
        lots = self.open_lots(code)
        if not lots:
            return None
        return max(lots, key=lambda lot: lot.buy_filled_at)

    def lowest_open_buy_lot(self, code: str) -> LotState | None:
        lots = self.open_lots(code)
        if not lots:
            return None
        return min(lots, key=lambda lot: lot.buy_price)

    def highest_open_buy_lot(self, code: str) -> LotState | None:
        lots = self.open_lots(code)
        if not lots:
            return None
        return max(lots, key=lambda lot: lot.buy_price)

    def open_lot_vwap_buy_price(self, code: str) -> int:
        lots = self.open_lots(code)
        quantity = sum(lot.remaining_quantity for lot in lots)
        if quantity <= 0:
            return 0
        return int(round(sum(lot.buy_price * lot.remaining_quantity for lot in lots) / quantity))

    def median_open_buy_price(self, code: str) -> int:
        lots = self.open_lots(code)
        if not lots:
            return 0
        return int(round(median(lot.buy_price for lot in lots)))

    def target_profit_pct(self, exposure: int) -> float:
        for band in self.config.exposure_sell_bands:
            if band.min_exposure <= exposure <= band.max_exposure:
                return band.target_profit_pct
        return self.config.exposure_sell_bands[-1].target_profit_pct

    def current_target_profit_info(self, code: str, exposure: int | None = None, cash_available: int | None = None) -> tuple[float, str, str]:
        """Return the active base target rate as a decimal plus source metadata."""
        if getattr(self.config, "adaptive_lot_enabled", False):
            rate = 0.03 * self.volatility_multipliers.get(code, 1.0) * trend_multiplier(-self.trend_score_for(code))
            if cash_available is None:
                # Fill booking/calendar metadata must remain possible without an
                # account query. This unadjusted placeholder never authorizes an
                # Adaptive decision: strategy callers require and pass cash.
                return rate, "", "adaptive_cash_snapshot_unavailable"
            return rate * cash_multipliers(cash_available)[1], "", "adaptive_volatility_trend_cash"
        if self.config.cash_target_profit_active():
            if cash_available is None:
                raise ValueError("cash target requires an actual account cash snapshot")
            rate = max(0, cash_available) / self.config.cash_target_reference_amount * self.config.cash_target_reference_rate
            return rate, "", "cash_available_linear"
        if self.config.lot_sizing_mode == "cycle_locked_by_entry_price":
            open_lot_count = len(self.open_lots(code))
            for band in self.config.target_profit_lot_bands:
                if band.min_lots <= open_lot_count <= band.max_lots:
                    return band.target_profit_rate, f"{band.min_lots}-{band.max_lots}", "current_lot_band"
            if self.config.target_profit_lot_bands:
                band = self.config.target_profit_lot_bands[-1]
                return band.target_profit_rate, f"{band.min_lots}-{band.max_lots}", "current_lot_band"
            return 0.0, "", "current_lot_band"
        exposure = self.cumulative_invested_amount(code) if exposure is None else exposure
        return self.target_profit_pct(exposure) / 100.0, "", "exposure_sell_band"

    def buy_plan(self, exposure: int) -> tuple[float, int] | None:
        for band in self.config.exposure_buy_bands:
            if band.min_exposure <= exposure <= band.max_exposure:
                return band.drop_pct, band.amount
        return None

    def sellable_lots(self, code: str, current_price: int, exposure: int, target_pct: float | None = None) -> list[LotState]:
        """Backward-compatible alias for non-loss profit-take candidates."""
        return self.profit_take_lots(code, current_price, exposure, target_pct)

    def profit_take_lots(self, code: str, current_price: int, exposure: int, target_pct: float | None = None) -> list[LotState]:
        base_rate = target_pct / 100.0 if target_pct is not None else self.current_target_profit_info(code, exposure)[0]
        lots = []
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price, current_base_target_profit_rate=base_rate)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            adaptive = getattr(self.config, "adaptive_lot_enabled", False)
            target = lot.buy_price * (1.0 + lot.effective_target_profit_rate)
            if adaptive and math.isfinite(target) and target > 0 and current_price >= ceil_price(target):
                lots.append(lot)
            elif not adaptive and realized_rate >= 0 and realized_rate >= lot.effective_target_profit_rate:
                lots.append(lot)
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

    def cleanup_candidate_lots(self, code: str, current_price: int, current_base_target_profit_rate: float | None = None) -> list[LotState]:
        lots = []
        base_rate = current_base_target_profit_rate if current_base_target_profit_rate is not None else self.current_target_profit_info(code)[0]
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price, current_base_target_profit_rate=base_rate)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            if (
                lot.effective_target_profit_rate < 0
                and realized_rate < 0
                and realized_rate >= self.config.cleanup_min_target_rate
            ):
                lot.cleanup_candidate = True
                lots.append(lot)
        return sorted(lots, key=lambda lot: (-lot.age_weeks, lot.profit_pct_at(current_price)))

    def stale_lots(self, code: str, current_price: int, current_base_target_profit_rate: float | None = None) -> list[LotState]:
        lots = []
        base_rate = current_base_target_profit_rate if current_base_target_profit_rate is not None else self.current_target_profit_info(code)[0]
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price, current_base_target_profit_rate=base_rate)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            price_gap_rate = (current_price - lot.buy_price) / lot.buy_price if lot.buy_price else 0.0
            if (
                realized_rate <= self.config.stale_lot_loss_rate
                and lot.age_weeks >= self.config.stale_lot_min_age_weeks
                and price_gap_rate <= self.config.stale_lot_price_gap_rate
            ):
                lot.cleanup_candidate = True
                lots.append(lot)
        return sorted(lots, key=lambda lot: (-lot.age_weeks, lot.profit_pct_at(current_price)))

    def create_buy_lot(self, fill: TradeFill) -> LotState:
        filled_amount = fill.filled_amount or fill.quantity * fill.price
        average = filled_amount / fill.quantity
        buy_price = int(average) if average.is_integer() else average
        exposure_after = self.cumulative_invested_amount(fill.code) + filled_amount
        adaptive = getattr(self.config, "adaptive_lot_enabled", False)
        # Actual executions must be booked even if calendar data is offline.
        # A placeholder is never used to authorize a new order.
        try:
            target_pct = self.current_target_profit_info(fill.code)[0] * 100.0 if adaptive else self.target_profit_pct(exposure_after)
        except CalendarUnavailable:
            target_pct = 0.0
        order_key = fill_order_key(fill)
        existing = self.lots.get(fill.lot_id) if fill.lot_id else None
        if existing is None and adaptive:
            existing = next((lot for lot in self.lots.values() if lot.code == fill.code and lot.buy_order_key == order_key), None)
        if existing is not None:
            if existing.code != fill.code:
                raise RuntimeError(f"buy fill LOT belongs to another symbol: {existing.lot_id}")
            existing.buy_quantity += fill.quantity
            existing.buy_amount += filled_amount
            average = existing.buy_amount / existing.buy_quantity
            existing.buy_price = int(average) if average.is_integer() else average
            existing.remaining_quantity += fill.quantity
            existing.buy_filled_at = min((existing.buy_filled_at, fill.filled_at.isoformat(timespec="microseconds")),
                                        key=lambda value: _local_datetime(datetime.fromisoformat(value.replace("Z", "+00:00"))))
            existing.buy_order_key = existing.buy_order_key or order_key
            existing.sell_completed = False
            existing.status = LotStatus.PARTIAL_SOLD.value if existing.partial_sold else LotStatus.OPEN.value
            return existing
        lot_id = fill.lot_id or self._new_buy_lot_id(fill)
        lot = LotState(
            lot_id=lot_id,
            code=fill.code,
            buy_filled_at=fill.filled_at.isoformat(timespec="microseconds"),
            buy_price=buy_price,
            buy_quantity=fill.quantity,
            buy_amount=filled_amount,
            remaining_quantity=fill.quantity,
            target_profit_pct=target_pct,
            target_sell_price=round_price(buy_price * (1.0 + target_pct / 100.0)),
            base_target_profit_rate=target_pct / 100.0,
            effective_target_profit_rate=target_pct / 100.0,
            buy_order_key=order_key,
        )
        self.lots[lot.lot_id] = lot
        return lot

    def _new_buy_lot_id(self, fill: TradeFill) -> str:
        base = f"{fill.code}-{fill.filled_at.strftime('%Y%m%d%H%M%S%f')}"
        if base not in self.lots:
            return base
        suffix_source = fill.execution_id or f"{fill.order_id}-{fill.quantity}-{fill.price}"
        suffix = "".join(char if char.isalnum() else "-" for char in suffix_source).strip("-")
        candidate = f"{base}-{suffix}" if suffix else f"{base}-dup"
        if candidate not in self.lots:
            return candidate
        counter = 2
        while f"{candidate}-{counter}" in self.lots:
            counter += 1
        return f"{candidate}-{counter}"

    def apply_sell_fill(self, fill: TradeFill) -> LotState:
        if not fill.lot_id or fill.lot_id not in self.lots:
            raise RuntimeError(f"sell fill missing known lot_id: {fill.lot_id}")
        lot = self.lots[fill.lot_id]
        sell_quantity = min(fill.quantity, lot.remaining_quantity)
        proceeds = (fill.filled_amount or fill.quantity * fill.price) * sell_quantity / fill.quantity
        gross_profit = proceeds - lot.buy_price * sell_quantity
        fee_tax = int(round(proceeds * self.config.estimated_fee_tax_pct / 100.0))
        lot.remaining_quantity -= sell_quantity
        lot.realized_profit_loss += gross_profit - fee_tax
        lot.estimated_fee_tax += fee_tax
        if fill.sell_reason and fill.sell_reason != SellReason.UNKNOWN.value:
            lot.last_sell_reason = fill.sell_reason
        elif gross_profit - fee_tax >= 0:
            lot.last_sell_reason = SellReason.PROFIT_TAKE.value
        else:
            lot.last_sell_reason = SellReason.CLEANUP_SELL.value
        lot.partial_sold = lot.remaining_quantity > 0
        lot.sell_completed = lot.remaining_quantity == 0
        lot.status = LotStatus.CLOSED.value if lot.sell_completed else LotStatus.PARTIAL_SOLD.value
        return lot

    def age_weeks(self, lot: LotState, now: datetime | None = None) -> float:
        now = now or datetime.now()
        if getattr(self.config, "adaptive_lot_enabled", False):
            return self.calendar.completed_since(lot.buy_filled_at, now) / 5.0
        bought_at = datetime.fromisoformat(lot.buy_filled_at.replace("Z", "+00:00"))
        return max(0.0, (_local_datetime(now) - _local_datetime(bought_at)).total_seconds() / (7 * 24 * 60 * 60))

    def age_decay_rate_for(self, code: str, now: datetime | None = None) -> float:
        """Use the current remaining LOT count once the cash policy is active."""
        if getattr(self.config, "adaptive_lot_enabled", False):
            return len(self.open_lots(code)) * 0.001
        if self.config.age_decay_mode == "current_open_lot_count" and self.config.cash_target_profit_active(now):
            return len(self.open_lots(code)) * self.config.age_decay_per_open_lot_rate
        return self.config.age_decay_rate

    def effective_target_profit_rate(self, lot: LotState, now: datetime | None = None, current_base_target_profit_rate: float | None = None) -> float:
        if getattr(self.config, "adaptive_lot_enabled", False):
            base = (current_base_target_profit_rate if current_base_target_profit_rate is not None
                    else self.current_target_profit_info(lot.code)[0])
            return base - self.age_weeks(lot, now) * self.age_decay_rate_for(lot.code, now)
        base = current_base_target_profit_rate if current_base_target_profit_rate is not None else (lot.base_target_profit_rate or lot.target_profit_pct / 100.0)
        return base - self.age_weeks(lot, now) * self.age_decay_rate_for(lot.code, now)

    def update_lot_target_metadata(self, lot: LotState, current_price: int, now: datetime | None = None, current_base_target_profit_rate: float | None = None) -> None:
        lot.age_weeks = self.age_weeks(lot, now)
        if getattr(self.config, "adaptive_lot_enabled", False):
            lot.base_target_profit_rate = (current_base_target_profit_rate if current_base_target_profit_rate is not None
                                          else self.current_target_profit_info(lot.code)[0])
            lot.effective_target_profit_rate = self.effective_target_profit_rate(lot, now, lot.base_target_profit_rate)
            lot.target_profit_pct = lot.effective_target_profit_rate * 100.0
            raw_target = lot.buy_price * (1.0 + lot.effective_target_profit_rate)
            lot.target_sell_price = ceil_price(raw_target) if math.isfinite(raw_target) and raw_target > 0 else 0
            lot.cleanup_candidate = False
            return
        lot.base_target_profit_rate = lot.base_target_profit_rate or lot.target_profit_pct / 100.0
        lot.effective_target_profit_rate = self.effective_target_profit_rate(lot, now, current_base_target_profit_rate)
        realized_pnl_rate = lot.profit_pct_at(current_price) / 100.0
        lot.cleanup_candidate = lot.cleanup_candidate or (lot.effective_target_profit_rate < 0 and realized_pnl_rate < 0)


def _local_datetime(value: datetime) -> datetime:
    korea = timezone(timedelta(hours=9))
    return value.replace(tzinfo=korea) if value.tzinfo is None else value.astimezone(korea)


def round_price(price: float) -> int:
    value = int(round(price))
    if value < 1_000:
        unit = 1
    elif value < 5_000:
        unit = 5
    elif value < 10_000:
        unit = 10
    elif value < 50_000:
        unit = 50
    elif value < 100_000:
        unit = 100
    elif value < 500_000:
        unit = 500
    else:
        unit = 1_000
    return max(unit, round(value / unit) * unit)


def ceil_price(price: float) -> int:
    """Round a SELL target up using the same tick ladder as round_price."""
    value = max(1.0, price)
    unit = (1 if value < 1_000 else 5 if value < 5_000 else 10 if value < 10_000 else
            50 if value < 50_000 else 100 if value < 100_000 else 500 if value < 500_000 else 1_000)
    return max(unit, int(math.ceil((value - 1e-9) / unit)) * unit)
