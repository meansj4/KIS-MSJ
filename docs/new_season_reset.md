# 새 시즌 reset/archive 절차

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `143 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


새 시즌 준비는 “기존 테스트/운영 기록을 안전하게 보관하고, 보유/미체결/동기화 상태를 깨끗하게 만든 뒤 새 config로 다시 시작하는 절차”입니다. 바로 DB를 지우면 실제 계좌에는 주식이 남아 있는데 내부 DB만 사라질 수 있으므로, 반드시 백업, 전량매도 예정표, 실제 계좌 잔고 확인, 체결 동기화, DB 초기화 순서로 진행해야 합니다.

사용자 친화적 용어:

- archive = 이전 시즌 백업
- liquidation plan = 전량매도 예정표
- KIS balance snapshot = 실제 계좌 잔고 확인 자료
- manual SELL request = 봇에게 전량매도 요청
- reset = DB 초기화

## UI 새 시즌 준비 마법사

UI의 “새 시즌 준비” 탭은 아래 순서로 현재 상태와 다음 행동을 보여줍니다.

1. 이전 시즌 백업: DB/config/log를 archive로 보관합니다.
2. 실제 계좌 잔고 확인: DB 보유수량과 KIS 실제 잔고 비교용 snapshot이 필요합니다.
3. 전량매도 예정표 생성: 현재 DB와 KIS snapshot 기준으로 매도 대상 LOT을 계산합니다.
4. 전량매도 요청 생성: UI가 직접 주문하지 않고 `manual_order_requests` 큐에 요청만 만듭니다.
5. 체결 및 동기화 확인: 주문 체결과 reconciliation 완료 여부를 확인합니다.
6. DB 초기화: OPEN LOT 0개, 미체결 0개, 미처리 수동 요청 0개, sync mismatch 없음일 때만 가능합니다.
7. 새 100종목 config 적용 확인: `expansion_100_safe`와 KOSPI 100 후보군을 확인합니다.
8. 새 시즌 시작 준비 완료: 모든 차단 조건이 해소되면 준비 완료 상태가 표시됩니다.

현재 UI에서는 아래 작업도 직접 실행할 수 있습니다.

- 백업 dry-run / 백업 생성
- KIS 잔고 snapshot JSON 경로를 입력해 전량매도 예정표 dry-run / 생성
- 예정표 파일 경로와 `전량매도 요청 확인` 문구를 입력해 manual SELL request dry-run / 생성
- `RESET 확인` 문구를 입력해 reset dry-run / DB 초기화 실행

주의: UI 버튼도 KIS 주문 API를 직접 호출하지 않습니다. 전량매도 요청 생성은 `manual_order_requests` 큐에 `SELL / REQUESTED`를 넣는 작업이며, 실제 주문은 실행 중인 Bot Core가 기존 runtime pause, risk guard, open order guard, order_manager 경로를 거쳐 처리합니다.

`request_creation_possible=false`는 내부 상태값입니다. UI에서는 대신 “전량매도 요청 생성 불가”, “전량매도 예정표가 없습니다”, “KIS 잔고 확인 자료가 만료되었습니다” 같은 사용자용 문구와 다음 행동을 먼저 표시합니다.

plan status 의미:

- `ACTIVE`: 현재 전량매도 예정표가 유효합니다.
- `EXPIRED`: 예정표가 오래되어 새로 만들어야 합니다.
- `SUPERSEDED`: 더 최신 예정표가 있어 이 예정표는 사용할 수 없습니다.
- `USED`: 이미 전량매도 요청 생성에 사용된 예정표입니다.
- `BLOCKED`: 차단 사유가 있어 사용할 수 없습니다.

DB 초기화 가능 조건:

- OPEN LOT 0개
- 미체결 주문 0개
- 미처리 manual request 0개
- `SYNC_REQUIRED` 0개
- lot quantity mismatch 0개
- 실제 계좌 잔고와 DB 수량 불일치 없음

이 절차는 기존 운영 기록을 보존한 뒤 새 후보군과 새 리스크 한도로 다시 시작하기 위한 안전 장치입니다. 스크립트 기본값은 dry-run이며, 실거래 주문 API를 호출하지 않습니다.

## 기본 원칙

- 기존 config, DB, logs는 삭제하지 않고 `archive/reset_YYYYMMDD_HHMMSS/` 아래로 복사합니다.
- DB 초기화는 `RESET 확인` 문구가 있어야 하며, open order 또는 sync mismatch가 있으면 차단됩니다.
- 전량매도는 즉시 주문하지 않고 liquidation plan 파일만 생성합니다.
- 전량매도 요청이 필요하면 별도 확인 후 manual order request 경로로만 처리해야 합니다.
- manual SELL request가 생성되더라도 실제 fill 전에는 lots/positions가 바뀌면 안 됩니다.

## dry-run 점검

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --liquidation-plan --profile expansion_100_safe --dry-run
```

