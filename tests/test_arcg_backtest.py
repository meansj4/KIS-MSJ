"""Offline replay invariants; no broker, live database, or network fixtures."""
from dataclasses import replace
from datetime import datetime, timedelta
import csv
import sqlite3

import pytest

from kis_msj import strategy as strategy_module
from kis_msj.arcg_backtest import DailyBar, DailyDataset, historical_clock, load_daily_dataset, run_backtest
from kis_msj.config import BotConfig, OrderConfig, PriceLotBand, RiskConfig, StrategyConfig
from kis_msj import arcg
from kis_msj.risk_manager import RiskManager


def config():
    return BotConfig(
        strategy=StrategyConfig(lot_sizing_mode='cycle_locked_by_entry_price',
            price_lot_bands=(PriceLotBand(0, 1_000_000, 100_000, 1_200_000, max_lots=12),)),
        risk=RiskConfig(min_cash_available=0, max_total_invested_amount=1_000_000_000),
        order=OrderConfig(buy_limit_markup_pct=2, sell_limit_markdown_pct=2))


def dataset(prices=(10000, 10000, 10000, 10000), *, symbols=('000001',), volumes=None):
    days, index = {}, {}
    for i, price in enumerate(prices):
        day = (datetime(2020, 1, 1) + timedelta(days=i)).date().isoformat()
        bar = DailyBar(datetime.fromisoformat(day).replace(hour=15, minute=30),
            price, price * 1.03, price * .97, price, volumes[i] if volumes else 100000)
        days[day] = {c: bar for c in symbols}
        index[day] = replace(bar, open=2000, high=2100, low=1900, close=2000)
    return DailyDataset(days, index, config())


def test_next_open_only_and_book_cash_conservation():
    result = run_backtest(dataset(), 'A')
    assert result.trades
    for trade in result.trades:
        assert trade['datetime'][:10] > trade['signal_date']
        assert trade['quantity'] > 0 and isinstance(trade['quantity'], int)
        signed = -1 if trade['side'] == 'BUY' else 1
        assert trade['cash_after'] == pytest.approx(trade['cash_before'] + signed * trade['amount'] - trade['fees_tax'])
    for row in result.equity:
        assert row['cash'] >= 0
        assert row['equity'] == pytest.approx(row['cash'] + row['stock_value'])
        assert row['equity'] - result.summary['initial_equity'] == pytest.approx(row['realized_pnl'] + row['unrealized_pnl'])


def test_zero_volume_veto_and_next_session_expiry():
    result = run_backtest(dataset(volumes=[100000, 0, 100000, 100000]), 'A')
    assert not any(t['datetime'].startswith('2020-01-02') for t in result.trades)
    assert result.diagnostics['counts']['expired_missing_halted_quarantine'] >= 1
    assert all(t['signal_date'] != '2020-01-01' for t in result.trades)


def test_gap_quarantine_is_causal_and_keeps_raw_mark_when_held():
    result = run_backtest(dataset((10000, 10000, 20000, 20000)), 'A')
    assert result.trades[0]['datetime'].startswith('2020-01-02')
    assert result.summary['economic_valid'] is False
    assert result.diagnostics['quarantine_events'][0]['date'] == '2020-01-03'
    assert result.equity[-1]['stock_value'] > result.equity[1]['stock_value']
    assert all(t['datetime'][:10] < '2020-01-03' for t in result.trades)


def test_new_cycle_entire_budget_reserved_before_next_symbol():
    result = run_backtest(dataset(symbols=('000001', '000002')), 'B',
        {'arcg': {'symbol_cap_fraction': .6, 'sector_cap_fraction': 1., 'account_cap_fraction': .9,
                  'safety_cash_fraction': .1}})
    buys = [t for t in result.trades if t['side'] == 'BUY']
    assert len({t['code'] for t in buys}) == 1
    assert result.summary['allocation_cash_blocks'] > 0


def test_clock_restores_modules_even_on_exception():
    original = strategy_module.datetime
    with pytest.raises(RuntimeError):
        with historical_clock(datetime(2012, 1, 1)):
            assert strategy_module.datetime.now().year == 2012
            raise RuntimeError('test')
    assert strategy_module.datetime is original


def test_higher_costs_lower_identical_no_exit_nav():
    data = dataset()
    a = run_backtest(data, 'A', {'buy_fee_bps': 0, 'slippage_bps': 0})
    b = run_backtest(data, 'A', {'buy_fee_bps': 10, 'slippage_bps': 5})
    assert b.summary['final_equity'] < a.summary['final_equity']


