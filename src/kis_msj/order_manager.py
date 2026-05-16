"""Order creation, price sampling, limit calculation, and fill confirmation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .config import BotConfig
from .kis_client import KisClient
from .models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, Quote, TradeFill
from .storage import StateStore
from .strategy import StrategyAction


class OrderManager:
    def __init__(self, config: BotConfig, client: KisClient, store: StateStore, logger: logging.Logger) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.logger = logger

    def sample_prices(self, code: str, name: str = "") -> tuple[Quote, ...]:
        samples: list[Quote] = []
        for index in range(self.config.order.price_sample_count):
            samples.append(self.client.quote(code, name=name))
            if index + 1 < self.config.order.price_sample_count:
                time.sleep(self.config.order.price_sample_interval_seconds)
        return tuple(samples)

    def prices_are_stable(self, samples: tuple[Quote, ...]) -> tuple[bool, str]:
        if len(samples) < self.config.order.price_sample_count:
            return False, "not_enough_price_samples"
        prices = [sample.price for sample in samples]
        low = min(prices)
        high = max(prices)
        if low <= 0:
            return False, "invalid_price_sample"
        volatility = (high - low) / low * 100.0
        if volatility >= self.config.risk.max_price_sample_volatility_pct:
            return False, f"price_volatility_{volatility:.2f}%"
        return True, "stable"

    def build_request(self, position: PositionState, action: StrategyAction, current_price: int) -> OrderRequest | None:
        if action.side is OrderSide.BUY:
            quantity = action.amount // current_price
            price = self.buy_limit_price(current_price)
        else:
            quantity = int(position.quantity * ((action.quantity_pct or 0) / 100.0))
            price = self.sell_limit_price(current_price)
        if quantity < 1:
            return None
        return OrderRequest(
            code=position.code,
            name=position.name,
            side=action.side,
            quantity=quantity,
            limit_price=price,
            reason=action.reason,
            market_order=False,
        )

    def submit_and_confirm(self, request: OrderRequest) -> tuple[OrderResult, TradeFill | None]:
        self.logger.info(
            "order_request live=%s code=%s side=%s qty=%s limit=%s reason=%s",
            self.config.order.live_trading,
            request.code,
            request.side.value,
            request.quantity,
            request.limit_price,
            request.reason,
        )
        if not self.config.order.live_trading:
            result = OrderResult(request, order_id=f"PAPER-{datetime.now().strftime('%Y%m%d%H%M%S%f')}", status=OrderStatus.FILLED)
            fill = TradeFill(
                code=request.code,
                name=request.name,
                side=request.side,
                quantity=request.quantity,
                price=request.limit_price,
                order_id=result.order_id,
                filled_at=datetime.now(),
            )
            self.store.record_order(result)
            self.store.record_fill(fill)
            return result, fill

        result = self.client.place_order(request)
        self.store.record_order(result)
        if result.status is not OrderStatus.REQUESTED:
            return result, None

        deadline = datetime.now() + timedelta(seconds=self.config.order.limit_order_timeout_seconds)
        while datetime.now() < deadline:
            for fill in self.client.executions():
                if fill.order_id == result.order_id and fill.code == request.code:
                    filled = OrderResult(request, result.order_id, OrderStatus.FILLED, result.message)
                    self.store.record_order(filled)
                    self.store.record_fill(fill)
                    return filled, fill
            time.sleep(2)

        status = self.client.cancel_order(result.order_id, request.code, request.quantity)
        canceled = OrderResult(request, result.order_id, status, "unfilled_limit_order_timeout")
        self.store.record_order(canceled)
        return canceled, None

    def buy_limit_price(self, current_price: int) -> int:
        return _round_price(current_price * (1.0 + self.config.order.buy_limit_markup_pct / 100.0))

    def sell_limit_price(self, current_price: int) -> int:
        return _round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))


def _round_price(price: float) -> int:
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
