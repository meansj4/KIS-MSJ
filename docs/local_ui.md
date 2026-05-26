# KIS LOT Bot Local UI and Control API

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `153 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


## REVIEW_REQUIRED 처리 가이드

`REVIEW_REQUIRED`는 "자동 로직만으로 계속 매수/정리하기에는 사람이 한 번 봐야 한다"는 상태입니다. 이 상태에서는 신규 BUY와 추가 BUY가 차단되고, 수익권 또는 본전 이상 LOT의 `PROFIT_TAKE` SELL은 허용됩니다. 손실 확정인 `CLEANUP_SELL`은 기본적으로 차단합니다.

UI의 종목 상세와 review API는 아래 정보를 보여줍니다.

- `review_reason`: 수동 검토가 된 대표 사유
- `review_created_at`: 처음 수동 검토 상태가 된 시각
- `review_trigger_values`: 당시 손실률, OPEN LOT 수, exposure, stale LOT ID 등 근거 값
- 현재도 조건이 남아 있는지 여부
- 추천 조치: 추가매수 중단, 수익권 LOT 정리, reconciliation 확인, 상태 재평가

지원 API:

- `GET /api/positions/{code}/review-status`
- `POST /api/positions/{code}/review/recheck`
- `POST /api/positions/{code}/review/acknowledge`

`review/recheck`는 현재 DB의 lots/positions 기준으로 review 조건을 다시 계산합니다. 조건이 해소되면 OPEN LOT이 있는 종목은 `HOLDING`으로, OPEN LOT이 없고 profit exit 이력이 있으면 `WAIT_REENTRY`로 복귀할 수 있습니다. 실제 잔고/LOT 불일치가 있으면 `SYNC_REQUIRED`로 보냅니다.

`review/acknowledge`는 사용자가 확인했다는 기록만 남깁니다. 조건이 아직 남아 있으면 `REVIEW_REQUIRED`와 BUY 차단은 계속 유지됩니다. 조건을 무시하고 강제로 `HOLDING`으로 바꾸는 기능은 기본 UI에서 제공하지 않습니다.

이 문서는 현재 구현된 localhost Web UI/API를 기준으로, 향후 Windows 로컬 런처와 Android 원격 관제를 확장할 때 지켜야 할 구조와 보안 원칙을 정리합니다.

## 현재 역할

현재 Web UI는 외부 서비스가 아니라 `127.0.0.1` 전용 로컬 관제 UI입니다. UI 서버 실행만으로 자동매매 루프가 시작되거나 KIS 주문 API가 호출되지 않습니다.

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765` 를 엽니다. 서버는 기본적으로 localhost만 허용하며, 외부 네트워크 공개 용도로 만들지 않았습니다.

editable 설치 상태에서는 다음 명령도 사용할 수 있습니다.

```powershell
kis-lot-ui --config config\lot_auto_trader.json
```

## 역할 분리

### Bot Core

`kis_msj.main`, `strategy`, `order_manager`, `position_manager`, `storage` 계층입니다.

- 자동매매 판단
- 주문 요청
- 체결 reconciliation
- lots/positions/fills/orders 갱신
- runtime pause 적용

이 계층만 KIS 주문 API를 호출할 수 있습니다.

### UI/API Layer

`ui_service.py`, `ui_server.py`, `runtime_control.py` 계층입니다.

- 상태 조회
- DB/log/config 현시
- config 검증 및 안전 저장
- runtime pause/emergency stop 제어
- execution mapping 점검
- dry-run decision preview

이 계층은 봇 관제/제어의 표준 API입니다. 향후 Windows 앱이나 Android 앱도 이 API를 재사용해야 합니다.

### Launcher Layer

아직 구현하지 않은 Windows 로컬 런처/트레이 앱 계층입니다.

- UI 서버 시작
- 브라우저로 UI 열기
- 봇 실행 상태 표시
- runtime pause/emergency stop 버튼 제공

런처는 직접 주문 API를 호출하거나 매매 판단을 해서는 안 됩니다. 반드시 localhost API 또는 `runtime_control.json` 을 통해서만 제어해야 합니다.

### Remote Client Layer

향후 Android 앱 또는 다른 원격 관제 클라이언트입니다.

- 초기 버전은 read-only 관제 중심 권장
- 제어 기능은 pause/emergency stop부터 단계적으로 허용
- config 변경은 기본 비활성 또는 별도 권한으로 설계
- 주문 생성 기능은 만들지 않음

## 구현된 기능

- Dashboard: 봇 상태 추정, 시장 시간 상태, 실거래 위험 배너, 계좌/전역 리스크 요약, 상태별 종목 수, 주문 상태 수, 주요 경고
- Stocks: config 종목, enabled/risk flag, 현재 position/lots/decision 요약
- Lots: OPEN/CLOSED LOT 현시, 손익률, stale/cleanup 후보, dedupe 관련 파생 정보
- Orders/Fills: 주문/체결 테이블, `dedupe_key_type`, duplicate 표시
- Logs: 기존 log 파일 tail, keyword/level/event 필터 API, 민감정보 마스킹
- Config: 항목별 설명/단위/입력 form, diff, validation, backup 후 atomic save, 저장 이력 기록
- Runtime Control: 전체 주문/매수/매도/cleanup/reentry pause, emergency stop
- Execution Mapping Check: 첫 실체결 raw mapping 검증 로그 요약
- Decision Preview: 주문 API 호출 없이 현재 DB/config/가격 기준 dry-run 후보 확인