def test_loader_filters_single_source_and_rejects_invalid_ohlc(tmp_path):
    db, index, conf = tmp_path / 'prices.db', tmp_path / 'index.csv', tmp_path / 'config.json'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE historical_daily_prices(code,date,name,open,high,low,close,volume,data_source)')
    con.executemany('INSERT INTO historical_daily_prices VALUES(?,?,?,?,?,?,?,?,?)', [
        ('000001', '2020-01-01', 'Test', 10, 11, 9, 10, 10, 'kis_historical_daily_readonly'),
        ('000001', '2020-01-01', 'Test', 99, 100, 98, 99, 10, 'naver_fchart_daily'),
        ('000002', '2020-01-01', 'Bad', 10, 5, 9, 10, 10, 'kis_historical_daily_readonly')])
    con.commit()
    con.close()
    index.write_text('date,open,high,low,close,volume\n2020-01-01,10,11,9,10,100\n', encoding='utf-8')
    conf.write_text('{}', encoding='utf-8')
    loaded = load_daily_dataset(db, index, conf)
    assert loaded.codes == ['000001']
    assert loaded.bars['2020-01-01']['000001'].close == 10
    assert len(loaded.manifest['rejected_rows']) == 1
    assert loaded.manifest['database_sha256']


def test_common_index_regime_is_equal_for_baseline_and_capital_model():
    data = dataset(prices=tuple(10000 for _ in range(55)))
    a, b = run_backtest(data, 'A'), run_backtest(data, 'B')
    assert [r['market_regime'] for r in a.equity] == [r['market_regime'] for r in b.equity]


@pytest.mark.parametrize('model', list('ABCDEF'))
def test_each_ablation_runs_with_prior_history_only(model):
    data = dataset(tuple(10000 + i * 5 for i in range(55)))
    result = run_backtest(data, model, {'start': '2020-02-15'})
    assert len(result.equity) == 10
    assert result.summary['model'] == model
    assert all(t['signal_date'] >= '2020-02-15' for t in result.trades)


def test_capital_model_can_add_above_original_price_band_cap_without_false_review(monkeypatch):
    data = dataset((10000, 10000, 9000, 9000, 8000, 8000), volumes=[1_000_000] * 6)
    observed = []
    original = strategy_module.LotGridStrategy.decide

    def record(self, position, *args, **kwargs):
        action = original(self, position, *args, **kwargs)
        if position.quantity:
            observed.append((position.max_symbol_amount, position.lot_unit_amount,
                             position.needs_review, position.review_reason))
        return action

    monkeypatch.setattr(strategy_module.LotGridStrategy, 'decide', record)
    result = run_backtest(data, 'B')
    buys = [t for t in result.trades if t['side'] == 'BUY']
    assert buys[0]['amount'] > 1_200_000  # original production price-band cap
    assert len(buys) >= 2
    assert observed and all(cap == 10_000_000 and tranche == 1_500_000 for cap, tranche, _, _ in observed)
    assert not any(review and reason == 'max_symbol_amount_reached' for _, _, review, reason in observed)
    assert result.summary['maximum_symbol_investment'] <= 10_000_000


def _ready_features(bars, config):
    last = bars[-1]
    return arcg.Features(close=last.close, atr=last.close * .01, atr_fraction=.01,
        fast_ma=last.close, slow_ma=last.close, average_turnover=1e10,
        entry_signal=True, stabilized=True, bar_time=last.time, ready=True)


def test_adaptive_model_never_invokes_legacy_buy_path_while_holding(monkeypatch):
    monkeypatch.setattr(arcg, 'compute_features', _ready_features)

    def forbidden(*args, **kwargs):
        raise AssertionError('D must not evaluate legacy addition logic')

    monkeypatch.setattr(strategy_module.LotGridStrategy, '_add_buy_action', forbidden)
    data = dataset((10000, 10000, 9000, 9000), volumes=[1_000_000] * 4)
    result = run_backtest(data, 'D', {'slippage_bps': 0})
    assert result.summary['buy_fills'] >= 2


def test_capital_adapter_preserves_genuine_loss_risk_gate(monkeypatch):
    monkeypatch.setattr(arcg, 'compute_features', _ready_features)
    data = dataset((10000, 10000, 7500, 7500), volumes=[1_000_000] * 4)
    result = run_backtest(data, 'D', {'slippage_bps': 0})
    assert result.summary['buy_fills'] == 1
    assert result.diagnostics['buy_blocks']['production_risk'] > 0


def test_account_total_pnl_snapshot_excludes_previously_realized_profits(monkeypatch):
    observed = []
    original = RiskManager.account_buy_allowed

    def capture(self, snapshot, positions):
        observed.append(snapshot)
        return original(self, snapshot, positions)

    monkeypatch.setattr(RiskManager, 'account_buy_allowed', capture)
    result = run_backtest(dataset((10000, 10000, 11000, 11000, 11000)), 'A')
    assert result.summary['realized_pnl'] > 0
    assert any(s.total_asset > 100_000_000 and s.total_profit_loss == 0 for s in observed)
