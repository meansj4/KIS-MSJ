"""Adaptive first entries share the existing portfolio and daily risk limits."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StrategyConfig
from kis_msj.main import AutoTrader
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState
from kis_msj.strategy import StrategyAction
from kis_msj.ui_service import UIService


def trader(tmp_path, **risk):
    config = BotConfig(
        strategy=StrategyConfig(adaptive_lot_enabled=True),
        risk=RiskConfig(**risk),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
    )
    return AutoTrader(config, use_mock_client=True)


def record(bot, reason, number, *, amount=10_000, status=OrderStatus.REQUESTED, when=None, side=OrderSide.BUY):
    result = OrderResult(
        OrderRequest("000001", "Test", side, 1, amount, reason),
        number, status,
        requested_at=(when or datetime.now()).isoformat(),
    )
    if when is not None:
        with patch("kis_msj.storage.datetime") as clock:
            clock.now.return_value = when
            bot.store.record_order(result)
    else:
        bot.store.record_order(result)


def test_adaptive_initial_entry_respects_active_symbol_cap(tmp_path):
    bot = trader(tmp_path, max_active_symbols=1)
    bot.position_manager.positions["000001"] = PositionState(
        "000001", position_state=PositionLifecycle.WAIT_REENTRY.value,
    )
    action = StrategyAction(OrderSide.BUY, 30_000, None, "adaptive_initial_buy")

    assert bot.portfolio_buy_block_reason(PositionState("005930"), action) == "max_active_symbols_reached"


@pytest.mark.parametrize("status", [OrderStatus.REQUESTED, OrderStatus.REJECTED, OrderStatus.CANCELED_NO_FILL])
def test_adaptive_initial_requests_consume_daily_count_even_without_fill(tmp_path, status):
    bot = trader(tmp_path, max_new_buy_per_day=1)
    record(bot, "adaptive_initial_buy", "ADAPTIVE-1", status=status)
    action = StrategyAction(OrderSide.BUY, 30_000, None, "adaptive_initial_buy")

    assert bot.store.count_today_initial_buy_orders() == 1
    assert bot.portfolio_buy_block_reason(PositionState("005930"), action) == "max_new_buy_per_day_reached"


@pytest.mark.parametrize("limit_key", ["max_new_buy_amount_per_day", "max_total_initial_buy_amount_per_day"])
def test_legacy_and_adaptive_first_entries_share_daily_amount_budget(tmp_path, limit_key):
    bot = trader(tmp_path, **{limit_key: 100_000})
    record(bot, "initial_buy", "LEGACY-1", amount=40_000)
    record(bot, "adaptive_initial_buy", "ADAPTIVE-1", amount=40_000)
    action = StrategyAction(OrderSide.BUY, 30_000, None, "adaptive_initial_buy")

    assert bot.store.count_today_initial_buy_orders() == 2
    assert bot.store.sum_today_initial_buy_order_amount() == 80_000
    assert bot.portfolio_buy_block_reason(PositionState("005930"), action) == "max_new_buy_amount_per_day_reached"


def test_first_entry_metrics_exclude_additions_reentries_other_days_and_sells(tmp_path):
    bot = trader(tmp_path, max_new_buy_per_day=1, max_new_buy_amount_per_day=50_000)
    record(bot, "adaptive_add_buy", "ADD-1")
    record(bot, "adaptive_reentry_buy", "REENTRY-1")
    record(bot, "adaptive_initial_buy", "YESTERDAY-1", when=datetime.now() - timedelta(days=1))
    record(bot, "adaptive_initial_buy", "SELL-1", side=OrderSide.SELL)

    assert bot.store.count_today_initial_buy_orders() == 0
    assert bot.store.sum_today_initial_buy_order_amount() == 0
    assert bot.portfolio_buy_block_reason(
        PositionState("005930"), StrategyAction(OrderSide.BUY, 30_000, None, "adaptive_initial_buy"),
    ) == ""


@pytest.mark.parametrize("reason", ["adaptive_add_buy", "adaptive_reentry_buy"])
def test_existing_symbol_actions_keep_their_separate_first_entry_exemption(tmp_path, reason):
    bot = trader(tmp_path, max_new_buy_per_day=1, max_new_buy_amount_per_day=1)
    record(bot, "adaptive_initial_buy", "ADAPTIVE-1")

    assert bot.portfolio_buy_block_reason(
        PositionState("005930"), StrategyAction(OrderSide.BUY, 30_000, None, reason),
    ) == ""


def test_ui_risk_usage_matches_persisted_adaptive_first_entries(tmp_path):
    bot = trader(tmp_path)
    record(bot, "initial_buy", "LEGACY-1", amount=20_000)
    record(bot, "adaptive_initial_buy", "ADAPTIVE-1", amount=30_000, status=OrderStatus.REJECTED)
    record(bot, "adaptive_reentry_buy", "REENTRY-1", amount=40_000)
    config_path = tmp_path / "bot.json"
    config_path.write_text(json.dumps(asdict(bot.config)), encoding="utf-8")
    service = UIService(config_path)
    orders = service.orders()

    status = service._load_status_metrics([])
    summary = service.risk_summary(bot.config, [], [], orders, [])
    usage = {row["key"]: row for row in service._limit_usage(
        bot.config, [], [], orders, set(), 0, datetime.now().date().isoformat(),
    )}

    assert status["new_buy_count_today"] == summary["new_buy_count_today"] == 2
    assert status["new_buy_amount_today"] == summary["new_buy_amount_today"] == 50_000
    assert usage["max_new_buy_per_day"]["current"] == 2
    assert usage["max_new_buy_amount_per_day"]["current"] == 50_000
