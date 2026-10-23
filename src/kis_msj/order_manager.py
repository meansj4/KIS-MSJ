"""Limit order building, submission, timeout, and fill confirmation."""

from __future__ import annotations

import logging
import json
import math
import time
from dataclasses import replace
from uuid import uuid4
from datetime import datetime, time as day_time, timedelta

from .config import BotConfig
from .adaptive_policy import floor_order_price
from .order_identity import broker_order_id
from .lot_manager import round_price
from .models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionState, ReentryType, TradeFill
from .storage import StateStore
from .strategy import StrategyAction


class OrderManager:
    def __init__(self, config: BotConfig, client, store: StateStore, logger: logging.Logger) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.logger = logger
        if (config.risk.cash_shortfall_rotation_enabled or config.strategy.cash_capacity_reentry_enabled()
                or config.strategy.adaptive_lot_enabled):
            # A durable ledger cannot prevent duplicate sales if the HTTP layer
            # silently repeats a POST whose broker outcome is unknown.
            self.client.durable_order_submission = True

    def build_request(
        self,
        position: PositionState,
        action: StrategyAction,
        current_price: int,
        *,
        market_order: bool = False,
    ) -> OrderRequest | None:
        if not _positive_finite(current_price):
            return None
        adaptive_auto = (self.config.strategy.adaptive_lot_enabled
                         and action.reason.startswith("adaptive_"))
        # Negative/non-finite targets must never become an unrelated fallback
        # order. Zero remains the explicit legacy/manual "use quote" sentinel.
        if not _nonnegative_finite(action.limit_price):
            return None
        adaptive_target_sell = (self.config.strategy.adaptive_lot_enabled and action.side is OrderSide.SELL
                                and action.target_lot is not None
                                and action.sell_reason not in {"RISK_EXIT", "DEEP_LOSS_RECOVERY_SELL"})
        if adaptive_target_sell:
            if (action.limit_price <= 0 or action.limit_price > current_price
                    or round_price(action.limit_price) != action.limit_price):
                return None
        if action.side is OrderSide.BUY:
            if not _positive_finite(action.amount):
                return None
            if adaptive_auto and (not _positive_finite(action.limit_price) or market_order):
                return None
            executable_buy_limit = self.buy_limit_price(current_price)
            if action.limit_price > 0 and (
                    adaptive_auto or action.reason == "reentry_buy"
                    or (self.config.strategy.cash_buy_drop_enabled
                        and (action.reason.startswith("add_buy_drop_")
                             or action.reentry_type == ReentryType.CASH_CAPACITY_REENTRY.value))):
                # A stale/decayed trigger can sit above today's exchange upper
                # limit.  Keep the trigger as the willingness-to-pay ceiling,
                # but submit a marketable limit near the live quote so KIS does
                # not reject an otherwise valid "price fell through trigger"
                # reentry before it reaches the order book.
                limit_price = min(action.limit_price, executable_buy_limit)
                if adaptive_auto:
                    limit_price = floor_order_price(limit_price)
                if limit_price != action.limit_price:
                    self.logger.info(
                        "buy_limit_capped code=%s reason=%s strategy_limit=%s executable_limit=%s current_price=%s",
                        position.code,
                        action.reason,
                        action.limit_price,
                        limit_price,
                        current_price,
                    )
            elif action.limit_price > 0:
                limit_price = action.limit_price
            else:
                limit_price = executable_buy_limit
            quantity_price = limit_price if self.config.strategy.adaptive_lot_enabled else current_price
            quantity = action.amount // quantity_price if _positive_finite(quantity_price) else 0
        else:
            quantity = action.quantity or 0
            if adaptive_target_sell:
                # A long-decayed trigger can lie outside today's daily price
                # band. The validated quote is executable and still clears
                # the LOT's threshold without submitting an ancient price.
                limit_price = max(action.limit_price, current_price)
            else:
                limit_price = action.limit_price if action.limit_price > 0 else self.sell_limit_price(current_price)
        if (action.side is OrderSide.BUY and self.config.risk.cash_shortfall_rotation_enabled
                and not self.config.strategy.adaptive_lot_enabled):
            from .cash_rotation import rebuy_price_limit
            ceiling = rebuy_price_limit(self, position)
            if ceiling is not None:
                limit_price = min(limit_price, ceiling)
                if limit_price <= 0:
                    return None
        if (not _positive_finite(limit_price) or not _positive_finite(quantity)
                or int(quantity) != quantity or round_price(limit_price) != limit_price):
            return None
        return OrderRequest(
            position.code,
            position.name,
            action.side,
            int(quantity),
            int(limit_price),
            action.reason,
            action.lot_id,
            market_order,
            action.sell_reason,
            action.reentry_type,
            action.cleanup_flag,
        )

    def submit_and_confirm(self, request: OrderRequest) -> tuple[OrderResult, TradeFill | None]:
        adaptive_enabled = self.config.strategy.adaptive_lot_enabled
        adaptive_reentry = self._adaptive_submission_guard(request) if adaptive_enabled else False
        confirmed_cash_snapshot = None
        if (not adaptive_enabled and self.config.strategy.cash_capacity_reentry_enabled() and request.side is OrderSide.BUY
                and request.reentry_type == ReentryType.CASH_CAPACITY_REENTRY.value):
            from .market_session import SessionUnavailable
            from .lot_manager import LotManager
            from .strategy import LotGridStrategy
            if not self.config.strategy.cash_capacity_reentry_active():
                raise SessionUnavailable("reentry_not_started_until_start_date")
            if self.store.open_orders():
                raise SessionUnavailable("cash_capacity_reentry_wait_open_orders")
            if self.config.order.live_trading:
                # Guard the final executable notional even for callers that
                # bypass the main strategy evaluation path.
                snapshot = self.client.account_snapshot()
                confirmed_cash_snapshot = snapshot
                strategy = LotGridStrategy(self.config, LotManager(self.config.strategy))
                sizing = strategy.lot_sizing_for_new_cycle(request.limit_price)
                unit_amount = int(sizing.get("lot_unit_amount", 0))
                if self.config.strategy.lot_sizing_mode != "cycle_locked_by_entry_price":
                    unit_amount = self.config.strategy.initial_buy_amount
                elif not sizing.get("enabled") or unit_amount <= 0:
                    raise SessionUnavailable("cash_capacity_reentry_lot_sizing_unavailable")
                required = max(unit_amount, request.quantity * request.limit_price) * self.config.strategy.reentry_cash_lot_multiple
                if snapshot.cash_available < required:
                    raise SessionUnavailable("cash_capacity_reentry_cash_insufficient")
        if (not adaptive_enabled and self.config.order.live_trading and self.config.strategy.cash_buy_drop_enabled
                and request.side is OrderSide.BUY
                and (request.reason.startswith("add_buy_drop_")
                     or request.reentry_type == ReentryType.CASH_CAPACITY_REENTRY.value)):
            from .market_session import SessionUnavailable
            from .lot_manager import LotManager
            from .strategy import LotGridStrategy
            # The main loop already re-evaluates against fresh cash. Enforce
            # the same price ceiling immediately before an automatic POST,
            # including callers that bypass that loop. Initial/manual entries
            # have no historical LOT reference and keep their own guards.
            snapshot = confirmed_cash_snapshot or self.client.account_snapshot()
            position = self.store.load_positions().get(request.code)
            if position is None:
                raise SessionUnavailable("cash_buy_reference_missing")
            strategy = LotGridStrategy(self.config, LotManager(self.config.strategy, self.store.load_lots()))
            details = strategy.cash_buy_details(position, request.limit_price, snapshot)
            if not details["cash_buy_price_condition_met"]:
                raise SessionUnavailable(str(details.get("skip_reason") or "cash_buy_price_not_reached"))
        if request.session:
            from .market_session import SessionUnavailable, Route
            if not self.config.extended_hours.enabled:
                raise SessionUnavailable("extended_hours_disabled")
            market = getattr(self, "session_market", None)
            if market is None:
                raise SessionUnavailable("session_market_validation_missing")
            market.validate_request(request)
            # Same ledger guard regardless of venue, before any network POST.
            if self.store.has_open_order(request.code, request.side, request.lot_id if request.side is OrderSide.SELL else ""):
                raise SessionUnavailable("existing_order_any_venue")
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
            if not self._record_new_fill(fill, "paper_submit"):
                return result, None
            return result, fill

        if (request.session or self.config.risk.cash_shortfall_rotation_enabled
                or self.config.strategy.cash_capacity_reentry_enabled() or adaptive_enabled):
            from .market_session import SessionUnavailable
            # Durable reservation *before* sending. Ambiguous transport failure
            # stays pending and blocks retransmission, including after restart.
            intent_id = "INTENT-" + uuid4().hex
            intent = OrderResult(request, intent_id, OrderStatus.REQUESTED, "submission_in_progress")
            reservation_options = ({"block_any_side": True, "block_pending_buy": adaptive_reentry}
                                   if adaptive_enabled else {})
            if not self.store.reserve_session_intent(intent, **reservation_options):
                raise SessionUnavailable("existing_order_any_venue")
            try:
                result = self.client.place_order(request)
            except Exception as error:
                from .kis_client import KisApiError
                try:
                    business_rejection = (isinstance(error, KisApiError) and error.status_code is None
                                          and str(json.loads(error.body).get("rt_cd")) in {"1", "7"})
                except (ValueError, TypeError):
                    business_rejection = False
                if business_rejection or isinstance(error, SessionUnavailable):
                    rejected = replace(intent, status=OrderStatus.REJECTED, message=str(error))
                    self.store.record_order(rejected)
                    if request.session:
                        route = Route(request.session, request.venue, request.quote_market, request.order_division)
                        market.reject_for_day(request.code, route, "session_order_rejected")
                    self.logger.warning("session_order_rejected code=%s session=%s venue=%s error=%s", request.code, request.session, request.venue, error)
                    return rejected, None
                self.store.record_order(replace(intent, message="submission_outcome_unknown:" + type(error).__name__))
                if request.session:
                    route = Route(request.session, request.venue, request.quote_market, request.order_division)
                    market.reject_for_day(request.code, route, "submission_requires_reconciliation")
                raise
            if not result.order_id and result.status is not OrderStatus.REJECTED:
                self.store.record_order(replace(intent, message="submission_outcome_unknown:missing_broker_order_id"))
                raise SessionUnavailable("submission_requires_reconciliation")
            try:
                result = self.store.resolve_session_intent(intent, result)
            except Exception:
                # Preserve the acknowledged broker ID for manual reconciliation
                # if the legacy order-number-only key collides or storage fails.
                self.store.record_order(replace(intent, message="broker_acknowledged:" + result.order_id + ":ledger_reconciliation_required"))
                if request.session:
                    market.reject_for_day(request.code, Route(request.session, request.venue, request.quote_market, request.order_division), "submission_requires_reconciliation")
                self.logger.exception("session_order_ack_storage_failed code=%s intent_id=%s broker_order_id=%s",
                                      request.code, intent.order_id, result.order_id)
                raise
            if request.session and result.status is OrderStatus.REJECTED:
                market.reject_for_day(request.code, Route(request.session, request.venue, request.quote_market, request.order_division), "session_order_rejected")
        else:
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
            status = self._cancel(result, request.quantity)
        except RuntimeError as error:
            fill = self._find_matching_fill(result)
            if fill:
                recorded, returned_fill = self._record_filled(result, fill, filled_after_cancel_request=True)
                self.store.mark_order_cancel_check(
                    result.order_id,
                    cancel_requested=True,
                    cancel_rejected=True,
                    filled_after_cancel_request=True,
                    response_message=str(error),
                    post_cancel_execution_checked=True,
                )
                return recorded, returned_fill
            if "40330000" in str(error):
                unconfirmed = OrderResult(request, result.order_id, OrderStatus.CANCEL_REJECTED, "cancel_failed_no_cancelable_quantity")
                self.store.record_order(unconfirmed)
                self.store.mark_order_cancel_check(
                    result.order_id,
                    cancel_requested=True,
                    cancel_rejected=True,
                    response_code="40330000",
                    response_message=str(error),
                    post_cancel_execution_checked=True,
                )
                self.logger.warning("order_unconfirmed code=%s order_id=%s reason=cancel_failed_no_cancelable_quantity", request.code, result.order_id)
                return unconfirmed, None
            raise
        post_cancel_fills = self._matching_fills(result)
        self.store.mark_order_cancel_check(result.order_id, cancel_requested=True, cancel_confirmed=status is OrderStatus.CANCELED, post_cancel_execution_checked=True)
        if post_cancel_fills:
            fill = self._dedupe_or_delta_fill(post_cancel_fills[0])
            if fill is not None:
                recorded, returned_fill = self._record_filled(result, fill, filled_after_cancel_request=True)
                self.store.mark_order_cancel_check(result.order_id, filled_after_cancel_request=True)
                return recorded, returned_fill
        final_status = OrderStatus.CANCELED_NO_FILL if status is OrderStatus.CANCELED else status
        canceled = OrderResult(request, result.order_id, final_status, "unfilled_limit_order_timeout")
        self.store.record_order(canceled)
        return canceled, None

    def _adaptive_submission_guard(self, request: OrderRequest) -> bool:
        """Recheck persisted anchors and spendable cash immediately before POST."""
        from .market_session import SessionUnavailable
        from .lot_manager import LotManager
        from .strategy import LotGridStrategy
        from .volatility import DailyVolatilityCache

        if (not _positive_finite(request.quantity) or int(request.quantity) != request.quantity
                or not _positive_finite(request.limit_price)
                or int(request.limit_price) != request.limit_price
                or round_price(request.limit_price) != request.limit_price):
            raise SessionUnavailable("adaptive_invalid_order")
        open_orders = self.store.open_orders()
        if any(order.request.code == request.code for order in open_orders):
            raise SessionUnavailable("existing_order_any_venue")
        if request.side is not OrderSide.BUY:
            if request.lot_id:
                lot = self.store.load_lots().get(request.lot_id)
                if lot is None or lot.code != request.code or request.quantity > lot.remaining_quantity:
                    raise SessionUnavailable("adaptive_sell_quantity_unavailable")
            return False

        positions = self.store.load_positions()
        position = positions.get(request.code)
        automatic = request.reason.startswith("adaptive_")
        if position is None and automatic:
            raise SessionUnavailable("adaptive_buy_reference_missing")
        if automatic and (position.position_state in {"SYNC_REQUIRED", "RISK_BLOCKED", "REVIEW_REQUIRED"}
                          or position.sync_status != "OK" or position.lot_quantity_mismatch
                          or position.trading_paused or not position.auto_buy_enabled):
            raise SessionUnavailable("adaptive_persisted_buy_block")
        restricted = bool(position is not None and position.rebuy_restriction_active)
        # Uncertain local BUY reservations can be absent from the broker's
        # spendable-cash response. Reentry waits for their reconciliation.
        if restricted and any(order.request.side is OrderSide.BUY for order in open_orders):
            raise SessionUnavailable("adaptive_reentry_wait_pending_buy")
        snapshot = self.client.account_snapshot()
        cash = snapshot.cash_available
        cost = request.quantity * request.limit_price
        if not _nonnegative_finite(cash) or cash < cost:
            raise SessionUnavailable("adaptive_cash_insufficient")
        if not automatic and not restricted:
            return False
        if request.market_order:
            raise SessionUnavailable("adaptive_market_buy_has_no_price_ceiling")
        lots = LotManager(self.config.strategy, self.store.load_lots())
        cache = DailyVolatilityCache(self.store.path.with_suffix(".volatility.json"))
        lots.set_volatility_multiplier(request.code, cache.multiplier(request.code))
        strategy = LotGridStrategy(self.config, lots)
        strategy.positions = positions
        details = strategy.adaptive_buy_details(position, request.limit_price, snapshot)
        target = details.get("buy_target_price")
        if (not _positive_finite(target) or request.limit_price > target
                or not details.get("buy_condition_met")):
            raise SessionUnavailable(str(details.get("skip_reason") or "adaptive_buy_price_not_reached"))
        if automatic:
            budget = details.get("buy_amount")
            if not _positive_finite(budget) or cost > budget:
                raise SessionUnavailable("adaptive_buy_budget_exceeded")
            if details.get("skip_reason"):
                raise SessionUnavailable(str(details["skip_reason"]))
        if restricted:
            reserve = strategy.adaptive_reentry_cash_details(position, snapshot, cost)
            if not reserve.get("reentry_cash_condition_met"):
                raise SessionUnavailable(str(reserve.get("skip_reason") or "adaptive_reentry_cash_insufficient"))
        return restricted

    def reconcile_open_orders(self) -> tuple[TradeFill, ...]:
        self._recover_acknowledged_intents()
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
            if result.order_id.startswith("INTENT-"):
                self.logger.error("order_submission_unknown code=%s intent_id=%s manual_broker_reconciliation_required=true",
                                  result.request.code, result.order_id)
                continue
            fills = self._matching_fills(result, fetched_fills)
            for original_fill in fills:
                fill = self._dedupe_or_delta_fill(original_fill)
                if fill is None:
                    duplicate_fill_count += 1
                    self._log_duplicate_fill(original_fill, "reconcile_open_orders_delta")
                    continue
                if self._record_new_fill(fill, "reconcile_open_orders"):
                    applied.append(fill)
                else:
                    duplicate_fill_count += 1
            filled_quantity = self._filled_quantity_for_result(result)
            if filled_quantity >= result.request.quantity:
                self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.FILLED, result.message))
                continue
            if filled_quantity > 0:
                self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.PARTIAL, result.message))
            if self._close_or_throttle_cancel_rejected(result, filled_quantity):
                continue
            elapsed = self.store.seconds_since_recent_order_request(result.request.code)
            if elapsed is None or elapsed < self.config.order.limit_order_timeout_seconds:
                continue
            try:
                status = self._cancel(result, max(1, result.request.quantity - filled_quantity))
            except RuntimeError as error:
                if filled_quantity == 0 and "APBK0927" in str(error):
                    # KIS reports that there is no remaining cancelable quantity when
                    # the order either completed or was already closed at the venue.
                    # Re-query executions once before deciding which terminal state to
                    # persist so this order does not remain open and get canceled again
                    # on every subsequent loop.
                    fetched_after_rejection = self.client.executions(since=query_start.date())
                    self.store.mark_order_cancel_check(
                        result.order_id,
                        cancel_requested=True,
                        cancel_rejected=True,
                        response_code="APBK0927",
                        response_message=str(error),
                        post_cancel_execution_checked=True,
                    )
                    fills_after_rejection = self._matching_fills(result, fetched_after_rejection)
                    for original_fill in fills_after_rejection:
                        fill = self._dedupe_or_delta_fill(original_fill)
                        if fill is None:
                            duplicate_fill_count += 1
                            self._log_duplicate_fill(original_fill, "reconcile_after_cancel_rejected_delta")
                            continue
                        if self._record_new_fill(fill, "reconcile_after_cancel_rejected"):
                            applied.append(fill)
                        else:
                            duplicate_fill_count += 1
                    filled_quantity = self._filled_quantity_for_result(result)
                    final_status = (
                        OrderStatus.FILLED_AFTER_CANCEL_REQUEST
                        if filled_quantity >= result.request.quantity
                        else OrderStatus.CANCELED_AFTER_PARTIAL_FILL
                        if filled_quantity > 0
                        else OrderStatus.CANCELED_NO_FILL
                    )
                    self.store.record_order(
                        OrderResult(result.request, result.order_id, final_status, "cancel_rejected_no_cancelable_quantity_reconciled")
                    )
                    self.logger.warning(
                        "order_cancel_rejected_no_cancelable_reconciled code=%s order_id=%s filled_qty=%s order_qty=%s final_status=%s",
                        result.request.code,
                        result.order_id,
                        filled_quantity,
                        result.request.quantity,
                        final_status.value,
                    )
                    continue
                if filled_quantity > 0 and _is_no_cancelable_quantity_error(error):
                    self.store.mark_order_cancel_check(
                        result.order_id,
                        cancel_requested=True,
                        cancel_rejected=True,
                        response_message=str(error),
                        post_cancel_execution_checked=True,
                    )
                    self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.CANCELED_AFTER_PARTIAL_FILL, "cancel_rejected_no_cancelable_quantity_after_partial_fill"))
                    self.logger.warning(
                        "order_cancel_rejected_closed_after_partial_fill code=%s order_id=%s filled_qty=%s order_qty=%s error=%s",
                        result.request.code,
                        result.order_id,
                        filled_quantity,
                        result.request.quantity,
                        error,
                    )
                    continue
                post_cancel_checked = _is_missing_original_order_error(error)
                self.store.mark_order_cancel_check(
                    result.order_id,
                    cancel_requested=True,
                    cancel_rejected=True,
                    response_message=str(error),
                    post_cancel_execution_checked=post_cancel_checked,
                    increment_cancel_retry=True,
                )
                if post_cancel_checked:
                    self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.CANCELED_NO_FILL, "cancel_rejected_missing_original_no_fill"))
                    self.logger.warning(
                        "order_cancel_rejected_closed_missing_original code=%s order_id=%s filled_qty=%s order_qty=%s error=%s",
                        result.request.code,
                        result.order_id,
                        filled_quantity,
                        result.request.quantity,
                        error,
                    )
                    continue
                self.store.record_order(OrderResult(result.request, result.order_id, OrderStatus.CANCEL_REJECTED, "cancel_rejected_pending_reconciliation"))
                self.logger.warning("order_cancel_or_requery_failed code=%s order_id=%s error=%s", result.request.code, result.order_id, error)
                continue
            self.store.mark_order_cancel_check(result.order_id, cancel_requested=True, cancel_confirmed=status is OrderStatus.CANCELED)
            fetched_after_cancel = self.client.executions(since=query_start.date())
            self.store.mark_order_cancel_check(result.order_id, post_cancel_execution_checked=True)
            fills_after_cancel = self._matching_fills(result, fetched_after_cancel)
            for original_fill in fills_after_cancel:
                fill = self._dedupe_or_delta_fill(original_fill)
                if fill is None:
                    duplicate_fill_count += 1
                    self._log_duplicate_fill(original_fill, "reconcile_after_cancel_delta")
                    continue
                if self._record_new_fill(fill, "reconcile_after_cancel"):
                    applied.append(fill)
                else:
                    duplicate_fill_count += 1
            filled_quantity = self._filled_quantity_for_result(result)
            final_status = self._post_cancel_status(result, status, filled_quantity)
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
        self._recover_acknowledged_intents()
        days = max(1, self.config.order.startup_execution_lookup_days)
        query_start = datetime.now() - timedelta(days=days - 1)
        fetched_fills = self.client.executions(since=query_start.date())
        applied: list[TradeFill] = []
        duplicate_fill_count = 0
        ignored_unmatched_count = 0
        for fill in fetched_fills:
            order = self.store.find_order(fill.order_id, code=fill.code, side=fill.side,
                                          trade_date=fill.filled_at.date().isoformat(), venue=fill.reported_order_venue)
            if order is None or order.request.code != fill.code or order.request.side != fill.side:
                ignored_unmatched_count += 1
                continue
            if order.request.session and not self._matching_fills(order, (fill,)):
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
                fill.reported_order_venue,
                fill.timestamp_source,
                fill.filled_amount,
            )
            if order.request.session:
                matched = self._matching_fills(order, (fill,))[0]
            original_matched = matched
            matched = self._dedupe_or_delta_fill(matched)
            if matched is None:
                duplicate_fill_count += 1
                self._log_duplicate_fill(original_matched, "startup_reconcile_delta")
                continue
            if self._record_new_fill(matched, "startup_reconcile"):
                applied.append(matched)
                filled_quantity = self._filled_quantity_for_result(order)
                status = OrderStatus.FILLED if filled_quantity >= order.request.quantity else OrderStatus.PARTIAL
                if order.status in {OrderStatus.CANCELED, OrderStatus.CANCELED_NO_FILL, OrderStatus.CANCEL_REJECTED}:
                    status = OrderStatus.FILLED_AFTER_CANCEL_REQUEST if filled_quantity >= order.request.quantity else OrderStatus.CANCELED_AFTER_PARTIAL_FILL
                    self.store.mark_order_cancel_check(order.order_id, filled_after_cancel_request=filled_quantity > 0, post_cancel_execution_checked=True)
                self.store.record_order(OrderResult(order.request, order.order_id, status, "startup_reconcile_fill_found"))
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
            already_filled = self.store.filled_quantity_for_order(
                fill.order_id,
                code=fill.code,
                side=fill.side,
                lot_id=fill.lot_id if fill.side is OrderSide.SELL else "",
                filled_at=fill.filled_at,
            )
            delta_quantity = fill.quantity - already_filled
            if delta_quantity <= 0:
                return None
            cumulative_amount = fill.filled_amount or fill.quantity * fill.price
            already_amount = self.store.recorded_fill_amount(
                fill.code, fill.order_id, fill.side, filled_at=fill.filled_at,
                lot_id=fill.lot_id if fill.side is OrderSide.SELL else "",
            )
            delta_amount = cumulative_amount - already_amount
            if not _positive_finite(delta_amount):
                self.logger.error(
                    "aggregate_fill_amount_inconsistent code=%s order_id=%s cumulative_qty=%s prior_qty=%s cumulative_amount=%s prior_amount=%s",
                    fill.code, fill.order_id, fill.quantity, already_filled, cumulative_amount, already_amount,
                )
                return None
            delta_price = delta_amount / delta_quantity
            delta_price = int(delta_price) if float(delta_price).is_integer() else delta_price
            delta_filled_at = fill.filled_at + timedelta(microseconds=max(1, already_filled))
            return TradeFill(
                fill.code,
                fill.name,
                fill.side,
                delta_quantity,
                delta_price,
                fill.order_id,
                delta_filled_at,
                fill.lot_id,
                f"{fill.execution_id}:delta:{already_filled}->{fill.quantity}",
                fill.sell_reason,
                fill.reentry_type,
                fill.reported_order_venue,
                fill.timestamp_source,
                delta_amount if fill.filled_amount else 0,
            )
        return fill

    def _find_matching_fill(self, result: OrderResult) -> TradeFill | None:
        fills = self._matching_fills(result)
        return fills[0] if fills else None

    def _cancel(self, result: OrderResult, quantity: int) -> OrderStatus:
        if result.request.session:
            try:
                return self.client.cancel_order(broker_order_id(result.order_id), quantity, original_request=result.request,
                                                original_requested_at=result.requested_at)
            except RuntimeError as error:
                # An order absent from the cancelable list during a venue gap is
                # not proof of cancellation. Require final broker quantities.
                if self.client.session_order_terminal(replace(result, order_id=broker_order_id(result.order_id))):
                    raise RuntimeError("APBK0927: session order terminal quantities confirmed") from error
                raise RuntimeError("session_cancel_outcome_unconfirmed") from error
        return self.client.cancel_order(broker_order_id(result.order_id), quantity)

    def _matching_fills(self, result: OrderResult, fetched_fills: tuple[TradeFill, ...] | None = None) -> list[TradeFill]:
        request = result.request
        order_id = _normalize_order_id(broker_order_id(result.order_id))
        matches = []
        source_fills = fetched_fills if fetched_fills is not None else self.client.executions()
        for fill in source_fills:
            if request.session or result.order_id.startswith("SESSION:"):
                if fill.reported_order_venue and fill.reported_order_venue != request.venue:
                    continue
                if result.requested_at and fill.filled_at.date().isoformat() != result.requested_at[:10]:
                    continue
            if _normalize_order_id(fill.order_id) == order_id and fill.code == request.code and fill.side == request.side:
                identity = fill.execution_id
                if identity and result.order_id.startswith("SESSION:"):
                    identity += ":" + result.order_id
                matches.append(replace(fill, order_id=result.order_id, execution_id=identity,
                                       lot_id=request.lot_id, sell_reason=request.sell_reason, reentry_type=request.reentry_type))
        return matches

    def _recover_acknowledged_intents(self) -> None:
        """Recover only a recorded acknowledgment verified by a fresh broker GET.

        Never submit a replacement order. Unknown outcomes without an explicit
        broker number remain blocked for manual reconciliation.
        """
        for intent in self.store.open_orders():
            if not intent.order_id.startswith("INTENT-") or not intent.message.startswith("broker_acknowledged:"):
                continue
            number = intent.message.split(":")[1]
            verifier = getattr(self.client, "verify_session_acknowledgment", None)
            if verifier is None:
                continue
            row = verifier(intent, number)
            if row is None:
                self.logger.error("session_ack_recovery_unconfirmed intent_id=%s broker_order_id=%s", intent.order_id, number)
                continue
            recovered = self.store.resolve_session_intent(intent, replace(intent, order_id=number,
                message="broker_ack_recovered", order_orgno=str(row.get("ord_gno_brno") or row.get("krx_fwdg_ord_orgno") or "")))
            self.logger.info("session_ack_recovered intent_id=%s local_order_id=%s broker_order_id=%s",
                             intent.order_id, recovered.order_id, number)

    def _execution_query_start(self, open_orders: tuple[OrderResult, ...]) -> datetime:
        now = datetime.now()
        today_start = datetime.combine(now.date(), day_time.min)
        requested_times = [_parse_timestamp(order.requested_at) for order in open_orders if order.requested_at]
        oldest_requested = min(requested_times, default=today_start)
        buffered = oldest_requested - timedelta(minutes=self.config.order.execution_query_buffer_minutes)
        if not self.config.order.include_previous_day_for_open_orders:
            return today_start
        query_start = min(today_start, buffered)
        if open_orders:
            previous_day_start = today_start - timedelta(days=1)
            query_start = min(query_start, previous_day_start)
        return query_start

    def _record_filled(self, result: OrderResult, fill: TradeFill, *, filled_after_cancel_request: bool = False) -> tuple[OrderResult, TradeFill | None]:
        status = OrderStatus.FILLED if fill.quantity >= result.request.quantity else OrderStatus.PARTIAL
        if filled_after_cancel_request:
            status = OrderStatus.FILLED_AFTER_CANCEL_REQUEST if fill.quantity >= result.request.quantity else OrderStatus.CANCELED_AFTER_PARTIAL_FILL
        recorded = OrderResult(result.request, result.order_id, status, result.message)
        self.store.record_order(recorded)
        if not self._record_new_fill(fill, "submit_confirm"):
            return recorded, None
        if status is OrderStatus.PARTIAL:
            self.logger.warning(
                "order_partial code=%s order_id=%s filled_qty=%s order_qty=%s",
                result.request.code,
                result.order_id,
                fill.quantity,
                result.request.quantity,
            )
        return recorded, fill

    def _filled_quantity_for_result(self, result: OrderResult) -> int:
        request = result.request
        return self.store.filled_quantity_for_order(
            result.order_id,
            code=request.code,
            side=request.side,
            lot_id=request.lot_id if request.side is OrderSide.SELL else "",
        )

    def _post_cancel_status(self, result: OrderResult, cancel_status: OrderStatus, filled_quantity: int) -> OrderStatus:
        if filled_quantity >= result.request.quantity:
            return OrderStatus.FILLED_AFTER_CANCEL_REQUEST
        if filled_quantity > 0:
            return OrderStatus.CANCELED_AFTER_PARTIAL_FILL
        if cancel_status is OrderStatus.CANCELED:
            return OrderStatus.CANCELED_NO_FILL
        return cancel_status

    def _close_or_throttle_cancel_rejected(self, result: OrderResult, filled_quantity: int) -> bool:
        if result.status is not OrderStatus.CANCEL_REJECTED:
            return False
        max_retries = max(1, self.config.order.cancel_rejected_max_retries)
        if result.cancel_retry_count >= max_retries:
            if result.request.session or self.config.strategy.adaptive_lot_enabled:
                self.logger.error("session_order_reconciliation_required order_id=%s reservation_retained=true", result.order_id)
                return True
            status = OrderStatus.CANCELED_AFTER_PARTIAL_FILL if filled_quantity > 0 else OrderStatus.CANCELED_NO_FILL
            self.store.mark_order_cancel_check(result.order_id, post_cancel_execution_checked=True)
            self.store.record_order(OrderResult(result.request, result.order_id, status, "cancel_rejected_retry_limit_no_remaining_fill"))
            self.logger.warning(
                "order_cancel_rejected_closed_retry_limit code=%s order_id=%s retry_count=%s filled_qty=%s order_qty=%s final_status=%s",
                result.request.code,
                result.order_id,
                result.cancel_retry_count,
                filled_quantity,
                result.request.quantity,
                status.value,
            )
            return True
        interval = max(0, self.config.order.cancel_rejected_retry_interval_seconds)
        if interval <= 0 or not result.cancel_checked_at:
            return False
        try:
            checked_at = _parse_timestamp(result.cancel_checked_at)
        except ValueError:
            return False
        retry_after = checked_at + timedelta(seconds=interval)
        if datetime.now() >= retry_after:
            return False
        self.logger.info(
            "order_cancel_rejected_retry_deferred code=%s order_id=%s retry_count=%s retry_after=%s",
            result.request.code,
            result.order_id,
            result.cancel_retry_count,
            retry_after.isoformat(timespec="seconds"),
        )
        return True

    def _record_new_fill(self, fill: TradeFill, source: str) -> bool:
        dedupe_key_type = _dedupe_key_type(fill)
        recorded = self.store.record_fill(fill)
        if recorded:
            self.logger.info(
                "fill_recorded source=%s code=%s side=%s qty=%s price=%s order_id=%s execution_id=%s dedupe_key_type=%s",
                source,
                fill.code,
                fill.side.value,
                fill.quantity,
                fill.price,
                fill.order_id,
                fill.execution_id,
                dedupe_key_type,
            )
            return True
        self.logger.warning(
            "record_fill_failed source=%s code=%s side=%s qty=%s price=%s order_id=%s execution_id=%s dedupe_key_type=%s reason=duplicate_or_existing_fill",
            source,
            fill.code,
            fill.side.value,
            fill.quantity,
            fill.price,
            fill.order_id,
            fill.execution_id,
            dedupe_key_type,
        )
        return False

    def _log_duplicate_fill(self, fill: TradeFill, source: str) -> None:
        self.logger.info(
            "duplicate_fill_ignored source=%s code=%s side=%s qty=%s price=%s order_id=%s execution_id=%s dedupe_key_type=%s",
            source,
            fill.code,
            fill.side.value,
            fill.quantity,
            fill.price,
            fill.order_id,
            fill.execution_id,
            _dedupe_key_type(fill),
        )

    def buy_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 + self.config.order.buy_limit_markup_pct / 100.0))

    def sell_limit_price(self, current_price: int) -> int:
        return round_price(current_price * (1.0 - self.config.order.sell_limit_markdown_pct / 100.0))


def _normalize_order_id(order_id: str) -> str:
    normalized = str(order_id).strip()
    return normalized.lstrip("0") or normalized


def _positive_finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _nonnegative_finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _is_no_cancelable_quantity_error(error: Exception) -> bool:
    message = str(error)
    return "APBK0927" in message or "40330000" in message


def _is_missing_original_order_error(error: Exception) -> bool:
    message = str(error)
    return "APBK0344" in message or "원주문정보" in message


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _dedupe_key_type(fill: TradeFill) -> str:
    return "fallback" if not fill.execution_id or fill.execution_id.startswith("AGG:") else "execution_id"