## 테이블 정렬

Stocks, Lots, Orders/Fills 화면의 테이블은 컬럼 헤더 클릭으로 정렬할 수 있습니다.

- 첫 클릭: 오름차순
- 두 번째 클릭: 내림차순
- 세 번째 클릭: 정렬 해제

현재 정렬 컬럼은 `▲` 또는 `▼` 로 표시됩니다. 정렬은 브라우저 화면에서만 수행되며 DB 값이나 전략 판단에는 영향을 주지 않습니다.

정렬 방식:

- 숫자처럼 보이는 값은 numeric sort
- `YYYY-MM-DD...` 형태의 날짜/시간 값은 timestamp sort
- `true`/`false` 값은 boolean sort
- 빈 값은 정렬 방향과 관계없이 아래쪽에 배치

현재 구현은 client-side sort입니다. 종목/LOT 수가 크게 늘어나면 같은 API 형태를 유지하면서 server-side sort로 확장할 수 있습니다.

기본 정렬:

- Stocks: `position_state` 기준
- Lots: `unrealized_pnl_rate` 낮은 순
- Orders: `requested_at` 최신순
- Fills: `filled_at` 최신순

## Config 화면

Config 탭은 기본적으로 원본 JSON 편집기가 아니라 항목별 form으로 표시됩니다.

각 항목은 다음 정보를 표시합니다.

- 한글 이름
- config key
- 현재 값
- 수정 입력란
- 단위
- 설명
- 저장 형식
- 재시작 필요 여부
- 위험 설정 여부

섹션:

- Strategy
- Risk
- Order
- Market Hours
- Paths / Account / Upstream

원본 JSON은 `고급 / 원본 JSON 보기` 로 접어 두었습니다. raw JSON을 직접 수정해도 validation, diff, backup, atomic save 흐름은 동일하게 적용됩니다.

### Percent / Decimal 변환

일부 값은 UI 표시와 config 저장 단위가 다릅니다.

- `decimal_rate`: UI에서는 percent로 보여주고 저장 시 100으로 나눈 소수로 저장합니다.
  - 예: `normal_reentry_drop_rate` UI `4.0%` -> config `0.04`
  - 예: `pnl_minus_threshold` UI `-1.0%` -> config `-0.01`
- `percent_value`: config 값 자체가 percent입니다.
  - 예: `estimated_fee_tax_pct` UI `0.25%` -> config `0.25`
  - 예: `exposure_buy_bands.drop_pct` UI `4.0%` -> config `4.0`

혼동을 줄이기 위해 Config schema에 `display_format` 과 `config_format` 을 분리해 두었습니다.

### Config 저장 흐름

1. 항목별 form에서 값 수정
2. `변경사항 확인` 클릭
3. 변경 전/후 diff 표시
4. validation 수행
5. 위험 설정 변경 여부 표시
6. `백업 후 저장` 클릭
7. `config/backups/` 에 백업 생성
8. atomic save
9. 저장 후 다시 읽어 round-trip 검증
10. `config/config_change_history.jsonl` 에 변경 이력 기록

위험 설정은 저장 전 이중 확인이 필요합니다.

- `order.live_trading`
- `order.emergency_market_order`
- `strategy.cleanup_enabled`
- `strategy.cleanup_auto_return_to_wait_reentry`
- `order.enable_execution_raw_log`
- `order.cancel_unfilled_on_start`
- `risk.block_on_lot_mismatch`

## 표준 API 구분

### Read-only API

아래 API는 Windows 앱과 Android 앱에서 우선 재사용하기 좋은 조회용 API입니다.

- `GET /api/status`
- `GET /api/config`
- `GET /api/config/schema`
- `GET /api/stocks`
- `GET /api/stocks/{code}`
- `GET /api/positions`
- `GET /api/positions/{code}`
- `GET /api/lots`
- `GET /api/orders`
- `GET /api/fills`
- `GET /api/decisions`
- `GET /api/logs/tail`
- `GET /api/risk/summary`
- `GET /api/execution-mapping/status`
- `GET /api/reconciliation/status`
- `GET /api/runtime`

Android 초기 버전은 위 read-only API만 사용하는 것을 권장합니다.

### Guarded Control API

아래 API는 주문을 직접 내지 않고 runtime pause 또는 config 관리만 수행합니다.

- `POST /api/runtime/pause-all`
- `POST /api/runtime/pause-buy`
- `POST /api/runtime/pause-sell`
- `POST /api/runtime/pause-cleanup`
- `POST /api/runtime/pause-reentry`
- `POST /api/runtime/resume`
- `POST /api/runtime/emergency-stop`
- `POST /api/config/validate`
- `POST /api/config/backup`
- `PATCH /api/config`
- `PATCH /api/stocks/{code}`
- `POST /api/decision-preview`
- `POST /api/reconciliation/dry-run`

