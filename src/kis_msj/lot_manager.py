"""Lot creation, sell-target calculation, and fill-based lot updates."""

from __future__ import annotations

from datetime import datetime

from .config import StrategyConfig
from .models import LotState, LotStatus, SellReason, TradeFill


class LotManager:
    def __init__(self, config: StrategyConfig, lots: dict[str, LotState] | None = None) -> None:
        self.config = config
        self.lots = lots or {}

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

    def target_profit_pct(self, exposure: int) -> float:
        for band in self.config.exposure_sell_bands:
            if band.min_exposure <= exposure <= band.max_exposure:
                return band.target_profit_pct
        return self.config.exposure_sell_bands[-1].target_profit_pct

    def buy_plan(self, exposure: int) -> tuple[float, int] | None:
        for band in self.config.exposure_buy_bands:
            if band.min_exposure <= exposure <= band.max_exposure:
                return band.drop_pct, band.amount
        return None

    def sellable_lots(self, code: str, current_price: int, exposure: int, target_pct: float | None = None) -> list[LotState]:
        """Backward-compatible alias for non-loss profit-take candidates."""
        return self.profit_take_lots(code, current_price, exposure, target_pct)

    def profit_take_lots(self, code: str, current_price: int, exposure: int, target_pct: float | None = None) -> list[LotState]:
        target_pct = self.target_profit_pct(exposure) if target_pct is None else target_pct
        lots = []
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            if realized_rate >= 0 and realized_rate >= lot.effective_target_profit_rate:
                lots.append(lot)
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

    def cleanup_candidate_lots(self, code: str, current_price: int) -> list[LotState]:
        lots = []
        for lot in self.open_lots(code):
            self.update_lot_target_metadata(lot, current_price)
            realized_rate = lot.profit_pct_at(current_price) / 100.0
            if (
                lot.effective_target_profit_rate < 0
                and realized_rate < 0
                and realized_rate >= self.config.cleanup_min_target_rate
            ):
                lot.cleanup_candidate = True
                lots.append(lot)
        return sorted(lots, key=lambda lot: (-lot.age_weeks, lot.profit_pct_at(current_price)))

    def create_buy_lot(self, fill: TradeFill) -> LotState:
        exposure_after = self.cumulative_invested_amount(fill.code) + fill.quantity * fill.price
        target_pct = self.target_profit_pct(exposure_after)
        lot_id = fill.lot_id or f"{fill.code}-{fill.filled_at.strftime('%Y%m%d%H%M%S%f')}"
        lot = LotState(
            lot_id=lot_id,
            code=fill.code,
            buy_filled_at=fill.filled_at.isoformat(timespec="microseconds"),
            buy_price=fill.price,
            buy_quantity=fill.quantity,
            buy_amount=fill.quantity * fill.price,
            remaining_quantity=fill.quantity,
            target_profit_pct=target_pct,
            target_sell_price=round_price(fill.price * (1.0 + target_pct / 100.0)),
            base_target_profit_rate=target_pct / 100.0,
            effective_target_profit_rate=target_pct / 100.0,
        )
        self.lots[lot.lot_id] = lot
        return lot

    def apply_sell_fill(self, fill: TradeFill) -> LotState:
        if not fill.lot_id or fill.lot_id not in self.lots:
            raise RuntimeError(f"sell fill missing known lot_id: {fill.lot_id}")
        lot = self.lots[fill.lot_id]
        sell_quantity = min(fill.quantity, lot.remaining_quantity)
        gross_profit = (fill.price - lot.buy_price) * sell_quantity
        fee_tax = int(round(fill.price * sell_quantity * self.config.estimated_fee_tax_pct / 100.0))
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
        bought_at = datetime.fromisoformat(lot.buy_filled_at.replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0.0, (now - bought_at).total_seconds() / (7 * 24 * 60 * 60))

    def effective_target_profit_rate(self, lot: LotState, now: datetime | None = None) -> float:
        base = lot.base_target_profit_rate or lot.target_profit_pct / 100.0
        return base - self.age_weeks(lot, now) * self.config.age_decay_rate

    def update_lot_target_metadata(self, lot: LotState, current_price: int, now: datetime | None = None) -> None:
        lot.age_weeks = self.age_weeks(lot, now)
        lot.base_target_profit_rate = lot.base_target_profit_rate or lot.target_profit_pct / 100.0
        lot.effective_target_profit_rate = self.effective_target_profit_rate(lot, now)
        realized_pnl_rate = lot.profit_pct_at(current_price) / 100.0
        lot.cleanup_candidate = lot.effective_target_profit_rate < 0 and realized_pnl_rate < 0


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
