"""Generate deterministic policy examples without account/DB/order access."""
from pathlib import Path
import csv,hashlib,itertools,json,math,socket,sqlite3,sys
from dataclasses import replace
from datetime import date,datetime,timedelta
from types import SimpleNamespace
import logging

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from kis_msj.adaptive_policy import cash_multipliers,trend_multiplier,floor_order_price
from kis_msj.arcg_backtest import historical_clock
from kis_msj.config import load_config
from kis_msj.lot_manager import LotManager,ceil_price
from kis_msj.models import AccountSnapshot,OrderSide,TradeFill
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskManager
from kis_msj.strategy import LotGridStrategy
from kis_msj.order_manager import OrderManager
from kis_msj.trading_calendar import TradingCalendar
from kis_msj.lot_completion import completion_event

NOW=datetime(2026,10,26,16)
EXAMPLES=[('175330','JB금융지주',10000),('024110','기업은행',20000),('035250','강원랜드',25000)]
CASES=[
 ('first','보유 0 최초 진입',dict(n=0,cash=100000000,ratio=1)),
 ('zero_initial','현금 0 최초 진입 차단',dict(n=0,cash=0,ratio=1)),
 ('low_wait','5천만 원: 하락폭 6% 미도달',dict(cash=50000000,ratio=.95)),
 ('mid_buy','1억 원: 하락폭 4% 도달',dict(cash=100000000,ratio=.95)),
 ('high_buy','2억 원: 하락폭 2% 도달',dict(cash=200000000,ratio=.975)),
 ('high_clamp','3억 원: 2억 원 끝점 배율 유지',dict(cash=300000000,ratio=.975)),
 ('buy_trend','매수 순점수 +4·6LOT',dict(n=6,score=4,cash=50000000,ratio=.85)),
 ('volatility','변동성 2.5배·3LOT',dict(n=3,vol=2.5,cash=50000000,ratio=.85)),
 ('low_sell','현금 0: 1.5% 매도 목표 도달',dict(cash=0,ratio=1.02)),
 ('high_sell_wait','2억 원: 6% 매도 목표 미도달',dict(cash=200000000,ratio=1.04)),
 ('sell_trend','매도 순점수 -4·변동성 2배',dict(score=-4,vol=2,cash=150000000,ratio=1.10)),
 ('decay_order','6LOT·20일: 4.5%-2.4%=2.1%',dict(n=6,days=20,cash=150000000,ratio=1.025)),
 ('negative_decay','현금 0·6LOT·20일: -0.9% Decay 매도',dict(n=6,days=20,cash=0,ratio=.995)),
 ('high_decay','2억 원·6LOT·20일: 3.6% 목표 대기',dict(n=6,days=20,cash=200000000,ratio=1.025)),
 ('reentry_low','5천만 원·전량매도 후 재진입 대기',dict(n=0,reentry=True,cash=50000000,ratio=.95)),
 ('reentry_mid','1억 원·전량매도 후 재진입',dict(n=0,reentry=True,cash=100000000,ratio=.95)),
 ('reentry_high','2억 원·전량매도 후 재진입',dict(n=0,reentry=True,cash=200000000,ratio=.975)),
 ('reentry_ceiling','3LOT·최근 매도가가 더 낮아 재진입 제한',dict(n=3,reentry=True,last_sale=.9,cash=100000000,ratio=.9)),
 ('reentry_reserve','가격은 충족하나 2LOT 예비금 부족',dict(n=3,reentry=True,cash=1000000,assets=20000000,ratio=.75)),
 ('max_lots','12LOT 추가매수 한도',dict(n=12,cash=100000000,ratio=.72)),
 ('partial_sell','12LOT 고노출 일반 매도 수량',dict(n=12,cash=100000000,ratio=1.04)),
 ('risk_review','-35% 손실: 종목 매수 위험 차단',dict(n=3,cash=200000000,ratio=.65)),
 ('deep_rebound','심층 손실 저점 대비 반등',dict(cash=100000000,ratio=.60,deep=30)),
 ('deep_deadline','심층 손실 60일 경과',dict(cash=100000000,ratio=.50,deep=60)),
 ('unreachable','고추세·변동성: 요구 하락폭 100% 초과',dict(n=11,score=10,vol=2.5,cash=50000000,ratio=.95)),
 ('cash_missing','현금 스냅샷 미확인: 매수·매도 보류',dict(cash=None,ratio=1.10)),
]

