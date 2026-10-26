"""Whole-LOT trend evidence, partial orders, recovery and the read-only UI."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import logging
from types import SimpleNamespace

import pytest

from kis_msj.arcg_backtest import historical_clock
from kis_msj.config import BotConfig, StrategyConfig, StockConfig
from kis_msj.fill_state import actual_fill_events
from kis_msj.lot_completion import completion_event, reconstruct_completions, read_completion_history
from kis_msj.lot_manager import LotManager
from kis_msj.models import OrderRequest, OrderResult, OrderSide, OrderStatus, TradeFill
from kis_msj.order_manager import OrderManager
from kis_msj.position_manager import PositionManager
from kis_msj.storage import StateStore
from kis_msj.trading_calendar import TradingCalendar, trend_score
from kis_msj.ui_service import UIService

NOW = datetime(2026, 10, 26, 10)
CODE = '000990'


def setup():
    config = StrategyConfig(adaptive_lot_enabled=True, estimated_fee_tax_pct=0)
    lm = LotManager(config)
    lm.calendar = TradingCalendar({(NOW.date()-timedelta(days=i)).isoformat():
                                   (NOW-timedelta(days=i)).weekday() < 5 for i in range(30)})
    pm = PositionManager(config, lm)
    return config, lm, pm


def buy(pm, qty=1, expected=5, at=NOW, order='B', execution='b1', lot='L'):
    fill = TradeFill(CODE, 'DB', OrderSide.BUY, qty, 10000, order, at, lot, execution,
                     order_quantity=expected)
    return pm.apply_fill(fill), fill


def sell(pm, qty=1, at=NOW, order='S', execution='s1', lot='L'):
    fill = TradeFill(CODE, 'DB', OrderSide.SELL, qty, 10300, order, at, lot, execution)
    return pm.apply_fill(fill), fill


def completed(position):
    return [event for event in actual_fill_events(position) if event.get('kind') == 'LOT_COMPLETE']


def test_partial_buy_waits_until_full_requested_quantity_and_counts_at_final_fill():
    with historical_clock(NOW):
        _, lm, pm = setup()
        p, first = buy(pm, qty=2, at=NOW-timedelta(minutes=10))
        assert not completed(p)
        p, final = buy(pm, qty=3, at=NOW, execution='b2')
        assert lm.trend_score_for(CODE, NOW) == 1
        assert len(completed(p)) == 1
        assert completed(p)[0]['at'].startswith('2026-10-26T10:00:00')
        pm.apply_fill(first); pm.apply_fill(final)
        assert lm.open_lots(CODE)[0].buy_quantity == 5
        assert len(completed(p)) == 1


def test_many_fully_executed_partial_sell_orders_count_only_when_lot_is_empty():
    with historical_clock(NOW):
        _, lm, pm = setup()
        p, _ = buy(pm, qty=5, at=NOW-timedelta(days=10))
        for i in range(4):
            p, fill = sell(pm, order=f'S{i}', execution=f's{i}', at=NOW+timedelta(seconds=i))
            assert lm.trend_score_for(CODE, NOW+timedelta(minutes=1)) == 0
            assert p.rebuy_restriction_active and p.last_sell_price == 10300
        p, final = sell(pm, order='S4', execution='s4', at=NOW+timedelta(seconds=4))
        assert lm.trend_score_for(CODE, NOW+timedelta(minutes=1)) == -1
        pm.apply_fill(final)
        assert len([e for e in completed(p) if e['side']=='SELL']) == 1


def test_partial_fills_of_single_sell_order_and_two_closed_lots_are_independent():
    with historical_clock(NOW):
        _, lm, pm = setup()
        for index in range(2):
            buy(pm, qty=2, expected=2, at=NOW-timedelta(days=10), order=f'B{index}',
                execution=f'b{index}', lot=f'L{index}')
            sell(pm, lot=f'L{index}', execution=f's{index}-a')
            assert lm.trend_score_for(CODE, NOW) == -index
            sell(pm, lot=f'L{index}', execution=f's{index}-b')
            assert lm.trend_score_for(CODE, NOW) == -(index+1)


def test_late_older_buy_fill_completes_at_latest_execution_and_symbol_is_independent():
    with historical_clock(NOW):
        _, lm, pm = setup()
        buy(pm, qty=3, at=NOW, execution='new')
        p, _ = buy(pm, qty=2, at=NOW-timedelta(minutes=5), execution='late')
        assert completed(p)[0]['at'].startswith('2026-10-26T10:00:00')
        assert lm.trend_score_for(CODE,NOW) == 1
        assert lm.trend_score_for('064350',NOW) == 0


@pytest.mark.parametrize('expected', [0, 5])
def test_unknown_or_canceled_partial_buy_does_not_count_as_a_complete_buy(expected):
    with historical_clock(NOW):
        _, lm, pm = setup()
        p, _ = buy(pm, qty=2, expected=expected)
        assert not completed(p)
        assert lm.trend_score_for(CODE, NOW) == 0


def test_only_completion_evidence_counts_with_kst_future_window_and_lot_deduplication():
    _, lm, _ = setup()
    raw = [{'order':str(i), 'side':'SELL', 'at':NOW.isoformat()} for i in range(30)]
    buy_event = completion_event('L', 'BUY', NOW.isoformat())
    sell_event = completion_event('L', 'SELL', NOW.isoformat())
    future = completion_event('future', 'SELL', (NOW+timedelta(days=1)).isoformat())
    assert trend_score(raw, lm.calendar, NOW) == 0
    assert trend_score([buy_event, buy_event, *raw, future], lm.calendar, NOW) == 1
    assert trend_score([buy_event, sell_event], lm.calendar, NOW) == 0
    utc = dict(buy_event, at='2026-10-26T01:00:00+00:00')
    assert trend_score([utc], lm.calendar, NOW) == 1


def test_original_request_quantity_survives_matching_and_aggregate_delta(tmp_path):
    config = BotConfig(storage_path=str(tmp_path/'state.sqlite3'))
    store = StateStore(config.storage_path)
    om = OrderManager(config, SimpleNamespace(), store, logging.getLogger(__name__))
    request = OrderRequest(CODE, 'DB', OrderSide.BUY, 5, 10000, 'buy')
    result = OrderResult(request, '123', OrderStatus.PARTIAL)
    raw = TradeFill(CODE, 'DB', OrderSide.BUY, 2, 10000, '123', NOW, execution_id='AGG:123')
    matched = om._matching_fills(result, (raw,))[0]
    assert matched.order_quantity == 5
    delta = om._dedupe_or_delta_fill(matched)
    assert delta.order_quantity == 5 and delta.quantity == 2


def persisted_history(tmp_path):
    config, lm, pm = setup()
    store = StateStore(tmp_path/'state.sqlite3')
    request = OrderRequest(CODE, 'DB', OrderSide.BUY, 5, 10000, 'buy', 'L')
    store.record_order(OrderResult(request, 'B', OrderStatus.FILLED, requested_at=NOW.isoformat()))
    with historical_clock(NOW):
        p, fill = buy(pm, qty=5, expected=0)
        store.record_fill(fill)
        for i in range(5):
            p, fill = sell(pm, at=NOW+timedelta(seconds=i+1), order=f'S{i}', execution=f's{i}')
            store.record_fill(fill)
    # Simulate the old version: keep its raw fill ledger but no completion events.
    p.actual_fill_events = json.dumps([e for e in actual_fill_events(p) if e.get('kind') != 'LOT_COMPLETE'])
    store.save_lots(lm.lots.values()); store.save_position(p)
    return config, store, p


def test_restart_recovers_closed_lot_once_from_many_orders_without_writing_db(tmp_path):
    config, store, old = persisted_history(tmp_path)
    with store._connect() as db:
        before = db.execute('SELECT actual_fill_events FROM positions').fetchone()[0]
    restored = store.load_positions()[CODE]
    events = completed(restored)
    assert [(e['lot'],e['side']) for e in events] == [('L','BUY'),('L','SELL')]
    assert events[-1]['at'].startswith('2026-10-26T10:00:05')
    with store._connect() as db:
        assert db.execute('SELECT actual_fill_events FROM positions').fetchone()[0] == before
    assert restored.rebuy_restriction_active == old.rebuy_restriction_active
    assert restored.consecutive_sell_count == old.consecutive_sell_count == 5
    store.save_position(restored)
    assert store.load_positions()[CODE].actual_fill_events == restored.actual_fill_events


def test_ui_and_strategy_use_same_recovered_completions_from_closed_lots(tmp_path):
    strategy, store, _ = persisted_history(tmp_path)
    cfg = BotConfig(strategy=strategy, stocks=(StockConfig(CODE,'DB'),),
                    storage_path=str(store.path),log_path=str(tmp_path/'bot.log'))
    path = tmp_path/'config.json'; path.write_text(json.dumps(asdict(cfg)),encoding='utf-8')
    ui = UIService(path,tmp_path/'runtime.json')
    states = ui.positions()
    assert len(completed(SimpleNamespace(actual_fill_events=states[0]['actual_fill_events']))) == 2
    manager = ui._configured_lot_manager({})  # closed LOTs are absent from the active book
    _, lm, _ = setup();manager.calendar=lm.calendar
    assert manager.trend_score_for(CODE,NOW+timedelta(minutes=1)) == 0


def test_historical_unknown_request_and_partial_cancellation_are_not_assumed_complete(tmp_path):
    _, store, _ = persisted_history(tmp_path)
    with store._connect() as db:
        db.execute("UPDATE orders SET quantity=10,status='CANCELED_AFTER_PARTIAL_FILL' WHERE side='BUY'")
        history = read_completion_history(db)[CODE]
        assert [e['side'] for e in history] == ['SELL']
        db.execute('DELETE FROM orders')
        assert [e['side'] for e in read_completion_history(db)[CODE]] == ['SELL']


def test_one_attributed_fill_closing_two_lots_counts_two_lots_not_one_order():
    lots = [dict(lot_id=f'L{i}',code=CODE,buy_order_key='',buy_filled_at=NOW.isoformat(),
                 buy_quantity=2,remaining_quantity=0,sell_completed=True,status='CLOSED') for i in range(2)]
    fills = [dict(id=1,code=CODE,side='SELL',quantity=4,order_id='S',filled_at=NOW.isoformat(),lot_id='')]
    allocations = [dict(fill_id=1,lot_id=f'L{i}',applied_quantity=2) for i in range(2)]
    events = reconstruct_completions(lots,fills,[],allocations)[CODE]
    _,lm,_=setup()
    assert trend_score(events,lm.calendar,NOW) == -2


def test_legacy_partial_lot_late_older_final_sell_uses_latest_recorded_lot_execution(tmp_path):
    config, lm, pm = setup()
    store = StateStore(tmp_path/'state.sqlite3')
    with historical_clock(NOW+timedelta(minutes=1)):
        p, fill = buy(pm,qty=5,at=NOW-timedelta(days=10))
        store.record_fill(fill)
        for i in range(4):
            p, fill = sell(pm,at=NOW+timedelta(seconds=i),order=f'S{i}',execution=f's{i}')
            store.record_fill(fill)
        raw = [e for e in actual_fill_events(p) if not e.get('kind')]
        for event in raw:
            for key in ('lot','quantity','order_quantity'):
                event.pop(key,None)
        p.actual_fill_events=json.dumps(raw)
        store.save_lots(lm.lots.values());store.save_position(p)
        restored_lm=LotManager(config,store.load_lots());restored_lm.calendar=lm.calendar
        restored_pm=PositionManager(config,restored_lm,store.load_positions())
        restored, _=sell(restored_pm,at=NOW-timedelta(days=7),order='late',execution='late')
        assert restored_lm.trend_score_for(CODE,NOW+timedelta(minutes=1)) == -1
        assert [e for e in completed(restored) if e['side']=='SELL'][0]['at'].startswith('2026-10-26T10:00:03')
