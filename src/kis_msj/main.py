"""Lot-based domestic stock auto-trader entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import datetime, time as day_time
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, BotConfig, config_hash, config_to_dict, load_config, write_default_config
from .kis_client import KisApiError, KisClient, MockKisClient
from .logger import configure_trade_logger, log_decision
from .lot_manager import LotManager
from .loop_profile import LoopProfile, key_value_line
from .models import AccountSnapshot, OrderSide, PositionState, Quote, SellReason
from .models import PositionLifecycle
from .notifier import LogNotifier
from .order_manager import OrderManager
from .position_manager import PositionManager
from .price_provider import PriceSampler
from .risk_manager import RiskManager
from .runtime_control import DEFAULT_RUNTIME_CONTROL_PATH, RuntimeControl, load_runtime_control, runtime_block_reason, save_runtime_control
from .storage import StateStore
from .strategy import LotGridStrategy, StrategyAction
from .upstream_watcher import UpstreamWatcher


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUOTE_CSV = PROJECT_ROOT / "data" / "kospi_current_quotes.csv"


def _null_stage():
    return nullcontext()


class AutoTrader:
    def __init__(self, config: BotConfig, *, use_mock_client: bool = False) -> None:
        self.config = config
        self.logger = configure_trade_logger(config.log_path)
        self.store = StateStore(config.storage_path)
        self.config_hash = config_hash(config)
        self.run_id = config.experiment.run_id or config.run_id or f"{config.risk.profile}_{self.config_hash}"
        self.experiment_name = config.experiment.experiment_name or config.experiment_name or config.risk.profile
        self.store.set_active_config(self.config_hash, run_id=self.run_id, experiment_name=self.experiment_name, profile_name=config.risk.profile)
        self.store.record_config_snapshot(
            self.config_hash,
            config_to_dict(config),
            source="bot_init",
            operator_note=config.experiment.operator_note or config.operator_note,
            run_id=self.run_id,
            experiment_name=self.experiment_name,
            profile_name=config.risk.profile,
        )
        self.lot_manager = LotManager(config.strategy, self.store.load_lots())
        self.position_manager = PositionManager(config.strategy, self.lot_manager, self.store.load_positions())
        self.risk_manager = RiskManager(config)
        self.strategy = LotGridStrategy(config, self.lot_manager)
        self.notifier = LogNotifier(self.logger)
        self.upstream_watcher = UpstreamWatcher(config.upstream_watch, self.notifier)
        self.client = MockKisClient(DEFAULT_QUOTE_CSV) if use_mock_client or not config.order.live_trading else KisClient(config.kis_account, enable_execution_raw_log=config.order.enable_execution_raw_log)
        if hasattr(self.client, "logger"):
            self.client.logger = self.logger
        self.price_sampler = PriceSampler(self.client, config.order.price_sample_count, config.order.price_sample_interval_seconds)
        self.order_manager = OrderManager(config, self.client, self.store, self.logger)
        self._startup_recent_executions_reconciled = False
        self._loop_id = 0
        self.last_loop_profile: dict[str, object] = {}
        self._active_loop_profile: LoopProfile | None = None
        self._last_loop_interval_warning_at = 0.0

    def reload_config(self, config: BotConfig, *, use_mock_client: bool = False) -> None:
        self.__init__(config, use_mock_client=use_mock_client)

    def startup_sync(self) -> AccountSnapshot:
        snapshot = self.client.account_snapshot()
        if self.config.order.live_trading:
            self.position_manager.sync_account(snapshot)
            self.risk_manager.data_mismatch_detected = self.position_manager.account_mismatch_detected
            if self.position_manager.account_mismatch_detected:
                self.notifier.notify("SYNC_REQUIRED", "Lot quantity differs from KIS account balance. Trading is paused for mismatched symbols.")
            try:
                open_orders = self.client.open_orders()
            except RuntimeError as error:
                if getattr(self.client, "is_demo", False) and "90000000" in str(error):
                    self.logger.warning("open_orders skipped reason=demo_api_unsupported")
                    open_orders = ()
                else:
                    raise
            if open_orders:
                self.logger.warning("open_orders count=%s buy_orders_blocked=true", len(open_orders))
                self.risk_manager.data_mismatch_detected = True
        self.store.save_positions(self.position_manager.positions.values())
        self.store.save_lots(self.lot_manager.lots.values())
        return snapshot

    def run_once(self) -> str:
        self._loop_id += 1
        profile = (
            LoopProfile(
                loop_id=self._loop_id,
                symbols_total=len(self.config.stocks),
                loop_interval_seconds=self.config.loop_interval_seconds,
                run_id=self.run_id,
                experiment_name=self.experiment_name,
                active_profile=self.config.risk.profile,
            )
            if self.config.loop_profiling_enabled
            else None
        )
        status = "ok"
        try:
            self._active_loop_profile = profile
            with profile.stage("upstream_watch") if profile else _null_stage():
                self.upstream_watcher.tick()
            with profile.stage("runtime_control") if profile else _null_stage():
                interrupt = self.runtime_interrupt_reason("after_upstream_tick")
            if interrupt:
                status = interrupt
                return interrupt
            with profile.stage("startup_reconciliation") if profile else _null_stage():
                self.reconcile_recent_executions_on_startup()
            with profile.stage("runtime_control") if profile else _null_stage():
                interrupt = self.runtime_interrupt_reason("after_startup_reconciliation")
            if interrupt:
                status = interrupt
                return interrupt
            with profile.stage("open_order_reconciliation") if profile else _null_stage():
                self.reconcile_open_orders()
            with profile.stage("runtime_control") if profile else _null_stage():
                interrupt = self.runtime_interrupt_reason("after_open_order_reconciliation")
            if interrupt:
                status = interrupt
                return interrupt
            with profile.stage("account_sync") if profile else _null_stage():
                snapshot = self.startup_sync()
            with profile.stage("risk_summary") if profile else _null_stage():
                account_risk = self.risk_manager.account_buy_allowed(snapshot, self.position_manager.positions)
            with profile.stage("manual_request") if profile else _null_stage():
                self.process_manual_order_requests(snapshot, account_risk)
            for stock in self.config.stocks:
                with profile.stage("runtime_control") if profile else _null_stage():
                    interrupt = self.runtime_interrupt_reason(f"before_symbol_{stock.code}")
                if interrupt:
                    status = interrupt
                    return interrupt
                symbol_start = time.perf_counter()
                try:
                    with profile.stage("manual_request") if profile else _null_stage():
                        self.process_manual_order_requests(snapshot, account_risk)
                    with profile.stage("runtime_control") if profile else _null_stage():
                        interrupt = self.runtime_interrupt_reason(f"after_manual_requests_{stock.code}")
                    if interrupt:
                        status = interrupt
                        return interrupt
                    if not stock.enabled:
                        if profile:
                            profile.symbols_skipped += 1
                        continue
                    position = self.position_manager.get(stock.code, stock.name)
                    if stock.danger_state:
                        position.danger_state = True
                        position.position_state = PositionLifecycle.RISK_BLOCKED.value
                        position.auto_buy_enabled = False
                    self.evaluate(position, snapshot, account_risk)
                    if profile:
                        profile.add_symbol(stock.code, (time.perf_counter() - symbol_start) * 1000.0)
                except Exception as error:  # noqa: BLE001
                    if profile:
                        profile.add_symbol(stock.code, (time.perf_counter() - symbol_start) * 1000.0)
                    if self._mark_symbol_blocked_for_kis_order_error(stock.code, stock.name, error):
                        continue
                    self.logger.exception(
                        "symbol_evaluate_failed code=%s name=%s error_type=%s error=%s continue_next_symbol=true",
                        stock.code,
                        stock.name,
                        type(error).__name__,
                        error,
                    )
                    continue
                with profile.stage("runtime_control") if profile else _null_stage():
                    interrupt = self.runtime_interrupt_reason(f"after_symbol_{stock.code}")
                if interrupt:
                    status = interrupt
                    return interrupt
            with profile.stage("db") if profile else _null_stage():
                self.store.save_positions(self.position_manager.positions.values())
                self.store.save_lots(self.lot_manager.lots.values())
            return ""
        finally:
            self._active_loop_profile = None
            if profile is not None:
                summary = profile.finish(status)
                self.last_loop_profile = summary
                self.logger.info(key_value_line("loop_profile", summary))
            else:
                summary = {}
            if summary.get("loop_over_interval") and self._should_log_loop_interval_warning():
                self.logger.warning(
                    "loop_duration_exceeded_interval loop_id=%s loop_duration_ms=%s loop_interval_seconds=%s",
                    summary["loop_id"],
                    summary["loop_duration_ms"],
                    summary["loop_interval_seconds"],
                )

    def _should_log_loop_interval_warning(self) -> bool:
        now = time.monotonic()
        minimum = max(0.0, self.config.loop_interval_warning_min_seconds)
        if self._last_loop_interval_warning_at and now - self._last_loop_interval_warning_at < minimum:
            return False
        self._last_loop_interval_warning_at = now
        return True

    def _mark_symbol_blocked_for_kis_order_error(self, code: str, name: str, error: Exception) -> bool:
        message = str(error)
        if not isinstance(error, KisApiError) or "APBK0066" not in message:
            return False
        position = self.position_manager.get(code, name)
        position.danger_state = True
        position.position_state = PositionLifecycle.RISK_BLOCKED.value
        position.auto_buy_enabled = False
        position.trading_paused = True
        position.skip_reason = "kis_trading_halted"
        position.review_reason = "kis_trading_halted: APBK0066 거래정지종목"
        position.last_order_status = "REJECTED"
        position.last_update_time = datetime.now().isoformat(timespec="seconds")
        self.store.save_position(position)
        self.logger.warning(
            "symbol_blocked_by_kis_order_error code=%s name=%s reason=kis_trading_halted kis_msg=APBK0066 continue_next_symbol=true error=%s",
            code,
            name,
            error,
        )
        return True

    def runtime_interrupt_reason(self, stage: str) -> str:
        runtime = load_runtime_control()
        if runtime.config_reload_requested:
            self.logger.info("loop_interrupted stage=%s reason=config_reload_requested", stage)
            return "config_reload_requested"
        if runtime.bot_paused:
            self.logger.info("loop_interrupted stage=%s reason=bot_paused", stage)
            return "bot_paused"
        return ""

    def process_manual_order_requests(self, snapshot: AccountSnapshot, account_risk) -> None:
        for manual in self.store.manual_order_requests("REQUESTED"):
            request_id = str(manual["request_id"])
            claimed = self.store.claim_manual_order_request(request_id)
            if claimed is None:
                self.logger.info("manual_order_request_claim_skipped request_id=%s reason=already_claimed_or_linked", request_id)
                continue
            manual = claimed
            code = str(manual["code"]).zfill(6)
            side = OrderSide(str(manual["side"]))
            position = self.position_manager.get(code)
            try:
                block_reason = self.manual_request_block_reason(manual, position, account_risk)
                if block_reason:
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason=block_reason)
                    self.logger.warning(
                        "manual_order_request_blocked request_id=%s code=%s side=%s reason=%s",
                        request_id,
                        code,
                        side.value,
                        block_reason,
                    )
                    continue
                samples = self.price_sampler.sample(position.code, position.name)
                stable, stable_reason = self.price_sampler.stable(samples, self.config.risk.max_price_sample_volatility_pct)
                current_price = samples[-1].price if samples else 0
                if not stable or current_price <= 0:
                    reason = stable_reason or "current_price_missing"
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason=reason)
                    self.logger.warning("manual_order_request_blocked request_id=%s code=%s side=%s reason=%s", request_id, code, side.value, reason)
                    continue
                position = self.position_manager.refresh_from_lots(code, current_price)
                action = self.manual_action_from_request(manual, current_price)
                sizing_block_reason = self.manual_lot_sizing_block_reason(manual, position, current_price, action)
                if sizing_block_reason:
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason=sizing_block_reason)
                    self.logger.warning("manual_order_request_blocked request_id=%s code=%s side=%s reason=%s", request_id, code, side.value, sizing_block_reason)
                    continue
                final_block_reason = self.pre_request_block_reason(position, action)
                if final_block_reason:
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason=final_block_reason)
                    self.logger.warning("manual_order_request_blocked request_id=%s code=%s side=%s reason=%s", request_id, code, side.value, final_block_reason)
                    continue
                interrupt = self.runtime_interrupt_reason(f"before_manual_submit_{request_id}")
                if interrupt:
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason=interrupt)
                    return
                order_request = self.order_manager.build_request(position, action, current_price)
                if order_request is None:
                    self.store.update_manual_order_request(request_id, status="BLOCKED", block_reason="quantity_below_one")
                    continue
                result, fill = self.order_manager.submit_and_confirm(order_request)
                self.store.update_manual_order_request(request_id, status="SUBMITTED", linked_order_id=result.order_id)
                self.logger.info(
                    "manual_order_request_submitted_to_order_manager request_id=%s code=%s side=%s order_id=%s status=%s",
                    request_id,
                    code,
                    side.value,
                    result.order_id,
                    result.status.value,
                )
                if fill is None:
                    continue
                updated = self.position_manager.apply_fill(fill)
                self.store.save_position(updated)
                self.store.save_lots(self.lot_manager.lots.values())
                self.store.update_manual_order_request(request_id, status="FILLED", linked_order_id=result.order_id)
            except Exception as error:  # noqa: BLE001
                self.store.update_manual_order_request(request_id, status="FAILED", block_reason=type(error).__name__, last_processing_error=str(error))
                self.logger.exception("manual_order_request_failed request_id=%s code=%s side=%s error=%s", request_id, code, side.value, error)

    def manual_request_block_reason(self, manual: dict[str, object], position: PositionState, account_risk) -> str:
        if not self.config.ui_manual_trading_enabled:
            return "ui_manual_trading_disabled"
        if not bool(manual.get("confirm_text_verified")):
            return "confirm_text_required"
        if position.sync_status == PositionLifecycle.SYNC_REQUIRED.value or position.position_state == PositionLifecycle.SYNC_REQUIRED.value:
            return "sync_required"
        stock_config = next((stock for stock in self.config.stocks if stock.code == position.code), None)
        if position.danger_state or position.position_state == PositionLifecycle.RISK_BLOCKED.value or (stock_config is not None and stock_config.danger_state):
            return "risk_blocked_buy_sell_blocked"
        side = OrderSide(str(manual["side"]))
        if side is OrderSide.BUY and not account_risk.allowed:
            return "|".join(account_risk.reasons) or "account_risk_blocked"
        if side is OrderSide.BUY and self.config.strategy.lot_sizing_mode == "cycle_locked_by_entry_price":
            requested_price = int(manual.get("current_price") or position.current_price or 0)
            if requested_price <= 0:
                requested_price = 0
            if requested_price <= 0:
                return ""
            if self.lot_manager.open_lots(position.code):
                sizing = self.strategy.ensure_lot_sizing(position, requested_price)
                open_lot_count = len(self.lot_manager.open_lots(position.code))
            else:
                sizing = self.strategy.lot_sizing_for_new_cycle(requested_price)
                open_lot_count = 0
            amount = int(manual.get("amount") or 0)
            quantity = int(manual.get("quantity") or 0)
            next_amount = amount or (quantity * requested_price if quantity > 0 else int(sizing.get("lot_unit_amount") or 0))
            sizing_block = self.strategy.lot_sizing_buy_block_reason(position, requested_price, sizing, next_buy_amount=next_amount, open_lot_count=open_lot_count)
            if sizing_block:
                return sizing_block
        if side is OrderSide.BUY and self.store.has_open_order(position.code, OrderSide.BUY):
            return "open_buy_order_exists"
        if side is OrderSide.SELL:
            lot_id = str(manual.get("lot_id") or "")
            lot = self.lot_manager.lots.get(lot_id)
            if lot is None:
                return "lot_not_found"
            if lot.remaining_quantity <= 0 or lot.status == "CLOSED":
                return "closed_lot"
            if int(manual.get("quantity") or 0) > lot.remaining_quantity:
                return "quantity_exceeds_remaining"
            if self.store.has_open_order(position.code, OrderSide.SELL, lot_id):
                return "open_sell_order_exists"
        return ""

    def manual_lot_sizing_block_reason(self, manual: dict[str, object], position: PositionState, current_price: int, action: StrategyAction) -> str:
        if action.side is not OrderSide.BUY or self.config.strategy.lot_sizing_mode != "cycle_locked_by_entry_price":
            return ""
        open_lots = self.lot_manager.open_lots(position.code)
        if open_lots:
            sizing = self.strategy.ensure_lot_sizing(position, current_price)
            open_lot_count = len(open_lots)
        else:
            sizing = self.strategy.lot_sizing_for_new_cycle(current_price)
            open_lot_count = 0
        amount = int(action.amount or manual.get("amount") or 0)
        quantity = int(manual.get("quantity") or 0)
        next_amount = amount or (quantity * current_price if quantity > 0 else int(sizing.get("lot_unit_amount") or 0))
        changed = self.manual_lot_sizing_changed_after_preview(manual, sizing)
        if changed:
            return changed
        return self.strategy.lot_sizing_buy_block_reason(position, current_price, sizing, next_buy_amount=next_amount, open_lot_count=open_lot_count)

    def manual_lot_sizing_changed_after_preview(self, manual: dict[str, object], actual_sizing: dict[str, object]) -> str:
        preview_raw = str(manual.get("preview_json") or "")
        if not preview_raw:
            return ""
        try:
            preview = json.loads(preview_raw)
        except json.JSONDecodeError:
            return ""
        preview_bucket = str(preview.get("price_lot_band") or preview.get("lot_sizing_bucket") or "")
        if not preview_bucket:
            return ""
        actual_bucket = str(actual_sizing.get("lot_sizing_bucket") or "")
        preview_lot_unit = int(preview.get("lot_unit_amount") or 0)
        actual_lot_unit = int(actual_sizing.get("lot_unit_amount") or 0)
        preview_max_amount = int(preview.get("max_symbol_amount") or 0)
        actual_max_amount = int(actual_sizing.get("max_symbol_amount") or 0)
        if preview_bucket != actual_bucket or preview_lot_unit != actual_lot_unit or preview_max_amount != actual_max_amount:
            return "lot_sizing_changed_after_preview"
        return ""

    def manual_action_from_request(self, manual: dict[str, object], current_price: int) -> StrategyAction:
        side = OrderSide(str(manual["side"]))
        if side is OrderSide.BUY:
            amount = int(manual.get("amount") or 0)
            quantity = int(manual.get("quantity") or 0)
            if amount <= 0 and quantity > 0:
                amount = quantity * current_price
            if amount <= 0 and self.config.strategy.lot_sizing_mode == "cycle_locked_by_entry_price":
                if self.lot_manager.open_lots(str(manual["code"]).zfill(6)):
                    sizing = self.strategy.ensure_lot_sizing(self.position_manager.get(str(manual["code"]).zfill(6)), current_price)
                else:
                    sizing = self.strategy.lot_sizing_for_new_cycle(current_price)
                amount = int(sizing.get("lot_unit_amount") or 0)
            return StrategyAction(OrderSide.BUY, amount, None, "local_ui_manual")
        lot_id = str(manual.get("lot_id") or "")
        quantity = int(manual.get("quantity") or 0)
        lot = self.lot_manager.lots.get(lot_id)
        if lot is not None and quantity <= 0:
            quantity = lot.remaining_quantity
        return StrategyAction(OrderSide.SELL, 0, quantity, "local_ui_manual", lot_id=lot_id, target_lot=lot, sell_reason=SellReason.MANUAL_SYNC.value)

    def reconcile_open_orders(self) -> None:
        for fill in self.order_manager.reconcile_open_orders():
            self.apply_reconciled_fill(fill)

    def reconcile_recent_executions_on_startup(self) -> None:
        if self._startup_recent_executions_reconciled:
            return
        self._startup_recent_executions_reconciled = True
        if not self.config.order.live_trading or not self.config.order.reconcile_recent_executions_on_startup:
            return
        for fill in self.order_manager.reconcile_recent_executions():
            self.apply_reconciled_fill(fill)

    def apply_reconciled_fill(self, fill) -> None:
        updated = self.position_manager.apply_fill(fill)
        self.store.save_position(updated)
        self.store.save_lots(self.lot_manager.lots.values())
        self.logger.info(
            "reconcile_fill_applied code=%s side=%s qty=%s price=%s lot_id=%s order_id=%s execution_id=%s",
            fill.code,
            fill.side.value,
            fill.quantity,
            fill.price,
            fill.lot_id,
            fill.order_id,
            fill.execution_id,
        )

    def evaluate(self, position: PositionState, snapshot: AccountSnapshot, account_risk, profile: LoopProfile | None = None) -> None:
        profile = profile or self._active_loop_profile
        if not in_trade_window(self.config):
            self.logger.info("trade_blocked code=%s name=%s reason=outside_trade_window", position.code, position.name)
            return
        if profile:
            with profile.stage("quote_fetch"):
                scan_quote = self.client.quote(position.code, name=position.name)
        else:
            scan_quote = self.client.quote(position.code, name=position.name)
        current_price = scan_quote.price
        scan_samples = (scan_quote,)
        if current_price <= 0:
            self.logger.info("trade_blocked code=%s price=%s reason=current_price_lookup_failed", position.code, current_price)
            return
        if profile:
            with profile.stage("lot_manager"):
                position = self.position_manager.refresh_from_lots(position.code, current_price)
        else:
            position = self.position_manager.refresh_from_lots(position.code, current_price)
        if self.strategy.update_reentry_tracking(position, current_price):
            if profile:
                with profile.stage("db"):
                    self.store.save_position(position)
            else:
                self.store.save_position(position)
        with profile.stage("strategy_decision") if profile else _null_stage():
            symbol_risk = self.risk_manager.symbol_buy_allowed(position)
            action = self.strategy.decide(position, current_price, snapshot, account_risk, symbol_risk)
        portfolio_preview = ""
        final_block_reason = ""
        action_created = bool(action)
        final_samples = scan_samples
        if action is not None:
            with profile.stage("quote_fetch") if profile else _null_stage():
                stable_samples = self.price_sampler.sample(position.code, position.name)
                stable, stable_reason = self.price_sampler.stable(stable_samples, self.config.risk.max_price_sample_volatility_pct)
            final_samples = stable_samples
            stable_price = stable_samples[-1].price if stable_samples else current_price
            if not stable:
                final_block_reason = stable_reason
                current_price = stable_price
            else:
                with profile.stage("quote_fetch") if profile else _null_stage():
                    final_quote = self.client.quote(position.code, name=position.name)
                final_samples = (*stable_samples, final_quote)
                current_price = final_quote.price
                if current_price <= 0:
                    final_block_reason = "final_quote_missing"
                else:
                    with profile.stage("lot_manager") if profile else _null_stage():
                        position = self.position_manager.refresh_from_lots(position.code, current_price)
                    with profile.stage("strategy_decision") if profile else _null_stage():
                        final_account_risk = self.risk_manager.account_buy_allowed(snapshot, self.position_manager.positions)
                        final_symbol_risk = self.risk_manager.symbol_buy_allowed(position)
                        final_action = self.strategy.decide(position, current_price, snapshot, final_account_risk, final_symbol_risk)
                    if final_action is None:
                        final_block_reason = "final_quote_action_cleared"
                    elif final_action.side is not action.side:
                        final_block_reason = "final_quote_action_changed"
                    else:
                        action = final_action
                        account_risk = final_account_risk
                        symbol_risk = final_symbol_risk
            with profile.stage("order_guard") if profile else _null_stage():
                portfolio_preview = self.portfolio_buy_block_reason(position, action) if action and not final_block_reason else ""
                guard_reason = self.pre_request_block_reason(position, action, portfolio_preview) if action and not final_block_reason else ""
                final_block_reason = final_block_reason or guard_reason
        next_skip_reason = final_block_reason or portfolio_preview
        if position.skip_reason != next_skip_reason:
            position.skip_reason = next_skip_reason
            if profile:
                with profile.stage("db"):
                    self.store.save_position(position)
            else:
                self.store.save_position(position)
        with profile.stage("decision_logging") if profile else _null_stage():
            self.log_symbol_decision(
                position,
                current_price,
                snapshot,
                account_risk,
                symbol_risk,
                action.reason if action else "NONE",
                portfolio_preview,
                final_block_reason,
                action_created,
                samples=final_samples,
            )
        if action is None:
            return
        if final_block_reason:
            self.log_pre_request_block(position, final_block_reason)
            return
        interrupt = self.runtime_interrupt_reason(f"before_auto_submit_{position.code}")
        if interrupt:
            self.log_pre_request_block(position, interrupt)
            return
        request = self.order_manager.build_request(position, action, current_price)
        if request is None:
            self.logger.info("trade_blocked code=%s reason=quantity_below_one", position.code)
            return
        if request.side is OrderSide.BUY and snapshot.cash_available < request.quantity * request.limit_price:
            self.logger.info("trade_blocked code=%s reason=insufficient_cash", position.code)
            return
        result, fill = self.order_manager.submit_and_confirm(request)
        if fill is None:
            self.logger.info("order_not_filled code=%s order_id=%s status=%s", position.code, result.order_id, result.status.value)
            return
        updated = self.position_manager.apply_fill(fill)
        self.store.save_position(updated)
        self.store.save_lots(self.lot_manager.lots.values())
        self.logger.info("fill_applied code=%s side=%s qty=%s price=%s lot_id=%s order_id=%s", fill.code, fill.side.value, fill.quantity, fill.price, fill.lot_id, fill.order_id)

    def log_symbol_decision(
        self,
        position: PositionState,
        current_price: int,
        snapshot: AccountSnapshot | None,
        account_risk,
        symbol_risk,
        action: str,
        portfolio_risk_block_reason: str = "",
        final_block_reason: str = "",
        action_created: bool = False,
        *,
        samples: Sequence[Quote] = (),
    ) -> None:
        last_lot = self.lot_manager.last_buy_lot(position.code)
        last_lot_drop = (current_price - last_lot.buy_price) / last_lot.buy_price * 100.0 if last_lot else 0.0
        lots = self.lot_manager.open_lots(position.code)
        context = self.strategy.context(position, current_price, snapshot)
        lot_summary = ";".join(
            f"{lot.lot_id}:{lot.buy_price}->{lot.target_sell_price}:{lot.profit_pct_at(current_price):.2f}%:{lot.remaining_quantity}"
            for lot in lots
        )
        avg_profit_pct = (current_price - position.average_price) / position.average_price * 100.0 if position.average_price else 0.0
        price_context = self.price_context(position, current_price, samples)
        decision_data = dict(
            config_hash=self.config_hash,
            run_id=self.run_id,
            experiment_name=self.experiment_name,
            risk_profile=self.config.risk.profile,
            lot_sizing_mode=self.config.strategy.lot_sizing_mode,
            code=position.code,
            name=position.name,
            current_price=current_price,
            sampled_price_source=price_context["source"],
            sampled_at=price_context["sampled_at"],
            previous_close=price_context["previous_close"] or "",
            day_open=price_context["day_open"] or "",
            day_high=price_context["day_high"] or "",
            day_low=price_context["day_low"] or "",
            volume=price_context["volume"] or "",
            trading_value=price_context["trading_value"] or "",
            bid_price=price_context["bid_price"] or "",
            ask_price=price_context["ask_price"] or "",
            spread=price_context["spread"] or "",
            spread_rate=price_context["spread_rate"] or "",
            price_context_available=price_context["available"],
            price_context_source=price_context["source"],
            price_context_missing_fields=",".join(price_context["missing_fields"]),
            price_context_error=price_context["error"],
            price_context_collected_at=price_context["collected_at"],
            position_state=context.position_state,
            position_pnl_rate=f"{context.position_pnl_rate:.4f}",
            pnl_mode=context.pnl_mode,
            average_price=f"{position.average_price:.2f}",
            exposure=position.cumulative_invested_amount,
            accumulated_invested_amount=position.cumulative_invested_amount,
            lowest_open_buy_lot_price=context.lowest_open_buy_lot_price,
            highest_open_buy_lot_price=context.highest_open_buy_lot_price,
            open_lot_vwap_buy_price=context.open_lot_vwap_buy_price,
            median_open_buy_price=context.median_open_buy_price,
            reference_buy_price=context.reference_buy_price,
            reference_buy_source=context.reference_buy_source,
            entry_price_for_lot_sizing=context.entry_price_for_lot_sizing,
            lot_unit_amount=context.lot_unit_amount,
            max_symbol_amount=context.max_symbol_amount,
            max_lots_per_symbol=context.max_lots_per_symbol,
            lot_sizing_bucket=context.lot_sizing_bucket,
            lot_sizing_locked=context.lot_sizing_locked,
            lot_sizing_locked_at=context.lot_sizing_locked_at,
            lot_sizing_skip_reason=context.lot_sizing_skip_reason,
            add_buy_lot_band=context.add_buy_lot_band,
            add_buy_drop_rate=f"{context.target_buy_drop_rate:.4f}",
            current_open_lot_count=context.current_open_lot_count,
            original_lot_base_target_profit_rate=f"{context.original_lot_base_target_profit_rate:.4f}",
            current_base_target_profit_rate=f"{context.current_base_target_profit_rate:.4f}",
            target_profit_source=context.target_profit_source,
            target_profit_lot_band=context.target_profit_lot_band,
            effective_target_profit_rate=f"{context.effective_target_profit_rate:.4f}",
            lot_age_weeks=f"{context.lot_age_weeks:.2f}",
            age_decay_rate=f"{context.age_decay_rate:.4f}",
            reference_sell_price=context.reference_sell_price,
            target_buy_drop_rate=f"{context.target_buy_drop_rate:.4f}",
            target_profit_rate=f"{context.target_profit_rate:.4f}",
            buy_condition_met=context.buy_condition_met,
            sell_signal_met=context.sell_signal_met,
            profitable_lots=context.profitable_lots,
            selected_sell_lot_id=context.selected_sell_lot_id,
            last_buy_lot_price=last_lot.buy_price if last_lot else 0,
            last_buy_lot_drop_pct=f"{last_lot_drop:.2f}",
            lot_profit_targets=lot_summary or "NONE",
            avg_profit_pct=f"{avg_profit_pct:.2f}",
            buy_risk="OK" if account_risk.allowed and symbol_risk.allowed else "|".join(account_risk.reasons + symbol_risk.reasons),
            sellable_lot_count=len(self.lot_manager.sellable_lots(position.code, current_price, position.cumulative_invested_amount)),
            needs_review=position.needs_review,
            realized_pnl=position.realized_profit_loss,
            unrealized_pnl=position.unrealized_profit_loss,
            total_pnl=position.total_profit_loss,
            last_sell_price=position.last_sell_price,
            reentry_anchor_price=position.reentry_anchor_price,
            reentry_condition_met=context.reentry_condition_met,
            reentry_type=context.reentry_type,
            exit_anchor_price=context.exit_anchor_price,
            cycle_highest_sell_price=context.cycle_highest_sell_price,
            cycle_last_sell_price=context.cycle_last_sell_price,
            cycle_sell_vwap_price=context.cycle_sell_vwap_price,
            cycle_sell_median_price=context.cycle_sell_median_price,
            normal_exit_anchor_price=context.normal_exit_anchor_price,
            trailing_exit_anchor_price=context.trailing_exit_anchor_price,
            normal_reentry_drop_rate=f"{self.config.strategy.normal_reentry_drop_rate:.4f}",
            trailing_activation_gain=f"{self.config.strategy.trailing_activation_gain:.4f}",
            trailing_reentry_drop_rate=f"{self.config.strategy.trailing_reentry_drop_rate:.4f}",
            cycle_sell_fill_count=context.cycle_sell_fill_count,
            anchor_single_fill=context.anchor_single_fill,
            anchor_confidence=context.anchor_confidence,
            post_exit_high_price=context.post_exit_high_price,
            normal_reentry_condition_met=context.normal_reentry_condition_met,
            trailing_reentry_condition_met=context.trailing_reentry_condition_met,
            sell_reason=context.sell_reason,
            realized_pnl_rate=f"{context.realized_pnl_rate:.4f}",
            net_realized_pnl=context.net_realized_pnl,
            cleanup_candidate=context.cleanup_candidate,
            cleanup_loss_budget=context.cleanup_loss_budget,
            expected_cleanup_loss=context.expected_cleanup_loss,
            cleanup_allowed=context.cleanup_allowed,
            cleanup_enabled=self.config.strategy.cleanup_enabled,
            stale_lot_loss_rate=f"{self.config.strategy.stale_lot_loss_rate:.4f}",
            stale_lot_min_age_weeks=self.config.strategy.stale_lot_min_age_weeks,
            review_symbol_loss_rate=f"{self.config.strategy.review_symbol_loss_rate:.4f}",
            cleanup_buy_cooldown_until=context.cleanup_buy_cooldown_until,
            cleanup_reentry_cooldown_until=context.cleanup_reentry_cooldown_until,
            profit_take_lot_count=context.profit_take_lot_count,
            cleanup_candidate_lot_count=context.cleanup_candidate_lot_count,
            cleanup_signal_met=context.cleanup_candidate,
            stale_lot_count=context.stale_lot_count,
            stale_lot_ids=context.stale_lot_ids,
            cleanup_candidate_count=context.cleanup_candidate_lot_count,
            review_required_condition_met=context.review_required_condition_met,
            review_reason=context.review_reason,
            skip_reason=context.skip_reason,
            open_order_exists=self.store.has_any_open_order(position.code),
            open_order_count=self.store.open_order_count(position.code),
            active_symbol_count=self.active_symbol_count(),
            new_buy_count_today=self.store.count_today_initial_buy_orders(),
            total_open_lot_count=self.total_open_lot_count(),
            total_invested_amount=self.total_invested_amount(),
            max_active_symbols=self.config.risk.max_active_symbols,
            max_new_buy_per_day=self.config.risk.max_new_buy_per_day,
            max_new_buy_amount_per_day=self.config.risk.max_new_buy_amount_per_day,
            max_total_open_lots=self.config.risk.max_total_open_lots,
            max_total_invested_amount=self.config.risk.max_total_invested_amount,
            portfolio_risk_block_reason=portfolio_risk_block_reason or self.portfolio_buy_block_reason(position, None),
            final_block_reason=final_block_reason,
            action_created=action_created,
            action_blocked_before_request=bool(final_block_reason),
            action_execution_state="blocked_before_request" if final_block_reason else ("pending_request" if action_created else "no_action"),
            risk_block_reasons=self.risk_block_reasons(position),
            sync_status=position.sync_status,
            action=action,
            candidate_action_type=action,
            block_reason=final_block_reason or portfolio_risk_block_reason or context.skip_reason,
        )
        log_decision(self.logger, **decision_data)
        decision_id = self.store.record_decision(decision_data)
        price_context.update(
            {
                "code": position.code,
                "current_price": current_price,
                "run_id": self.run_id,
                "config_hash": self.config_hash,
                "decision_id": decision_id,
            }
        )
        snapshot_id = self.store.record_price_snapshot(price_context)
        self.store.link_decision_price_snapshot(decision_id, snapshot_id)

    def price_context(self, position: PositionState, current_price: int, samples: Sequence[Quote]) -> dict[str, object]:
        sampled_at = samples[-1].timestamp.isoformat(timespec="seconds") if samples else datetime.now().isoformat(timespec="seconds")
        missing = [
            "previous_close",
            "day_open",
            "day_high",
            "day_low",
            "volume",
            "trading_value",
            "bid_price",
            "ask_price",
            "spread",
            "spread_rate",
        ]
        return {
            "sampled_at": sampled_at,
            "current_price": current_price,
            "previous_close": None,
            "day_open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "trading_value": None,
            "bid_price": None,
            "ask_price": None,
            "spread": None,
            "spread_rate": None,
            "source": type(self.client).__name__,
            "available": current_price > 0,
            "missing_fields": missing,
            "error": "" if current_price > 0 else "current_price_missing",
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "raw_json": {"sample_count": len(samples), "name": position.name},
        }

    def pre_request_block_reason(self, position: PositionState, action, portfolio_preview: str = "") -> str:
        """Return the final guard reason that blocks an action before any order request."""
        sync_block = self.sync_required_block_reason(position)
        if sync_block:
            return sync_block
        if position.position_state == PositionLifecycle.RISK_BLOCKED.value or position.danger_state:
            return "risk_blocked"
        if position.position_state == PositionLifecycle.REVIEW_REQUIRED.value or position.needs_review:
            return "review_required"
        partial = self.partial_order_block_reason(position)
        if partial:
            return partial
        duplicate = self.open_order_block_reason(position, action)
        if duplicate:
            return duplicate
        runtime_block = runtime_block_reason(load_runtime_control(), action)
        if runtime_block:
            return runtime_block
        portfolio_block = portfolio_preview or self.portfolio_buy_block_reason(position, action)
        if portfolio_block:
            return portfolio_block
        request_gap = self.recent_order_request_block_reason(position)
        if request_gap:
            return request_gap
        if action.side is OrderSide.BUY:
            cooldown = self.order_cooldown_reason(position)
            if cooldown:
                return cooldown
        return ""

    def log_pre_request_block(self, position: PositionState, reason: str) -> None:
        self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, reason)
        if reason in {"sync_required", "trading_paused"}:
            self.notifier.notify("SYNC_REQUIRED", f"{position.code} {position.name}: manual reconciliation required before trading resumes.")

    def order_cooldown_reason(self, position: PositionState) -> str:
        elapsed = self.store.seconds_since_recent_fill(position.code, OrderSide.BUY)
        if elapsed is not None and elapsed < self.config.order.order_cooldown_seconds:
            return "buy_fill_cooldown"
        return ""

    def sync_required_block_reason(self, position: PositionState) -> str:
        if position.trading_paused:
            return "trading_paused"
        if position.sync_status == "SYNC_REQUIRED":
            return "sync_required"
        return ""

    def partial_order_block_reason(self, position: PositionState) -> str:
        if not self.store.has_partial_order(position.code):
            return ""
        elapsed = self.store.seconds_since_oldest_partial_order(position.code)
        if elapsed is not None and elapsed >= self.config.order.limit_order_timeout_seconds:
            return "partial_order_cancel_or_requery"
        return "partial_order_exists"

    def open_order_block_reason(self, position: PositionState, action) -> str:
        if action.side is OrderSide.SELL and action.sell_reason == SellReason.CLEANUP_SELL.value and self.store.has_any_open_order(position.code):
            position.skip_reason = "open_order_exists_for_cleanup"
            return "open_order_exists_for_cleanup"
        if action.side is OrderSide.BUY and self.store.has_open_order(position.code, OrderSide.BUY):
            return "open_buy_order_exists"
        if action.side is OrderSide.SELL and self.store.has_open_order(position.code, OrderSide.SELL, action.lot_id):
            return "open_sell_order_exists"
        return ""

    def recent_order_request_block_reason(self, position: PositionState) -> str:
        elapsed = self.store.seconds_since_recent_order_request(position.code)
        minimum = self.config.order.min_order_request_interval_seconds
        if elapsed is not None and elapsed < minimum:
            return "recent_order_request"
        return ""

    def portfolio_buy_block_reason(self, position: PositionState, action) -> str:
        if action is not None and action.side is not OrderSide.BUY:
            return ""
        risk = self.config.risk
        total_open_lots = self.total_open_lot_count()
        if total_open_lots >= risk.max_total_open_lots:
            return "max_total_open_lots_reached"
        total_invested = self.total_invested_amount()
        if total_invested >= risk.max_total_invested_amount:
            return "max_total_invested_amount_reached"
        if action is None:
            return ""
        if action.reason == "initial_buy":
            active_codes = self.active_symbol_codes()
            if position.code not in active_codes and len(active_codes) >= risk.max_active_symbols:
                return "max_active_symbols_reached"
            if self.store.count_today_initial_buy_orders() >= risk.max_new_buy_per_day:
                return "max_new_buy_per_day_reached"
            day_amount_limit = risk.max_total_initial_buy_amount_per_day or risk.max_new_buy_amount_per_day
            if day_amount_limit and self.store.sum_today_initial_buy_order_amount() + action.amount > day_amount_limit:
                return "max_new_buy_amount_per_day_reached"
        return ""

    def active_symbol_codes(self) -> set[str]:
        active_states = {
            PositionLifecycle.HOLDING.value,
            PositionLifecycle.WAIT_REENTRY.value,
            PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value,
            PositionLifecycle.REVIEW_REQUIRED.value,
            PositionLifecycle.RISK_BLOCKED.value,
            PositionLifecycle.SYNC_REQUIRED.value,
        }
        codes = {lot.code for lot in self.lot_manager.lots.values() if lot.remaining_quantity > 0}
        codes.update(self.store.open_order_codes())
        codes.update(code for code, position in self.position_manager.positions.items() if position.position_state in active_states)
        return codes

    def active_symbol_count(self) -> int:
        return len(self.active_symbol_codes())

    def total_open_lot_count(self) -> int:
        return sum(1 for lot in self.lot_manager.lots.values() if lot.remaining_quantity > 0)

    def total_invested_amount(self) -> int:
        return sum(lot.open_amount for lot in self.lot_manager.lots.values() if lot.remaining_quantity > 0)

    def risk_block_reasons(self, position: PositionState) -> str:
        flag_names = (
            "trading_halted",
            "administrative_issue",
            "investment_alert",
            "audit_opinion_issue",
            "delisting_risk",
            "accounting_issue",
            "liquidity_warning",
        )
        for stock in self.config.stocks:
            if stock.code == position.code:
                reasons = [name for name in flag_names if getattr(stock, name)]
                return ",".join(reasons) or ("danger_state" if position.danger_state else "")
        return "danger_state" if position.danger_state else ""


def in_trade_window(config: BotConfig) -> bool:
    open_hour, open_minute = [int(part) for part in config.market_hours.open_time.split(":", 1)]
    close_hour, close_minute = [int(part) for part in config.market_hours.close_time.split(":", 1)]
    today = datetime.today()
    start = datetime.combine(today, day_time(open_hour, open_minute)).timestamp() + config.market_hours.block_after_open_minutes * 60
    end = datetime.combine(today, day_time(close_hour, close_minute)).timestamp() - config.market_hours.block_before_close_minutes * 60
    return start <= datetime.now().timestamp() <= end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the KIS lot-based domestic stock auto-trader.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--init-config", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mock", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.init_config:
        write_default_config(args.config)
        print(f"created sample config: {args.config}")
        return 0
    config = load_config(args.config)
    trader = AutoTrader(config, use_mock_client=args.mock)
    loop_count = 0
    while True:
        try:
            runtime = load_runtime_control()
            if runtime.config_reload_requested:
                config = load_config(args.config)
                trader.reload_config(config, use_mock_client=args.mock)
                save_runtime_control(
                    RuntimeControl(
                        bot_paused=runtime.bot_paused,
                        all_orders_paused=runtime.all_orders_paused,
                        buy_paused=runtime.buy_paused,
                        sell_paused=runtime.sell_paused,
                        cleanup_paused=runtime.cleanup_paused,
                        reentry_paused=runtime.reentry_paused,
                        reason="config_reloaded",
                        updated_by=runtime.updated_by,
                        expires_at=runtime.expires_at,
                    ),
                    DEFAULT_RUNTIME_CONTROL_PATH,
                )
                trader.logger.info("runtime_config_reloaded config=%s", args.config)
                runtime = load_runtime_control()
            if runtime.bot_paused:
                trader.logger.info("bot_loop_paused reason=%s", runtime.reason)
            else:
                run_status = trader.run_once()
                if run_status == "config_reload_requested":
                    continue
        except Exception as error:  # noqa: BLE001
            trader.risk_manager.data_mismatch_detected = True
            trader.logger.exception("auto_trader_loop_failed error_type=%s error=%s", type(error).__name__, error)
            trader.notifier.notify("auto-trader error", f"{type(error).__name__}: {error}")
        loop_count += 1
        if args.once or (config.max_loop_count is not None and loop_count >= config.max_loop_count):
            break
        responsive_sleep(trader, config.loop_interval_seconds)
    return 0


def responsive_sleep(trader: AutoTrader, seconds: int) -> None:
    """Sleep in short slices so runtime controls and manual requests wake the loop promptly."""
    deadline = time.monotonic() + max(0, seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        runtime = load_runtime_control()
        if runtime.config_reload_requested:
            return
        if runtime.bot_paused:
            time.sleep(min(1.0, remaining))
            continue
        if trader.store.manual_order_requests("REQUESTED"):
            trader.logger.info("loop_wakeup reason=manual_order_request")
            return
        time.sleep(min(1.0, remaining))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
