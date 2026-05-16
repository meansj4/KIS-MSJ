"""Entry point for the risk-limited domestic stock auto-trader."""

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
from .models import AccountSnapshot, OrderSide, PositionState
from .notifier import LogNotifier
from .order_manager import OrderManager
from .position_manager import PositionManager
from .risk_manager import RiskManager
from .storage import StateStore
from .strategy import GridStrategy
from .upstream_watcher import UpstreamWatcher


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUOTE_CSV = PROJECT_ROOT / "data" / "kospi_current_quotes.csv"


class AutoTrader:
    def __init__(self, config: BotConfig, *, use_mock_client: bool = False) -> None:
        self.config = config
        self.logger = configure_trade_logger(config.log_path)
        self.store = StateStore(config.storage_path)
        self.position_manager = PositionManager(self.store.load_positions())
        self.risk_manager = RiskManager(config)
        self.strategy = GridStrategy(config)
        self.notifier = LogNotifier(self.logger)
        self.upstream_watcher = UpstreamWatcher(config.upstream_watch, self.notifier)
        if use_mock_client or not config.order.live_trading:
            self.client = MockKisClient(DEFAULT_QUOTE_CSV)
        else:
            self.client = KisClient(config.kis_account)
        self.order_manager = OrderManager(config, self.client, self.store, self.logger)  # type: ignore[arg-type]

    def startup_sync(self) -> AccountSnapshot:
        snapshot = self.client.account_snapshot()
        if not self.config.order.live_trading:
            return snapshot
        self.position_manager.sync_account(snapshot)
        self.risk_manager.state_mismatch_detected = self.position_manager.state_mismatch_detected
        if self.position_manager.state_mismatch_detected:
            self.notifier.notify("state mismatch synchronized", "Actual account balance was used as the source of truth.")
        open_orders = self.client.open_orders()
        if open_orders:
            message = f"open_order_count={len(open_orders)}"
            if self.config.order.cancel_unfilled_on_start:
                self.logger.warning("%s cancel_unfilled_on_start=true manual cancel not implemented for unknown rows", message)
            else:
                self.logger.warning("%s buy orders blocked until unresolved orders are reviewed", message)
                self.risk_manager.state_mismatch_detected = True
        self.store.save_positions(self.position_manager.positions.values())
        return snapshot

    def run_once(self) -> None:
        self.upstream_watcher.tick()
        snapshot = self.startup_sync()
        account_risk = self.risk_manager.account_buy_allowed(snapshot, self.position_manager.positions)
        for stock in self.config.stocks:
            if not stock.enabled:
                continue
            position = self.position_manager.get(stock.code, stock.name)
            self._evaluate_symbol(position, snapshot, account_risk)
        self.store.save_positions(self.position_manager.positions.values())

    def _evaluate_symbol(self, position: PositionState, snapshot: AccountSnapshot, account_risk) -> None:
        ban_reason = self._trade_ban_reason(position)
        if ban_reason:
            self.logger.info("trade_blocked code=%s reason=%s", position.code, ban_reason)
            return
        samples = self.order_manager.sample_prices(position.code, position.name)
        stable, stable_reason = self.order_manager.prices_are_stable(samples)
        current_price = samples[-1].price if samples else 0
        if not stable:
            self.logger.info("trade_blocked code=%s price=%s reason=%s", position.code, current_price, stable_reason)
            return

        self._refresh_unrealized(position, current_price, snapshot.total_profit_loss_pct)
        symbol_risk = self.risk_manager.symbol_buy_allowed(position)
        action = self.strategy.decide(position, current_price, snapshot, account_risk, symbol_risk)
        reference = position.reference_price(self.config.strategy.reference_price_mode)
        log_decision(
            self.logger,
            code=position.code,
            price=current_price,
            average_price=f"{position.average_price:.2f}",
            reference_price=f"{reference:.2f}",
            quantity=position.quantity,
            pnl_pct=f"{position.position_profit_loss_pct:.2f}",
            add_buy_stage=position.add_buy_stage,
            account_risk="OK" if account_risk.allowed else "|".join(account_risk.reasons),
            symbol_risk="OK" if symbol_risk.allowed else "|".join(symbol_risk.reasons),
            action=action.reason if action else "NONE",
        )
        if action is None:
            return
        request = self.order_manager.build_request(position, action, current_price)
        if request is None:
            self.logger.info("trade_blocked code=%s reason=quantity_below_one action=%s", position.code, action.reason)
            return
        if action.side is OrderSide.BUY and snapshot.cash_available < request.quantity * request.limit_price:
            self.logger.info("trade_blocked code=%s reason=insufficient_cash", position.code)
            return
        result, fill = self.order_manager.submit_and_confirm(request)
        self.position_manager.mark_order_requested(position.code, result.order_id, result.status.value)
        if fill is None:
            self.logger.info("order_not_filled code=%s order_id=%s status=%s", position.code, result.order_id, result.status.value)
            return
        updated = self.position_manager.apply_fill(fill, snapshot.total_profit_loss_pct)
        self._advance_stage(updated, request.reason)
        self.store.save_position(updated)
        self.logger.info(
            "fill_applied code=%s side=%s qty=%s price=%s order_id=%s reason=%s",
            fill.code,
            fill.side.value,
            fill.quantity,
            fill.price,
            fill.order_id,
            request.reason,
        )

    def _trade_ban_reason(self, position: PositionState) -> str:
        if not _in_trade_window(self.config):
            return "outside_trade_window"
        if position.last_order_time:
            try:
                last_order_time = datetime.fromisoformat(position.last_order_time)
            except ValueError:
                last_order_time = datetime.min
            seconds = (datetime.now() - last_order_time).total_seconds()
            if seconds < self.config.order.order_cooldown_seconds and position.position_profit_loss_pct > self.config.risk.half_stop_loss_pct:
                return "order_cooldown"
        return ""

    def _refresh_unrealized(self, position: PositionState, current_price: int, account_profit_loss_pct: float) -> None:
        if position.quantity > 0 and position.average_price > 0:
            position.unrealized_profit_loss = int(round((current_price - position.average_price) * position.quantity))
            position.position_profit_loss_pct = (current_price - position.average_price) / position.average_price * 100.0
        position.account_profit_loss_pct = account_profit_loss_pct
        position.last_update_time = datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _advance_stage(position: PositionState, reason: str) -> None:
        if reason.startswith("add_buy_stage_"):
            position.add_buy_stage = max(position.add_buy_stage, int(reason.rsplit("_", 1)[-1]))
        if reason.startswith("take_profit_stage_"):
            position.sell_stage = max(position.sell_stage, int(reason.rsplit("_", 1)[-1]))


