# 로컬 운영 UI

## 실행

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765`로 접속한다. 외부에 노출하도록 바인딩하지 않는 것이 기본 원칙이다.

## 화면 구성

- Portfolio/Risk: 계좌·위험·실행 정합성 요약
- Dashboard: 종목별 요약과 포트폴리오 변화
- Stocks: 설정 종목, 활성화, 매수차단, 청산 후 중지 정책
- Lots: 개별 LOT의 매수가·목표·상태·심각손실 recovery 정보
- Orders/Fills: 주문과 실제 체결 내역
- Logs: 봇 로그 열람
- Config: 설정 스키마 기반 검증·백업·수정
- Runtime: 시작, 일시정지, reload, 긴급정지 등 실행 제어
- Manual: 수동주문 미리보기와 요청 큐
- New Season: 백업, 잔고 스냅샷 검증, 청산계획, 초기화 마법사
- Review: `REVIEW_REQUIRED` 사유 확인, 재검사와 운영자 확인

## 쓰기 작업의 원칙

조회 화면은 DB와 설정을 읽는다. 설정변경·런타임 제어·수동주문·정합성 복구·신규시즌 작업은 확인문구와 검증 절차가 있는 보호된 API를 거친다. UI의 수동주문은 KIS API를 직접 호출하지 않고 `manual_order_requests` 큐에 기록하며 봇이 이를 소비한다. `ui_manual_trading_enabled=false`이면 생성 기능 전체가 차단된다.

설정 항목에 `requires_restart`가 표시되면 저장만으로 실행 중 전략 객체가 바뀌었다고 가정하면 안 된다. 주문 중·동기화 이상·봇 중복실행 상태에서 재시작하지 않는다.

## 그래프 내보내기

포트폴리오 이력 그래프에는 JSON과 CSV 내보내기가 있다.

- JSON: 조회기간, 지표 정의, 가격 기준, 데이터 품질 주석, 시계열을 포함한다.
- CSV: Excel 호환 UTF-8 BOM과 시계열 열을 포함한다.
- 이력은 LOT·체결·보유 가격으로 재구성한 분석값이다. 과거 각 날짜의 실제 KIS 총자산 스냅샷이 아니므로 예수금 변화나 입출금을 완전히 복원하지 못한다.

따라서 실제 자산 증감 판단은 KIS 계좌 총평가와 입출금 내역을 함께 사용해야 한다.

## 장애 시 확인 순서

1. Runtime에서 봇 실행·pause·emergency 상태를 확인한다.
2. Portfolio/Risk에서 sync, reconciliation, API 오류를 확인한다.
3. Orders/Fills에서 미체결·부분체결·취소 상태를 확인한다.
4. Review에서 종목별 차단 사유를 재검사한다.
5. Logs에서 동일 시각의 주문번호와 오류코드를 확인한다.

DB나 JSON을 수동으로 고치기 전에 UI의 dry-run/preview/backup 기능을 우선 사용한다.