원격 클라이언트에서는 config 변경 API를 기본 비활성으로 두고, 별도 인증/승인 설계를 붙인 뒤 허용해야 합니다.

### Disabled or Intentionally Limited API

- `POST /api/reconciliation/apply` 는 현재 단계에서 DB 반영도 비활성화되어 있으며 상태 메시지만 반환합니다.
- KIS 주문 API를 직접 호출하는 UI API는 없습니다. 시장가/지정가 직접 매수/매도 API는 만들지 않았습니다. 다만 `manual_order_requests` 큐에 수동 주문 요청을 생성하는 API는 있습니다.
- 시장가/지정가 직접 매수/매도 API는 만들지 않습니다.
- 주문 취소 API도 현재 범위에서는 만들지 않습니다.

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

runtime control은 config보다 우선 적용됩니다. 차단 사유는 decision/order 로그의 `final_block_reason` 으로 남습니다.

차단 사유:

- `runtime_all_orders_paused`
- `runtime_buy_paused`
- `runtime_sell_paused`
- `runtime_cleanup_paused`
- `runtime_reentry_paused`

## Windows 앱화 방향

완전한 PySide6/PyQt 대시보드로 갈아타는 것은 현재 우선순위가 아닙니다. 기존 Web UI/API를 유지하고, Windows 쪽은 얇은 런처/트레이 앱으로 시작하는 것이 안전합니다.

권장 기능:

- UI 서버 시작/중지
- 기본 브라우저로 `http://127.0.0.1:8765` 열기
- 봇 프로세스 실행 여부 표시
- runtime_control 상태 표시
- 전체 주문 일시정지
- 매수 일시정지
- 매도 일시정지
- emergency stop

금지:

- 런처에서 KIS 주문 API 직접 호출
- 런처에서 자체 매매 판단
- 시장가/지정가 주문 버튼
- DB 직접 수정 버튼

런처는 localhost API 또는 `runtime_control.json` 만 사용해야 합니다.

## Android 원격 관제 방향

초기 Android 앱은 read-only 관제 중심으로 시작하는 것이 안전합니다.

권장 1단계:

- `/api/status`
- `/api/stocks`
- `/api/positions`
- `/api/lots`
- `/api/orders`
- `/api/fills`
- `/api/execution-mapping/status`
- `/api/logs/tail`

권장 2단계:

- pause-all
- pause-buy
- pause-sell
- emergency-stop

권장 3단계:

- config 검증
- 제한된 stock enabled/risk flag 변경

원격에서 config 전체 수정은 초기 버전에서 비활성으로 두는 것을 권장합니다.

## 원격접속 보안 원칙

지금 바로 원격접속을 구현하지 않습니다. 특히 `8765` 포트를 공유기 포트포워딩으로 외부 공개하는 방식은 금지합니다.

금지:

- `0.0.0.0:8765` 로 직접 bind 후 외부 공개
- 공유기 포트포워딩으로 UI 서버 공개
- 인증 없는 public endpoint
- Android 앱에 KIS appkey/appsecret/token 저장
- Android 앱에서 주문 API 직접 호출

권장:

- VPN 기반 접속
- Tailscale, WireGuard 같은 private network
- 향후 secure relay를 쓴다면 강한 인증, 권한 분리, audit log 필수
- remote mode에서는 read-only API 기본값
- 제어 API는 pause/emergency stop부터 단계적 허용
- config 변경은 별도 권한과 이중 확인 필요

## Config 저장 안전장치

- 저장 전 `config/backups/` 에 백업 생성
- validation 실패 시 저장 금지
- 임시 파일을 통한 atomic replace
- 저장 후 다시 읽어 round-trip 검증
- 변경 이력은 `config/config_change_history.jsonl` 에 기록
- `live_trading=true`, `emergency_market_order=true`, `enable_execution_raw_log=true`, `cleanup_enabled=true` 는 UI에서 강한 경고 표시

## 민감정보 마스킹

UI 로그 표시는 아래 키 또는 계좌번호처럼 보이는 긴 숫자를 마스킹합니다.

- `account`, `acct`, `cano`, `acnt`
- `appkey`, `appsecret`
- `token`, `authorization`, `auth`

계좌번호, appkey, appsecret, token 원문은 UI에 표시하지 않는 것이 원칙입니다.

## 보류된 기능

- 실제 주문 취소 버튼
- DB 직접 수정 maintenance mode
- 수동 매수/매도 요청 생성
- reconciliation apply
- 차트
- 알림 연동
- CSV export
- Android 앱 구현
- Windows tray launcher 구현

이 기능들은 실거래 안전성 검토 후 별도 작업으로 추가하는 편이 안전합니다.

## 수동 주문 요청 설계

현재 UI에는 실거래 주문 버튼이 없습니다. 수동 매수/매도는 바로 구현하지 않고, 다음 구조로 별도 단계에서 추가하는 것이 안전합니다.

