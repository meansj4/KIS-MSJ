"""Cash endpoints, ordering, reentry and real strategy/request integration."""
from dataclasses import replace
from datetime import date,datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
import json,logging,math

import pytest
from kis_msj.adaptive_policy import cash_multipliers
from kis_msj.arcg_backtest import historical_clock
from kis_msj.config import BotConfig,StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import AccountSnapshot,OrderRequest,OrderSide,TradeFill
from kis_msj.order_manager import OrderManager
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskDecision
from kis_msj.strategy import LotGridStrategy
from kis_msj.market_session import SessionUnavailable
from kis_msj.trading_calendar import TradingCalendar
from kis_msj.lot_completion import completion_event

NOW=datetime(2026,10,26,16)
CODE='005930'

def setup(count=1,days=0,score=0,volatility=1):
    cfg=BotConfig(strategy=StrategyConfig(adaptive_lot_enabled=True,lot_sizing_mode='legacy',
        initial_buy_amount=100000,auto_buy_limit=1200000,absolute_max_investment=1200000))
    lm=LotManager(cfg.strategy)
    first=date(2026,1,1)
    sessions={(first+timedelta(days=i)).isoformat():(first+timedelta(days=i)).weekday()<5 for i in range(400)}
    lm.calendar=TradingCalendar(sessions)
    pm=PositionManager(cfg.strategy,lm);s=LotGridStrategy(cfg,lm);s.positions=pm.positions
    before=sorted(d for d,v in sessions.items() if v and d<NOW.date().isoformat())
    bought=NOW.replace(hour=10) if days==0 else datetime.fromisoformat(before[-days]).replace(hour=10)
    for i in range(count):pm.apply_fill(TradeFill(CODE,'example',OrderSide.BUY,2,10000,f'B{i}',bought,f'L{i}',f'E{i}'))
    p=pm.refresh_from_lots(CODE,10000)
    events=[completion_event(str(i),'BUY' if score>0 else 'SELL',NOW.replace(hour=10).isoformat())
            for i in range(abs(score))]
    p.actual_fill_events=json.dumps(events);lm.set_volatility_multiplier(CODE,volatility)
    return cfg,lm,pm,s,p

@pytest.mark.parametrize('cash,buy,sell',[(0,2,.5),(25000000,1.75,.625),(50000000,1.5,.75),
    (75000000,1.25,.875),(100000000,1,1),(125000000,.875,1.25),(150000000,.75,1.5),
    (175000000,.625,1.75),(200000000,.5,2),(300000000,.5,2)])
def test_piecewise_linear_anchors_and_interiors(cash,buy,sell):
    assert cash_multipliers(cash)==pytest.approx((buy,sell))

@pytest.mark.parametrize('cash',[None,-1,float('nan'),float('inf'),True,'100000000'])
def test_unknown_or_invalid_cash_is_not_zero(cash):
    with pytest.raises(ValueError,match='adaptive_cash_snapshot_unavailable'):cash_multipliers(cash)

def test_cash_after_volatility_and_trend_and_before_decay():
    with historical_clock(NOW):
        _,lm,_,s,p=setup(count=6,days=20,score=-4,volatility=2)
        snapshot=AccountSnapshot(150000000,300000000,0,0)
        details=s.adaptive_buy_details(p,10000,snapshot)
        assert details['effective_buy_gap_pct']==pytest.approx(14*2*1*.75)
        s._adaptive_sell_candidate(p,10000,snapshot)
        lot=lm.open_lots(CODE)[0]
        assert lot.base_target_profit_rate==pytest.approx(.03*2*1.6*1.5)
        assert lot.effective_target_profit_rate==pytest.approx(.03*2*1.6*1.5-.024)
        assert lot.effective_target_profit_rate!=pytest.approx((.03*2*1.6-.024)*1.5)

def test_cash_changes_recompute_same_lot_and_can_create_negative_decay_target():
    with historical_clock(NOW):
        _,lm,_,s,p=setup(count=6,days=20)
        low=AccountSnapshot(0,200000000,0,0);high=replace(low,cash_available=200000000)
        action=s.decide(p,9950,low,RiskDecision(False,('cash_below_minimum',)),RiskDecision(True))
        assert action.side is OrderSide.SELL and action.sell_reason=='AUTO_DECAY_CLEANUP_SELL'
        assert lm.open_lots(CODE)[0].effective_target_profit_rate==pytest.approx(-.009)
        assert s.decide(p,9950,high,RiskDecision(True),RiskDecision(True)) is None
        assert lm.open_lots(CODE)[0].effective_target_profit_rate==pytest.approx(.036)
        assert s.context(p,9950,high).cash_sell_multiplier==2

def test_reentry_price_uses_cash_but_keeps_reserve_gate():
    with historical_clock(NOW):
        _,_,_,s,p=setup(count=0)
        p.rebuy_restriction_active=True;p.last_sell_price=10000
        low=s.adaptive_buy_details(p,9500,AccountSnapshot(50000000,200000000,0,0))
        mid=s.adaptive_buy_details(p,9500,AccountSnapshot(100000000,200000000,0,0))
        high=s.adaptive_buy_details(p,9500,AccountSnapshot(200000000,300000000,0,0))
        assert [low['reentry_target'],mid['reentry_target'],high['reentry_target']]==[9400,9600,9800]
        assert not low['buy_condition_met'] and mid['buy_condition_met']
        blocked=s.adaptive_buy_details(p,9000,AccountSnapshot(1000000,200000000,0,0))
        assert blocked['buy_condition_met'] and blocked['skip_reason']=='adaptive_reentry_cash_insufficient'

@pytest.mark.parametrize('cash',[None,-1,float('nan'),float('inf'),True])
def test_invalid_cash_denies_sell_as_well_as_buy(cash):
    with historical_clock(NOW):
        _,_,_,s,p=setup()
        snapshot=AccountSnapshot(cash,200000000,0,0)
        assert s.decide(p,12000,snapshot,RiskDecision(True),RiskDecision(True)) is None
        assert p.skip_reason=='adaptive_cash_snapshot_unavailable'
        d=s.adaptive_buy_details(p,8000,snapshot)
        assert d['buy_target_price']==0 and not d['buy_condition_met']

def test_submission_guard_rechecks_cash_changed_since_signal(tmp_path):
    with historical_clock(NOW):
        cfg,lm,_,s,p=setup();p.current_price=9700
        high=AccountSnapshot(200000000,300000000,0,0)
        action=s.decide(p,9700,high,RiskDecision(True),RiskDecision(True))
        assert action.side is OrderSide.BUY
        broker=SimpleNamespace(account_snapshot=lambda:AccountSnapshot(50000000,200000000,0,0))
        store=SimpleNamespace(path=tmp_path/'state.sqlite3',open_orders=lambda:(),
            load_lots=lambda:lm.lots,load_positions=lambda:{CODE:p})
        manager=OrderManager(cfg,broker,store,logging.getLogger(__name__))
        request=manager.build_request(p,action,9700)
        assert request is not None
        with pytest.raises(SessionUnavailable,match='adaptive_buy_price_not_reached'):
            manager._adaptive_submission_guard(request)
