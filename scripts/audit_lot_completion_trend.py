"""Read-only LOT-completion comparison; no account query or order API."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import re
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from kis_msj.adaptive_policy import cash_multipliers, trend_multiplier
from kis_msj.arcg_backtest import historical_clock
from kis_msj.config import load_config
from kis_msj.fill_state import actual_fill_events
from kis_msj.logger import discover_log_files
from kis_msj.lot_completion import read_completion_history, merge_completions
from kis_msj.lot_manager import LotManager
from kis_msj.trading_calendar import TradingCalendar, completed_lot_counts, local, trend_score
from kis_msj.ui_service import _lot_state_from_row, _position_state_from_row
from kis_msj.volatility import DailyVolatilityCache


def main():
    def forbidden(*args, **kwargs):
        raise RuntimeError('No external account/network access in LOT audit')
    socket.socket.connect = forbidden
    socket.create_connection = forbidden
    cfg_path = ROOT/'config/lot_auto_trader.json'
    cfg = load_config(cfg_path)
    now = datetime.now()
    codes = ('000990', '064350')
    path = Path(cfg.storage_path)
    # A single consistent read transaction. Do not instantiate StateStore.
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        history = read_completion_history(db)
        positions = {row['code']:_position_state_from_row(dict(row)) for row in db.execute(
            'SELECT * FROM positions WHERE code IN (?,?)', codes)}
        lots = [_lot_state_from_row(dict(row)) for row in db.execute(
            "SELECT * FROM lots WHERE code IN (?,?) AND remaining_quantity>0 AND status!='CLOSED' ORDER BY code,buy_filled_at", codes)]
    cash = observed = None
    for log in reversed(discover_log_files(cfg.log_path)):
        with log.open('rb') as handle:
            handle.seek(max(0, log.stat().st_size-8_000_000))
            lines = handle.read().decode('utf-8', errors='replace').splitlines()
        for line in reversed(lines):
            match = re.search(r'cash_target_account_snapshot cash_available=(\d+) snapshot_at=(\S+)', line)
            if match:
                observed = match[2]
                if 0 <= (now-datetime.fromisoformat(observed)).total_seconds() <= 900:
                    cash = int(match[1])
                break
        if observed:
            break
    lm = LotManager(cfg.strategy, {lot.lot_id:lot for lot in lots})
    lm.symbol_positions = positions
    lm.calendar = TradingCalendar(path=path.with_suffix('.krx_calendar.json'), close_time=cfg.market_hours.close_time)
    cache = DailyVolatilityCache(path.with_suffix('.volatility.json'))
    window = lm.calendar.window(now)
    report = {'snapshot_at':now.isoformat(), 'cash_available':cash, 'cash_observed_at':observed,
              'config_sha256':hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
              'operational_db_read_only':True, 'account_api_calls':0, 'orders_sent':0,
              'window':sorted(window), 'symbols':[]}
    with historical_clock(now):
        for code, position in positions.items():
            seen = set()
            old_buys = old_sells = 0
            for event in sorted(actual_fill_events(position),key=lambda e:e['at']):
                if event.get('kind') in {'LOT_COMPLETE','LOT_PROGRESS'}:
                    continue
                at = local(datetime.fromisoformat(event['at']))
                if at > local(now) or at.date().isoformat() not in window or event['order'] in seen:
                    continue
                seen.add(event['order'])
                old_buys += event['side'] == 'BUY'
                old_sells += event['side'] == 'SELL'
            old_score = max(-12,min(12,old_buys-old_sells))
            merge_completions(position, history.get(code,[]))
            events = actual_fill_events(position)
            buys, sells = completed_lot_counts(events,lm.calendar,now)
            score = trend_score(events,lm.calendar,now)
            lm.set_volatility_multiplier(code,cache.multiplier(code))
            item = dict(code=code,old_buy_orders=old_buys,old_sell_orders=old_sells,
                        old_score=old_score,complete_buy_lots=buys,complete_sell_lots=sells,score=score,
                        old_sell_trend_multiplier=trend_multiplier(-old_score),sell_trend_multiplier=trend_multiplier(-score),
                        volatility_multiplier=cache.multiplier(code),volatility_as_of=cache.get(code).as_of,lots=[])
            if cash is not None:
                base = lm.current_target_profit_info(code,cash_available=cash)[0]
                item['cash_sell_multiplier'] = cash_multipliers(cash)[1]
                item['base_profit_pct'] = base*100
                for lot in lm.open_lots(code):
                    stored_pct, stored_target = lot.target_profit_pct,lot.target_sell_price
                    sessions = lm.calendar.completed_since(lot.buy_filled_at,now)
                    lm.update_lot_target_metadata(lot,position.current_price,now,base)
                    item['lots'].append(dict(lot_id=lot.lot_id,bought=lot.buy_filled_at,buy_price=lot.buy_price,
                        remaining_quantity=lot.remaining_quantity,completed_sessions=sessions,
                        decay_pp=len(lm.open_lots(code))*.1*sessions/5,stored_profit_pct=stored_pct,
                        stored_target_price=stored_target,corrected_profit_pct=lot.target_profit_pct,
                        corrected_target_price=lot.target_sell_price))
            report['symbols'].append(item)
    out=ROOT/'reports/lot_completion_trend_20261026'
    out.mkdir(parents=True,exist_ok=True)
    (out/'current_targets.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 완료 LOT 추세 기준 정정 및 현재 장부 비교','',
           f"조회 시각: {report['snapshot_at']}. 원본 DB는 `mode=ro`, 단일 읽기 트랜잭션으로 조회했다. 계좌 API·주문·DB 수정·봇 재시작은 실행하지 않았다.",'',
           '기존 정책은 체결된 주문 수를 세어, 하나의 LOT을 여러 주문으로 나눠 팔면 매도 추세가 부풀었다. 수정 정책은 원 주문수량을 모두 산 LOT당 +1, 잔여수량이 0인 전량 매도 LOT당 -1이다. 완료 시점의 최근 5거래일을 사용하며 부분매수·부분매도는 점수가 없다. 창과 배율 수식은 유지한다.','',
           f"거래일 창: {', '.join(report['window'])}. 최근 주문가능현금: {cash:,}원 ({observed})." if cash is not None else '최근 현금 미확인: 목표수익률 비교는 계산하지 않았다.','',
           '| 종목 | 기존 매수/매도 주문 수 | 기존 점수 | 완전 매수/전량 매도 LOT 수 | 수정 점수 | 매도 추세 배율 |',
           '|---|---:|---:|---:|---:|---:|']
    names={'000990':'DB하이텍','064350':'현대로템'}
    for item in report['symbols']:
        lines.append(f"|{names[item['code']]}|{item['old_buy_orders']} / {item['old_sell_orders']}|{item['old_score']}|{item['complete_buy_lots']} / {item['complete_sell_lots']}|{item['score']}|{item['old_sell_trend_multiplier']} → {item['sell_trend_multiplier']}|")
    lines += ['', '아래 수정값은 같은 조회 시점 장부와 최근 현금으로 메모리에서 계산한 결과다. DB에 저장하거나 실행 중인 봇에 적용한 값이 아니다. DB 저장값은 별도 열로 구분했다. 목표가는 기존 호가 올림 함수를 그대로 사용한다.','',
              '| 종목 | 매수일 | LOT 매수가 | 완료 거래일 | Decay(%p) | DB 저장 목표% | 수정 후 목표% | 수정 후 목표가 |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for item in report['symbols']:
        for lot in item['lots']:
            lines.append(f"|{names[item['code']]}|{lot['bought'][:10]}|{lot['buy_price']:,}|{lot['completed_sessions']}|{lot['decay_pp']:.2f}|{lot['stored_profit_pct']:.4f}|{lot['corrected_profit_pct']:.4f}|{lot['corrected_target_price']:,}|")
    lines += ['', '완료 복원은 원 주문·체결 합계·LOT 수량·귀속 증거를 대조한다. 불명확한 과거 기록은 주문 건수나 추정 완료로 대체하지 않는다. 닫힌 과거 LOT도 추세 증거에 포함한다. 원본 JSON: `reports/lot_completion_trend_20261026/current_targets.json`.', '',
              '재현: `.\\.venv\\Scripts\\python.exe scripts/audit_lot_completion_trend.py`. 운영 장부와 현금이 변하면 결과도 변한다. 정책 수식·한계는 [현재 거래 정책](CURRENT_TRADING_POLICY.md), 대표 입력 조합은 [시나리오](ADAPTIVE_CASH_SCENARIOS.md)를 참조한다.','']
    (ROOT/'docs/LOT_COMPLETION_TREND_FIX.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':
    main()
