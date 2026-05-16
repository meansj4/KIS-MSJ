"""Limit order building, submission, timeout, and fill confirmation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .config import BotConfig
from .lot_manager import round_price
from .models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, TradeFill
from .storage import StateStore
from .strategy import StrategyAction


class OrderManager:
    def __init__(self, config: BotConfig, client, store: StateStore, logger: logging.Logger) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.logger = logger

    def build_request(self, position: PositionState, action: StrategyAction, current_price: int) -> OrderRequest | None:
        if action.side is OrderSide.BUY:
            quantity = action.amount // current_price
            limit_price = self.buy_limit_price(current_price)
        else:
            quantity = action.quantity or 0
            limit_price = self.sell_limit_price(current_price)
        if quantity < 1:
            return None
        return OrderRequest(position.code, position.name, action.side, quantity, limit_price, action.reason, action.lot_id, False)

    def submit_and_confirm(self, request: OrderRequest) -> tuple[OrderResult, TradeFill | None]:
        self.logger.info(
            "order_request live=%s code=%s side=%s qty=%s limit=%s lot_id=%s reason=%s",
            self.config.order.live_trading,
            request.code,
            request.side.value,
            request.quantity,
            request.limit_price,
            request.lot_id,
            request.reason,
        )
        if not self.config.order.live_trading:
            result = OrderResult(request, f"PAPER-{datetime.now().strftime('%Y%m%d%H%M%S%f')}", OrderStatus.FILLED)
            lot_id = request.lot_id or f"{request.code}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            fill = TradeFill(request.code, request.name, request.side, request.quantity, request.limit_price, result.order_id, datetime.now(), lot_id)
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
                    matched = TradeFill(fill.code, fill.name, fill.side, fill.quantity, fill.price, fill.order_id, fill.filled_at, request.lot_id)
                    filled = OrderResult(request, result.order_id, OrderStatus.FILLED, result.message)
                    self.store.record_order(filled)
                    self.store.record_fill(matched)
                    return filled, matched
            time.sleep(2)
        status = self.client.cancel_order(result.order_id, request.quantity)
        canceled = OrderResult(request, result.order_id, status, "unfilled_limit_order_timeout")
        self.store.record_order(canceled)
        return canceled, None

    def buy_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 + self.config.order.buy_limit_markup_pct / 100.0))

    def sell_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))
