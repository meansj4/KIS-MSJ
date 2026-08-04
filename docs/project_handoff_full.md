# KIS-MSJ 전체 인수인계

기준: 2026-08-04 현재 워크스페이스. 운영 전 실제 JSON, DB, 실행 프로세스를 다시 확인한다.

## 시스템 구성

- 진입점: `main.py`, `src/kis_msj/main.py`
- 설정: `config/lot_auto_trader.json`
- 실운영 DB: `data/lot_auto_trader_real_test.sqlite3`
- 로그: `logs/lot_auto_trader.log`
- 전략: `src/kis_msj/strategy.py`
- 포지션 상태: `src/kis_msj/position_manager.py`
- 주문·체결: `src/kis_msj/order_manager.py`, broker/reconciliation 모듈
- 저장소: `src/kis_msj/state_store.py`
- UI: `src/kis_msj/ui_server.py`, `src/kis_msj/ui_service.py`

## 전략 요약

가격대별 LOT 금액과 종목 최대액을 최초 진입가로 사이클 동안 고정한다. 최대 12 LOT, 추가매수 간격 -4/-8/-12/-16/-20/-24%, 목표수익 +3/+2.5/+2/+1.5/+1/+0.5%다. 비용 추정은 매도금액 0.21%다.

전량 이익실현 후 일반·trailing 재진입을 운영한다. trailing은 매도 기능이 아니라 재진입 기능이다. 매수차단 종목은 매도만 허용하며 청산예정 종목은 전량 종료 후 거래중지가 된다.

심각손실 recovery는 완료 종가 -40% 이하에서 시작하고 최저 완료 종가를 갱신한다. 요구 반등은 +10%에서 30 캘린더일 동안 0%까지 감소하며 만료일에는 강제매도한다. -40% 위 회복 후에도 추적을 유지한다. 동일 종목 다수 LOT은 오래된 순서이고 일일 개수 제한은 없으나 OPEN 주문 중복방지로 순차 처리된다.

오래 보유했다는 이유만으로 `REVIEW_REQUIRED`가 되는 조건은 없다. 주요 검토 원인은 종목 손실 -20%, 수량 동기화, 위험 및 LOT 구조 이상이다.

## 운영 설정 요약

- 실거래 활성, 긴급 시장가 비활성
- 매수 지정가 +0.3%, 매도 지정가 -0.5%, timeout 30초
- 09:00~15:30, 시초·종료 각 2분 주문 차단
- 루프 15초, 계좌 스냅샷 최소 30초
- 232 활성종목, 이 중 72개 매수차단·청산 후 중지
- 총 투자 2억원, 전체 3,000 LOT, 일일 신규매수 300건/3천만원 한도

## UI와 데이터

UI 탭은 Portfolio/Risk, Dashboard, Stocks, Lots, Orders/Fills, Logs, Config, Runtime, Manual, New Season, Review다. 쓰기 작업은 확인·preview·dry-run·감사로그를 사용한다. 수동주문은 DB 큐를 거쳐 봇이 처리한다.

포트폴리오 그래프 JSON/CSV export가 있다. 과거 그래프는 LOT/체결/가격의 재구성값이며 실제 과거 예수금 포함 계좌 총자산 원장이 아니다.

## 성능과 정합성

완료 일봉은 하루 단위 bulk cache, positions/lots는 일괄 저장한다. 장외에는 불필요한 전체 종목 walk/save를 피한다. 주문 timeout·취소 후에도 체결 가능성이 있으므로 KIS 재조회로 최종 체결량을 확정한다.

## 변경·배포 체크리스트

1. `git diff`와 실제 JSON 변경 범위를 확인한다.
2. `\.venv\Scripts\python.exe -m pytest`를 실행한다.
3. 봇 중복실행, OPEN 주문, sync 상태를 확인한다.
4. DB·설정을 백업한다.
5. 안전하게 프로세스를 재시작한다.
6. 시작 reconciliation, UI Runtime, 첫 루프 profile을 확인한다.

최근 deep-loss DB 필드 추가는 프로세스 재시작 시 자동 migration 대상이다. 실행 중인 봇은 재시작 전까지 새 코드가 적용됐다고 간주하지 않는다.
