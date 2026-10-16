# KIS-MSJ LOT 자동매매 봇

> 문서 기준: 2026-10-17 튜닝 반영 코드와 `config/lot_auto_trader.json`. 현금 연동 매도·재진입 정책의 적용 예약일은 2026-10-19입니다. 코드와 문서가 다르면 코드 및 실제 로드된 설정이 우선입니다.

한국투자증권(KIS) 국내주식 API를 이용하는 LOT 단위 자동매매 봇입니다. 종목별 최초 진입가에서 LOT 크기와 최대 투자액을 고정하고, 하락 구간에서 LOT을 추가하며, 각 LOT의 목표수익 도달 시 회수합니다. 주문·체결·포지션은 SQLite에 보존하고 로컬 웹 UI에서 조회·통제합니다.

## 빠른 실행

```powershell
.\.venv\Scripts\python.exe main.py --config config\lot_auto_trader.json
```

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

UI: `http://127.0.0.1:8765`

실계좌 설정은 `order.live_trading=true`입니다. 실행 전 계좌 환경변수, DB 경로, 로그 경로를 반드시 확인하십시오. 코드·설정 변경은 실행 중인 프로세스에 자동 반영되지 않으므로 재시작 또는 UI의 안전한 reload 절차가 필요합니다.

## 현재 핵심 정책

- 가격대별 LOT 금액과 종목 최대금액은 최초 진입가로 사이클 동안 고정됩니다.
- 추가매수 하락률은 여섯 LOT 구간 모두 8.8%입니다. 적용 예약일부터 기본 매도 목표는 주문가능현금 2억원당 +0.72%에 비례하고, 각 LOT의 보유기간과 현재 종목 OPEN LOT 하나당 주간 0.05%p decay를 차감합니다.
- 주문가능현금 1,200만원 이하 또는 음수 목표 손실매도에서는 최고원가 LOT 정리 규칙을 사용합니다. 재진입은 오래 기다린 종목부터 새 LOT 금액 60배 이상의 현금 여유를 확인하고 1 LOT만 매수합니다.
- 이익매도는 LOT 단위이며, 고노출 구간에서는 설정에 따라 부분매도가 적용될 수 있습니다.
- `buy_blocked=true`는 신규·추가·재진입 매수만 막고 필요한 매도는 허용합니다.
- 현금 연동 매도·현금 여유 재진입 정책에서는 BUY 자금 조달을 위한 기존 회전매도를 사용하지 않습니다. 기존 회전매도 후 추가매수 가격 보호는 유지합니다. 이전 정책은 [전체 부족금액 기반 교체 정책](docs/cash_rotation_aggregate.md)에 있습니다.
- `retire_after_exit=true` 종목은 남은 LOT이 모두 청산되면 `TRADE_STOPPED_AFTER_EXIT`가 됩니다.
- 심각손실 LOT은 -40% 이하 종가에서 recovery 추적을 시작합니다. 최저 종가 대비 요구 반등률은 +15%에서 60일간 0%까지 선형 감소하고, 60일째에는 강제 정리합니다. 추적 중 -40% 위로 회복해도 상태를 유지합니다.
- 단순 장기보유만으로 `REVIEW_REQUIRED`가 되지는 않습니다.
- 일반 매도 지정가는 현재가 대비 -0.5%, 매수 지정가는 +0.3%이며 호가단위로 보정됩니다.
- UI 손익의 수수료·세금 추정은 매도금액의 0.21%입니다. 실제 증권사 정산과 차이날 수 있습니다.

## 문서 지도

- [전략과 LOT 계산](docs/strategy_lot_sizing.md)
- [2026-10-19 예수금 연동 목표·저현금 LOT 매도](docs/cash_target_profit.md)
- [재진입 대기 순서·60 LOT 현금 기준](docs/cash_capacity_reentry.md)
- [2026-10-17 튜닝 결과와 검증·적용 기록](reports/expanded_tuning_20261017/report.md)
- [로컬 UI](docs/local_ui.md)
- [포트폴리오 대시보드](docs/portfolio_dashboard.md)
- [주문 취소·체결 정합성](docs/order_cancel_reconciliation.md)
- [성능과 루프 운영](docs/performance_loop_tuning.md)
- [분석 데이터와 튜닝](docs/analysis_and_tuning.md)
- [신규 시즌 초기화](docs/new_season_reset.md)
- [전체 인수인계](docs/project_handoff_full.md)

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest
```

운영 데이터의 기준 파일은 `config/lot_auto_trader.json`, `data/lot_auto_trader_real_test.sqlite3`, `logs/lot_auto_trader.log`입니다. DB를 직접 수정하지 말고 UI의 보호된 작업 또는 검증된 스크립트를 사용하십시오.
