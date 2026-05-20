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
            fill = TradeFill(request.code, request.name, request.side, request.quantity, request.limit_price, result.order_id, datetime.now(), lot_id, result.order_id)
            self.store.record_order(result)
            self.store.record_fill(fill)
            return result, fill

        result = self.client.place_order(request)
        self.store.record_order(result)
        if result.status is not OrderStatus.REQUESTED:
            return result, None
        deadline = datetime.now() + timedelta(seconds=self.config.order.limit_order_timeout_seconds)
        while datetime.now() < deadline:
            fill = self._find_matching_fill(result)
            if fill:
                return self._record_filled(result, fill)
            time.sleep(2)
        try:
            status = self.client.cancel_order(result.order_id, request.quantity)
        except RuntimeError as error:
            fill = self._find_matching_fill(result)
            if fill:
                return self._record_filled(result, fill)
            if "40330000" in str(error):
                unconfirmed = OrderResult(request, result.order_id, OrderStatus.REQUESTED, "cancel_failed_no_cancelable_quantity")
                self.store.record_order(unconfirmed)
                self.logger.warning("order_unconfirmed code=%s order_id=%s reason=cancel_failed_no_cancelable_quantity", request.code, result.order_id)
                return unconfirmed, None
            raise
        canceled = OrderResult(request, result.order_id, status, "unfilled_limit_order_timeout")
        self.store.record_order(canceled)
        return canceled, None

    def reconcile_open_orders(self) -> tuple[TradeFill, ...]:
        applied: list[TradeFill] = []
        for result in self.store.open_orders():
            fills = self._matching_fills(result)
            for fill in fills:
                fill = self._dedupe_or_delta_fill(fill)
                if fill is None:
                    continue
                if self.store.record_fill(fill):
                    applied.append(fill)
            filled_quantity = self.store.filled_quantity_for_order(result.order_id)
            if filled_quantity >= result.request.quantity:
                self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.FILLED, result.message))
                continue
            if filled_quantity > 0:
                self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.PARTIAL, result.message))
            elapsed = self.store.seconds_since_recent_order_request(result.request.code)
            if elapsed is None or elapsed < self.config.order.limit_order_timeout_seconds:
                continue
            try:
                status = self.client.cancel_order(result.order_id, max(1, result.request.quantity - filled_quantity))
            except RuntimeError as error:
                self.logger.warning("order_cancel_or_requery_failed code=%s order_id=%s error=%s", result.request.code, result.order_id, error)
                continue
            fills_after_cancel = self._matching_fills(result)
            for fill in fills_after_cancel:
                fill = self._dedupe_or_delta_fill(fill)
                if fill is None:
                    continue
                if self.store.record_fill(fill):
                    applied.append(fill)
            filled_quantity = self.store.filled_quantity_for_order(result.order_id)
            final_status = OrderStatus.PARTIAL_CANCELED if filled_quantity > 0 else status
            self.store.record_order(OrderResult(result.request, result.order_id, final_status, "cancel_or_reprice"))
        return tuple(applied)

    def _dedupe_or_delta_fill(self, fill: TradeFill) -> TradeFill | None:
        if fill.execution_id.startswith("AGG:"):
            already_filled = self.store.filled_quantity_for_order(fill.order_id)
            delta_quantity = fill.quantity - already_filled
            if delta_quantity <= 0:
                return None
            return TradeFill(
                fill.code,
                fill.name,
                fill.side,
                delta_quantity,
                fill.price,
                fill.order_id,
                fill.filled_at,
                fill.lot_id,
                f"{fill.execution_id}:delta:{already_filled}->{fill.quantity}",
            )
        return fill

    def _find_matching_fill(self, result: OrderResult) -> TradeFill | None:
        fills = self._matching_fills(result)
        return fills[0] if fills else None

    def _matching_fills(self, result: OrderResult) -> list[TradeFill]:
        request = result.request
        order_id = _normalize_order_id(result.order_id)
        matches = []
        for fill in self.client.executions():
            if _normalize_order_id(fill.order_id) == order_id and fill.code == request.code:
                matches.append(TradeFill(fill.code, fill.name, fill.side, fill.quantity, fill.price, fill.order_id, fill.filled_at, request.lot_id, fill.execution_id))
        return matches

    def _record_filled(self, result: OrderResult, fill: TradeFill) -> tuple[OrderResult, TradeFill]:
        status = OrderStatus.FILLED if fill.quantity >= result.request.quantity else OrderStatus.PARTIAL
        recorded = OrderResult(result.request, result.order_id, status, result.message)
        self.store.record_order(recorded)
        self.store.record_fill(fill)
        if status is OrderStatus.PARTIAL:
            self.logger.warning(
                "order_partial code=%s order_id=%s filled_qty=%s order_qty=%s",
                result.request.code,
                result.order_id,
                fill.quantity,
                result.request.quantity,
            )
        return recorded, fill

    def buy_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 + self.config.order.buy_limit_markup_pct / 100.0))

    def sell_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))


def _normalize_order_id(order_id: str) -> str:
    normalized = str(order_id).strip()
    return normalized.lstrip("0") or normalized