def csvout(path,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def example(cfg,symbol,case):
    code,name,price=symbol;ident,title,overrides=case
    a=dict(n=1,days=0,score=0,vol=1.,cash=100000000,assets=400000000,ratio=1.,reentry=False,last_sale=1.)
    a.update(overrides)
    lm=LotManager(cfg.strategy)
    first=date(2025,1,1)
    # Explicit synthetic sessions. These examples make no historical KRX claim.
    days={(first+timedelta(days=i)).isoformat():(first+timedelta(days=i)).weekday()<5 for i in range(750)}
    lm.calendar=TradingCalendar(days)
    pm=PositionManager(cfg.strategy,lm);s=LotGridStrategy(cfg,lm);s.positions=pm.positions
    before=sorted(d for d,v in days.items() if v and d<NOW.date().isoformat())
    bought=NOW.replace(hour=10) if not a['days'] else datetime.fromisoformat(before[-a['days']]).replace(hour=10)
    sizing=s.lot_sizing_for_new_cycle(price);qty=max(1,int(sizing['lot_unit_amount'])//price)
    for i in range(a['n']):
        at=NOW.replace(hour=10) if a['score']>0 and i>=max(1,a['n']-a['score']) else bought
        pm.apply_fill(TradeFill(code,name,OrderSide.BUY,qty,price,f'B{i}',at,f'L{i}',f'E{i}'))
    p=pm.refresh_from_lots(code,int(price*a['ratio']))
    # A snapshot-level assumed recent ledger, including already closed history.
    b=max(a['score'],0);e=max(-a['score'],0)
    if a['reentry'] and not e:b+=1;e+=1
    if not a['reentry'] and e:b+=1;e+=1
    sides=(['BUY']*b+['SELL']*e) if a['reentry'] else (['SELL']*e+['BUY']*b)
    p.actual_fill_events=json.dumps([completion_event(f'H{i}',side,NOW.replace(hour=15,minute=0,second=i).isoformat())
                                   for i,side in enumerate(sides)])
    p.rebuy_restriction_active=a['reentry'];p.last_sell_price=int(price*a['last_sale']) if a['reentry'] else 0
    lm.set_volatility_multiplier(code,a['vol'])
    if a.get('deep'):
        for lot in lm.open_lots(code):
            lot.deep_loss_started_on=(NOW-timedelta(days=a['deep'])).date().isoformat()
            lot.deep_loss_low_price=int(price*.5);lot.deep_loss_last_close=int(price*.55);lot.deep_loss_observation_count=3
    snapshot=AccountSnapshot(a['cash'],a['assets'],0,0)
    risk=RiskManager(cfg)
    # Actual risk functions, but do not compare invalid cash with numeric limits.
    risk_snapshot=snapshot if a['cash'] is not None else replace(snapshot,cash_available=0)
    action=s.decide(p,p.current_price,snapshot,risk.account_buy_allowed(risk_snapshot,pm.positions),risk.symbol_buy_allowed(p))
    block=p.skip_reason if action is None else ''
    details=s.adaptive_buy_details(p,p.current_price,snapshot)
    candidate=s._adaptive_sell_candidate(p,p.current_price,snapshot)
    selected=candidate.lot if candidate else next(iter(lm.open_lots(code)),None)
    orders=OrderManager(cfg,SimpleNamespace(),None,logging.getLogger(__name__))
    request=orders.build_request(p,action,p.current_price) if action else None
    return dict(case_id=ident,case=title,code=code,name=name,assumed_buy_price=price,current_price=p.current_price,
        cash=a['cash'],volatility=a['vol'],trend_score=a['score'],lots=a['n'],representative_completed_sessions=a['days'],
        reentry=a['reentry'],cash_buy_multiplier=details['cash_buy_multiplier'],cash_sell_multiplier=details['cash_sell_multiplier'],
        buy_gap_pct=details['effective_buy_gap_pct'],normal_buy_target=details['normal_buy_target'],
        reentry_target=details['reentry_target'],final_buy_target=details['buy_target_price'],
        representative_sell_rate=selected.effective_target_profit_rate if selected and a['cash'] is not None else None,
        representative_sell_target=selected.target_sell_price if selected and a['cash'] is not None else None,
        reserve_required=details['reentry_required_cash'],reserve_met=details['reentry_cash_condition_met'],
        strategy_action=action.side.value if action else 'WAIT',reason=action.sell_reason if action and action.side is OrderSide.SELL else action.reason if action else block,
        request_quantity=request.quantity if request else None,request_limit=request.limit_price if request else None)

def main():
    def forbidden(*args,**kwargs):raise RuntimeError('Scenarios forbid network/SQLite connections')
    socket.socket.connect=forbidden;socket.create_connection=forbidden;sqlite3.connect=forbidden
    cfg=load_config(ROOT/'config/lot_auto_trader.json');cfg=replace(cfg,order=replace(cfg.order,live_trading=False))
    out=ROOT/'docs';out.mkdir(exist_ok=True)
    rows=[]
    with historical_clock(NOW):
        for symbol,case in itertools.product(EXAMPLES,CASES):rows.append(example(cfg,symbol,case))
    csvout(out/'ADAPTIVE_CASH_SCENARIO_ACTIONS.csv',rows)
    matrix=[]
    for symbol,cash,vol,score,n,d in itertools.product(EXAMPLES,[0,50000000,100000000,150000000,200000000],
                                                    [.75,1.,2.5],[-4,0,4],[1,3,6,12],[0,5,20]):
        code,name,price=symbol;cb,cs=cash_multipliers(cash)
        gap=(4+2*(n-1))*vol*trend_multiplier(score)*cb
        sell=3*vol*trend_multiplier(-score)*cs-n*.1*d/5
        normal=price*(1-gap/100) if gap<100 else 0
        matrix.append(dict(code=code,name=name,assumed_buy_price=price,cash=cash,volatility=vol,score=score,lots=n,
            completed_sessions=d,cash_buy_multiplier=cb,cash_sell_multiplier=cs,buy_gap_pct=gap,
            buy_ceiling=floor_order_price(normal),sell_target_pct=sell,
            sell_trigger=ceil_price(price*(1+sell/100)) if sell>-100 else 0,
            reentry_ceiling_after_lower_sale=floor_order_price(.9*price*(1-4*vol*cb/100)),
            reentry_ceiling_after_higher_sale=floor_order_price(1.1*price*(1-4*vol*cb/100))))
    csvout(out/'ADAPTIVE_CASH_SCENARIO_MATRIX.csv',matrix)
    selected=[r for r in rows if r['code']==EXAMPLES[0][0]]
    lines=['| 사례 | 현재가 | 예수금(원) | N / D / s / m | 매수 하락폭% | 매수 상한 | 대표 매도 목표% | 판단 | 원인 |',
           '|---|---:|---:|---|---:|---:|---:|---|---|']
    def fmt(x):return '미확인' if x is None else f'{x:,.3f}'.rstrip('0').rstrip('.') if isinstance(x,(float,int)) else str(x)
    for r in selected:
        lines.append('| '+' | '.join([r['case'],fmt(r['current_price']),fmt(r['cash']),f"{r['lots']} / {r['representative_completed_sessions']} / {r['trend_score']} / {r['volatility']}",
            fmt(r['buy_gap_pct']),fmt(r['final_buy_target']),fmt(r['representative_sell_rate']*100 if r['representative_sell_rate'] is not None else None),r['strategy_action'],r['reason'].replace('|',' / ')])+' |')
    doc=f'''# Adaptive LOT V2 예수금 배율과 동작 시나리오

기준 소스: 2026-10-26. 현재 설정의 금액·한도와 실제 전략/위험/주문 작성 함수를 사용한 **가상 입력 시나리오**다. 실계좌·운영 DB·시세 API를 조회하지 않고, 실제 주문·봇 재시작도 실행하지 않는다. 예시 가격은 실시간 시세가 아니다.

## 배율과 적용 순서

| 주문가능현금 | 매수 하락폭 배율 | 매도 목표 배율 |
|---:|---:|---:|
|0원|2|0.5|
|5천만 원|1.5|0.75|
|1억 원|1|1|
|1억 5천만 원|0.75|1.5|
|2억 원 이상|0.5|2|

0~1억, 1억~2억을 각각 직선으로 연결한다. 한 직선이나 서로 역수인 배율은 아니다. 2억 원 초과는 끝점으로 고정하며, 미확인·음수·NaN·무한대·불리언 현금은 0원으로 간주하지 않고 자동 매수·매도를 보류한다. 현금은 `AccountSnapshot.cash_available`(증권사 주문가능현금)이며 승인된 예약금을 다시 차감하지 않는다.

`매수 하락폭 = (4+2×(N-1)) × 변동성m × 매수추세배율 × 예수금매수배율`

`매도 목표% = 3 × 변동성m × 매도추세배율 × 예수금매도배율 − N×0.1×D/5`

`재진입 상한 = 최근 실제 매도가 × (1 − 4×m×예수금매수배율/100)`

N은 현재 LOT 수, D는 각 LOT의 완료 보유거래일, s는 최근 5거래일 완전 매수 LOT 수 − 전량 매도 LOT 수다. 부분체결·부분매도 주문 수는 추세에서 제외한다. 매수 추세는 s의 양수 방향, 매도 추세는 음수 방향을 사용한다. 재진입 상한에는 기존대로 추세를 넣지 않는다. 매수에는 Decay가 없고, 매도 Decay 금액에는 예수금 배율을 곱하지 않는다.

예: N=6, D=20, m=1, s=0, 현금 1억 5천만 원이면 매도는 `3×1.5−2.4=2.1%`다. `(3−2.4)×1.5=0.9%`로 계산하지 않는다. 현금 0원에서는 `3×0.5−2.4=−0.9%`가 되어 기존 음수 Decay 매도 경로를 사용할 수 있다.

## 실제 전략 함수의 분기 예시

JB금융지주 175330은 가상 LOT 매수가 10,000원, 기업은행 024110은 20,000원, 강원랜드 035250은 25,000원으로 가정했다. 계좌 총자산은 기본 4억원이며 예비금 부족 사례는 2천만원으로 별도 지정했다. 종목별 금액/한도는 현재 설정의 해당 가격 구간을 사용한다. 기본 LOT당 수량은 그 예산 내 정수 수량이다.

최근 체결 이력과 가격·계좌 상태는 각각의 독립 시나리오 입력이며 실제 계좌의 과거 수익 경로를 재구성한 것은 아니다. 대표 LOT의 D를 지정하고, 양수 추세의 다른 일부 LOT는 최근 체결로 구성한다. 따라서 같은 종목 내 LOT별 나이는 다를 수 있다. 캘린더는 명시한 합성 평일 일정이며 실제 KRX 휴장일 주장이나 백테스트 성과가 아니다.

{chr(10).join(lines)}

이 표의 BUY/SELL은 전략 신호와 주문 작성 결과다. 운영의 거래시간, 동기화, 5회 가격 안정성, 미해결 주문, 일별 한도, 요청 간격 등은 추가 통과가 필요하다. 현금 0원에서는 하락폭이 계산되어도 매수할 수 없다. 재진입 가격이 충족돼도 2LOT 예비금·총자산 10% 조건이 부족하면 매수하지 않는다. 일반 수익실현 → 독립 심층 회복 → 음수 Decay 우선순위와 고노출 부분매도를 유지한다. 심층 회복의 -40%·반등·60일 경과 조건 자체에는 이 예수금 배율을 곱하지 않는다.

## 전체 조합 파일과 재현

- [슬라이더로 직접 비교하는 계산기](ADAPTIVE_CASH_SCENARIOS.html): 현금·추세·변동성·LOT·거래일을 바꿔 문턱을 확인하는 오프라인 설명 도구.
- [실제 함수 분기 {len(rows)}개](ADAPTIVE_CASH_SCENARIO_ACTIONS.csv): 3종목 × {len(CASES)}개 상황, 가격·배율·목표·원인·요청 수량/가격.
- [수식 조합 {len(matrix)}개](ADAPTIVE_CASH_SCENARIO_MATRIX.csv): 3종목 × 현금5 × 변동성3 × 순점수3 × LOT4 × 거래일3. 필터로 조합을 비교하는 독립 입력 계산표이며 모든 행이 실제 발생 가능한 체결 이력이라는 뜻은 아니다. 연속값의 모든 경우를 유한 표로 나열할 수 없어 기준점·중간값·대표 조합과 별도 경계 테스트로 검증한다.

재현: `.\\.venv\\Scripts\\python.exe scripts/generate_adaptive_cash_scenarios.py`

입력 설정 SHA256: `{hashlib.sha256((ROOT/'config/lot_auto_trader.json').read_bytes()).hexdigest()}`. 설정을 바꾸면 재생성 결과의 LOT 금액·차단 조건은 달라질 수 있다. 이 프로그램은 주문 작성까지만 사용하며 전송·예약·DB 생성 기능을 호출하지 않는다.
'''
    (out/'ADAPTIVE_CASH_SCENARIOS.md').write_text(doc,encoding='utf-8')
    print(json.dumps({'action_scenarios':len(rows),'matrix_rows':len(matrix),'account_calls':0,'db_calls':0,'orders_submitted':0}),flush=True)

if __name__=='__main__':main()