이 명령은 어떤 파일도 삭제하거나 변경하지 않고, archive/config/liquidation 계획을 JSON으로 미리 보여줍니다.

## archive + 새 config 적용

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --profile expansion_100_safe --execute
```

동작:

- 현재 config/DB/log를 archive 폴더에 백업합니다.
- config의 후보 종목을 KOSPI 100 후보군으로 교체합니다.
- `risk.profile=expansion_100_safe`를 적용합니다.
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`로 시작합니다.

## DB 초기화

DB 초기화는 기존 보유/미체결/동기화 상태가 완전히 정리된 뒤에만 수행해야 합니다.

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --execute
```

차단 조건:

- orders에 `REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW` 같은 진행 중 주문이 남아 있음
- manual_order_requests에 `REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING` 같은 진행 중 요청이 남아 있음
- OPEN LOT이 남아 있음
- positions에 `SYNC_REQUIRED` 상태가 있음
- positions에 lot quantity mismatch가 있음
- KIS/DB balance mismatch가 있음

## 전량매도 계획

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute
```

이 명령은 `exports/liquidation_plan_YYYYMMDD_HHMMSS.json` 파일을 만듭니다. 주문 요청은 만들지 않습니다.

계획 확인 항목:

- 종목 코드
- LOT ID
- DB 잔여수량
- 현재가 기준 예상 매도금액
- 예상 손익

실제 매도 전 확인:

- KIS 실제 잔고와 DB OPEN LOT 수량이 일치하는지 확인
- 미체결 주문이 없는지 확인
- 수동매도 요청은 Bot Core/manual_order_requests 경로로만 생성
- 체결 reconciliation 후 lots remaining quantity가 0인지 확인

## 첫 운영 전 체크리스트

- DB 백업 완료
- 기존 logs archive 완료
- 기존 config archive 완료
- 기존 보유 전량매도 완료 여부 확인
- KIS 잔고와 DB positions/lots 불일치 없음
- manual_order_requests 미처리 `REQUESTED` 없음
- orders 중 `REQUESTED`/`PARTIAL` 없음
- fills 중 미반영 항목 없음
- `enable_execution_raw_log=true`
- `live_trading=false` 상태에서 paper/mock 테스트 통과
- live trading 전환 전 사용자 명시 확인
## Liquidation plan latestness guard

전량매도 예정표는 고정 문서가 아니라 “생성 시점의 DB OPEN LOT 상태 + KIS 잔고 snapshot”입니다. 따라서 예전에 만든 plan을 나중에 그대로 재사용하면 안 됩니다.

plan 파일에는 다음 메타데이터가 저장됩니다.

- `plan_id`, `created_at`
- `db_snapshot_at`, `kis_balance_snapshot_at`
- `source_db_path`, `source_kis_snapshot_path`
- `db_open_lot_hash`, `kis_snapshot_hash`
- `open_lot_count`
- `pending_order_count`
- `pending_manual_request_count`
- `sync_required_count`
- `lot_mismatch_count`
- `status`: `ACTIVE`, `EXPIRED`, `SUPERSEDED`, `USED`, `BLOCKED`
- `expires_at`, `max_age_minutes`

새 plan을 생성하면 기존 `ACTIVE` plan은 `SUPERSEDED`로 바뀝니다. 전량매도 manual SELL request를 만들기 직전에는 아래를 다시 검증합니다.

