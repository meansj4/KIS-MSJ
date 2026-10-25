"""The read-only dashboard shows the same cash-adjusted targets as the bot."""
from dataclasses import asdict
from datetime import datetime,timedelta
import json
import pytest
from kis_msj.config import BotConfig,StrategyConfig,StockConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import TradeFill,OrderSide
from kis_msj.position_manager import PositionManager
from kis_msj.storage import StateStore
from kis_msj.ui_service import UIService

CODE='005930'

def service(tmp_path):
    cfg=BotConfig(stocks=(StockConfig(CODE,'Example'),),strategy=StrategyConfig(adaptive_lot_enabled=True),
        storage_path=str(tmp_path/'state.sqlite3'),log_path=str(tmp_path/'bot.log'))
    config_path=tmp_path/'config.json';config_path.write_text(json.dumps(asdict(cfg)),encoding='utf-8')
    lm=LotManager(cfg.strategy);pm=PositionManager(cfg.strategy,lm)
    pm.apply_fill(TradeFill(CODE,'Example',OrderSide.BUY,2,10000,'B',datetime.now(),'L','E'))
    pm.refresh_from_lots(CODE,10000)
    store=StateStore(cfg.storage_path);store.save_positions(pm.positions.values());store.save_lots(lm.lots.values())
    return UIService(config_path,tmp_path/'runtime.json')

def logs(cash,age=0):
    at=(datetime.now()-timedelta(minutes=age)).isoformat(timespec='seconds')
    return [f'{at} INFO cash_target_account_snapshot cash_available={cash} snapshot_at={at} total_asset=300000000']

@pytest.mark.parametrize('cash,buy,sell,target',[(0,2,.5,10150),(100000000,1,1,10300),(200000000,.5,2,10600)])
def test_live_cached_cash_changes_lot_and_preview_targets(tmp_path,monkeypatch,cash,buy,sell,target):
    ui=service(tmp_path);monkeypatch.setattr('kis_msj.ui_service.tail_hourly_logs',lambda *_:logs(cash))
    policy=ui.cash_target_policy();row=ui.lots()[0]
    assert policy['cash_buy_multiplier']==buy and policy['cash_sell_multiplier']==sell
    assert row['target_sell_price']==target
    assert row['current_base_target_profit_rate']==pytest.approx(.03*sell)
    assert ui.portfolio_unrealized_detail()['rows'][0]['target_price']==target
    preview=ui.decision_preview(CODE)['previews'][0]
    assert preview['context']['cash_sell_multiplier']==sell
    assert preview['context']['cash_buy_multiplier']==buy

@pytest.mark.parametrize('lines',[[],logs(0,age=16)])
def test_missing_or_stale_cash_hides_adaptive_targets(tmp_path,monkeypatch,lines):
    ui=service(tmp_path);monkeypatch.setattr('kis_msj.ui_service.tail_hourly_logs',lambda *_:lines)
    assert ui.cash_target_policy()['cash_available_for_target'] is None
    row=ui.lots()[0]
    assert row['target_sell_price'] is None and row['effective_target_profit_rate'] is None
    assert row['target_profit_pct'] is None
    assert ui.portfolio_unrealized_detail()['rows'][0]['target_price'] is None
    assert ui.stock_detail(CODE)['strategy_context']=={}
    preview=ui.decision_preview(CODE)['previews'][0]
    assert not preview['action_created'] and preview['final_block_reason']=='cash_snapshot_unavailable'
