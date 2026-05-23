"""Limit order building, submission, timeout, and fill confirmation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as day_time, timedelta

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
        return OrderRequest(
            position.code,
            position.name,
            action.side,
            quantity,
            limit_price,
            action.reason,
            action.lot_id,
            False,
            action.sell_reason,
            action.reentry_type,
            action.cleanup_flag,
        )

    def submit_and_confirm(self, request: OrderRequest) -> tuple[OrderResult, TradeFill | None]:
        self.logger.info(
            "order_request live=%s code=%s side=%s qty=%s limit=%s lot_id=%s reason=%s sell_reason=%s reentry_type=%s cleanup_flag=%s",
            self.config.order.live_trading,
            request.code,
            request.side.value,
            request.quantity,
            request.limit_price,
            request.lot_id,
            request.reason,
            request.sell_reason,
            request.reentry_type,
            request.cleanup_flag,
        )
        if not self.config.order.live_trading:
            result = OrderResult(request, f"PAPER-{datetime.now().strftime('%Y%m%d%H%M%S%f')}", OrderStatus.FILLED)
            lot_id = request.lot_id or f"{request.code}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            fill = TradeFill(request.code, request.name, request.side, request.quantity, request.limit_price, result.order_id, datetime.now(), lot_id, result.order_id, request.sell_reason, request.reentry_type)
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
        open_orders = self.store.open_orders()
        query_start = self._execution_query_start(open_orders)
        fetched_fills = self.client.executions(since=query_start.date()) if open_orders else ()
        duplicate_fill_count = 0
        if open_orders:
            self.logger.info(
                "reconcile_execution_query open_order_count_for_reconciliation=%s oldest_open_order_requested_at=%s execution_query_start=%s execution_query_end=%s execution_query_buffer_minutes=%s fetched_execution_count=%s",
                len(open_orders),
                min((order.requested_at for order in open_orders if order.requested_at), default=""),
                query_start.isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"),
                self.config.order.execution_query_buffer_minutes,
                len(fetched_fills),
            )
        for result in open_orders:
            fills = self._matching_fills(result, fetched_fills)
            for fill in fills:
                fill = self._dedupe_or_delta_fill(fill)
                if fill is None:
                    duplicate_fill_count += 1
                    continue
                if self.store.record_fill(fill):
                    applied.append(fill)
                else:
                    duplicate_fill_count += 1
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
            fills_after_cancel = self._matching_fills(result, fetched_fills)
            for fill in fills_after_cancel:
                fill = self._dedupe_or_delta_fill(fill)
                if fill is None:
                    duplicate_fill_count += 1
                    continue
                if self.store.record_fill(fill):
                    applied.append(fill)
                else:
                    duplicate_fill_count += 1
            filled_quantity = self.store.filled_quantity_for_order(result.order_id)
            final_status = OrderStatus.PARTIAL_CANCELED if filled_quantity > 0 else status
            self.store.record_order(OrderResult(result.request, result.order_id, final_status, "cancel_or_reprice"))
        if open_orders:
            self.logger.info(
                "reconcile_execution_result open_order_count_for_reconciliation=%s fetched_execution_count=%s new_fill_count=%s duplicate_fill_count=%s",
                len(open_orders),
                len(fetched_fills),
                len(applied),
                duplicate_fill_count,
            )
        return tuple(applied)

    def reconcile_recent_executions(self) -> tuple[TradeFill, ...]:
        days = max(1, self.config.order.startup_execution_lookup_days)
        query_start = datetime.now() - timedelta(days=days - 1)
        fetched_fills = self.client.executions(since=query_start.date())
        applied: list[TradeFill] = []
        duplicate_fill_count = 0
        ignored_unmatched_count = 0
        for fill in fetched_fills:
            order = self.store.find_order(fill.order_id)
            if order is None or order.request.code != fill.code or order.request.side != fill.side:
                ignored_unmatched_count += 1
                continue
            matched = TradeFill(
                fill.code,
                fill.name,
                fill.side,
                fill.quantity,
                fill.price,
                fill.order_id,
                fill.filled_at,
                order.request.lot_id,
                fill.execution_id,
                order.request.sell_reason,
                order.request.reentry_type,
            )
            matched = self._dedupe_or_delta_fill(matched)
            if matched is None:
                duplicate_fill_count += 1
                continue
            if self.store.record_fill(matched):
                applied.append(matched)
            else:
                duplicate_fill_count += 1
        self.logger.info(
            "startup_execution_reconcile execution_query_start=%s execution_query_end=%s startup_execution_lookup_days=%s fetched_execution_count=%s new_fill_count=%s duplicate_fill_count=%s ignored_unmatched_execution_count=%s",
            query_start.date().isoformat(),
            datetime.now().date().isoformat(),
            days,
            len(fetched_fills),
            len(applied),
            duplicate_fill_count,
            ignored_unmatched_count,
        )
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
                fill.sell_reason,
                fill.reentry_type,
            )
        return fill

    def _find_matching_fill(self, result: OrderResult) -> TradeFill | None:
        fills = self._matching_fills(result)
        return fills[0] if fills else None

    def _matching_fills(self, result: OrderResult, fetched_fills: tuple[TradeFill, ...] | None = None) -> list[TradeFill]:
        request = result.request
        order_id = _normalize_order_id(result.order_id)
        matches = []
        source_fills = fetched_fills if fetched_fills is not None else self.client.executions()
        for fill in source_fills:
            if _normalize_order_id(fill.order_id) == order_id and fill.code == request.code and fill.side == request.side:
                matches.append(TradeFill(fill.code, fill.name, fill.side, fill.quantity, fill.price, fill.order_id, fill.filled_at, request.lot_id, fill.execution_id, request.sell_reason, request.reentry_type))
        return matches

    def _execution_query_start(self, open_orders: tuple[OrderResult, ...]) -> datetime:
        now = datetime.now()
        today_start = datetime.combine(now.date(), day_time.min)
        requested_times = [_parse_timestamp(order.requested_at) for order in open_orders if order.requested_at]
        oldest_requested = min(requested_times, default=today_start)
        buffered = oldest_requested - timedelta(minutes=self.config.order.execution_query_buffer_minutes)
        query_start = min(today_start, buffered)
        if self.config.order.include_previous_day_for_open_orders and open_orders:
            previous_day_start = today_start - timedelta(days=1)
            query_start = min(query_start, previous_day_start)
        return query_start

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


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