1. confirm text가 `전량매도 요청 확인`인지 확인
2. plan이 존재하고 `ACTIVE`인지 확인
3. 현재 DB OPEN LOT hash가 plan의 `db_open_lot_hash`와 같은지 확인
4. KIS balance snapshot hash가 plan의 `kis_snapshot_hash`와 같은지 확인
5. plan이 만료되지 않았는지 확인
6. plan 생성 후 미체결 order나 pending manual request가 생기지 않았는지 확인
7. `SYNC_REQUIRED` 또는 lot quantity mismatch가 없는지 확인
8. DB 수량과 KIS snapshot 수량, sellable quantity가 모두 충분한지 확인

검증 실패 시 `manual_order_requests`를 만들지 않으며, KIS 주문 API도 호출하지 않습니다. 차단 사유는 `liquidation_plan_db_changed`, `liquidation_plan_snapshot_expired`, `liquidation_plan_pending_work_created` 같은 `block_reason`으로 남깁니다.

전량매도 request 생성 후에도 DB reset은 바로 허용되지 않습니다. 모든 수동 SELL request와 orders가 종결되고, OPEN LOT 0개, KIS/DB mismatch 없음, `SYNC_REQUIRED` 0개가 확인되어야 reset이 가능합니다.
## UI 마법사 방식으로 진행하기

UI의 `새 시즌 New Season` 탭은 개발자용 내부 flag를 그대로 보여주는 화면이 아니라, 사용자가 다음 행동을 알 수 있게 단계형 마법사로 구성합니다.

가장 쉬운 사용법은 `새 시즌 준비 계속 진행` 버튼을 누르는 것입니다. 이 버튼은 현재 상태를 확인한 뒤 한 단계씩만 진행합니다.

1. **이전 시즌 백업**: 버튼을 처음 누르면 config/DB/log archive 백업 생성을 확인합니다.
2. **실제 계좌 잔고 확인**: OPEN LOT이 있으면 KIS 잔고 snapshot JSON 경로가 필요합니다. 이 단계는 주문이 아니라 잔고 비교 자료 준비입니다.
3. **전량매도 예정표 생성**: 현재 DB OPEN LOT과 KIS 잔고 snapshot을 기준으로 새 plan을 만듭니다. 기존 ACTIVE plan은 새 plan 생성 시 더 이상 사용하지 않게 됩니다.
4. **전량매도 요청 생성**: plan이 유효하고 DB/KIS 수량이 맞으며 미체결/미처리 요청이 없을 때만 `manual_order_requests`에 SELL 요청을 만듭니다. 확인 문구는 `전량매도 요청 확인`입니다.
5. **체결 및 동기화 확인**: UI는 주문 API를 직접 호출하지 않습니다. 실행 중인 Bot Core가 manual request를 기존 order_manager 경로로 처리하고, fill/reconciliation이 끝나야 다음 단계로 갈 수 있습니다.
6. **DB 초기화**: OPEN LOT 0개, 진행 중 주문 0개, 진행 중 manual request 0개, sync mismatch 없음일 때만 가능합니다. 확인 문구는 `RESET 확인`입니다.
7. **새 config 적용 확인**: `expansion_100_safe` profile과 100종목 후보군이 적용되어 있으면 새 시즌 준비 완료로 표시됩니다.

버튼이 비활성화되거나 진행이 막힐 때는 내부 값보다 사용자용 안내를 먼저 봅니다.

- `liquidation_plan_missing`: 전량매도 예정표를 생성해야 합니다.
- `liquidation_plan_db_changed`: 예정표 생성 후 보유 LOT이 바뀌었으므로 예정표를 다시 만들어야 합니다.
- `liquidation_plan_snapshot_expired`: KIS 잔고 확인 자료가 오래되었으므로 snapshot을 다시 준비해야 합니다.
- `liquidation_plan_pending_work_created`: 미체결 주문 또는 미처리 manual request가 있어 먼저 완료를 기다려야 합니다.
- `reset_open_lot_exists`: 아직 OPEN LOT이 남아 있어 DB 초기화가 불가능합니다.
- `reset_pending_order_exists`: 미체결 주문이 있어 DB 초기화가 불가능합니다.
- `reset_pending_manual_request_exists`: 미처리 manual request가 있어 DB 초기화가 불가능합니다.
- `reset_sync_required`: DB와 실제 계좌 동기화 확인이 먼저 필요합니다.

