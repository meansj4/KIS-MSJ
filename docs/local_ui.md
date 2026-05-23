# KIS LOT Bot Local UI and Control API

이 문서는 현재 구현된 localhost Web UI/API를 기준으로, 향후 Windows 로컬 런처와 Android 원격 관제를 확장할 때 지켜야 할 구조와 보안 원칙을 정리합니다.

## 현재 역할

현재 Web UI는 외부 서비스가 아니라 `127.0.0.1` 전용 로컬 관제 UI입니다. UI 서버 실행만으로 자동매매 루프가 시작되거나 KIS 주문 API가 호출되지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
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
- 실제 주문 요청 API는 없습니다.
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
