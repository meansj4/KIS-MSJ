# KIS LOT Bot Local UI

이 UI는 기존 자동매매 루프와 분리된 로컬 관제/제어 화면입니다. UI 서버 실행만으로 자동매매가 시작되거나 KIS 주문 API가 호출되지 않습니다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765` 를 엽니다. 서버는 기본적으로 localhost만 허용하며, 외부 네트워크 공개 용도로 만들지 않았습니다.

패키지를 editable 설치해서 쓰는 경우 `kis-lot-ui --config config\lot_auto_trader.json` 명령도 사용할 수 있습니다.

## 구현된 기능

- Dashboard: 봇 상태 추정, 시장 시간 상태, 실거래 위험 배너, 계좌/전역 리스크 요약, 상태별 종목 수, 주문 상태 수, 주요 경고
- Stocks: config의 종목, enabled/risk flag, 현재 position/lots/decision 요약
- Lots: OPEN/CLOSED LOT 현시, 손익률, stale/cleanup 후보, dedupe 관련 파생 정보
- Orders/Fills: 주문/체결 테이블, `dedupe_key_type`, duplicate 표시
- Logs: 기존 log 파일 tail, 키워드/레벨/event 필터 API, 민감정보 마스킹
- Config: JSON 조회, validation, 백업 후 atomic save, 저장 이력 기록
- Runtime Control: 전체 주문/매수/매도/cleanup/reentry pause, emergency stop
- Execution Mapping Check: 첫 실체결 raw mapping 검증 로그 요약
- Decision Preview: 주문 API 호출 없이 현재 DB/config/가격 기준 dry-run 후보 확인

## 안전 정책

- UI에는 실제 주문 요청 API가 없습니다.
- `/api/reconciliation/apply` 는 현재 단계에서 DB 반영도 비활성화되어 있으며 상태 메시지만 반환합니다.
- config 저장 전 `config/backups/` 에 백업을 만들고, 임시 파일을 통한 atomic replace 후 다시 읽어 round-trip 검증합니다.
- 변경 이력은 `config/config_change_history.jsonl` 에 기록됩니다.
- runtime 제어는 `config/runtime_control.json` 에 저장되고, 매매 루프의 주문 요청 직전 차단 조건으로 적용됩니다.

## Runtime Control

`config/runtime_control.json` 필드:

- `all_orders_paused`
- `buy_paused`
- `sell_paused`
- `cleanup_paused`
- `reentry_paused`
- `reason`
- `updated_at`
- `updated_by`
- `expires_at`

차단 사유는 decision/order 로그의 `final_block_reason` 으로 남습니다.

## 민감정보 마스킹

UI 로그 표시는 아래 키 또는 계좌번호처럼 보이는 긴 숫자를 마스킹합니다.

- `account`, `acct`, `cano`, `acnt`
- `appkey`, `appsecret`
- `token`, `authorization`, `auth`

## 보류된 기능

- 실제 주문 취소 버튼
- DB 직접 수정 maintenance mode
- reconciliation apply
- 차트, 알림 연동, CSV export

이 기능들은 실거래 안전성 검토 후 별도 작업으로 추가하는 편이 안전합니다.