원칙:

- UI 서버는 KIS 주문 API를 직접 호출하지 않습니다.
- UI는 `manual order request` 또는 `operator request` 만 생성합니다.
- Bot Core가 해당 요청을 읽고 기존 `order_manager`, runtime pause, risk guard, open order guard, live trading guard를 거쳐 주문을 생성합니다.
- 체결은 기존 reconciliation 또는 즉시 체결 확인 경로에서 `fills` 로 저장합니다.
- `lots` 와 `positions` 는 기존 원칙대로 fill insert 성공 후에만 갱신합니다.

권장 request 모델:

```json
{
  "request_id": "MANUAL-...",
  "source": "local_ui_manual",
  "requested_by": "operator",
  "requested_at": "ISO timestamp",
  "code": "005930",
  "side": "BUY",
  "amount": 30000,
  "quantity": 1,
  "lot_id": "",
  "preview": {},
  "runtime_snapshot": {},
  "live_trading": true,
  "confirm_text_verified": true,
  "status": "REQUESTED"
}
```

기본 정책:

- `ui_manual_trading_enabled=false` 를 기본값으로 둡니다.
- 비활성 상태에서도 버튼은 보이되 disabled 처리합니다.
- live trading에서는 사용자가 확인 문구를 직접 입력해야 요청 생성이 가능하게 합니다.
- paper mode에서 request 생성, audit log, Bot Core 소비, reconciliation 반영까지 먼저 검증합니다.

차단 조건:

- `SYNC_REQUIRED`
- `RISK_BLOCKED`
- runtime pause 상태
- 동일 종목 또는 동일 LOT open order 존재
- CLOSED LOT 매도 요청
- 수량 0 또는 예산 부족

감사 로그에는 `requested_by`, `requested_at`, `source`, `code`, `side`, `quantity`, `amount`, `lot_id`, `preview`, `runtime_snapshot`, `live_trading`, `confirm_text_verified` 를 남기는 것을 권장합니다.

## UI 표시 규칙 / 한글화 규칙

테이블 화면은 사용자가 먼저 의미를 이해할 수 있도록 한글 라벨을 기본으로 표시하고, 디버깅을 위해 내부 key를 작은 회색 monospace 글씨로 함께 표시합니다.

예:

- 보유 상태 / `position_state`
- 잔여 수량 / `remaining_quantity`
- 평가손익률 / `unrealized_pnl_rate`
- 중복방지 키 / `dedupe_key_type`

상태값은 badge 형태로 표시합니다.

- `HOLDING`: 보유 중
- `WAIT_REENTRY`: 재진입 대기
- `REVIEW_REQUIRED`: 수동 검토 필요
- `RISK_BLOCKED`: 위험 차단
- `SYNC_REQUIRED`: 동기화 필요
- `OPEN`: 미청산
- `CLOSED`: 청산 완료
- `REQUESTED`: 요청됨
- `PARTIAL`: 부분체결
- `FILLED`: 체결완료
- `CANCELED`: 취소됨
- `REJECTED`: 거절됨
- `BUY`: 매수
- `SELL`: 매도

색상 규칙:

- 수익/양수: 초록 계열
- 손실/음수: 빨강 계열
- 주의/대기/부분체결: 주황 계열
- 위험/차단/거절/동기화 필요: 빨강 계열
- 일반 상태: 파랑 또는 회색 계열

표시 규칙:

- 빈 값, `None`, 빈 문자열은 `-` 로 표시합니다.
- 숫자, 금액, 수량, 손익률 계열 컬럼은 우측 정렬합니다.
- raw key와 내부 값은 작은 회색 monospace 스타일로 병기합니다.
- `skip_reason`, `final_block_reason`, `action_execution_state`, `dedupe_key_type` 은 가능한 경우 한글 설명을 badge와 함께 표시합니다.

## 수동 주문 요청 구현 상태

수동 주문은 UI가 직접 주문 API를 호출하지 않고 `manual_order_requests` 큐에 요청만 생성하는 구조입니다. 수동 주문 요청 탭의 “수동 주문 요청 목록”도 Stocks/Lots/Orders/Fills와 동일하게 핵심 컬럼만 기본 표시하고, `컬럼 선택`에서 숨긴 필드를 펼쳐 볼 수 있습니다.

`PROCESSING` 상태가 오래 지속되고 `linked_order_id`가 비어 있으면 주문 생성 전 단계에서 멈췄을 가능성이 있습니다. UI는 이를 “처리 멈춤 가능성”으로 표시합니다. 이 경우 운영자는 해당 요청을 `재시도 대기`로 되돌리거나 `차단 처리`할 수 있습니다. 단, `linked_order_id`가 있는 요청은 이미 주문 생성 단계까지 간 것으로 보므로 재시도/차단 버튼이 비활성화됩니다. 실제 주문 상태를 먼저 확인해야 합니다.

저장 위치:

- SQLite table: `manual_order_requests`

주요 필드:

- `request_id`
- `source`
- `requested_by`
- `requested_at`
- `code`
- `side`
- `amount`
- `quantity`
- `lot_id`
- `order_type`
- `preview_json`
- `runtime_snapshot_json`
- `live_trading`
- `confirm_text_verified`
- `status`
- `block_reason`
- `linked_order_id`
- `processing_started_at`
- `processing_claimed_by`
- `claim_attempt_count`
- `last_processing_error`
- `stale_processing_reason`
- `created_at`
- `updated_at`

기본값:

- `ui_manual_trading_enabled=false`

이 값이 false이면 UI 버튼과 API 요청 생성이 모두 차단됩니다.

처리 흐름:

1. UI가 preview API로 차단 조건과 예상 금액/손익을 계산합니다.
2. live trading이면 사용자가 `수동주문 확인` 문구를 입력해야 합니다.
3. UI가 `manual_order_requests` 에 요청을 생성합니다.
4. Bot Core가 `REQUESTED` 상태 요청을 읽습니다.
5. Bot Core가 runtime pause, risk guard, open order guard, live trading confirm, symbol/lot 상태를 검증합니다.
6. 통과한 요청만 기존 `order_manager` 경로로 주문 요청됩니다.
7. 체결은 기존 reconciliation 또는 즉시 체결 확인 경로로 `fills` 에 저장됩니다.
8. `lots` 와 `positions` 는 fill insert 성공 후에만 갱신됩니다.

UI 서버는 KIS 주문 API를 직접 호출하지 않습니다.

## 최근 UI 사용성 개선

- Stocks, Lots, Orders, Fills 테이블은 각 표 안쪽에 독립 스크롤 영역을 둡니다. 페이지 맨 아래까지 내리지 않아도 표 하단의 좌우 스크롤바를 바로 사용할 수 있습니다.
- Stocks, Lots, Orders, Fills 테이블은 기본적으로 핵심 컬럼만 표시합니다. `컬럼 선택`을 열면 숨겨진 컬럼을 개별 선택할 수 있고, `전체보기` 버튼으로 모든 내부 필드를 펼쳐 볼 수 있습니다.
- Stocks 탭의 각 종목 행에는 `LOT 보기` 버튼이 있습니다. 이 버튼은 해당 종목의 LOT 목록을 같은 화면 아래 패널에 표시하며, LOT별 잔여 수량, 평가손익률, cleanup/stale 여부 등을 확인할 수 있습니다.
- Stocks 탭의 `수동 매수` 버튼과 LOT 행의 `수동 매도` 버튼은 수동 주문 요청 화면을 열고 종목코드/LOT ID/잔여수량을 자동 입력합니다. UI는 KIS 주문 API를 직접 호출하지 않고 `manual_order_requests` 큐에 요청만 생성합니다.
- 수동 주문 요청 기능은 `ui_manual_trading_enabled=false`가 기본값입니다. 비활성 상태에서는 미리보기/요청 생성이 서버에서 차단됩니다.
- 첫 KIS raw execution mapping 검증이 완료되어 상단 탭의 `Execution Check` 화면은 제거했습니다. 관련 API는 내부 진단용으로 남겨둘 수 있지만, 일반 운영 UI에서는 노출하지 않습니다.

## Config 설명 방식

Config 탭의 설명은 단순히 "무엇인지"만 적지 않고, 실제 자동매매 흐름에서 "어디에 어떻게 쓰이는지"를 함께 적는 방향으로 관리합니다.

`strategy.reentry_drop_rate`는 예전 단일 재진입 기준값이었고, 현재 로직에서는 사용하지 않습니다. 현재 재진입은 아래 두 값으로 분리되어 동작합니다.

- `strategy.normal_reentry_drop_rate`: 전량 PROFIT_TAKE 후 일반 재진입 기준입니다. 기준가격은 전량 매도 사이클의 SELL 체결 VWAP과 median 중 낮은 값인 `normal_exit_anchor_price`입니다.
- `strategy.trailing_activation_gain` / `strategy.trailing_reentry_drop_rate`: 전량 매도 후 더 올라간 종목을 고점 대비 조정 시 다시 보는 trailing 재진입 기준입니다. 활성화 기준가격은 SELL 체결 VWAP과 median 중 높은 값인 `trailing_exit_anchor_price`입니다.

따라서 UI Config 화면에서는 `strategy.reentry_drop_rate`를 더 이상 표시하지 않습니다.

예를 들어 Order 설정의 `price_sample_count`와 `price_sample_interval_seconds`는 함께 동작합니다.

- `price_sample_count`: 주문 직전에 현재가를 몇 번 확인할지 정합니다. BUY/SELL action이 만들어진 뒤 바로 주문하지 않고, 이 횟수만큼 가격을 읽어 주문 직전 가격이 너무 흔들리지 않는지 확인합니다.
- `price_sample_interval_seconds`: 위 가격 읽기 사이의 대기 시간입니다. 예를 들어 샘플 수가 5이고 간격이 0.2초이면, 주문 직전 약 0.8초 동안 가격을 5번 확인합니다.
- 이 샘플들의 변동성이 `risk.max_price_sample_volatility_pct`를 넘으면 주문을 피하는 방식으로 주문 직전 급변 가격을 줄입니다.

