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
from .models import AccountSnapshot, OrderSide, PositionState
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
                self.notifier.notify("lot/account mismatch", "Lot quantity differs from KIS account balance. New buys are blocked.")
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
        snapshot = self.startup_sync()
        account_risk = self.risk_manager.account_buy_allowed(snapshot, self.position_manager.positions)
        for stock in self.config.stocks:
            if not stock.enabled:
                continue
            position = self.position_manager.get(stock.code, stock.name)
            if stock.danger_state:
                position.danger_state = True
                position.auto_buy_enabled = False
            self.evaluate(position, snapshot, account_risk)
        self.store.save_positions(self.position_manager.positions.values())
        self.store.save_lots(self.lot_manager.lots.values())

    def evaluate(self, position: PositionState, snapshot: AccountSnapshot, account_risk) -> None:
        ban = self.trade_ban_reason(position)
        if ban:
            self.logger.info("trade_blocked code=%s name=%s reason=%s", position.code, position.name, ban)
            return
        samples = self.price_sampler.sample(position.code, position.name)
        stable, stable_reason = self.price_sampler.stable(samples, self.config.risk.max_price_sample_volatility_pct)
        current_price = samples[-1].price if samples else 0
        if not stable:
            self.logger.info("trade_blocked code=%s price=%s reason=%s", position.code, current_price, stable_reason)
            return
        position = self.position_manager.refresh_from_lots(position.code, current_price)
        symbol_risk = self.risk_manager.symbol_buy_allowed(position)
        action = self.strategy.decide(position, current_price, snapshot, account_risk, symbol_risk)
        self.log_symbol_decision(position, current_price, account_risk, symbol_risk, action.reason if action else "NONE")
        if action is None:
            return
        request = self.order_manager.build_request(position, action, current_price)
        if request is None:
            self.logger.info("trade_blocked code=%s reason=quantity_below_one", position.code)
            return
        if request.side is OrderSide.BUY and snapshot.cash_available < request.quantity * request.limit_price:
            self.logger.info("trade_blocked code=%s reason=insufficient_cash", position.code)
            return
        result, fill = self.order_manager.submit_and_confirm(request)
        self.position_manager.mark_order_requested(position.code, result.order_id, result.status.value)
        self.store.save_position(self.position_manager.get(position.code))
        if fill is None:
            self.logger.info("order_not_filled code=%s order_id=%s status=%s", position.code, result.order_id, result.status.value)
            return
        updated = self.position_manager.apply_fill(fill)
        self.store.save_position(updated)
        self.store.save_lots(self.lot_manager.lots.values())
        self.logger.info("fill_applied code=%s side=%s qty=%s price=%s lot_id=%s order_id=%s", fill.code, fill.side.value, fill.quantity, fill.price, fill.lot_id, fill.order_id)

    def log_symbol_decision(self, position: PositionState, current_price: int, account_risk, symbol_risk, action: str) -> None:
        last_lot = self.lot_manager.last_buy_lot(position.code)
        last_lot_drop = (current_price - last_lot.buy_price) / last_lot.buy_price * 100.0 if last_lot else 0.0
        lots = self.lot_manager.open_lots(position.code)
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
            average_price=f"{position.average_price:.2f}",
            exposure=position.cumulative_invested_amount,
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
            action=action,
        )

    def trade_ban_reason(self, position: PositionState) -> str:
        if not in_trade_window(self.config):
            return "outside_trade_window"
        if position.last_order_time:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(position.last_order_time)).total_seconds()
            except ValueError:
                elapsed = self.config.order.order_cooldown_seconds
            if elapsed < self.config.order.order_cooldown_seconds:
                return "order_cooldown"
        return ""


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