이 UI 흐름은 KIS 주문 API를 직접 호출하지 않으며, 전량매도도 Bot Core가 기존 runtime pause, risk guard, open order guard, order_manager 경로를 통과한 뒤에만 처리합니다.



## Current reset guard and Runbook ??

This section is the canonical wording for reset guards and PowerShell commands in this document. If older text above is less specific, use this section and `docs/project_handoff_full.md` as the source of truth.

### Reset-blocking order statuses

The DB reset must be blocked if any order is still in one of these in-progress statuses:

- `REQUESTED`
- `PARTIAL`
- `SUBMITTED`
- `ACCEPTED`
- `PENDING`
- `OPEN`
- `NEW`

Terminal order statuses do not block reset by themselves:

- `FILLED`
- `CANCELED`
- `REJECTED`
- `FAILED`
- `EXPIRED`
- `PARTIAL_CANCELED`
- `NONE`

### Reset-blocking manual_order_requests statuses

The DB reset must be blocked if any manual request is still in one of these in-progress statuses:

- `REQUESTED`
- `PROCESSING`
- `ACCEPTED`
- `SUBMITTED`
- `PENDING`
- `OPEN`
- `NEW`
- `CREATED`
- `RETRYING`

Terminal manual request statuses do not block reset by themselves:

- `FILLED`
- `CANCELED`
- `REJECTED`
- `FAILED`
- `BLOCKED`
- `EXPIRED`

Additional reset blockers:

- OPEN LOT exists
- `SYNC_REQUIRED` exists
- `lot_quantity_mismatch` exists
- KIS/DB balance mismatch
- pending liquidation/manual request exists

### PowerShell command convention

Run commands from repository root `C:\MSJ\KIS-MSJ`. Include `$env:PYTHONPATH='src'` when running local modules/scripts. Dry-run and execute commands must be separated. Commands below do not call KIS order APIs.

Archive dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --dry-run
```

Archive execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --execute
```

Liquidation plan dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --dry-run
```

Liquidation plan execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute
```

Manual SELL request dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "전량매도 요청 확인" --dry-run
```

Manual SELL request execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "전량매도 요청 확인" --execute
```

This creates `manual_order_requests` only. It does not call KIS order APIs. The running Bot Core must consume the requests through the existing guard and `order_manager` path.

DB reset dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --dry-run
```

DB reset execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --execute
```

DB reset execute is allowed only when OPEN LOT is 0, in-progress orders are 0, in-progress manual requests are 0, `SYNC_REQUIRED` is 0, lot mismatch is 0, and KIS/DB balance mismatch is absent.

## KIS balance snapshot current implementation status

The current implementation validates a KIS balance snapshot JSON file path supplied by the operator/UI/script. `scripts/prepare_new_season.py` does not currently auto-create this snapshot from KIS. The operator must prepare or select a fresh JSON snapshot before creating liquidation requests. Do not create liquidation requests without a fresh snapshot.

Supported JSON shape:

- A top-level list of position rows, or
- An object with `positions: [...]`

Fields accepted by the current loader:

- code: `code`, `pdno`, or `symbol`
- holding quantity: `holding_quantity`, `hldg_qty`, or `quantity`
- sellable quantity: `sellable_quantity`, `ord_psbl_qty`, or `available_quantity`

`sellable_quantity` falls back to holding quantity if omitted, but real liquidation safety requires an actual sellable quantity from the account snapshot whenever possible. `generated_at` is recommended metadata for the operator/UI, but the current loader does not parse it as a required field. Plan expiration is based on the liquidation plan creation time and `--plan-max-age-minutes`.

If the snapshot is missing, stale by plan age, unparsable, or inconsistent with DB quantities, liquidation request creation must be blocked.
