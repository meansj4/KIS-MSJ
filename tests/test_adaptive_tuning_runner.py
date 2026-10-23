"""Boundary and cash accounting checks for the isolated tuning runner."""
from __future__ import annotations
from research.adaptive_tuning_20261023 import legacy_policy

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_oct23_policy.config import load_config
from research.adaptive_tuning_20261023.engine import run_quote_backtest
from scripts.run_adaptive_tuning_20261023 import segment


def test_segment_actual_replay_carries_seeded_lot_and_preperiod_unrealized():
    config = load_config(Path(__file__).resolve().parents[1] / "config/lot_auto_trader.json")
    observations = [
        ("2026-06-01", 19300, 19270),  # Fill the first buy below its limit.
        ("2026-06-02", 20000, 20030),  # Sell the pre-boundary LOT.
        ("2026-06-03", 19000, 18970),  # A restricted reentry now qualifies.
        ("2026-06-04", 19010, 18980),
    ]
    records = [dict(code="024110", timestamp=f"{day}T09:29:59",
                    exposure_timestamp=f"{day}T09:30:00", price=price,
                    next_after_exposure_timestamp=f"{day}T09:30:05",
                    next_after_exposure_price=following)
               for day, price, following in observations]
    capital = 30_000_000
    result = run_quote_backtest(config, records, options={"capital": capital})
    before = result.equity[0]
    ending = result.equity[2]
    actual = segment(result, "2026-06-02", "2026-06-03", capital=capital)
    assert before["open_lots"] == 1
    assert before["unrealized_pnl"] < 0
    assert actual["initial_equity"] == pytest.approx(before["equity"])
    assert actual["net_pnl"] == pytest.approx(ending["equity"] - before["equity"])
    assert actual["realized_pnl"] + actual["unrealized_change"] == pytest.approx(actual["net_pnl"])
    assert abs(actual["pnl_reconciliation_error"]) < .0001
    assert actual["reentry_attempts"] == 1
    assert actual["reentry_filled_orders"] == 1
    assert actual["reentry_fill_rate_pct"] == 100.
    assert actual["buy_fills"] == 1
    assert actual["sell_fills"] == 1
    assert actual["minimum_available_cash"] < capital
    assert actual["average_completed_lot_holding_days"] == pytest.approx(1.)


def test_segment_uses_only_selected_period_counters_and_intraday_extrema():
    before = dict(date="2026-08-21", equity=1000., cash=500., stock_value=500.,
        reserved_cash=0., available_cash=500., open_lots=1,
        realized_pnl=0., unrealized_pnl=-100.,
        cumulative_counts={"buy_fills": 4, "sell_fills": 2, "reentry_fills": 2},
        cumulative_buy_blocks={"cash_not_enough": 100, "adaptive_reentry_cash_insufficient": 50},
        cumulative_reentry_orders=5, cumulative_reentry_fills=2,
        minimum_available_cash_intraday_day=1., maximum_open_lots_intraday_day=15)
    after = dict(date="2026-08-24", equity=1010., cash=550., stock_value=460.,
        reserved_cash=0., available_cash=550., open_lots=1,
        realized_pnl=-30., unrealized_pnl=-60.,
        cumulative_counts={"buy_fills": 5, "sell_fills": 3, "reentry_fills": 3},
        cumulative_buy_blocks={"cash_not_enough": 103, "adaptive_reentry_cash_insufficient": 52},
        cumulative_reentry_orders=7, cumulative_reentry_fills=3,
        minimum_available_cash_intraday_day=300., maximum_open_lots_intraday_day=2)
    future = deepcopy(after)
    future.update(date="2026-09-21", minimum_available_cash_intraday_day=0.,
                  maximum_open_lots_intraday_day=100)
    # Whole-path diagnostics are deliberately contaminated with test-period
    # extrema. Correct slicing must ignore them and use daily deltas.
    result = SimpleNamespace(equity=[before, after, future], trades=[], diagnostics={
        "minimum_available_cash_intraday": 0., "max_open_lots_intraday": 100,
        "closed_lot_holding_days": [999.], "decay_realized_pnl": -99999.})
    actual = segment(result, "2026-08-24", "2026-08-24", capital=30_000_000)
    assert actual["initial_equity"] == 1000.
    assert actual["net_pnl"] == 10.
    assert actual["realized_pnl"] == -30.
    assert actual["unrealized_change"] == 40.
    assert actual["minimum_available_cash"] == 300.
    assert actual["maximum_simultaneous_open_lots"] == 2
    assert actual["cash_shortfall_blocks"] == 5
    assert actual["reentry_attempts"] == 2
    assert actual["reentry_filled_orders"] == 1
    assert actual["reentry_fill_rate_pct"] == 50.
    assert actual["total_trades"] == 2
    assert actual["average_completed_lot_holding_days"] is None
    assert actual["decay_realized_pnl"] == 0.
