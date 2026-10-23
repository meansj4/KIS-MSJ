from datetime import datetime, date, timedelta
import json
from types import SimpleNamespace
from dataclasses import replace
import pytest
from kis_msj.trading_calendar import TradingCalendar, CalendarUnavailable, trend_score
from kis_msj.config import BotConfig, StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.position_manager import PositionManager
from kis_msj.models import TradeFill, OrderSide, AccountSnapshot
from kis_msj.strategy import LotGridStrategy
from kis_msj.arcg_backtest import historical_clock

NOW = datetime(2026, 10, 23, 16)


def calendar():
    start = date(2026, 8, 1)
    days = {(start+timedelta(days=i)).isoformat(): (start+timedelta(days=i)).weekday()<5 for i in range(100)}
    # Explicit fake exchange evidence includes a weekday closure.
    days['2026-10-09'] = False
    return TradingCalendar(days)


def setup():
    cfg = BotConfig(strategy=StrategyConfig(adaptive_lot_enabled=True))
    lm = LotManager(cfg.strategy)
    lm.calendar = calendar()
    pm = PositionManager(cfg.strategy, lm)
    strategy = LotGridStrategy(cfg, lm)
    strategy.positions = pm.positions
    return lm, pm, strategy


def buy(pm, number, price=10000, qty=2, when=NOW):
    return pm.apply_fill(TradeFill('111111','test',OrderSide.BUY,qty,price,str(number),when,
                                 f'L{number}',f'E{number}'))


@pytest.mark.parametrize('count,threshold', [(1,-4),(2,-6),(3,-8),(11,-24),(12,-26)])
def test_pnl_thresholds_and_max_lots(count, threshold):
    with historical_clock(NOW):
        lm, pm, s = setup()
        for i in range(count): buy(pm,i,when=NOW-timedelta(days=10))
        p = pm.refresh_from_lots('111111',7000)
        d = s.adaptive_buy_details(p,7000,AccountSnapshot(10000000,20000000,0,0))
        assert d['effective_buy_pnl_threshold'] == threshold
        assert d['position_vwap'] == 10000
        if count==12: assert 'max_lots' in d['skip_reason']


def test_score_partials_types_kst_window_restart_and_caps():
    cal = calendar()
    events = [{'order':str(i),'side':side,'at':NOW.isoformat()} for i,side in enumerate(['BUY']*3+['SELL']+['BUY']*2)]
    events.append(dict(events[0]))
    assert trend_score(events, cal, NOW)==4
    assert trend_score(json.loads(json.dumps(events)), cal,NOW)==4
    assert trend_score(events,cal,NOW+timedelta(days=10))==0
    assert trend_score([{'order':str(i),'side':'SELL','at':NOW.isoformat()} for i in range(20)],cal,NOW)==-12
    assert trend_score([{'order':str(i),'side':'BUY','at':NOW.isoformat()} for i in range(20)],cal,NOW)==12


def test_completed_decay_not_buy_day_weekends_holiday_or_intraday():
    cal = calendar()
    assert cal.completed_since('2026-10-08T09:00:00',datetime(2026,10,8,16))==0
    assert cal.completed_since('2026-10-08T09:00:00',datetime(2026,10,12,12))==0
    assert cal.completed_since('2026-10-08T09:00:00',datetime(2026,10,12,16))==1
    assert cal.completed_since('2026-10-08T09:00:00',datetime(2026,10,16,16))==5
    assert cal.window(datetime(2026,10,11,12))=={'2026-10-08','2026-10-07','2026-10-06','2026-10-05','2026-10-02'}


def test_quantity_weighted_pnl_reentry_and_no_legacy_streak():
    with historical_clock(NOW):
        lm,pm,s=setup()
        buy(pm,0,10000,2,NOW-timedelta(days=10))
        buy(pm,1,12000,1,NOW-timedelta(days=10))
        p=pm.refresh_from_lots('111111',9000)
        p.consecutive_buy_count=12
        p.rebuy_restriction_active=True
        p.last_sell_price=9000
        d=s.adaptive_buy_details(p,9000,AccountSnapshot(10000000,20000000,0,0))
        assert d['position_vwap']==pytest.approx(32000/3)
        assert d['effective_buy_pnl_threshold']==-6
        assert d['buy_target_price']==8640
        assert not d['buy_condition_met']


