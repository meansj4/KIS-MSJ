from __future__ import annotations

import pytest

from research.adaptive_tuning_20261023.metrics import summarize_path


def test_seeded_unrealized_reconciles_period_nav_not_lifetime_pnl():
    rows = [{"date": "2026-01-02", "equity": 1000., "cash": 500., "stock_value": 500.,
             "realized_pnl": 0., "unrealized_pnl": -100., "open_lots": 1},
            {"date": "2026-01-12", "equity": 1010., "cash": 550., "stock_value": 460.,
             "realized_pnl": -30., "unrealized_pnl": -60., "open_lots": 1}]
    result = summarize_path(rows, initial_equity=1000., initial_unrealized=-100.)
    assert result["net_pnl"] == pytest.approx(10.)
    assert result["unrealized_change"] == pytest.approx(40.)
    assert result["pnl_reconciliation_error"] == pytest.approx(0.)
    assert result["cagr_is_short_period_extrapolation"]


def test_drawdown_includes_initial_capital_and_reservations_do_not_destroy_nav():
    result = summarize_path([
        {"date": "2026-01-01", "equity": 900., "cash": 700., "stock_value": 200.,
         "reserved_cash": 600., "available_cash": 100., "open_lots": 2},
        {"date": "2026-12-31", "equity": 1100., "cash": 1000., "stock_value": 100., "open_lots": 1},
    ], initial_equity=1000.)
    assert result["max_drawdown_pct"] == pytest.approx(-10.)
    assert result["minimum_available_cash"] == 100.
    assert result["max_reserved_cash"] == 600.
    assert result["maximum_simultaneous_open_lots"] == 2


def test_costs_and_partial_sales_are_quantity_weighted_and_not_deducted_twice():
    result = summarize_path([
        {"date": "2026-01-01", "equity": 1000., "cash": 1000.},
        {"date": "2026-01-02", "equity": 1010., "cash": 1010.},
    ], initial_equity=1000., trades=[
        {"side": "BUY", "quantity": 10, "fees_tax": 1., "slippage_cost": 2., "reentry_type": "NORMAL"},
        {"side": "SELL", "quantity": 2, "holding_days": 5., "fees_tax": 3.,
         "slippage_cost": 4., "sell_reason": "AGE_DECAY", "realized_pnl": -7.},
        {"side": "SELL", "quantity": 8, "holding_days": 10., "fees_tax": 5., "realized_pnl": 17.},
    ], diagnostics={"reentry_attempts": 2, "counts": {"reentry_fills": 1},
                    "buy_blocks": {"reentry_cash_reserve": 3, "max_lots": 9}})
    assert result["net_pnl"] == 10.
    assert result["total_execution_cost"] == 15.
    assert result["average_sold_quantity_holding_days"] == 9.
    assert result["decay_realized_pnl"] == -7.
    assert result["reentry_fill_rate_pct"] == 50.
    assert result["cash_shortfall_blocks"] == 3


def test_rejects_bad_accounting_or_chronology():
    with pytest.raises(ValueError, match="NAV must equal"):
        summarize_path([{"date": "2026-01-01", "equity": 1000., "cash": 900., "stock_value": 200.}], initial_equity=1000.)
    with pytest.raises(ValueError, match="chronological"):
        summarize_path([{"date": "2026-01-02", "equity": 1000., "cash": 1000.},
                        {"date": "2026-01-01", "equity": 1000., "cash": 1000.}], initial_equity=1000.)
    with pytest.raises(ValueError, match="realized plus"):
        summarize_path([{"date": "2026-01-01", "equity": 1100., "cash": 1100.,
                         "realized_pnl": 5., "unrealized_pnl": 0.}], initial_equity=1000.)


def test_one_observation_has_no_annualization_and_zero_drawdown_has_no_calmar():
    result = summarize_path([{"date": "2026-01-01", "equity": 1000., "cash": 1000.}], initial_equity=1000.)
    assert result["cagr_pct"] is None
    assert result["calmar_ratio"] is None


def test_intraday_reservation_extrema_are_distinct_from_daily_cash_marks():
    result = summarize_path([
        {"date": "2026-01-01", "equity": 1000., "cash": 1000., "open_lots": 0},
        {"date": "2026-01-02", "equity": 1000., "cash": 1000., "open_lots": 0},
    ], initial_equity=1000., diagnostics={"minimum_available_cash_intraday": 600.,
        "max_open_lots_intraday": 2, "counts": {"buy_fills": 3, "sell_fills": 4},
        "fees_tax": 20., "slippage_cost": 5.})
    assert result["minimum_available_cash"] == 600.
    assert result["sampled_minimum_available_cash"] == 1000.
    assert result["maximum_simultaneous_open_lots"] == 2
    assert result["sampled_maximum_simultaneous_open_lots"] == 0
    assert result["total_trades"] == 7
    assert result["total_execution_cost"] == 25.
