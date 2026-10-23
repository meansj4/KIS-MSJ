from research.adaptive_tuning_20261023 import legacy_policy
"""Research adapter equivalence and chronological no-broker replay contracts."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import logging
import random
from types import SimpleNamespace

import pytest

from adaptive_oct23_policy.arcg_backtest import historical_clock
from adaptive_oct23_policy.config import BotConfig, MarketHoursConfig, OrderConfig, RiskConfig, StockConfig, StrategyConfig
from adaptive_oct23_policy.lot_manager import LotManager
from adaptive_oct23_policy.models import AccountSnapshot, OrderRequest, OrderSide, TradeFill
from adaptive_oct23_policy.order_manager import OrderManager
from adaptive_oct23_policy.position_manager import PositionManager
from adaptive_oct23_policy.risk_manager import RiskManager
from adaptive_oct23_policy.strategy import LotGridStrategy
from adaptive_oct23_policy.volatility import volatility_multiplier
from research.adaptive_tuning_20261023.engine import _Pending, _PortfolioReplay, experiment_components, run_quote_backtest
from research.adaptive_tuning_20261023.parameters import BASELINE, Parameters, RANGES
from research.adaptive_tuning_20261023.metrics import summarize_path


NOW = datetime(2026, 10, 23, 12)


def config():
    return BotConfig(strategy=StrategyConfig(adaptive_lot_enabled=True),
        stocks=(StockConfig("111111"), StockConfig("222222")),
        risk=RiskConfig(min_cash_available=0, max_total_invested_amount=20_000_000,
                        max_total_open_lots=1000, max_active_symbols=100, max_new_buy_per_day=100),
        order=OrderConfig(live_trading=False, limit_order_timeout_seconds=30))


def signature(action):
    return (action.side, action.amount, action.quantity, action.reason, action.lot_id,
            action.sell_reason, action.reentry_type, action.cleanup_flag, action.limit_price) if action else None


@pytest.mark.parametrize("seed", range(70))
def test_baseline_adapter_matches_production_randomized_full_decisions_and_requests(seed):
    rng = random.Random(seed)
    cfg = config()
    prod_lm = LotManager(cfg.strategy)
    prod_pm = PositionManager(cfg.strategy, prod_lm)
    prod_strategy = LotGridStrategy(cfg, prod_lm)
    prod_strategy.positions = prod_pm.positions
    prod_orders = OrderManager(cfg, SimpleNamespace(), None, logging.getLogger(__name__))
    _, lm, pm, strategy, risk, orders = experiment_components(cfg)
    with historical_clock(NOW):
        for i in range(rng.randrange(14)):
            code = "111111" if i % 3 else "222222"
            fill = TradeFill(code, code, OrderSide.BUY, rng.randrange(1, 12), rng.randrange(600, 2300) * 10,
                f"order{i}", NOW - timedelta(days=rng.choice([0, 3, 14, 90, 200, 8000])), f"lot{i}", f"exec{i}")
            prod_pm.apply_fill(fill)
            pm.apply_fill(fill)
        price = rng.randrange(400, 2500) * 10
        a, b = prod_pm.refresh_from_lots("111111", price), pm.refresh_from_lots("111111", price)
        settings = dict(consecutive_buy_count=rng.randrange(15), consecutive_sell_count=rng.randrange(15),
            rebuy_restriction_active=rng.choice([True, False]), last_sell_price=rng.randrange(600, 2300) * 10,
            retire_after_exit=rng.randrange(10) == 0, danger_state=rng.randrange(13) == 0,
            trading_paused=rng.randrange(14) == 0, needs_review=rng.randrange(7) == 0,
            auto_buy_enabled=rng.randrange(9) != 0)
        for position in (a, b):
            for key, value in settings.items():
                setattr(position, key, value)
        vol = rng.uniform(.75, 2.5)
        prod_lm.set_volatility_multiplier(a.code, vol)
        lm.set_volatility_multiplier(a.code, vol)
        snapshot = AccountSnapshot(rng.choice([0, 1, 30_000, 250_000, 10_000_000]), 20_000_000,
            rng.choice([0, -2_000_000]), rng.choice([0, -500_000]))
        a_details = prod_strategy.adaptive_buy_details(a, price, snapshot)
        b_details = strategy.adaptive_buy_details(b, price, snapshot)
        assert b_details == a_details
        prod_risk = RiskManager(cfg)
        account = prod_risk.account_buy_allowed(snapshot, prod_pm.positions)
        symbol = prod_risk.symbol_buy_allowed(a)
        action_a = prod_strategy.decide(a, price, snapshot, account, symbol)
        action_b = strategy.decide(b, price, snapshot,
            risk.account_buy_allowed(snapshot, pm.positions), risk.symbol_buy_allowed(b))
        assert signature(action_b) == signature(action_a)
        if action_a:
            assert orders.build_request(b, action_b, price) == prod_orders.build_request(a, action_a, price)
        for lot_id, lot in prod_lm.lots.items():
            assert lm.lots[lot_id].effective_target_profit_rate == pytest.approx(lot.effective_target_profit_rate)


def test_volatility_baseline_and_numeric_bounds():
    for vol in [0, 4, 18.41, 50, 120, 500]:
        assert BASELINE.volatility_multiplier(vol, vol) == pytest.approx(volatility_multiplier(vol))
    assert BASELINE.vol20_weight + BASELINE.vol60_weight == 1
    assert Parameters(buy_gap_pct=3).volatility_multiplier(18.41, 18.41) == pytest.approx(1)
    for name, (low, high) in RANGES.items():
        with pytest.raises(ValueError):
            replace(BASELINE, **{name: high + 1})


def test_tuning_buy_ceiling_only_removes_price_guard_and_preserves_cash_and_retirement():
    cfg = config()
    _, lm, pm, strategy, _, _ = experiment_components(cfg, BASELINE.changed(buy_gap_pct=3))
    with historical_clock(NOW):
        pm.apply_fill(TradeFill("111111", "", OrderSide.BUY, 2, 10_000, "b", NOW, "lot", "b"))
        position = pm.refresh_from_lots("111111", 9700)
        assert strategy.adaptive_buy_details(position, 9700, AccountSnapshot(10_000_000, 20_000_000, 0, 0))["skip_reason"] == ""
        assert strategy.adaptive_buy_details(position, 9700, AccountSnapshot(1, 20_000_000, 0, 0))["skip_reason"] == "cash_not_enough"
        position.max_lots_per_symbol = 1
        assert strategy.adaptive_buy_details(position, 9700, AccountSnapshot(10_000_000, 20_000_000, 0, 0))["skip_reason"] == "max_lots_per_symbol_reached"


def row(code, time, price, nexttime=None, nextprice=None):
    return dict(code=code, timestamp=time, exposure_timestamp=time, price=price, market_session="REGULAR",
                next_after_exposure_timestamp=nexttime, next_after_exposure_price=nextprice)


def test_execution_price_is_only_seen_after_signal_and_missing_late_quotes_expire():
    cfg = config()
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9900),
            row("222222", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:40", 9900),
            row("111111", "2026-10-01T10:01:00", 10_000)]
    result = run_quote_backtest(cfg, rows, options=dict(capital=100_000, slippage_bps=0))
    assert result.summary["buy_fills"] == 1
    assert result.trades[0]["datetime"] == "2026-10-01T10:00:10"
    assert result.trades[0]["price"] == 9900
    assert result.diagnostics["counts"]["no_execution_observation_within_timeout"] == 1
    assert result.summary["final_equity"] == pytest.approx(100_000 - 9900 * 3 * .00015 + 100 * 3)


def test_reserved_cash_prevents_simultaneous_initial_orders_overspending():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990),
            row("222222", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:05", 9990)]
    result = run_quote_backtest(config(), rows, options=dict(capital=50_000, slippage_bps=0))
    assert result.summary["buy_fills"] == 1
    assert result.trades[0]["code"] == "111111"
    assert result.summary["minimum_available_cash"] >= 0


def test_require_volatility_gate_and_source_chronology():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990)]
    missing = run_quote_backtest(config(), rows, options=dict(capital=100_000, require_volatility=True))
    assert missing.summary["total_trades"] == 0
    assert missing.diagnostics["counts"]["missing_volatility_signal_block"] == 1
    valid = run_quote_backtest(config(), rows, options=dict(capital=100_000, require_volatility=True, slippage_bps=0),
        volatility={"2026-10-01": {"111111": (18.41, 18.41)}})
    assert valid.summary["buy_fills"] == 1
    bad = row("111111", "2026-10-01T10:00:01", 10_000)
    bad["exposure_timestamp"] = "2026-10-01T10:00:00"
    with pytest.raises(ValueError, match="future-dated"):
        run_quote_backtest(config(), [bad])


def test_adapters_cannot_call_broker_or_operating_store():
    _, _, _, _, _, orders = experiment_components(config())
    assert orders.store is None
    assert not hasattr(orders.client, "place_order")
    assert not hasattr(orders.client, "account_snapshot")


def test_quote_path_integrates_with_metrics_and_completed_daily_marks():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990),
            row("111111", "2026-10-02T10:00:00", 10_500, "2026-10-02T10:00:10", 10_510)]
    result = run_quote_backtest(config(), rows, options=dict(capital=100_000, slippage_bps=0,
        completed_closes={"2026-10-01": {"111111": 10_100}, "2026-10-02": {"111111": 10_520}}))
    assert result.equity[0]["stock_value"] == 3 * 10_100
    metrics = summarize_path(result.equity, initial_equity=100_000, initial_cash=100_000,
                             trades=result.trades, diagnostics=result.diagnostics)
    assert metrics["pnl_reconciliation_error"] == pytest.approx(0, abs=1e-6)
    assert metrics["total_trades"] == 2
    assert result.trades[-1]["closed_lot"]


def test_stale_signal_cutoff_preserves_latest_known_quote_for_valuation():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990),
            row("111111", "2026-10-02T09:25:00", 9000)]
    rows[-1]["exposure_timestamp"] = "2026-10-02T09:30:00"
    result = run_quote_backtest(config(), rows, options=dict(capital=100_000, slippage_bps=0,
        max_signal_age_seconds=120))
    assert result.equity[-1]["stock_value"] == 3 * 9000
    assert result.diagnostics["counts"]["stale_signal_quote"] == 1
    assert result.summary["total_trades"] == 1


def test_recorded_execution_quotes_mark_holdings_even_when_no_new_order_exists():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990),
            row("111111", "2026-10-02T10:00:00", 10_000, "2026-10-02T10:00:10", 10_100)]
    result = run_quote_backtest(config(), rows, options=dict(capital=100_000, slippage_bps=0))
    assert result.summary["total_trades"] == 1
    assert result.equity[-1]["stock_value"] == 3 * 10_100
    assert result.diagnostics["counts"]["scheduled_market_quote_observations"] == 2


def test_late_execution_quote_is_market_data_but_does_not_fill_expired_order():
    rows = [row("111111", "2026-10-01T10:00:00", 10_000, "2026-10-01T10:00:10", 9990),
            row("111111", "2026-10-02T10:00:00", 9400, "2026-10-02T10:01:00", 9300)]
    result = run_quote_backtest(config(), rows, options=dict(capital=1_000_000, slippage_bps=0))
    assert result.summary["total_trades"] == 1
    assert result.equity[-1]["stock_value"] == 3 * 9300
    assert result.diagnostics["counts"]["expired_orders"] >= 1


def test_random_causal_quote_moves_reconcile_incremental_bookkeeping():
    rng = random.Random(20261023)
    rows = []
    for day_index in range(4):
        day = datetime(2026, 10, 5) + timedelta(days=day_index)
        for i in range(24):
            for code in ("111111", "222222"):
                t = day.replace(hour=10) + timedelta(minutes=i * 6)
                price = rng.choice([9000, 9500, 10_000, 10_500, 11_000])
                execution = price - 20 if rng.randrange(2) == 0 else price + 20
                rows.append(row(code, t.isoformat(), price, (t + timedelta(seconds=5)).isoformat(), execution))
    result = run_quote_backtest(config(), rows, options=dict(capital=1_000_000, slippage_bps=0))
    checked = summarize_path(result.equity, initial_equity=1_000_000, initial_cash=1_000_000,
        trades=result.trades, diagnostics=result.diagnostics)
    assert checked["pnl_reconciliation_error"] == pytest.approx(0, abs=.001)
    assert result.summary["total_trades"] > 5


def test_inherited_profit_recovery_decay_priority_and_invalid_negative_target():
    cfg = replace(config(), strategy=replace(config().strategy, deep_loss_recovery_enabled=True))
    _, lm, pm, strategy, _, _ = experiment_components(cfg)
    with historical_clock(NOW):
        for lot_id, price, days in [("profit", 10_000, 0), ("recovery", 20_000, 200), ("decay", 12_000, 200)]:
            pm.apply_fill(TradeFill("111111", "", OrderSide.BUY, 1, price, lot_id,
                                   NOW - timedelta(days=days), lot_id, lot_id))
        lm.lots["recovery"].deep_loss_started_on = "2026-08-01"
        lm.lots["recovery"].deep_loss_low_price = 9000
        position = pm.refresh_from_lots("111111", 11_000)
        cash = AccountSnapshot(10_000_000, 20_000_000, 0, 0)
        assert strategy._sell_candidate(position, 11_000, cash).lot.lot_id == "profit"
        lm.lots["profit"].remaining_quantity = 0
        assert strategy._sell_candidate(position, 11_000, cash).lot.lot_id == "recovery"
        lm.lots["recovery"].remaining_quantity = 0
        lot = lm.lots["decay"]
        lot.buy_filled_at = (NOW - timedelta(days=8000)).isoformat()
        assert strategy._sell_candidate(position, 1, cash) is None


def test_partial_execution_cancels_remainder_and_preserves_lot_and_fee_accounting():
    replay = _PortfolioReplay(config(), BASELINE, 1_000_000, {"slippage_bps": 0})
    replay.update_price("111111", 10_000)
    buy = OrderRequest("111111", "", OrderSide.BUY, 3, 10_000, "adaptive_initial_buy")
    with historical_clock(NOW):
        replay.pending["111111"] = _Pending(buy, NOW, NOW)
        replay.fill("111111", NOW, 10_000, capacity=2)
        lot = replay.lm.open_lots("111111")[0]
        assert lot.remaining_quantity == 2 and "111111" not in replay.pending
        assert replay.reservation() == 0
        sale = OrderRequest("111111", "", OrderSide.SELL, 2, 11_000,
            "sell_profitable_lot", lot.lot_id, sell_reason="PROFIT_TAKE")
        replay.pending["111111"] = _Pending(sale, NOW, NOW)
        replay.fill("111111", NOW, 11_000, capacity=1)
        assert lot.remaining_quantity == 1 and "111111" not in replay.pending
        assert replay.counts["partial_fills"] == 2
        replay.mark("2026-10-23")
    result = replay.result("test_partial", [])
    checked = summarize_path(result.equity, initial_equity=1_000_000, initial_cash=1_000_000,
        trades=result.trades, diagnostics=result.diagnostics)
    assert checked["pnl_reconciliation_error"] == pytest.approx(0, abs=.001)
    assert result.trades[-1]["realized_pnl"] == pytest.approx(1000 - 1.5 - 27.5)


def test_first_execution_locks_actual_price_sizing_bucket_at_boundary():
    replay = _PortfolioReplay(config(), BASELINE, 1_000_000, {"slippage_bps": 0})
    replay.update_price("111111", 10_010)
    request = OrderRequest("111111", "", OrderSide.BUY, 9, 10_010, "adaptive_initial_buy")
    with historical_clock(NOW):
        replay.pending["111111"] = _Pending(request, NOW, NOW)
        replay.fill("111111", NOW, 10_000)
    position = replay.pm.positions["111111"]
    assert position.entry_price_for_lot_sizing == 10_000
    assert position.lot_unit_amount == 30_000
    assert position.max_symbol_amount == 360_000