def test_vwap_after_addition_tightening_and_sale_restriction():
    with historical_clock(NOW):
        lm,pm,s=setup()
        buy(pm,0,10000,10,NOW-timedelta(days=10))
        buy(pm,1,9600,10)
        p=pm.refresh_from_lots('111111',9600)
        d=s.adaptive_buy_details(p,9600,AccountSnapshot(10000000,20000000,0,0))
        assert d['position_vwap']==9800
        assert d['normal_buy_target']==9212
        assert not d['buy_condition_met']
        pm.apply_fill(TradeFill('111111','test',OrderSide.SELL,10,9600,'S',NOW,'L0','ES'))
        d=s.adaptive_buy_details(p,9600,AccountSnapshot(10000000,20000000,0,0))
        assert d['buy_target_price']<=9216
        assert not d['buy_condition_met']


def test_retroactive_decay_and_negative_target():
    with historical_clock(NOW):
        lm,pm,s=setup()
        buy(pm,0,when=datetime(2026,10,8,9))
        a=lm.open_lots('111111')[0]
        days=lm.calendar.completed_since(a.buy_filled_at,NOW)
        assert lm.effective_target_profit_rate(a,NOW)==pytest.approx(.03-days*.001/5)
        buy(pm,1)
        assert lm.effective_target_profit_rate(a,NOW)==pytest.approx(.03-days*.002/5)
        a.buy_filled_at='2026-08-03T09:00:00'
        for i in range(2,12): buy(pm,i,when=NOW-timedelta(days=10))
        assert lm.effective_target_profit_rate(a,NOW)<0


def test_unreachable_threshold_no_relaxation():
    with historical_clock(NOW):
        lm,pm,s=setup()
        for i in range(11): buy(pm,i)
        # 12 actual orders in the window, including another split-independent order.
        buy(pm,11)
        lm.set_volatility_multiplier('111111',2.5)
        p=pm.refresh_from_lots('111111',1000)
        d=s.adaptive_buy_details(p,1000,AccountSnapshot(10000000,20000000,0,0))
        assert d['effective_buy_pnl_threshold']==pytest.approx(-208)
        assert d['normal_buy_target']==0
        assert not d['buy_condition_met']


def test_missing_calendar_denies_decision_but_never_loses_actual_fill():
    with historical_clock(NOW):
        lm,pm,s=setup()
        lm.calendar=TradingCalendar()
        p=buy(pm,0)
        assert p.quantity==2 and len(lm.open_lots('111111'))==1
        assert 'calendar' in p.skip_reason
        from kis_msj.risk_manager import RiskDecision
        assert s.decide(p,9000,AccountSnapshot(1000000,2000000,0,0),RiskDecision(True),RiskDecision(True)) is None
        assert s.context(p,9000).skip_reason.startswith('krx_calendar')
        with pytest.raises(CalendarUnavailable): lm.calendar.window(NOW)


def test_guarded_calendar_get_cache_restart_and_unknown_response(tmp_path):
    calls=[]
    def request(method,path,**kwargs):
        calls.append((method,path,kwargs))
        return {'output':[{'bass_dt':'20261023','opnd_yn':'Y'}, {'bass_dt':'20261024','opnd_yn':'N'}]}
    path=tmp_path/'calendar.json'
    cal=TradingCalendar(client=SimpleNamespace(_request=request),path=path)
    assert cal.is_session(date(2026,10,23))
    assert not cal.is_session(date(2026,10,24))
    assert calls[0][0]=='GET' and calls[0][2]['tr_id']=='CTCA0903R'
    assert TradingCalendar(path=path).is_session(date(2026,10,23))
    with pytest.raises(CalendarUnavailable): cal.is_session(date(2026,10,25))
