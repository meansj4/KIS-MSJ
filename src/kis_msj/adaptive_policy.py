"""Pure calculations for the volatility/trend LOT policy (percent units)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

KOREA = timezone(timedelta(hours=9))


def trend_multiplier(consecutive_count: int) -> float:
    return 1.0 + 0.2 * max(0, min(consecutive_count, 12) - 1)


def cash_multipliers(cash_available: int | float) -> tuple[float, float]:
    """Return BUY gap / SELL profit multipliers for actual spendable KRW.

    Connect (0, 2, .5), (100m, 1, 1), (200m, .5, 2) piecewise
    linearly. Above 200m retain the endpoint. Missing/invalid cash is not zero.
    """
    if type(cash_available) not in (int, float) or not math.isfinite(cash_available) or cash_available < 0:
        raise ValueError("adaptive_cash_snapshot_unavailable")
    x = min(cash_available, 200_000_000) / 100_000_000
    return (2.0 - x, 0.5 + 0.5 * x) if x <= 1.0 else (1.0 - 0.5 * (x - 1.0), x)


def lot_decay_pp(buy_time: str | datetime, current_time: datetime, active_lot_count: int, calendar=None) -> float:
    from .trading_calendar import TradingCalendar
    return max(0, active_lot_count) * 0.1 * (calendar or TradingCalendar()).completed_since(buy_time, current_time) / 5.0


def final_sell_profit_pct(vol_multiplier: float, consecutive_sell_count: int, decay_pp: float,
                          cash_available: int | float = 100_000_000) -> float:
    return 3.0 * vol_multiplier * trend_multiplier(consecutive_sell_count) * cash_multipliers(cash_available)[1] - decay_pp


def lowest_lot_buy_target(active_lots, gap_pct: float) -> float:
    prices = [lot.buy_price for lot in active_lots if lot.remaining_quantity > 0]
    return min(prices) * (1.0 - gap_pct / 100.0) if prices else 0.0


def reentry_buy_target(last_sold_price: int, gap_pct: float) -> float:
    return last_sold_price * (1.0 - gap_pct / 100.0) if last_sold_price > 0 else 0.0


def floor_order_price(price: float) -> int:
    """Round a BUY ceiling down, using the project's domestic tick ladder."""
    if not math.isfinite(price) or price <= 0:
        return 0
    unit = (1 if price < 1000 else 5 if price < 5000 else 10 if price < 10000 else
            50 if price < 50000 else 100 if price < 100000 else 500 if price < 500000 else 1000)
    return int(math.floor((price + 1e-9) / unit)) * unit
