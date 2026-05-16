"""Lot creation, sell-target calculation, and fill-based lot updates."""

from __future__ import annotations

from datetime import datetime

from .config import StrategyConfig
from .models import LotState, LotStatus, OrderSide, TradeFill


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

    def sellable_lots(self, code: str, current_price: int, exposure: int) -> list[LotState]:
        target_pct = self.target_profit_pct(exposure)
        lots = [lot for lot in self.open_lots(code) if lot.profit_pct_at(current_price) >= target_pct]
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

    def create_buy_lot(self, fill: TradeFill) -> LotState:
        exposure_after = self.cumulative_invested_amount(fill.code) + fill.quantity * fill.price
        target_pct = self.target_profit_pct(exposure_after)
        lot_id = fill.lot_id or f"{fill.code}-{fill.filled_at.strftime('%Y%m%d%H%M%S%f')}"
        lot = LotState(
            lot_id=lot_id,
            code=fill.code,
            buy_filled_at=fill.filled_at.isoformat(timespec="seconds"),
            buy_price=fill.price,
            buy_quantity=fill.quantity,
            buy_amount=fill.quantity * fill.price,
            remaining_quantity=fill.quantity,
            target_profit_pct=target_pct,
            target_sell_price=round_price(fill.price * (1.0 + target_pct / 100.0)),
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
        lot.partial_sold = lot.remaining_quantity > 0
        lot.sell_completed = lot.remaining_quantity == 0
        lot.status = LotStatus.CLOSED.value if lot.sell_completed else LotStatus.PARTIAL_SOLD.value
        return lot


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
