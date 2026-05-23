"""Lot-based domestic stock auto-trader entry point."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, time as day_time
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, BotConfig, load_config, write_default_config
from .kis_client import KisClient, MockKisClient
from .logger import configure_trade_logger, log_decision
from .lot_manager import LotManager
from .models import AccountSnapshot, OrderSide, PositionState, SellReason
from .models import PositionLifecycle
from .notifier import LogNotifier
from .order_manager import OrderManager
from .position_manager import PositionManager
from .price_provider import PriceSampler
from .risk_manager import RiskManager
from .storage import StateStore
from .strategy import LotGridStrategy
from .upstream_watcher import UpstreamWatcher


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUOTE_CSV = PROJECT_ROOT / "data" / "kospi_current_quotes.csv"


class AutoTrader:
    def __init__(self, config: BotConfig, *, use_mock_client: bool = False) -> None:
        self.config = config
        self.logger = configure_trade_logger(config.log_path)
        self.store = StateStore(config.storage_path)
        self.lot_manager = LotManager(config.strategy, self.store.load_lots())
        self.position_manager = PositionManager(config.strategy, self.lot_manager, self.store.load_positions())
        self.risk_manager = RiskManager(config)
        self.strategy = LotGridStrategy(config, self.lot_manager)
        self.notifier = LogNotifier(self.logger)
        self.upstream_watcher = UpstreamWatcher(config.upstream_watch, self.notifier)
        self.client = MockKisClient(DEFAULT_QUOTE_CSV) if use_mock_client or not config.order.live_trading else KisClient(config.kis_account)
        self.price_sampler = PriceSampler(self.client, config.order.price_sample_count, config.order.price_sample_interval_seconds)
        self.order_manager = OrderManager(config, self.client, self.store, self.logger)

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

    def run_once(self) -> None:
        self.upstream_watcher.tick()
        self.reconcile_open_orders()
        snapshot = self.startup_sync()
        account_risk = self.risk_manager.account_buy_allowed(snapshot, self.position_manager.positions)
        for stock in self.config.stocks:
            if not stock.enabled:
                continue
            position = self.position_manager.get(stock.code, stock.name)
            if stock.danger_state:
                position.danger_state = True
                position.position_state = PositionLifecycle.RISK_BLOCKED.value
                position.auto_buy_enabled = False
            self.evaluate(position, snapshot, account_risk)
        self.store.save_positions(self.position_manager.positions.values())
        self.store.save_lots(self.lot_manager.lots.values())

    def reconcile_open_orders(self) -> None:
        for fill in self.order_manager.reconcile_open_orders():
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

    def evaluate(self, position: PositionState, snapshot: AccountSnapshot, account_risk) -> None:
        if not in_trade_window(self.config):
            self.logger.info("trade_blocked code=%s name=%s reason=outside_trade_window", position.code, position.name)
            return
        samples = self.price_sampler.sample(position.code, position.name)
        stable, stable_reason = self.price_sampler.stable(samples, self.config.risk.max_price_sample_volatility_pct)
        current_price = samples[-1].price if samples else 0
        if not stable:
            self.logger.info("trade_blocked code=%s price=%s reason=%s", position.code, current_price, stable_reason)
            return
        position = self.position_manager.refresh_from_lots(position.code, current_price)
        if self.strategy.update_reentry_tracking(position, current_price):
            self.store.save_position(position)
        symbol_risk = self.risk_manager.symbol_buy_allowed(position)
        action = self.strategy.decide(position, current_price, snapshot, account_risk, symbol_risk)
        self.log_symbol_decision(position, current_price, snapshot, account_risk, symbol_risk, action.reason if action else "NONE")
        if action is None:
            return
        sync_block = self.sync_required_block_reason(position)
        if sync_block:
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, sync_block)
            self.notifier.notify("SYNC_REQUIRED", f"{position.code} {position.name}: manual reconciliation required before trading resumes.")
            return
        partial = self.partial_order_block_reason(position)
        if partial:
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, partial)
            return
        duplicate = self.open_order_block_reason(position, action)
        if duplicate:
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, duplicate)
            return
        portfolio_block = self.portfolio_buy_block_reason(position, action)
        if portfolio_block:
            position.skip_reason = portfolio_block
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, portfolio_block)
            return
        request_gap = self.recent_order_request_block_reason(position)
        if request_gap:
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, request_gap)
            return
        if action.side is OrderSide.BUY:
            cooldown = self.order_cooldown_reason(position)
            if cooldown:
                self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, cooldown)
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

    def log_symbol_decision(self, position: PositionState, current_price: int, snapshot: AccountSnapshot | None, account_risk, symbol_risk, action: str) -> None:
        last_lot = self.lot_manager.last_buy_lot(position.code)
        last_lot_drop = (current_price - last_lot.buy_price) / last_lot.buy_price * 100.0 if last_lot else 0.0
        lots = self.lot_manager.open_lots(position.code)
        context = self.strategy.context(position, current_price, snapshot)
        lot_summary = ";".join(
            f"{lot.lot_id}:{lot.buy_price}->{lot.target_sell_price}:{lot.profit_pct_at(current_price):.2f}%:{lot.remaining_quantity}"
            for lot in lots
        )
        avg_profit_pct = (current_price - position.average_price) / position.average_price * 100.0 if position.average_price else 0.0
        log_decision(
            self.logger,
            code=position.code,
            name=position.name,
            current_price=current_price,
            position_state=context.position_state,
            position_pnl_rate=f"{context.position_pnl_rate:.4f}",
            pnl_mode=context.pnl_mode,
            average_price=f"{position.average_price:.2f}",
            exposure=position.cumulative_invested_amount,
            accumulated_invested_amount=position.cumulative_invested_amount,
            lowest_open_buy_lot_price=context.lowest_open_buy_lot_price,
            highest_open_buy_lot_price=context.highest_open_buy_lot_price,
            reference_buy_price=context.reference_buy_price,
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
            max_total_open_lots=self.config.risk.max_total_open_lots,
            max_total_invested_amount=self.config.risk.max_total_invested_amount,
            portfolio_risk_block_reason=self.portfolio_buy_block_reason(position, None),
            sync_status=position.sync_status,
            action=action,
        )

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
            trader.run_once()
        except Exception as error:  # noqa: BLE001
            trader.risk_manager.data_mismatch_detected = True
            trader.notifier.notify("auto-trader error", f"{type(error).__name__}: {error}")
        loop_count += 1
        if args.once or (config.max_loop_count is not None and loop_count >= config.max_loop_count):
            break
        time.sleep(config.loop_interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