지정가 관련 설정도 실제 주문 가격 계산 방식을 함께 설명합니다.

- `buy_limit_markup_pct`: 매수 지정가를 현재가보다 얼마나 높게 둘지 정합니다. 예: 현재가 10,000원, 값 0.3이면 약 10,030원 지정가 매수입니다.
- `sell_limit_markdown_pct`: 매도 지정가를 현재가보다 얼마나 낮게 둘지 정합니다. 예: 현재가 10,000원, 값 0.3이면 약 9,970원 지정가 매도입니다.

## Stocks 위험 플래그 한글 라벨

Stocks 탭의 위험 플래그 컬럼은 한글 라벨과 내부 key를 함께 보여줍니다.

- `trading_halted`: 거래정지
- `administrative_issue`: 관리종목 이슈
- `investment_alert`: 투자주의/경고
- `audit_opinion_issue`: 감사의견 이슈
- `delisting_risk`: 상장폐지 위험
- `accounting_issue`: 회계 이슈
- `liquidity_warning`: 유동성 경고
## Auto Refresh / Bot Loop Control

UI 상단에는 `새로고침` 버튼과 자동 갱신 설정이 있습니다.

- `새로고침`: 현재 보고 있는 탭의 데이터를 즉시 다시 읽습니다.
- `자동 갱신`: 설정한 초 간격으로 Dashboard, Stocks, Lots, Orders/Fills, Logs, Runtime, 수동 주문 요청 화면을 다시 읽습니다.
- Config 화면은 사용자가 값을 편집 중일 수 있으므로 자동 갱신 대상에서 제외합니다.
- 자동 갱신은 UI 표시만 갱신하며 DB 값이나 매매 판단을 바꾸지 않습니다.

Runtime Control 화면에는 봇 루프 제어 버튼이 있습니다.

- `Start / 루프 재개`: 이미 실행 중인 봇 프로세스가 `bot_paused=true` 때문에 쉬고 있을 때 다시 루프를 돌게 합니다.
- `Loop Pause`: 봇 프로세스는 켜 둔 채로 다음 루프부터 자동 판단/수동 요청 소비를 멈춥니다.
- `Reset / Config 다시 읽기`: UI에서 config를 저장한 뒤 실행 중인 봇이 다음 루프에서 최신 config를 다시 읽도록 `config_reload_requested=true` 플래그를 남깁니다.

중요한 제한:

- UI는 새 봇 프로세스를 직접 실행하지 않습니다. 먼저 PowerShell 등에서 봇 프로세스가 실행 중이어야 합니다.
- UI는 KIS 주문 API를 직접 호출하지 않습니다.
- 수동 매수/매도 요청도 UI가 주문을 내는 것이 아니라 `manual_order_requests` 큐에 `REQUESTED` 상태로 저장합니다.
- 실행 중인 Bot Core가 다음 루프에서 이 요청을 읽고 runtime pause, risk guard, open order guard, live trading guard를 통과한 경우에만 기존 `order_manager` 경로로 넘깁니다.
- lots/positions/fills는 기존 원칙대로 실제 fill 저장 성공 후에만 갱신됩니다.

수동 주문 테스트 순서:

1. Config 화면에서 `ui_manual_trading_enabled=true`로 바꾸고 백업 후 저장합니다.
2. Runtime Control 화면에서 `Reset / Config 다시 읽기`를 누릅니다.
3. 수동 주문 요청 화면이나 Stocks/Lots 화면에서 Preview를 먼저 확인합니다.
4. live trading이면 `수동주문 확인` 문구를 입력해야 요청 생성이 가능합니다.
5. 요청이 생성되면 DB의 `manual_order_requests`에 기록되고, 실행 중인 봇 루프가 다음 루프에서 소비합니다.
6. 실거래 주문 없이 흐름만 테스트하려면 `order.live_trading=false` 또는 mock/paper 경로에서 먼저 확인합니다.

## Config 배열형 설정 편집

Config 탭에서 현재 기본 로직에 쓰는 `price_lot_bands`, `add_buy_lot_bands`, `target_profit_lot_bands` 같은 배열/구간 형태 값은 기본적으로 JSON textarea가 아니라 표 형태 편집기로 표시합니다.

현재 일반 Strategy 설정 화면은 최종 기본 로직인 `cycle_locked_by_entry_price` 중심으로 표시합니다. `initial_buy_amount`, `auto_buy_limit`, `absolute_max_investment`, `exposure_buy_bands`, `exposure_sell_bands`, `reentry_drop_rate`는 기존 DB/config 호환 또는 legacy mode 해석용으로 남아 있을 수 있지만 일반 UI Config에서는 숨깁니다. 운영자가 조정해야 하는 현재 기준 항목은 `price_lot_bands`, `add_buy_lot_bands`, `target_profit_lot_bands`, `max_lots_per_symbol_default`, normal/trailing reentry, cleanup, stale/review 설정입니다.