def _in_trade_window(config: BotConfig) -> bool:
    now = datetime.now().time()
    open_hour, open_minute = [int(part) for part in config.market_hours.open_time.split(":", 1)]
    close_hour, close_minute = [int(part) for part in config.market_hours.close_time.split(":", 1)]
    open_dt = datetime.combine(datetime.today(), day_time(open_hour, open_minute))
    close_dt = datetime.combine(datetime.today(), day_time(close_hour, close_minute))
    start = open_dt.timestamp() + config.market_hours.block_after_open_minutes * 60
    end = close_dt.timestamp() - config.market_hours.block_before_close_minutes * 60
    current = datetime.combine(datetime.today(), now).timestamp()
    return start <= current <= end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the KIS domestic stock risk-limited auto-trader.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="JSON config path")
    parser.add_argument("--init-config", action="store_true", help="Write a conservative sample config and exit")
    parser.add_argument("--once", action="store_true", help="Run one decision loop and exit")
    parser.add_argument("--mock", action="store_true", help="Force offline mock client")
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
        except Exception as error:  # noqa: BLE001 - trading loop must fail closed.
            trader.risk_manager.state_mismatch_detected = True
            trader.notifier.notify("auto-trader error", f"{type(error).__name__}: {error}")
        loop_count += 1
        if args.once or (config.max_loop_count is not None and loop_count >= config.max_loop_count):
            break
        time.sleep(config.loop_interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
