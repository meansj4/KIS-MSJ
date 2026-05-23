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
- Config: JSON 조회, validation, backup 후 atomic save, 저장 이력 기록
- Runtime Control: 전체 주문/매수/매도/cleanup/reentry pause, emergency stop
- Execution Mapping Check: 첫 실체결 raw mapping 검증 로그 요약
- Decision Preview: 주문 API 호출 없이 현재 DB/config/가격 기준 dry-run 후보 확인

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
- reconciliation apply
- 차트
- 알림 연동
- CSV export
- Android 앱 구현
- Windows tray launcher 구현

이 기능들은 실거래 안전성 검토 후 별도 작업으로 추가하는 편이 안전합니다.