- 각 행은 하나의 가격 구간, LOT 구간, 노출 구간을 뜻합니다.
- `행 추가`와 `행 삭제`로 구간을 조정할 수 있습니다.
- 숫자, bool, 문자열 값은 각 셀에서 바로 수정합니다.
- 원본 구조 확인이 필요하면 해당 항목의 `이 항목 원본 JSON 보기`를 열면 됩니다.
- 저장 흐름은 다른 설정과 동일하게 diff, validation, backup, atomic save를 거칩니다.
- 현재값과 변경 결과도 가능한 경우 JSON 덩어리 대신 표/카드 형태로 표시하고, 원본이 필요할 때만 `원본 JSON 보기`를 엽니다.

예를 들어 `target_profit_lot_bands`는 “현재 OPEN LOT 수가 몇 개인지”에 따라 적용할 목표수익률 구간입니다. 1~2 LOT, 3~4 LOT 같은 행을 표로 직접 수정하므로 JSON 문법을 몰라도 값을 바꿀 수 있습니다.
 
## 새 시즌 / 100종목 확장 운용

새 시즌을 시작할 때는 `scripts/prepare_new_season.py`를 사용합니다. 이 스크립트는 기본값이 dry-run이고 KIS 주문 API를 호출하지 않습니다.

- 상세 절차: `docs/new_season_reset.md`
- 100종목 확장 config 설명: `docs/expansion_100_config.md`

UI에서 확인해야 할 핵심 항목:

- Dashboard: 현재 `risk.profile`, 후보 종목 수, enabled 종목 수, 전체 투입 한도, 오늘 신규 매수 수/금액
- Config: `price_lot_bands`, `add_buy_lot_bands`, `target_profit_lot_bands`, `risk.max_new_buy_amount_per_day`
- Reset/New Season 작업 전: open order, SYNC_REQUIRED, lot mismatch, 미처리 manual order request 여부

초기 확장 운용 권장값은 `expansion_100_safe`입니다. 이 프로파일은 총 투입 한도 2천만 원, 하루 신규 initial buy 10종목, 하루 신규 initial buy 주문금액 200만 원, 전체 OPEN LOT 300개를 기본으로 합니다.

## 새 시즌 준비 마법사 사용 방식

새 시즌 준비 탭에는 `새 시즌 준비 계속 진행` 버튼이 있습니다. 이 버튼은 현재 상태를 다시 조회한 뒤 다음으로 진행 가능한 안전 단계 하나만 실행하거나, 막힌 경우 어떤 입력이 필요한지 알려줍니다.

진행 흐름:

1. 먼저 config/DB/log archive 백업을 생성합니다.
2. OPEN LOT이 남아 있으면 KIS 잔고 snapshot JSON 경로를 입력해야 합니다.
3. 입력한 snapshot과 현재 DB를 기준으로 전량매도 예정표를 생성합니다.
4. 예정표가 유효하고 차단 사유가 없으면 `전량매도 요청 확인` 문구를 입력한 뒤 manual SELL request를 생성합니다.
5. 이후 실제 주문/체결/reconciliation은 실행 중인 Bot Core가 기존 안전장치를 통해 처리합니다.
6. OPEN LOT, 미체결 주문, 미처리 manual request, sync mismatch가 모두 없어져야 DB 초기화 단계가 활성화됩니다.
7. DB 초기화는 `RESET 확인` 문구가 있어야 실행됩니다.

중간에 막히면 버튼은 KIS 주문 API를 호출하지 않고, “KIS 잔고 snapshot 경로 필요”, “전량매도 체결/reconciliation 필요”, “RESET 확인 문구 필요”처럼 다음 행동을 안내합니다.

전량매도 예정표 미리보기와 실제 request 생성 가능 여부는 다릅니다.

- 예정표 미리보기/plan 생성: `generated_at`이 없거나 `sellable_quantity`가 없으면 warning을 표시하면서 plan을 보여줄 수 있습니다.
- 실제 전량매도 request 생성: 최신 `generated_at`과 실제 `sellable_quantity`가 포함된 snapshot이 필요합니다.
- `generated_at` 누락, 파싱 실패, snapshot age 초과, `sellable_quantity` 누락, 매도가능수량 부족은 request 생성 버튼을 차단합니다.
- New Season 화면의 `snapshot 검증` 버튼은 plan을 만들기 전에 JSON 경로를 검증합니다. UI는 `전량매도 예정표 미리보기 가능 여부`와 `전량매도 요청 생성 가능 여부`를 분리해서 보여주며, DB OPEN LOT 수량과 snapshot 보유수량이 맞는지도 같이 보여줍니다.
- UI에는 `전량매도 예정표 미리보기 가능`, `전량매도 요청 생성 불가`, `요청 생성 차단 사유`, `미리보기 경고`, `strict 검증 오류`가 구분되어 표시됩니다.

새 시즌 탭은 기본 화면에서 내부 처리 flag를 최소화합니다. `request_creation_possible`, `block_reason`, hash 같은 진단값은 `고급 작업 / 내부 진단 열기` 안에 접어 두고, 평소에는 “막힌 이유”와 “다음에 할 일”만 먼저 보이게 합니다.

UI의 숫자 출력은 너무 긴 소수로 보이지 않도록 정수는 그대로, 소수는 소수점 4번째 자리에서 반올림해 3번째 자리까지만 표시합니다. 원본 JSON 보기에서는 원래 저장값을 확인할 수 있습니다.

Config 화면의 항목별 form은 좌우 스크롤을 줄이기 위해 “항목 설명 / 수정 입력 / 현재값” 3개 영역의 폭을 균형 있게 배분합니다. 현재값이 배열이나 객체인 경우에도 원본 JSON을 바로 펼치지 않고 줄바꿈 가능한 표로 먼저 보여주며, 원본은 접힌 고급 보기에서 확인합니다.

Stocks, Lots, Orders/Fills, 수동 주문 요청 같은 표 화면은 컬럼 헤더 오른쪽 경계에 마우스를 올리면 좌우 화살표 커서가 표시됩니다. 그 상태에서 드래그하면 엑셀 열 너비처럼 컬럼 폭을 직접 조절할 수 있고, 조절값은 테이블/컬럼별로 브라우저 localStorage에 저장되어 새로고침 후에도 유지됩니다.

Config 화면도 항목명/수정 입력/현재값 사이의 세로 구분선에 마우스를 올려 폭을 조절할 수 있습니다. 이 설정 역시 브라우저 localStorage에 저장되며, `기본값` 버튼으로 Config 화면의 저장된 폭을 되돌릴 수 있습니다.

새 시즌 준비 화면도 기본 화면에서는 핵심 상태와 다음 행동만 표시합니다. reset block guides, wizard steps, guidance 같은 내부 진단값은 `고급 작업 / 내부 진단 열기` 안쪽에 접어 두어 평소에는 좌우 스크롤 없이 현재 막힌 이유와 다음 버튼을 먼저 볼 수 있게 했습니다.
## 새 시즌 준비 화면과 plan 최신성

UI의 “새 시즌 준비” 탭은 현재 DB 상태를 다시 읽어 다음 값을 보여줍니다.

- OPEN LOT 수
- 미체결 주문 수
- 미처리 manual request 수
- `SYNC_REQUIRED` 수
- lot quantity mismatch 수
- 현재 risk profile
- 최신 liquidation plan의 생성 시각, status, 만료 여부
- 현재 DB OPEN LOT hash와 plan의 hash 일치 여부

liquidation plan은 과거 파일을 자동 재사용하지 않습니다. plan 생성 후 LOT, 주문, manual request, sync 상태가 바뀌면 `liquidation_plan_db_changed` 또는 `liquidation_plan_pending_work_created` 같은 사유로 전량매도 request 생성이 차단됩니다.

사용자는 “전량매도 예정표가 오래되었습니다”, “보유 LOT이 변경되었습니다”, “KIS 잔고 확인 자료가 만료되었습니다” 같은 문구를 보고 plan을 새로 생성해야 합니다.

## REVIEW_REQUIRED 전용 탭

“수동검토 필요” 탭은 `REVIEW_REQUIRED` 또는 `needs_review=true` 종목을 한곳에 모아 보여줍니다.

표시 정보:

- 종목코드/종목명
- `review_reason`, `review_created_at`, `review_trigger_values`
- 현재 평가손익률
- OPEN LOT 수, STALE LOT 수
- DB 보유수량
- sync 상태와 lot quantity mismatch
- 현재도 남아 있는 trigger reason
- 해제 조건과 추천 조치

제공 액션:

- 상태 재평가: 현재 DB lots/positions 기준으로 review 조건을 다시 계산합니다. 조건이 해소되면 `HOLDING` 또는 `WAIT_REENTRY` 등으로 복귀할 수 있고, sync mismatch가 있으면 `SYNC_REQUIRED`로 전환됩니다.
- 확인/메모: 사용자가 확인했다는 기록만 남깁니다. BUY 차단은 해제하지 않습니다.
- 수동매도 요청으로 이동: UI는 KIS 주문 API를 직접 호출하지 않고 manual order request 경로만 사용합니다.

강제 해제 버튼은 제공하지 않습니다. 조건을 무시하고 `HOLDING`으로 바꾸면 자동매수 재개와 DB/KIS 불일치 위험이 있으므로, 반드시 reconciliation과 상태 재평가를 거쳐야 합니다.


## ?? ?? API ?? ??

?? ?? ?? ??? ???? ?????.

- KIS ?? API? ?? ???? UI API? ????.
- UI? ???/??? ?? ??/?? API? ????.
- ?? `manual_order_requests` ?? ?? ?? ??? ???? API? ????.
- ?? ?? ?? ?? API? ?? ??? ??? Bot Core? ??? ??? ??? ????.
- ?? ??? Bot Core? runtime pause, risk guard, open order guard, live trading guard? ??? ? ?? `order_manager` ??? ?????.
- fill insert ??? `lots`, `positions`, `fills`? ???? ????.

??? ??? ?? ?? API ?????? ??? ?KIS ?? ?? API ?? / manual request ?? API? ????? ???? ???.
