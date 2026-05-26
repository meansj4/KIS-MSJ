# KIS LOT 자동거래 봇 전체 인수인계 문서

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `156 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


관련 문서:

- [전체 인수인계](project_handoff_full.md)
- [요약본](project_handoff_summary.md)
- [새 thread 첫 메시지용 프롬프트](project_handoff_thread_prompt.md)
- [로컬 UI 문서](local_ui.md)
- [LOT sizing 전략](strategy_lot_sizing.md)
- [새 시즌 reset](new_season_reset.md)
- [100종목 확장 config](expansion_100_config.md)
- [전체 프로젝트 감사/정리 보고서](project_audit_cleanup_report.md)

최신 전체 소스/문서 감사 결과는 [docs/project_audit_cleanup_report.md](project_audit_cleanup_report.md)를 참고한다. 2026-05-27 추가 보강 기준으로 일반 UI Config에서는 legacy exposure/initial amount 항목을 숨겼고, manual order request는 원자적 claim 후 처리하며, New Season 화면에는 KIS balance snapshot validator가 추가되었다.

Last updated: 2026-05-26  
기준 테스트 결과: `156 passed` (`.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check`)  
기준 config profile: `expansion_100_safe`  
주의: 실제 운영값은 실행 시점의 `config/lot_auto_trader.json`, SQLite DB, logs, KIS 계좌 상태를 다시 확인해야 한다.

대상 저장소: `C:\MSJ\KIS-MSJ`  
주요 config: `config/lot_auto_trader.json`  
작성 목적: 새 ChatGPT 채팅방, 새 Codex thread, 새 개발 세션에서 이 문서만 보고 현재 구현 상태와 운영 원칙을 이어받기 위한 인수인계

## 0. 현재 상태 스냅샷

| 항목 | 현재값 |
| --- | --- |
| 문서 작성/갱신 시각 | 2026-05-26 |
| 저장소 경로 | `C:\MSJ\KIS-MSJ` |
| 현재 config 파일 | `config/lot_auto_trader.json` |
| 현재 risk profile | `expansion_100_safe` |
| KOSPI 후보 수 / enabled 수 / manual_only 수 | 100 / 97 / 3 |
| `order.live_trading` | false |
| `strategy.cleanup_enabled` | false |
| `ui_manual_trading_enabled` | false |
| `order.enable_execution_raw_log` | true |
| 최신 테스트 결과 | `156 passed`, pytest cache warning 1개는 기능 실패 아님 |
| 현재 OPEN LOT 수 | 19개로 확인됨 |
| 현재 reset 가능 여부 | 불가. OPEN LOT이 남아 있으므로 차단되는 것이 정상 |
| 현재 liquidation plan 존재 여부 | 실행 시점 `exports/liquidation_plan_*.json` 및 UI `New Season` 탭에서 재확인 필요 |
| 현재 KIS balance snapshot 존재 여부 | 실행 시점 사용자가 준비/선택해야 함. 없으면 전량매도 request 생성 차단 |
| 현재 archive 경로 | `archive/reset_YYYYMMDD_HHMMSS/...` 형식. 실제 최신 archive는 폴더에서 재확인 |
| 다음 1순위 작업 | KIS balance snapshot 준비 -> liquidation plan 생성 -> 전량매도 request 생성 여부 결정 |

현재는 `expansion_100_safe` config가 적용되어 있으나 OPEN LOT이 남아 있으므로 DB reset은 차단되는 것이 정상이다. 다음 단계는 실제 계좌 잔고 확인 자료인 KIS balance snapshot 준비, 전량매도 예정표 생성, 전량매도 request 생성 여부 결정이다.

## 1. 전체 목적과 한 줄 요약

이 프로젝트는 KIS API 기반 KOSPI LOT 단위 자동매매 봇이다. 사용자는 여러 KOSPI 우량 후보군을 소액 LOT 단위로 분산 매수하고, 평균단가 하나가 아니라 개별 LOT의 매수가, 잔여수량, 나이, 손익률, 목표수익률을 기준으로 매수/매도/재진입/손실정리/수동검토를 관리하려고 한다.

한 줄 요약:

> 이 봇은 주문 요청이 아니라 실제 체결 fill이 중복 없이 저장된 뒤에만 `lots`와 `positions`를 갱신하는 LOT 기반 자동거래 시스템이며, UI는 관제/제어/수동 요청 큐 생성만 담당하고 KIS 주문 API를 직접 호출하지 않는다.

핵심 철학:

- 수익률 극대화보다 운영 안정성, 체결 동기화, 과다 진입 방지, 상태 추적성을 우선한다.
- 모든 매수/매도 판단은 LOT 단위로 추적한다.
- 손실 LOT은 무조건 손절하지 않지만, 오래된 손실 LOT이 무한 방치되지 않도록 STALE_LOT, CLEANUP_SELL, REVIEW_REQUIRED로 관리한다.
- 전량 PROFIT_TAKE 이후에는 바로 initial_buy하지 않고 WAIT_REENTRY에서 별도 재진입 조건을 본다.
- 기존 운영 데이터는 삭제하지 않고 archive 후 새 시즌을 시작한다.
- 새 시즌 reset은 실제 계좌/KIS snapshot, DB, manual request, open order가 모두 안전한 상태일 때만 가능하다.

현재 최종 상태:

| 항목 | 현재값 |
| --- | --- |
| risk profile | `expansion_100_safe` |
| 후보 종목 수 | 100 |
| enabled 종목 수 | 97 |
| disabled/manual_only 종목 | 3 |
| `max_active_symbols` | 100 |
| `max_total_invested_amount` | 20,000,000 |
| `max_new_buy_per_day` | 10 |
| `max_new_buy_amount_per_day` | 2,000,000 |
| `max_total_open_lots` | 300 |
| `lot_sizing_mode` | `cycle_locked_by_entry_price` |
| `cleanup_enabled` | false |
| `ui_manual_trading_enabled` | false |
| `live_trading` | false |
| `enable_execution_raw_log` | true |
| 현재 주의 | OPEN LOT이 남아 있으면 DB reset 차단이 정상 |

아직 실제 운용 전 남은 검증:

- 최신 KIS balance snapshot 준비
- liquidation plan, 즉 전량매도 예정표 생성
- 전량매도 manual SELL request 생성 여부 결정
- Bot Core를 통한 매도 처리와 reconciliation 완료
- OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 확인
- DB reset
- 새 시즌 시작 후 첫 실체결 raw execution field mapping 최종 확인

## 2. 절대 깨면 안 되는 핵심 원칙

| 원칙 | 설명 | 깨졌을 때 위험 |
| --- | --- | --- |
| 주문 요청만으로 lots/positions를 바꾸지 않는다 | `orders`는 주문 의도와 주문 상태를 기록할 뿐이다. | 주문 거절, 취소, 부분체결 시 DB와 실제 계좌가 어긋난다. |
| fills insert 성공 후에만 lots/positions를 갱신한다 | `store.record_fill(fill)`이 true인 신규 체결만 `position_manager.apply_fill()`로 간다. | 중복 체결이 LOT/position에 중복 반영된다. |
| duplicate fill 또는 `record_fill_failed`는 apply_fill 금지 | `order_manager._record_filled()`는 record_fill 반환값을 확인한다. | 같은 체결이 여러 LOT 또는 position에 반영된다. |
| UI 서버는 KIS 주문 API를 직접 호출하지 않는다 | UI는 상태 조회, 설정 저장, runtime 제어, manual request 생성만 한다. | 관제 UI 조작이 곧 실거래 주문이 되는 위험이 생긴다. |
| 수동 주문도 UI가 직접 주문하지 않는다 | UI는 `manual_order_requests` 큐만 생성하고 Bot Core가 소비한다. | 자동/수동 주문 안전장치가 갈라진다. |
| Bot Core만 기존 order_manager 경로로 주문한다 | runtime pause, risk guard, open order guard, live trading guard를 통과해야 한다. | 수동 주문이 자동 주문보다 더 위험한 우회로가 된다. |
| DB reset은 안전 조건 충족 후만 가능 | OPEN LOT 0, 진행 중 order 0, pending manual request 0, sync mismatch 없음. | 실제 보유와 DB가 영구히 어긋난다. |
| 기존 DB/log/config는 archive/backup 후 시작한다 | 새 시즌 전 이전 시즌 자료를 보존한다. | 문제 분석과 복구가 불가능해진다. |
| DB/KIS 불일치는 SYNC_REQUIRED로 막는다 | 실제 계좌와 내부 LOT/position이 다르면 신규 주문 차단. | 잘못된 수량으로 매수/매도할 수 있다. |
| REVIEW_REQUIRED는 강제 해제하지 않는다 | recheck, acknowledge, 수동매도, reconciliation 흐름으로 처리한다. | 위험 상태에서 자동 BUY가 다시 열릴 수 있다. |
| cleanup/review/risk 상태에서 BUY 차단 정책 유지 | REVIEW_REQUIRED, RISK_BLOCKED, SYNC_REQUIRED, COOLDOWN은 보수적으로 동작한다. | 손실/불일치/위험 상태에서 물타기가 이어진다. |

## 3. 전체 아키텍처

### Bot Core

| 파일 | 책임 | 중요한 원칙 |
| --- | --- | --- |
| `src/kis_msj/main.py` | 봇 루프, strategy decision 실행, 주문 전 최종 guard, reconciliation, manual request 소비, runtime pause/config reload 반영 | 실제 주문 전 runtime/risk/open-order/live/global guard를 다시 확인한다. |
| `src/kis_msj/strategy.py` | initial/add/reentry/sell 후보 생성, reference price, lot sizing context, target profit 동적 계산 | action 후보 생성 단계이며 실제 주문은 main/order_manager 경로로 간다. |
| `src/kis_msj/order_manager.py` | 주문 요청 기록, KIS 주문 연동, 체결 조회/정규화, fill record/dedupe logging | `record_fill()` 실패 시 fill 반환 금지. |
| `src/kis_msj/position_manager.py` | fill 기준으로 LOT/position 상태 갱신, 상태 전이, cycle anchor, lot sizing lock, review/stale 평가 | fill 없이는 수량을 바꾸지 않는다. |
| `src/kis_msj/risk_manager.py` | 계좌/가격/종목 위험 guard, 전역 리스크 context | BUY 차단 중심. SELL은 전역 노출 제한 때문에 막지 않는다. |
| `src/kis_msj/lot_manager.py` | LOT 계산, age decay, target profit, stale/cleanup 후보 계산 보조 | OPEN LOT 기준은 `remaining_quantity > 0` and `status != CLOSED`. |
| `src/kis_msj/storage.py` | SQLite schema, CRUD, fill dedupe, migration column 보강 | DB schema 변경은 `_ensure_column`으로 backward compatible하게 처리. |
| `src/kis_msj/config.py` | config dataclass와 JSON load/parse | strategy/risk/order/stocks 구조의 기준. |
| `src/kis_msj/models.py` | Position, Lot, Order, Fill, enum/status 모델 | 상태값과 필드 의미를 코드 전체에서 공유. |
| `src/kis_msj/kis_client.py` | KIS 조회/주문 API wrapper | UI에서 직접 호출 금지. 주문은 Bot Core/order_manager 경로만. |

### UI/API Layer

| 파일 | 책임 | 주의 |
| --- | --- | --- |
| `src/kis_msj/ui_server.py` | localhost Web UI와 HTTP API server. HTML/CSS/JS 포함. | KIS 주문 API를 호출하지 않는다. |
| `src/kis_msj/ui_service.py` | UI 데이터 집계, config validate/save/backup, runtime, manual preview/request, review, new season API service | DB 직접 수정은 manual request/status/review 등 관리 데이터로 제한. lots/positions/fills 직접 수정 금지. |
| `src/kis_msj/runtime_control.py` | `config/runtime_control.json` load/save와 pause block reason 계산 | runtime control은 config보다 우선 적용된다. |

### Scripts

| 파일 | 책임 | 안전 기본값 |
| --- | --- | --- |
| `scripts/prepare_new_season.py` | archive, liquidation plan, liquidation manual SELL request 생성, reset dry-run/실행 함수 | 기본 dry-run. KIS 주문 API 호출 없음. reset/전량매도 request는 confirm text 필요. |

### Config / Docs

| 파일 | 내용 |
| --- | --- |
| `config/lot_auto_trader.json` | 운영 config. stocks, strategy, risk, order, market_hours, paths/account/upstream 포함. |
| `config/runtime_control.json` | runtime pause/reload/start 상태. UI와 Bot Core가 공유한다. |
| `docs/local_ui.md` | UI 실행, 안전 설계, 표시 규칙, 수동 주문, 새 시즌/리뷰 탭 사용법. |
| `docs/strategy_lot_sizing.md` | 가격대별 LOT sizing, cycle lock, target profit lot bands 설명. |
| `docs/new_season_reset.md` | 새 시즌 준비, archive/reset/liquidation plan 흐름. |
| `docs/expansion_100_config.md` | KOSPI 100 후보군과 expansion profile 설명. |

## 4. DB / 모델 구조

### positions

`positions`는 종목 단위 상태를 저장한다. `position_state`, 보유수량, 투자금, realized/unrealized 관련 값, reentry anchor, review, lot sizing cycle lock 필드가 들어간다.

| 필드 | 의미 | 수정 주체 |
| --- | --- | --- |
| `code`, `name` | 종목 식별 | storage/position manager |
| `position_state` | NEVER_BOUGHT/HOLDING/WAIT_REENTRY/COOLDOWN_AFTER_CLEANUP/REVIEW_REQUIRED/RISK_BLOCKED/SYNC_REQUIRED | position_manager/review/reconciliation |
| `total_quantity`, `invested_amount` | fill 반영 후 내부 보유 상태 | `position_manager.apply_fill` |
| `normal_exit_anchor_price` | NORMAL_REENTRY 기준 anchor | position_manager |
| `trailing_exit_anchor_price` | TRAILING_REENTRY activation 기준 anchor | position_manager |
| `exit_anchor_price` | legacy/deprecated anchor. 호환/로그용 | position_manager |
| `cycle_sell_vwap_price`, `cycle_sell_median_price`, `cycle_highest_sell_price`, `cycle_last_sell_price` | 전량 매도 사이클의 매도 체결 요약 | position_manager |
| `post_exit_high_price` | WAIT_REENTRY 이후 고점 tracking | `update_reentry_tracking()` |
| `review_reason`, `review_created_at`, `review_trigger_values` | REVIEW_REQUIRED 사유와 트리거 값 | position_manager/ui_service |
| `review_acknowledged_at`, `review_acknowledged_by`, `review_note` | 사용자가 검토했음을 기록. BUY 차단 해제 아님 | ui_service |
| `entry_price_for_lot_sizing`, `lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`, `lot_sizing_locked_at`, `lot_sizing_mode` | cycle-locked lot sizing | position_manager/strategy migration |
| `sync_status`, `lot_quantity_mismatch` | DB/KIS 불일치 관련 상태 | reconciliation/review recheck |

직접 수정 금지: 보유 수량, invested amount, state를 UI에서 임의로 덮어쓰면 안 된다. 수동 조정은 별도 maintenance mode가 필요하다.

### lots

`lots`는 매수 체결 단위의 보유 LOT을 저장한다.

| 필드 | 의미 |
| --- | --- |
| `lot_id` | LOT 식별자 |
| `code`, `name` | 종목 |
| `buy_price`, `buy_quantity`, `buy_amount`, `buy_time` | 매수 체결 정보 |
| `remaining_quantity` | 아직 남아 있는 수량. 부분매도 때 감소 |
| `status` / `lot_status` | OPEN/CLOSED/STALE 등 |
| `base_target_profit_rate` | 과거 호환/로그용. 실제 sell 판단은 current lot band target 우선 |
| `effective_target_profit_rate` | current base target - age decay |
| `cleanup_candidate` | 손실정리 후보 표시 |
| `last_sell_reason` | 마지막 매도 사유 |

OPEN LOT 기준은 `remaining_quantity > 0`이고 `status != CLOSED`이다. CLOSED LOT은 매수/매도 판단과 current_open_lot_count에서 제외한다.

### orders

`orders`는 주문 요청과 상태를 저장한다. 주문 요청 직후 lots/positions는 바뀌지 않는다.

진행 중으로 간주하는 status:

`REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW`

종결로 간주하는 status:

`FILLED`, `CANCELED`, `REJECTED`, `FAILED`, `EXPIRED`, `PARTIAL_CANCELED`, `NONE`

### fills

`fills`는 실제 체결을 저장한다. fill dedupe가 안전성의 중심이다.

| 필드 | 의미 |
| --- | --- |
| `fill_id` | 내부 체결 row id |
| `execution_id` | KIS 체결번호가 있으면 최우선 dedupe key |
| `order_id` | 내부 주문 id |
| `code`, `side`, `price`, `quantity`, `filled_at` | 체결 정보 |
| `lot_id` | SELL일 때 대상 LOT, BUY일 때 생성 LOT 연결 |
| `dedupe_key_type` | `execution_id` 또는 `fallback` |

fallback dedupe key는 `order_id`, `code`, `side`, `lot_id`, `price`, `quantity`, `filled_at` 조합이다. `filled_at`은 조회시각이 아니라 KIS 원본 체결시각에서 파싱해야 한다.

### manual_order_requests

UI나 liquidation script가 수동 주문 요청을 저장하는 큐다. UI는 이 테이블에 요청만 넣고, 실제 주문은 Bot Core가 소비한다.

주요 필드: `request_id`, `source`, `requested_by`, `requested_at`, `code`, `side`, `amount`, `quantity`, `lot_id`, `order_type`, `preview_json`, `runtime_snapshot_json`, `live_trading`, `confirm_text_verified`, `status`, `block_reason`, `linked_order_id`, `created_at`, `updated_at`.

reset을 막는 진행 중 status:

`REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING`

종결 status:

`FILLED`, `CANCELED`, `REJECTED`, `FAILED`, `BLOCKED`, `EXPIRED`

## 5. position_state 상태 정의와 전이

| 상태 | 의미 | BUY | SELL | 진입/전이 | 주요 block/skip |
| --- | --- | --- | --- | --- | --- |
| `NEVER_BOUGHT` | 한 번도 매수한 적 없는 후보 종목 | initial_buy 가능 | 없음 | config stock 후보, OPEN LOT 없음 | price/lot sizing/global/risk/order guard |
| `HOLDING` | OPEN LOT 1개 이상 | 추가매수 가능, guard 통과 필요 | PROFIT_TAKE 가능, CLEANUP 조건부 가능 | BUY fill 반영 후 | open order, global BUY limit, cleanup cooldown |
| `WAIT_REENTRY` | PROFIT_TAKE 전량 매도 후 재진입 대기 | NORMAL/TRAILING_REENTRY만 가능 | 없음 | 전량 PROFIT_TAKE 후 | initial_buy 금지, reentry guard |
| `COOLDOWN_AFTER_CLEANUP` | CLEANUP_SELL 전량 매도 후 보수 대기 | 모든 BUY 금지 | 일반적으로 OPEN LOT 없음 | 전량 cleanup 후 | cleanup cooldown, 자동 재진입 금지 |
| `REVIEW_REQUIRED` | 자동 판단만으로 계속 진행하기 위험 | BUY 금지 | PROFIT_TAKE 허용, CLEANUP_SELL 차단 | 손실/LOT과다/stale/cleanup 완료 등 | `review_required` |
| `RISK_BLOCKED` | 위험 플래그 상태 | 차단 | 현재 보수정책상 차단 | stock risk flag | `risk_blocked_buy_sell_blocked` |
| `SYNC_REQUIRED` | KIS 잔고와 DB 불일치 | 차단 | 신규 주문 차단 | reconciliation mismatch | `sync_required` |

WAIT_REENTRY와 COOLDOWN_AFTER_CLEANUP은 명확히 분리된다. PROFIT_TAKE 전량 매도만 WAIT_REENTRY로 간다. CLEANUP_SELL 전량 매도는 WAIT_REENTRY로 바로 가지 않는다.

## 6. LOT 단위 매매 전략

LOT은 개별 매수 체결 단위다. 이 봇은 종목 평균단가 하나로 매도 판단을 하지 않고, 각 LOT의 매수가, 남은 수량, 나이, 현재가, target, 예상 실현손익을 기준으로 판단한다.

부분매도 시 해당 LOT의 `remaining_quantity`만 감소한다. `remaining_quantity`가 0이 되면 CLOSED 처리된다. 여러 LOT이 한 종목에 있어도 각 LOT은 독립적으로 PROFIT_TAKE/CLEANUP 후보가 된다.

STALE_LOT은 자동 손절 신호가 아니라 오래된 손실 LOT 표시다. cleanup 조건과 loss budget을 만족해야 CLEANUP_SELL 후보가 된다. 조건이 심해지면 REVIEW_REQUIRED 후보가 된다.

## 7. 가격대별 LOT sizing / cycle lock

현재 `lot_sizing_mode = cycle_locked_by_entry_price`이다.

| 가격 구간 | 1 LOT 금액 | 종목당 최대금액 | enabled | note |
| --- | ---: | ---: | --- | --- |
| 0~300 | 1,000 | 10,000 | true | 초저가 구간도 1 LOT 소액 운용 가능 |
| 301~1,000 | 3,000 | 30,000 | true | 저가 구간 |
| 1,001~3,000 | 10,000 | 100,000 | true | 저가~중저가 구간 |
| 3,001~10,000 | 30,000 | 300,000 | true | 3만원 LOT 구간 |
| 10,001~30,000 | 100,000 | 1,000,000 | true | 10만원 LOT 구간 |
| 30,001~100,000 | 300,000 | 3,000,000 | true | 중고가 구간 |
| 100,001~300,000 | 1,000,000 | 10,000,000 | true | 고가 구간 |
| 300,001~1,000,000 | 3,000,000 | 30,000,000 | true | 매우 고가 구간 |
| 1,000,001~3,000,000 | 10,000,000 | 100,000,000 | true | 초고가 구간, 전역 리스크 한도와 함께 운영 주의 |

3,000,000원을 초과하는 가격은 어떤 band에도 포함되지 않으므로 `price_out_of_lot_sizing_range`로 BUY가 차단된다. 실제 매수는 위 band만으로 결정되지 않고, 전역 리스크 한도, runtime pause, risk/sync guard, open order guard, 가격 샘플 안정성도 모두 통과해야 한다.

cycle lock 원칙:

1. NEVER_BOUGHT initial buy 또는 WAIT_REENTRY 재진입처럼 새 사이클이 시작될 때 현재가 기준 price_lot_band를 고른다.
2. 선택된 `lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`을 position에 저장한다.
3. HOLDING 중 현재가가 다른 가격 구간으로 이동해도 저장된 sizing을 계속 쓴다.
4. OPEN LOT이 남아 있으면 sizing을 재계산하거나 overwrite하지 않는다.
5. 전량 PROFIT_TAKE 후 WAIT_REENTRY에서 재진입하면 새 사이클로 보고 재진입 당시 가격 기준으로 overwrite한다.
6. CLEANUP 전량 매도 후 새 진입도 새 사이클이다.
7. 기존 OPEN LOT이 있는데 lot sizing 필드가 비어 있으면 첫 OPEN LOT buy_price 기준으로 migration/fallback한다. 수량은 절대 바꾸지 않는다.
8. UI manual BUY preview와 Bot Core 처리 직전 실제 가격 기준 bucket/amount가 달라지면 `lot_sizing_changed_after_preview`로 차단하고 다시 preview하게 한다.

관련 skip/block reason:

- `lot_sizing_band_disabled`
- `price_out_of_lot_sizing_range`
- `max_lots_per_symbol_reached`
- `max_symbol_amount_reached`
- `lot_unit_amount_below_price`
- `lot_sizing_changed_after_preview`
- `lot_sizing_missing`
- `lot_sizing_migrated`

## 8. 추가매수 로직

추가매수는 예전의 절대금액 exposure band 대신 LOT 배수 band를 사용한다.

| OPEN LOT 수 | 하락 조건 | 추가 LOT |
| --- | ---: | ---: |
| 1~2 | -4.0% | 1 |
| 3~4 | -6.0% | 1 |
| 5~6 | -8.0% | 1 |
| 7~8 | -10.0% | 1 |
| 9~10 | -12.0% | 1 |

current_open_lot_count는 반드시 OPEN LOT 기준이다. 즉 `remaining_quantity > 0`이고 `status != CLOSED`인 LOT만 센다. 9 LOT 보유 상태에서 1 LOT 추가는 허용되어 10 LOT까지 갈 수 있지만, 10 LOT 상태에서는 추가매수 차단이다.

추가매수 제한은 다음을 모두 통과해야 한다.

| 제한 | 설명 |
| --- | --- |
| `current_open_lot_count < max_lots_per_symbol` | 종목별 LOT 개수 상한. |
| `current_invested_amount + next_buy_amount <= max_symbol_amount` | 종목별 금액 상한. |
| `next_buy_amount = lot_unit_amount * add_lot_count` | cycle-locked LOT 금액 기준. |
| `quantity >= 1` | 현재가가 lot_unit_amount보다 높아 1주도 못 사면 차단. |
| 전역 리스크 제한 | max_total_open_lots, max_total_invested_amount, max_new_buy_amount_per_day 등은 계속 살아 있다. |

reference_buy_price 계산:

| PnL mode | reference_buy_price |
| --- | --- |
| MINUS | `min(open_lot_vwap_buy_price, median_open_buy_price)` |
| NEUTRAL | `min(open_lot_vwap_buy_price, median_open_buy_price)` |
| PLUS | `max(open_lot_vwap_buy_price, median_open_buy_price)` |

`lowest_open_buy_lot_price`, `highest_open_buy_lot_price`는 로그/디버깅용으로 남기지만 reference 계산에는 직접 쓰지 않는다.

## 9. target_profit_pct / SELL 판단

target profit은 매수 당시 LOT에 고정하지 않는다. 매도 판단 직전의 현재 OPEN LOT 수 기준으로 모든 OPEN LOT에 같은 current lot band target을 적용한다.

| 현재 OPEN LOT 수 | 동적 target_profit_rate |
| --- | ---: |
| 1~2 | 6.0% |
| 3~4 | 5.0% |
| 5~6 | 4.0% |
| 7~8 | 3.0% |
| 9~10 | 2.0% |

예: 과거 1~2 LOT 구간에서 산 LOT도 현재 6 OPEN LOT 상태라면 5~6 LOT 구간 target인 4% 기준으로 SELL 판단한다. 일부 LOT을 매도해서 4 OPEN LOT이 되면 다음 판단부터 남은 LOT들은 3~4 LOT 구간 target인 5% 기준으로 재평가된다.

공식:

```text
current_base_target_profit_rate = target_profit_lot_bands[current_open_lot_count]
effective_target_profit_rate = current_base_target_profit_rate - lot_age_weeks * age_decay_rate
```

`original_lot_base_target_profit_rate` 또는 LOT row의 `base_target_profit_rate`는 과거 호환/로그용이다. 실제 SELL 판단은 `current_base_target_profit_rate`를 우선한다.

중요 로그 필드: `original_lot_base_target_profit_rate`, `current_base_target_profit_rate`, `target_profit_source=current_lot_band`, `target_profit_lot_band`, `effective_target_profit_rate`, `lot_age_weeks`, `age_decay_rate`.

## 10. PROFIT_TAKE / CLEANUP_SELL / STALE_LOT

PROFIT_TAKE는 실제 예상 손익이 0 이상인 매도다. 본전 매도도 PROFIT_TAKE다. effective target이 음수여도 실제 손익이 플러스면 PROFIT_TAKE다.

CLEANUP_SELL은 실제 예상 손익이 음수인 손실 정리 매도다. cleanup은 손실 확정이므로 아래 조건을 만족해야 한다.

| 조건 | 의미 |
| --- | --- |
| `cleanup_enabled=true` | 현재 expansion_100_safe 초기값은 false. |
| LOT 나이 >= cleanup_min_age_weeks | 오래된 LOT만 cleanup 가능. |
| `effective_target_profit_rate < 0` | 시간 경과로 목표가 음수 영역까지 내려온 LOT. |
| `realized_pnl_rate < 0` | 실제 손실 매도만 cleanup. |
| `realized_pnl_rate >= cleanup_min_target_rate` | 기본 -4%보다 큰 손실은 자동 cleanup 금지. |
| `symbol_state == HOLDING` | REVIEW_REQUIRED에서는 cleanup 차단. |
| open order 없음 | 같은 symbol에 REQUESTED/PARTIAL 등 진행 중 주문이 있으면 차단. |
| cleanup_loss_budget 충족 | 당일 실현수익 일부로만 손실 상쇄. |

STALE_LOT 조건은 `lot_unrealized_pnl_rate <= -15%`, `lot_age_weeks >= 8`, `current_price <= buy_price * 0.90`이다. STALE_LOT은 즉시 매도하지 않는다. 오래 지속되거나 종목 손실이 심하면 REVIEW_REQUIRED로 간다.

## 11. Reentry 로직

전량 PROFIT_TAKE 후 OPEN LOT이 0개가 되면 `WAIT_REENTRY`로 간다. CLEANUP_SELL 전량 매도는 WAIT_REENTRY가 아니라 COOLDOWN_AFTER_CLEANUP이다.

Reentry anchor는 normal/trailing 용도로 분리되어 있다.

| 필드 | 계산 | 용도 |
| --- | --- | --- |
| `normal_exit_anchor_price` | `min(cycle_sell_vwap_price, cycle_sell_median_price)` | 내가 판 대표가격보다 충분히 싸졌는지 판단. |
| `trailing_exit_anchor_price` | `max(cycle_sell_vwap_price, cycle_sell_median_price)` | 매도 후 더 오른 종목을 다시 추적할 activation 기준. |
| `exit_anchor_price` | deprecated/fallback, 보통 normal anchor와 호환 | 새 로직에서 직접 기준으로 쓰지 않는다. |
| `cycle_highest_sell_price`, `cycle_last_sell_price` | 로그/참고 | anchor 계산에 직접 쓰지 않는다. |

NORMAL_REENTRY 조건: `current_price <= normal_exit_anchor_price * (1 - normal_reentry_drop_rate)`.

TRAILING_REENTRY 조건:

1. `post_exit_high_price >= trailing_exit_anchor_price * (1 + trailing_activation_gain)`
2. `current_price <= post_exit_high_price * (1 - trailing_reentry_drop_rate)`
3. `now - exit_time >= min_reentry_wait_minutes`
4. `trailing_reentry_count_today < max_trailing_reentry_per_day`

`update_reentry_tracking()`은 WAIT_REENTRY 중 `post_exit_high_price`만 갱신한다. `check_reentry_conditions()`는 상태 변경 부작용 없이 판단한다.

## 12. 주문/체결/DB 반영

흐름:

1. strategy가 action 후보를 만든다.
2. main이 runtime/risk/open-order/live/global guard를 한 번 더 확인한다.
3. order_manager가 주문을 요청하고 `orders`에 기록한다.
4. KIS executions 조회나 즉시 체결 확인으로 raw execution을 정규화한다.
5. `store.record_fill(fill)`이 신규 체결이면 true를 반환한다.
6. true일 때만 `position_manager.apply_fill(fill)`을 호출한다.
7. duplicate 또는 record_fill 실패면 로그만 남기고 positions/lots를 바꾸지 않는다.

체결 조회:

| 상황 | 동작 |
| --- | --- |
| startup recent reconciliation | `reconcile_recent_executions_on_startup=true`, 최근 1일 조회. 저장된 주문과 매칭되는 체결만 반영. |
| 일반 루프 | open order가 있을 때 executions 조회. |
| open order query range | oldest open order requested_at - buffer와 오늘 00:00 중 더 이른 시각, previous day 옵션 가능. |
| unmatched/manual execution | 자동 LOT에 섞지 않고 ignored_unmatched로 집계. |

raw execution log는 `enable_execution_raw_log=true`일 때만 남긴다. 민감정보는 마스킹해야 한다. 실제 KIS raw log에서 확인할 필드: order_no, execution_id, filled_at, side, code, price, quantity.

## 13. Manual order requests

UI 수동 주문 구조:

1. UI에서 preview 생성. 이 단계에서는 주문 API 호출 없음.
2. live trading이면 confirm text 필요.
3. `ui_manual_trading_enabled=false`이면 API/버튼 모두 request 생성 차단.
4. request 생성 시 `manual_order_requests`에 `REQUESTED` row만 insert.
5. Bot Core가 루프 중 REQUESTED request를 읽는다.
6. runtime pause, risk guard, open order guard, live guard, lot/symbol 상태, lot sizing 재검증을 수행한다.
7. 통과하면 기존 order_manager 경로로 주문 요청을 만든다.
8. fill 전까지 lots/positions는 바뀌지 않는다.

manual BUY는 NEVER_BOUGHT/WAIT_REENTRY이면 현재 처리 시점 가격 기준 lot sizing을 새로 산정한다. HOLDING이면 기존 cycle lock을 따른다. preview와 처리 직전 bucket이 다르면 `lot_sizing_changed_after_preview`로 block한다.

manual SELL은 LOT별 `lot_id`와 잔여 수량을 유지한다. CLOSED LOT, 남은 수량 초과, open SELL order, RISK_BLOCKED, SYNC_REQUIRED, runtime sell pause 상태는 차단한다.

## 14. REVIEW_REQUIRED 처리

REVIEW_REQUIRED는 자동 BUY를 멈추고 사람이 확인해야 하는 상태다. 강제 해제는 만들지 않는다.

진입 조건 예:

| reason | 의미 |
| --- | --- |
| `symbol_loss_review` | 종목 손실률이 기준 이하. |
| `too_many_open_lots` | OPEN LOT 수가 제한 초과. |
| `stale_lot_review_age` | 오래된 STALE_LOT 장기 지속. |
| `auto_buy_limit_exceeded` | 레거시/금액 기준 자동매수 한도 초과. |
| `cleanup_cooldown_complete` | cleanup 전량 매도 후 자동 재진입 대신 수동검토로 보낸 상태. |

UI/API:

| API | 역할 |
| --- | --- |
| `GET /api/review-required` | REVIEW_REQUIRED 종목 목록, reason, trigger values, 추천 조치. |
| `GET /api/positions/{code}/review-status` | 특정 종목 review 상태. |
| `POST /api/positions/{code}/review/recheck` 또는 `/api/review-required/{code}/recheck` | 현재 DB/sync 기준 재평가. 조건 해소 시 HOLDING/WAIT_REENTRY 등 복귀, mismatch면 SYNC_REQUIRED. |
| `POST /api/positions/{code}/review/acknowledge` 또는 `/api/review-required/{code}/acknowledge` | 사용자가 확인했다는 기록만 남김. BUY 차단 해제 아님. |

수동매도 후에는 reconciliation이 완료되어야 recheck가 의미 있다. sync mismatch가 있으면 REVIEW_REQUIRED 해제가 아니라 SYNC_REQUIRED로 가야 한다.

## 15. Runtime Control

`config/runtime_control.json`은 운영 중 즉시 적용되는 제어 플래그다. config보다 우선한다.

| 플래그 | 차단 대상 | block reason |
| --- | --- | --- |
| `all_orders_paused` | BUY/SELL 전체 | `runtime_all_orders_paused` |
| `buy_paused` | initial/add/reentry BUY | `runtime_buy_paused` |
| `sell_paused` | PROFIT_TAKE/CLEANUP SELL | `runtime_sell_paused` |
| `cleanup_paused` | CLEANUP_SELL만 | `runtime_cleanup_paused` |
| `reentry_paused` | NORMAL/TRAILING_REENTRY | `runtime_reentry_paused` |
| emergency stop | 모든 pause 플래그 true | emergency stop reason |
| `bot_paused` | 자동 루프 진행 정지 | UI loop pause |
| `config_reload_requested` | 실행 중 config reload 요청 | Reset / Config 다시 읽기 |

최근 구현에서는 runtime control과 manual request가 loop sleep 중에도 빠르게 반영되도록 interrupt 체크를 넣었다.

## 16. Risk / Global limits

현재 expansion_100_safe 기준:

| 항목 | 현재값 | 의미 |
| --- | --- | --- |
| `risk.profile` | `expansion_100_safe` | 현재 적용 중인 리스크 프로필 |
| `max_active_symbols` | 100 | 관리/보유 가능한 활성 종목 상한 |
| `max_new_buy_per_day` | 10 | 하루 신규 initial buy 주문 수 제한. reentry는 포함하지 않는다 |
| `max_new_buy_amount_per_day` | 2,000,000 | 하루 신규 매수 금액 제한 |
| `max_total_initial_buy_amount_per_day` | 2,000,000 | initial buy 총액 제한 |
| `max_total_open_lots` | 300 | 계좌 전체 OPEN LOT 수 제한 |
| `max_total_invested_amount` | 20,000,000 | 계좌 전체 투입금 제한 |
| `cleanup_enabled` | false | 초기 100종목 확장 운용에서는 false 유지 권장 |
| `ui_manual_trading_enabled` | false | UI 수동 주문 요청 생성 기본값 |
| `live_trading` | false | 실거래 주문 가능 여부 |
| `enable_execution_raw_log` | true | 첫 실체결 raw mapping 확인용 |

profile 후보:

| profile | max_total_invested_amount | max_new_buy_per_day | max_new_buy_amount_per_day | max_total_open_lots | max_active_symbols |
| --- | ---: | ---: | ---: | ---: | ---: |
| expansion_100_safe | 20,000,000 | 10 | 2,000,000 | 300 | 100 |
| expansion_100_medium | 30,000,000 | 15 | 3,000,000 | 450 | 100 |
| expansion_100_aggressive | 50,000,000 | 20 | 5,000,000 | 700 | 100 |

전역 리스크 제한은 LOT sizing과 별도로 계속 살아 있다. 종목별 max_symbol_amount/max_lots가 통과되어도 계좌 전체 max_total_open_lots, max_total_invested_amount, max_new_buy_per_day, max_new_buy_amount_per_day에 걸리면 BUY는 차단된다. SELL은 전역 노출 제한 때문에 막지 않는다.

## 17. KOSPI 100 후보군

현재 config에는 100종목이 들어 있고 enabled 97종목, disabled/manual_only 3종목이다. disabled/manual_only 종목은 자동매수 제외이며 UI와 config에서 note/risk flag로 표시된다.

특별 처리 3종목:

| code | name | 처리 | 이유 |
| --- | --- | --- | --- |
| 005935 | 삼성전자우 | enabled=false, manual_only=true, liquidity_warning=true | KIS KOSPI master 검증에서 미확인되어 자동매수 비활성 |
| 001230 | 동국홀딩스 | enabled=false, manual_only=true, trading_halted=true | KIS KOSPI master 기준 trading_halt_yn=Y |
| 020560 | 아시아나항공 | enabled=false, manual_only=true, administrative_issue=true | 대한항공 통합/브랜드 종료 일정 관련 이벤트 리스크 |

전체 후보군은 `config/lot_auto_trader.json`의 `stocks` 배열이 원본이다. 아래 표는 현재 문서화 시점의 후보군이다. 새 세션에서 실제 작업을 이어갈 때는 config를 다시 읽어 최신 enabled/risk flag를 확인하는 것이 가장 안전하다.

| # | code | name | market | sector | enabled | manual_only | risk flags / note |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 005930 | 삼성전자 | KOSPI | 반도체 | true | false | - |
| 2 | 000660 | SK하이닉스 | KOSPI | 반도체 | true | false | - |
| 3 | 005380 | 현대차 | KOSPI | 자동차 | true | false | - |
| 4 | 000270 | 기아 | KOSPI | 자동차 | true | false | - |
| 5 | 012330 | 현대모비스 | KOSPI | 자동차부품 | true | false | - |
| 6 | 005935 | 삼성전자우 | KOSPI | 반도체우선주 | false | true | liquidity_warning, KIS KOSPI master 미확인으로 자동매수 비활성 |
| 7 | 035420 | NAVER | KOSPI | 플랫폼 | true | false | - |
| 8 | 035720 | 카카오 | KOSPI | 플랫폼 | true | false | - |
| 9 | 207940 | 삼성바이오로직스 | KOSPI | 바이오 | true | false | - |
| 10 | 068270 | 셀트리온 | KOSPI | 바이오 | true | false | - |
| 11 | 051910 | LG화학 | KOSPI | 화학/배터리 | true | false | - |
| 12 | 373220 | LG에너지솔루션 | KOSPI | 2차전지 | true | false | - |
| 13 | 006400 | 삼성SDI | KOSPI | 2차전지 | true | false | - |
| 14 | 003670 | 포스코퓨처엠 | KOSPI | 2차전지소재 | true | false | - |
| 15 | 005490 | POSCO홀딩스 | KOSPI | 철강/2차전지 | true | false | - |
| 16 | 066570 | LG전자 | KOSPI | 전자 | true | false | - |
| 17 | 034220 | LG디스플레이 | KOSPI | 디스플레이 | true | false | - |
| 18 | 011070 | LG이노텍 | KOSPI | 전자부품 | true | false | - |
| 19 | 009150 | 삼성전기 | KOSPI | 전자부품 | true | false | - |
| 20 | 018260 | 삼성SDS | KOSPI | IT서비스 | true | false | - |
| 21 | 003550 | LG | KOSPI | 지주사 | true | false | - |
| 22 | 034730 | SK | KOSPI | 지주사 | true | false | - |
| 23 | 028260 | 삼성물산 | KOSPI | 지주/건설 | true | false | - |
| 24 | 086790 | 하나금융지주 | KOSPI | 금융 | true | false | - |
| 25 | 105560 | KB금융 | KOSPI | 금융 | true | false | - |
| 26 | 055550 | 신한지주 | KOSPI | 금융 | true | false | - |
| 27 | 316140 | 우리금융지주 | KOSPI | 금융 | true | false | - |
| 28 | 024110 | 기업은행 | KOSPI | 금융 | true | false | - |
| 29 | 138930 | BNK금융지주 | KOSPI | 지방금융 | true | false | - |
| 30 | 175330 | JB금융지주 | KOSPI | 지방금융 | true | false | - |
| 31 | 139130 | DGB금융지주 | KOSPI | 지방금융 | true | false | - |
| 32 | 032830 | 삼성생명 | KOSPI | 보험 | true | false | - |
| 33 | 000810 | 삼성화재 | KOSPI | 보험 | true | false | - |
| 34 | 005830 | DB손해보험 | KOSPI | 보험 | true | false | - |
| 35 | 088350 | 한화생명 | KOSPI | 보험 | true | false | - |
| 36 | 071050 | 한국금융지주 | KOSPI | 증권 | true | false | - |
| 37 | 039490 | 키움증권 | KOSPI | 증권 | true | false | - |
| 38 | 006800 | 미래에셋증권 | KOSPI | 증권 | true | false | - |
| 39 | 030200 | KT | KOSPI | 통신 | true | false | - |
| 40 | 017670 | SK텔레콤 | KOSPI | 통신 | true | false | - |
| 41 | 032640 | LG유플러스 | KOSPI | 통신 | true | false | - |
| 42 | 015760 | 한국전력 | KOSPI | 유틸리티 | true | false | - |
| 43 | 036460 | 한국가스공사 | KOSPI | 유틸리티 | true | false | - |
| 44 | 051600 | 한전KPS | KOSPI | 전력서비스 | true | false | - |
| 45 | 052690 | 한전기술 | KOSPI | 원전/엔지니어링 | true | false | - |
| 46 | 010950 | S-Oil | KOSPI | 정유 | true | false | - |
| 47 | 096770 | SK이노베이션 | KOSPI | 에너지/배터리 | true | false | - |
| 48 | 078930 | GS | KOSPI | 에너지지주 | true | false | - |
| 49 | 267250 | HD현대 | KOSPI | 조선/에너지지주 | true | false | - |
| 50 | 329180 | HD현대중공업 | KOSPI | 조선 | true | false | - |
| 51 | 010140 | 삼성중공업 | KOSPI | 조선 | true | false | - |
| 52 | 042660 | 한화오션 | KOSPI | 조선/방산 | true | false | - |
| 53 | 009540 | HD한국조선해양 | KOSPI | 조선지주 | true | false | - |
| 54 | 064350 | 현대로템 | KOSPI | 방산/철도 | true | false | - |
| 55 | 012450 | 한화에어로스페이스 | KOSPI | 방산 | true | false | - |
| 56 | 047810 | 한국항공우주 | KOSPI | 방산/항공 | true | false | - |
| 57 | 079550 | LIG넥스원 | KOSPI | 방산 | true | false | - |
| 58 | 000880 | 한화 | KOSPI | 지주/방산 | true | false | - |
| 59 | 009830 | 한화솔루션 | KOSPI | 화학/태양광 | true | false | - |
| 60 | 011780 | 금호석유 | KOSPI | 화학 | true | false | - |
| 61 | 011170 | 롯데케미칼 | KOSPI | 화학 | true | false | - |
| 62 | 010060 | OCI홀딩스 | KOSPI | 화학/태양광 | true | false | - |
| 63 | 010130 | 고려아연 | KOSPI | 비철금속 | true | false | - |
| 64 | 004020 | 현대제철 | KOSPI | 철강 | true | false | - |
| 65 | 001230 | 동국홀딩스 | KOSPI | 철강/지주 | false | true | trading_halted, KIS master 기준 trading_halt_yn=Y |
| 66 | 000720 | 현대건설 | KOSPI | 건설 | true | false | - |
| 67 | 006360 | GS건설 | KOSPI | 건설 | true | false | - |
| 68 | 047040 | 대우건설 | KOSPI | 건설 | true | false | - |
| 69 | 375500 | DL이앤씨 | KOSPI | 건설 | true | false | - |
| 70 | 294870 | HDC현대산업개발 | KOSPI | 건설 | true | false | - |
| 71 | 180640 | 한진칼 | KOSPI | 항공지주 | true | false | - |
| 72 | 003490 | 대한항공 | KOSPI | 항공 | true | false | - |
| 73 | 020560 | 아시아나항공 | KOSPI | 항공 | false | true | administrative_issue, 대한항공 통합 이벤트 리스크 |
| 74 | 086280 | 현대글로비스 | KOSPI | 물류 | true | false | - |
| 75 | 000120 | CJ대한통운 | KOSPI | 물류 | true | false | - |
| 76 | 028670 | 팬오션 | KOSPI | 해운 | true | false | - |
| 77 | 011200 | HMM | KOSPI | 해운 | true | false | - |
| 78 | 004990 | 롯데지주 | KOSPI | 지주/소비 | true | false | - |
| 79 | 023530 | 롯데쇼핑 | KOSPI | 유통 | true | false | - |
| 80 | 004170 | 신세계 | KOSPI | 유통 | true | false | - |
| 81 | 139480 | 이마트 | KOSPI | 유통 | true | false | - |
| 82 | 282330 | BGF리테일 | KOSPI | 편의점 | true | false | - |
| 83 | 007070 | GS리테일 | KOSPI | 편의점 | true | false | - |
| 84 | 271560 | 오리온 | KOSPI | 음식료 | true | false | - |
| 85 | 097950 | CJ제일제당 | KOSPI | 음식료 | true | false | - |
| 86 | 004370 | 농심 | KOSPI | 음식료 | true | false | - |
| 87 | 007310 | 오뚜기 | KOSPI | 음식료 | true | false | - |
| 88 | 280360 | 롯데웰푸드 | KOSPI | 음식료 | true | false | - |
| 89 | 090430 | 아모레퍼시픽 | KOSPI | 화장품 | true | false | - |
| 90 | 051900 | LG생활건강 | KOSPI | 화장품/생활용품 | true | false | - |
| 91 | 161890 | 한국콜마 | KOSPI | 화장품ODM | true | false | - |
| 92 | 192820 | 코스맥스 | KOSPI | 화장품ODM | true | false | - |
| 93 | 001040 | CJ | KOSPI | 지주/소비 | true | false | - |
| 94 | 003240 | 태광산업 | KOSPI | 섬유/화학 | true | false | - |
| 95 | 000150 | 두산 | KOSPI | 지주/로봇/에너지 | true | false | - |
| 96 | 034020 | 두산에너빌리티 | KOSPI | 원전/플랜트 | true | false | - |
| 97 | 241560 | 두산밥캣 | KOSPI | 기계 | true | false | - |
| 98 | 042700 | 한미반도체 | KOSPI | 반도체장비 | true | false | - |
| 99 | 000990 | DB하이텍 | KOSPI | 반도체 | true | false | - |
| 100 | 112610 | 씨에스윈드 | KOSPI | 풍력/신재생 | true | false | - |

## 18. UI 전체 구조

UI는 localhost 관제/제어 화면이다. 외부 공개 금지. 계좌번호/API 키/token은 표시하지 않는다. 테이블은 한글 라벨과 내부 key를 병기하고, 사용자가 column 선택과 column width resize를 할 수 있다. 폭 조절값은 localStorage에 저장된다.

| 탭 | 역할 | 주요 API/동작 | 금지사항 |
| --- | --- | --- | --- |
| Dashboard / 대시보드 | 봇 상태, 리스크, warnings, runtime 요약 | `/api/status` | 주문 없음 |
| Stocks / 종목 | 후보/보유 종목, risk flags, 종목별 LOT 보기, manual BUY preview 연결 | `/api/stocks`, `/api/stocks/{code}` | KIS 주문 직접 호출 없음 |
| Lots / LOT | LOT 목록, 손익, stale/cleanup, manual SELL preview 연결 | `/api/lots` | LOT 직접 수정 금지 |
| Orders/Fills / 주문/체결 | 주문/체결 연결, dedupe_key_type, fallback 체결 확인 | `/api/orders`, `/api/fills` | 주문 취소/정정 직접 호출 없음 |
| Logs / 로그 | log tail, keyword/level/event filter, masking | `/api/logs/tail` | 민감정보 노출 금지 |
| Config / 설정 | 항목별 설명/입력, diff, backup, atomic save, raw JSON 고급 보기 | `/api/config`, `/api/config/schema`, `/api/config/validate` | 잘못된 config 저장 금지 |
| Runtime / 런타임 | pause/resume/emergency stop/start-loop/reload-config | `/api/runtime/*` | 주문 직접 호출 없음 |
| Manual / 수동 주문 | manual order preview/request 목록 | `POST /api/manual-orders/preview`, `POST /api/manual-orders`, `GET /api/manual-order-requests` | UI 직접 주문 금지 |
| New Season / 새 시즌 | archive, liquidation plan, request 생성, reset guard wizard | `/api/new-season/*` | KIS 주문 API/DB reset 직접 실행 금지. 버튼은 confirm과 guard 필요 |
| Review / 수동검토 | REVIEW_REQUIRED 목록, recheck, acknowledge, 수동매도 안내 | `/api/review-required` | 강제 해제 금지 |

참고: `ui_server.py`에는 `/api/execution-mapping/status`와 `loadExecution()` 함수가 남아 있지만, 별도 nav 탭은 제거된 상태다. raw mapping 상태는 Dashboard warning/log/API로 확인한다.

## 19. Prepare New Season / 새 시즌 준비

목적은 기존 시즌 운영 기록을 안전하게 archive하고, 보유분을 실제 계좌와 맞춰 정리한 뒤, 새 DB/config 상태로 시작하는 것이다. 바로 DB 파일을 지우면 안 된다. 실제 보유가 남아 있거나 체결 동기화가 끝나지 않은 상태에서 reset하면 계좌와 DB가 영구히 어긋난다.

용어:

| 용어 | 사용자 친화적 의미 |
| --- | --- |
| archive | 이전 시즌 백업 |
| liquidation plan | 전량매도 예정표 |
| KIS balance snapshot | 실제 계좌 잔고 확인 자료 |
| manual SELL request | 봇에게 전량매도 요청 |
| reset | DB 초기화 |
| dry-run | 실행 전 미리보기 |

Wizard 단계:

1. 이전 시즌 백업: config, DB, logs, table exports를 timestamp archive로 복사.
2. 실제 계좌 잔고 확인: KIS balance snapshot이 필요하다. 주문이 아니라 잔고 확인 자료다.
3. 전량매도 예정표 생성: 현재 DB OPEN LOT과 KIS snapshot을 비교해 매도 예정표 생성.
4. 전량매도 요청 생성: confirm text `전량매도 요청 확인` 후 manual_order_requests 큐에 SELL request 생성. UI/script는 KIS 주문 API를 호출하지 않는다.
5. 체결 및 동기화 확인: Bot Core가 request를 처리하고, fills/reconciliation 후 OPEN LOT이 줄어야 한다.
6. DB 초기화: OPEN LOT 0, 진행 중 order 0, pending manual request 0, SYNC_REQUIRED 0, mismatch 0일 때만 confirm text `RESET 확인`으로 가능.
7. 새 100종목 config 적용 확인: expansion_100_safe와 KOSPI 100 후보군 적용 확인.
8. 새 시즌 시작 준비 완료: 모든 조건 충족 시 UI에 준비 완료 표시.

Plan metadata:

| 필드 | 의미 |
| --- | --- |
| `plan_id`, `created_at` | 예정표 식별/생성시각 |
| `db_snapshot_at`, `kis_balance_snapshot_at` | 기준 snapshot 시각 |
| `db_open_lot_hash`, `kis_snapshot_hash` | 생성 후 상태 변경 검증용 hash |
| `open_lot_count`, `pending_order_count`, `pending_manual_request_count` | 생성 시점 상태 |
| `sync_required_count`, `lot_mismatch_count` | mismatch guard |
| `status` | ACTIVE / EXPIRED / SUPERSEDED / USED / BLOCKED |
| `expires_at`, `max_age_minutes` | KIS snapshot/plan 유효시간 |

전량매도 request 생성 전 plan을 다시 검증한다. plan 생성 후 DB OPEN LOT hash가 달라지거나, pending order/manual request가 생기거나, KIS snapshot이 만료되면 request 생성은 차단된다. 새 ACTIVE plan 생성 시 기존 ACTIVE plan은 SUPERSEDED/EXPIRED 처리된다. request 생성 성공 후 plan은 USED 또는 REQUESTED 계열로 간주된다.

reset 차단 조건:

| 조건 | block reason |
| --- | --- |
| OPEN LOT 존재 | `reset_open_lot_exists` |
| 진행 중 order 존재 | `reset_pending_order_exists` |
| pending manual request 존재 | `reset_pending_manual_request_exists` |
| SYNC_REQUIRED | `reset_sync_required` |
| lot mismatch | `liquidation_plan_lot_mismatch` |
| KIS/DB mismatch | liquidation request 단계에서 차단 |

현재 상태는 OPEN LOT이 남아 있으면 DB reset이 차단되는 것이 정상이다.

## 20. KIS balance snapshot / execution raw mapping

KIS balance snapshot은 전량매도 예정표/DB reset safety를 위해 필요하다. DB 기준 수량만으로 전량매도 request를 만들면 실제 계좌 잔고와 다를 수 있다.

Snapshot에 필요한 값:

| 필드 | 의미 |
| --- | --- |
| `code` / `pdno` / `symbol` | 종목코드. 코드에서는 6자리 문자열로 정규화한다. |
| `holding_quantity` / `hldg_qty` / `quantity` | 실제 보유수량. 코드 검증에서 수량 비교에 사용한다. |
| `sellable_quantity` / `ord_psbl_qty` / `available_quantity` | 매도가능수량. plan preview/dry-run에서는 없으면 보유수량으로 fallback하고 warning을 남길 수 있지만, 실제 liquidation request 생성 단계에서는 필수다. |
| `generated_at` | snapshot 생성시각. plan preview/dry-run에서는 없으면 warning을 남길 수 있지만, 실제 liquidation request 생성 단계에서는 필수이며 ISO 시간 파싱과 max age 검증을 통과해야 한다. |
| name/price 등 | 있으면 UI/plan 표시용 |

검증:

1. DB OPEN LOT total quantity와 DB position quantity를 계산한다.
2. KIS holding_quantity와 비교한다.
3. sellable_quantity가 매도 요청 수량 이상인지 확인한다.
4. open order/pending manual SELL/SYNC_REQUIRED/lot mismatch가 있으면 차단한다.
5. 조회 실패 또는 snapshot 만료면 request 생성 금지.

Execution raw mapping은 체결내역 필드 검증용이다. 전량매도 snapshot과 목적이 다르다. raw mapping은 order_no, execution_id, filled_at, side, code, price, quantity가 들어오는지 확인하고 민감정보가 마스킹되는지 본다.

## 21. Config 구조

핵심 섹션:

| 섹션 | 내용 | 위험 설정 |
| --- | --- | --- |
| `stocks` | 100개 후보군, enabled/manual_only/risk flags/sector/note | risk flag true이면 RISK_BLOCKED 후보 |
| `strategy` | LOT sizing, add bands, target bands, reentry, cleanup, stale/review | cleanup_enabled, lot sizing disabled band |
| `risk` | 전역 계좌 제한, profile, max_open_lots, invested amount, daily limits | max 값 변경은 노출 직접 영향 |
| `order` | live_trading, price sample, limit order, raw log, reconciliation | live_trading/emergency/cancel/raw log 위험 |
| `market_hours` | 장중/장전/장마감 차단 시간 | 매매 가능 시간 영향 |
| `kis_account` | env key 이름만 저장 | 실제 계좌번호/API 키 노출 금지 |
| `upstream_watch` | repo update watcher | 자동 코드 변경 주의 |
| `ui_manual_trading_enabled` | UI 수동 주문 요청 생성 허용 여부 | 기본 false 유지 권장 |

Config 저장 UX는 backup, validation, diff, atomic write, round-trip verify, change history를 거친다. raw JSON은 고급 보기로 유지하되 기본은 항목별 form이다.

## 22. Logs / Decision log

중요 로그 필드:

| 필드/이벤트 | 의미 |
| --- | --- |
| `action_created` | strategy가 BUY/SELL 후보 action을 만들었는지 |
| `action_blocked_before_request` | main guard에서 주문 요청 전 차단 |
| `action_execution_state` | 생성/차단/요청/실패 등 최종 실행상태 |
| `final_block_reason`, `skip_reason` | 실제로 주문이 나가지 않은 최종 이유 |
| `pnl_mode`, `reference_buy_price`, `open_lot_vwap_buy_price`, `median_open_buy_price` | 추가매수 판단 기준 |
| `lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`, `entry_price_for_lot_sizing`, `lot_sizing_locked_at` | cycle sizing 추적 |
| `add_buy_lot_band`, `current_open_lot_count` | 추가매수 band 추적 |
| `target_profit_lot_band`, `current_base_target_profit_rate`, `effective_target_profit_rate` | 동적 sell target 추적 |
| `review_reason`, `review_trigger_values` | REVIEW_REQUIRED 이유 |
| `manual_order_request_*` | 수동 주문 preview/created/blocked/submitted/failed |
| `liquidation_plan_*`, `reset_*` | 새 시즌 준비/전량매도/초기화 guard |
| `kis_raw_executions` | raw execution sample/mapping. raw log 옵션 true에서만 |
| `dedupe_key_type` | execution_id/fallback dedupe 구분 |

로그만 보고 “왜 샀는지/왜 안 샀는지/왜 팔았는지/왜 막혔는지” 추적하는 것이 UI/로그 설계 목표다.

## 23. 테스트 현황

최신 확인 기준 전체 테스트는 다음 명령에서 `156 passed`였다. pytest cache warning 1개는 basetemp/cache write 관련이며 기능 실패가 아니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

테스트 범주:

| 범주 | 검증 내용 |
| --- | --- |
| LOT sizing | 가격대별 band, cycle lock, migration, manual preview mismatch |
| add buy lot bands | 1~2/3~4/5~6/7~8/9~10 LOT band, 9->10 허용, 10 차단 |
| target profit dynamic | 현재 OPEN LOT 수 기준 target, 일부 매도 후 target 재계산 |
| REVIEW_REQUIRED | 진입, recheck, acknowledge, sync mismatch 시 SYNC_REQUIRED |
| manual order requests | UI/API request 생성, guard, Bot Core 소비, fill 전 positions 불변 |
| UI 한글화/정렬/컬럼 | label/key 병기, column 선택, width resize/localStorage |
| Prepare New Season | archive, liquidation plan freshness, reset guard, no KIS order API |
| reconciliation/dedupe | execution_id/fallback dedupe, duplicate count, raw mapping mock |
| global risk limits | active/new buy/open lot/invested amount/day amount 제한 |

## 24. 현재 운영 상태와 다음 단계

현재 상태:

| 항목 | 상태 |
| --- | --- |
| config profile | `expansion_100_safe` |
| KOSPI 후보군 | 100종목 적용 |
| enabled/manual_only | enabled 97, disabled/manual_only 3 |
| OPEN LOT | 현재 DB에 남아 있으면 reset 차단이 정상 |
| DB reset | OPEN LOT이 남아 있으면 차단 |
| 전량매도 request | 최신 KIS balance snapshot과 liquidation plan 필요 |
| live_trading | false |
| cleanup_enabled | false |
| ui_manual_trading_enabled | false |
| enable_execution_raw_log | true |
| raw execution mapping | 첫 실체결 row 최종 확인 필요 warning 가능 |

다음 단계:

1. UI 새 시즌 탭에서 현재 차단 사유 확인.
2. KIS balance snapshot 준비.
3. liquidation plan 생성.
4. 전량매도 request 생성 여부 결정. confirm text 필요.
5. Bot Core가 manual SELL request를 처리하도록 봇 실행.
6. 체결/reconciliation 완료 확인.
7. OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 확인.
8. DB reset dry-run 후 confirm reset.
9. 새 시즌 시작 준비 완료 확인.
10. 제한 운용 시작.
11. decision log로 lot sizing/target profit 확인.
12. raw execution mapping 확인 후 `enable_execution_raw_log=false`로 복구.

## 25. 아직 하지 말아야 할 것

| 금지 | 이유 |
| --- | --- |
| OPEN LOT 남은 상태에서 DB reset | 실제 보유/체결 이력과 DB가 어긋남 |
| KIS snapshot 없이 전량매도 request 생성 | 실제 잔고 수량과 DB 수량 불일치 위험 |
| UI에서 KIS 주문 API 직접 호출 | 관제 UI가 주문기가 되는 위험 |
| live_trading=true 전환 후 대규모 운용 | raw mapping/전량매도/reset 검증 전 위험 |
| cleanup_enabled 즉시 true | 100종목 초기 로그/동기화 안정화 전 손실 확정 위험 |
| 100종목 전체 첫날 무제한 진입 | max_new_buy/day amount 제한을 우회하면 과다 노출 |
| pending manual request 상태에서 reset | 요청 처리 결과를 잃음 |
| DB/KIS mismatch 상태에서 reset | SYNC_REQUIRED 상태를 무시하게 됨 |

## 26. 새 thread에서 이어가기 위한 확인 질문

새 채팅방/새 Codex 세션에서 이어갈 때 먼저 확인할 질문:

1. 현재 DB에 OPEN LOT이 몇 개 남아 있는가?
2. KIS balance snapshot을 준비했는가?
3. liquidation plan은 ACTIVE이고 최신 DB/KIS snapshot과 일치하는가?
4. reset 가능 조건을 만족하는가?
5. `live_trading`은 false인가?
6. `enable_execution_raw_log`는 true인가, 첫 실체결 mapping 확인이 끝났는가?
7. 최신 전체 테스트는 통과했는가?
8. 현재 config profile은 `expansion_100_safe`인가?
9. 사용자가 원하는 다음 작업은 UI 개선, 실제 전량매도 준비, config 튜닝, 로그 검증 중 무엇인가?
10. manual_order_requests에 pending request가 있는가?
11. SYNC_REQUIRED/REVIEW_REQUIRED/RISK_BLOCKED 종목이 있는가?

## 27. 확인 필요 / 보류 / 주의사항

| 항목 | 상태 | 설명 |
| --- | --- | --- |
| 실제 KIS raw execution field mapping | 확인 필요 | mock과 일부 실로그 검증은 있으나 Dashboard warning 기준 첫 실제 raw row 최종 확인 필요가 남아 있을 수 있다. |
| KIS balance snapshot 최신 생성 방식 | 확인 필요 | UI/script는 snapshot path를 받는 구조다. 실제 조회 파일 생성/선택 흐름은 운영 절차로 확인해야 한다. |
| Execution Mapping Check 탭 | 변경됨 | nav 탭은 제거되었고 API/function은 남아 있다. 필요하면 다시 노출 가능. |
| Native Windows/Android 앱 | 보류 | 현재 표준 API는 localhost Web UI/API. 원격 공개 금지, VPN/secure relay 권장. |
| cleanup 자동화 | 보류/비활성 | `cleanup_enabled=false`로 시작. 로그 안정화 후 별도 검토. |
| DB maintenance mode | 보류 | LOT/position 직접 수정 기능은 기본 제공하지 않는다. |

## 28. 실제 실행 Runbook

이 섹션은 운영자가 PowerShell에서 복붙해 따라갈 수 있는 절차다. 모든 명령은 저장소 루트 `C:\MSJ\KIS-MSJ`에서 실행한다. `kis_msj` 모듈은 현재 소스 레이아웃상 `PYTHONPATH=src`가 필요할 수 있다.

### 28-1. UI 실행

| 항목 | 내용 |
| --- | --- |
| 목적 | localhost 관제/설정/수동 요청/새 시즌 준비 UI 실행 |
| 실행 전 조건 | 실거래 주문을 내는 명령이 아님. UI는 KIS 주문 API를 직접 호출하지 않는다. |
| 명령 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765` |
| 실행 후 확인 | 브라우저에서 `http://127.0.0.1:8765` 접속. Dashboard에 `live_trading=false`, profile, OPEN LOT 수 확인 |
| 절대 금지 | 외부 IP로 공개, 포트포워딩, 계좌/API key 노출 |

### 28-2. 봇 실행

| 항목 | 내용 |
| --- | --- |
| 목적 | Bot Core 자동 루프 실행 |
| 실행 전 조건 | config, runtime pause, live_trading, OPEN order 상태 확인. 실거래 전 충분한 paper/mock 확인 필요 |
| 1회 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.main --config config\lot_auto_trader.json --once --mock` |
| 일반 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.main --config config\lot_auto_trader.json` |
| 실행 후 확인 | Logs, Orders/Fills, Manual Order Request, Dashboard warnings |
| 절대 금지 | 의도 없이 `live_trading=true` 상태에서 장중 대규모 운용 시작 |

### 28-3. 테스트 실행

| 항목 | 내용 |
| --- | --- |
| 목적 | 전체 회귀 테스트 |
| 명령 | `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check` |
| 기대 결과 | 최신 기준 `156 passed`. warning 1개는 pytest cache 관련이면 기능 실패 아님 |
| 실패 시 | 실패 테스트 이름과 관련 파일을 먼저 확인. 실거래 주문으로 확인하려 하지 말 것 |

### 28-4. 새 시즌 상태 확인

| 항목 | 내용 |
| --- | --- |
| 목적 | 새 시즌 준비 가능 여부, 차단 사유 확인 |
| UI | `New Season / 새 시즌` 탭 |
| API | `GET /api/new-season/status` |
| 확인 | OPEN LOT, pending order, pending manual request, SYNC_REQUIRED, plan status, reset_possible |
| 절대 금지 | OPEN LOT이 남은 상태에서 reset 실행 |

### 28-5. archive 생성

| 항목 | 내용 |
| --- | --- |
| 목적 | config/DB/logs/exports를 이전 시즌 archive로 보존 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --dry-run` |
| 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --execute` |
| 확인 | `archive/reset_YYYYMMDD_HHMMSS/` 아래 config/db/logs/exports 생성 |
| 절대 금지 | archive 없이 기존 DB/log/config 삭제 |

### 28-6. KIS balance snapshot 준비/검증

| 항목 | 내용 |
| --- | --- |
| 목적 | DB 보유수량과 실제 계좌 잔고 수량 비교 |
| 입력 | JSON snapshot 파일. 예: `exports/kis_balance_snapshot_YYYYMMDD_HHMMSS.json` |
| 확인 | code/pdno/symbol, holding_quantity/hldg_qty/quantity, sellable_quantity/ord_psbl_qty/available_quantity, generated_at. dry-run은 일부 누락을 warning으로 허용할 수 있지만 실제 request 생성은 strict 검증을 통과해야 한다. |
| 절대 금지 | snapshot 없이 전량매도 request 생성 |

현재 구현은 snapshot path를 입력받아 검증한다. KIS 주문 API를 호출하지 않는다. 코드 기준으로는 KIS 잔고 snapshot JSON을 자동 생성하는 기능이 `prepare_new_season.py`에 없다. 운영자는 UI/CLI에 넘길 JSON 파일을 별도로 준비해야 한다. plan preview/dry-run에서는 `generated_at` 또는 `sellable_quantity` 누락을 warning으로 표시할 수 있지만, 실제 manual SELL request 생성 단계에서는 `generated_at` 파싱, snapshot max age, 실제 매도가능수량을 strict 검증한다.

### 28-7. liquidation plan 생성

| 항목 | 내용 |
| --- | --- |
| 목적 | DB OPEN LOT과 KIS snapshot을 기준으로 전량매도 예정표 생성 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --dry-run` |
| 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute` |
| 확인 | `exports/liquidation_plan_*.json`, status, eligible_for_liquidation_request |
| 절대 금지 | 예전 plan을 최신 검증 없이 재사용 |

### 28-8. liquidation plan 보기/검증

| 항목 | 내용 |
| --- | --- |
| 목적 | plan이 ACTIVE인지, DB hash/KIS snapshot이 최신인지 확인 |
| UI | New Season 탭에서 plan status, block_reason, next_action 확인 |
| API | `GET /api/new-season/status` |
| 차단 예 | `liquidation_plan_db_changed`, `liquidation_plan_snapshot_expired`, `liquidation_kis_balance_mismatch` |

### 28-9. 전량매도 manual SELL request 생성

| 항목 | 내용 |
| --- | --- |
| 목적 | plan 기준 SELL request를 `manual_order_requests` 큐에 생성 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "전량매도 요청 확인" --dry-run` |
| 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "전량매도 요청 확인" --execute` |
| 확인 | Manual Order Request 탭에 SELL REQUESTED 생성, Bot Core가 이후 처리 |
| 절대 금지 | script/UI가 KIS 주문 API 직접 호출, fill 전 lots/positions 선반영 |

### 28-10. DB reset dry-run / 실행

| 항목 | 내용 |
| --- | --- |
| 목적 | OPEN LOT 0 등 안전 조건 충족 후 DB 초기화 가능 여부 확인 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --dry-run` |
| 실행 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --execute` |
| 실행 전 조건 | OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0, lot mismatch 0 |
| 절대 금지 | 전량매도/reconciliation 완료 전 reset |

### 28-11. raw execution log 확인 후 끄기

| 항목 | 내용 |
| --- | --- |
| 목적 | 첫 실체결 raw field mapping 확인 후 민감 로그 최소화 |
| 확인할 값 | `has_order_no`, `has_filled_at`, `has_side`, `has_execution_id` 또는 fallback 안정성, `dedupe_key_type`, masked sample |
| 끄는 절차 | UI Config -> Order -> `enable_execution_raw_log=false` 저장 -> Runtime `Reset / Config 다시 읽기` |
| 절대 금지 | raw log true를 장기간 유지하거나 민감정보 원문 출력 |

## 29. KIS Balance Snapshot 상세 포맷

KIS balance snapshot은 “실제 계좌 잔고 확인 자료”다. 새 시즌 전량매도 request를 DB 수량만으로 만들면 실제 계좌와 다를 수 있으므로, DB OPEN LOT 수량과 KIS 실제 보유/매도가능 수량을 비교하기 위해 필요하다.

snapshot이 없으면 liquidation request 생성은 막혀야 한다. 대표 block_reason은 `liquidation_kis_balance_fetch_required` 또는 `liquidation_kis_balance_fetch_failed`다.

지원 포맷은 현재 문서 기준 JSON을 표준으로 둔다. CSV를 쓰려면 script/service에서 명시 지원 여부를 먼저 확인해야 한다.

### 예시 JSON

```json
{
  "generated_at": "2026-05-26T15:30:00+09:00",
  "account_id_masked": "****1234",
  "source": "kis_balance_snapshot",
  "positions": [
    {
      "code": "005930",
      "name": "삼성전자",
      "holding_quantity": 3,
      "sellable_quantity": 3,
      "average_price": 70000,
      "current_price": 71000
    }
  ]
}
```

| 필드 | 필수 | 의미 |
| --- | --- | --- |
| `generated_at` | request 생성 시 필수 | snapshot 생성 시각. plan preview/dry-run에서는 warning으로 허용 가능하지만, 실제 request 생성 단계에서는 ISO 시간 파싱과 max age 검증을 통과해야 한다. |
| `source` | 권장 | `kis_balance_snapshot` 등 출처 표시 |
| `account_id_masked` | 선택 | 계좌 식별용 마스킹 값. 원문 계좌번호 금지 |
| `positions[].code` / `pdno` / `symbol` | 필수 | 종목코드. 6자리 문자열로 정규화 |
| `positions[].name` | 권장 | 종목명 |
| `positions[].holding_quantity` / `hldg_qty` / `quantity` | 필수 | 실제 계좌 보유수량 |
| `positions[].sellable_quantity` / `ord_psbl_qty` / `available_quantity` | request 생성 시 필수 | 실제 매도가능수량. plan preview/dry-run에서는 없으면 보유수량 fallback + warning이 가능하지만, 실제 request 생성 단계에서는 누락 시 `liquidation_kis_sellable_quantity_missing`으로 차단한다. |
| `positions[].average_price` | 선택 | 실제 계좌 평균단가. DB LOT 판단에는 직접 쓰지 않음 |
| `positions[].current_price` | 선택 | 표시/예상금액 계산용 |

비교 방식:

1. DB OPEN LOT quantity = `lots.remaining_quantity > 0 AND status != CLOSED` 합계.
2. DB position total quantity와도 비교한다.
3. KIS `holding_quantity`와 DB OPEN LOT quantity가 다르면 `liquidation_kis_balance_mismatch`.
4. KIS `sellable_quantity`가 request 수량보다 작으면 `liquidation_sellable_quantity_insufficient`.
5. snapshot의 `generated_at`이 없으면 request 생성 단계에서 `liquidation_kis_balance_snapshot_missing_generated_at`.
6. `generated_at` 파싱 실패 시 `liquidation_kis_balance_snapshot_invalid_generated_at`.
7. snapshot age가 max age를 초과하면 `liquidation_kis_balance_snapshot_stale`.
8. snapshot이 오래된 plan이면 `liquidation_plan_snapshot_expired`.

raw execution mapping과의 차이:

| 항목 | KIS balance snapshot | raw execution mapping |
| --- | --- | --- |
| 목적 | 실제 보유/매도가능 수량 확인 | 체결내역 row 필드명 검증 |
| 사용 시점 | 전량매도 plan/request/reset 전 | 첫 실체결 후 reconciliation 검증 |
| 핵심 필드 | code/pdno/symbol, holding_quantity/hldg_qty/quantity, sellable_quantity/ord_psbl_qty/available_quantity, generated_at(request 생성 시 필수) | order_no, execution_id, filled_at, side, code, price, quantity |
| 없을 때 | 전량매도 request 차단 | raw mapping warning 유지 |

## 30. API Endpoint / Payload 예시

모든 API는 localhost UI 서버 기준이다. UI 서버는 KIS 주문 API를 직접 호출하지 않는다.

| API | 목적 | KIS 주문 API 호출 | lots/positions/fills 직접 변경 |
| --- | --- | --- | --- |
| `GET /api/status` | Dashboard 전체 상태 | 아니오 | 아니오 |
| `GET /api/stocks` | 종목 목록 | 아니오 | 아니오 |
| `GET /api/lots` | LOT 목록 | 아니오 | 아니오 |
| `GET /api/orders` | 주문 목록 | 아니오 | 아니오 |
| `GET /api/fills` | 체결 목록 | 아니오 | 아니오 |
| `GET /api/manual-order-requests` | 수동 요청 목록 | 아니오 | 아니오 |
| `POST /api/manual-orders/preview` | 수동 주문 미리보기 | 아니오 | 아니오 |
| `POST /api/manual-orders` | manual_order_requests row 생성 | 아니오 | 아니오 |
| `GET /api/review-required` | REVIEW_REQUIRED 목록 | 아니오 | 아니오 |
| `GET /api/positions/{code}/review-status` | 특정 종목 review 상태 | 아니오 | 아니오 |
| `POST /api/positions/{code}/review/recheck` | review 조건 재평가 | 아니오 | position_state/review field는 바뀔 수 있음 |
| `POST /api/positions/{code}/review/acknowledge` | 사용자 확인 기록 | 아니오 | review ack 필드만 |
| `GET /api/new-season/status` | 새 시즌 wizard 상태 | 아니오 | 아니오 |
| `POST /api/new-season/archive` | archive dry-run/실행 | 아니오 | DB 내용 변경 없음 |
| `POST /api/new-season/validate-snapshot` | KIS balance snapshot JSON 검증 | 아니오 | 아니오 |
| `POST /api/new-season/liquidation-plan` | liquidation plan 생성 | 아니오 | DB 내용 변경 없음 |
| `POST /api/new-season/liquidation-requests` | manual SELL request 생성 | 아니오 | manual_order_requests만 |
| `POST /api/new-season/reset-db` | DB reset dry-run/실행 | 아니오 | execute+confirm+guard 통과 시 DB 초기화 |

Snapshot strict validation:

- `POST /api/new-season/liquidation-plan`: preview/plan 생성 목적이다. `generated_at` 또는 `sellable_quantity` 누락은 plan의 `snapshot_warnings`, `request_creation_allowed=false`, `request_creation_block_reason`으로 남길 수 있다.
- `POST /api/new-season/liquidation-requests`: 실제 manual SELL request 생성 직전 strict 모드다. `generated_at` 누락/파싱 실패/age 초과, `sellable_quantity` 누락/부족은 request 생성을 차단한다.

### 수동 BUY preview 예시

```json
{
  "code": "005930",
  "side": "BUY",
  "amount": 30000,
  "requested_by": "local_ui",
  "confirm_text": ""
}
```

핵심 response 필드:

- `allowed`
- `block_reasons`
- `quantity`
- `estimated_amount`
- `current_price`
- `lot_sizing_bucket`
- `lot_unit_amount`
- `max_symbol_amount`
- `runtime_snapshot`

### 수동 주문 request 생성 예시

```json
{
  "code": "005930",
  "side": "BUY",
  "amount": 30000,
  "requested_by": "local_ui",
  "confirm_text": "수동주문 확인"
}
```

response 핵심:

- 생성 성공 시 `request_id`, `status=REQUESTED`
- 차단 시 `created=false`, `block_reason` 또는 `block_reasons`

주의: 이 API는 KIS 주문을 내지 않고 큐만 만든다. 실제 주문은 실행 중인 Bot Core가 처리한다.

### REVIEW recheck 예시

```json
{}
```

`POST /api/positions/005930/review/recheck` 또는 `POST /api/review-required/005930/recheck`.

핵심 response:

- `cleared`
- `state`
- `remaining_reasons`
- `sync_required`

### REVIEW acknowledge 예시

```json
{
  "acknowledged_by": "local_ui",
  "note": "수동매도 후 재확인 예정"
}
```

acknowledge는 확인 기록만 남기며 BUY 차단 해제가 아니다.

### New Season liquidation plan 예시

```json
{
  "execute": true,
  "kis_balance_json_path": "exports/kis_balance_snapshot_20260526_153000.json",
  "max_age_minutes": 60
}
```

### New Season liquidation request 예시

```json
{
  "execute": true,
  "plan_path": "exports/liquidation_plan_20260526_153500_xxxx.json",
  "kis_balance_json_path": "exports/kis_balance_snapshot_20260526_153000.json",
  "confirm": "전량매도 요청 확인"
}
```

### New Season reset 예시

```json
{
  "execute": true,
  "confirm": "RESET 확인"
}
```

reset은 위험 동작이다. OPEN LOT, pending order, pending manual request, sync mismatch가 있으면 차단되어야 한다.

## 31. Troubleshooting / 상황별 대응표

| 증상 | 가능한 원인 | 확인할 UI/API/log | 하면 되는 조치 | 하면 안 되는 조치 |
| --- | --- | --- | --- | --- |
| `request_creation_possible=false` | plan 없음, snapshot 없음, plan stale, pending work | New Season, `/api/new-season/status` | 한글 block guide의 다음 행동 수행 | 내부 flag만 보고 강제 진행 |
| `liquidation_plan_missing` | 전량매도 예정표 없음 | New Season | KIS snapshot 준비 후 plan 생성 | 예전 plan 임의 지정 |
| `liquidation_plan_stale` | plan max age 초과 | New Season | 최신 snapshot/DB 기준 plan 재생성 | 오래된 plan으로 request 생성 |
| `liquidation_plan_db_changed` | plan 생성 후 OPEN LOT 변경 | New Season | plan 재생성 | 기존 plan 재사용 |
| `liquidation_kis_balance_fetch_required` | KIS balance snapshot 없음 | New Season | snapshot 준비/선택 | DB 수량만 믿고 전량매도 |
| `liquidation_kis_balance_mismatch` | DB LOT 수량과 KIS 보유수량 불일치 | Reconciliation, New Season | reconciliation/sync 확인 | reset 또는 전량매도 강행 |
| `reset_open_lot_exists` | OPEN LOT 남아 있음 | Lots, New Season | 전량매도/reconciliation 완료 | DB reset |
| `reset_pending_order_exists` | 진행 중 주문 있음 | Orders/Fills | 주문 체결/취소/거절 종결 대기 | 주문 무시하고 reset |
| `reset_pending_manual_request_exists` | 미처리 수동 요청 있음 | Manual Order Request | 요청 처리 완료 대기 | request row 삭제로 우회 |
| `reset_sync_required` | DB/KIS 동기화 필요 | Dashboard, Review, Reconciliation | reconciliation 우선 | 상태 강제 변경 |
| SYNC_REQUIRED 상태 | 실제 잔고와 DB 불일치 | Stocks/Review/Reconciliation | 체결/잔고 동기화 확인 | 신규 주문 |
| REVIEW_REQUIRED 상태 | 손실/LOT과다/stale/cleanup 완료 | Review 탭 | reason 확인, 수동매도/ack/recheck | 강제 HOLDING 변경 |
| RISK_BLOCKED 상태 | risk flag true | Stocks risk flags | 사유 확인, 필요 시 config flag 조정 | SELL 허용 정책 임의 변경 |
| `lot_sizing_changed_after_preview` | preview 후 가격 구간 변경 | Manual request log | 다시 preview | 이전 preview로 주문 |
| `max_lots_per_symbol_reached` | 종목 LOT 수 상한 도달 | Decision log, Lots | 추가매수 중단/수동검토 | 상한 우회 |
| `max_symbol_amount_reached` | 종목별 금액 상한 도달 | Decision log | 노출 축소/검토 | max만 임의 확대 |
| `max_new_buy_amount_per_day_reached` | 하루 신규매수 금액 제한 | Dashboard/Risk log | 다음 거래일 대기 | 제한 즉시 해제 |
| `record_fill_failed` | 중복 fill 또는 insert 실패 | Logs, Fills | dedupe_key_type 확인 | apply_fill 수동 호출 |
| duplicate fill 증가 | 조회 범위 확대/재조회 | Reconciliation log | new_fill_count와 함께 확인 | 중복이라고 무조건 오류 판단 |
| raw execution mapping warning | 첫 실제 row 최종 확인 전 | Dashboard/Logs | raw log 확인 후 필드 검증 | raw log 장기 방치 |
| `enable_execution_raw_log=true` 계속 유지 | 검증 후 off 누락 | Config Order | false 저장 후 config reload | 민감 raw sample 방치 |
| disabled/manual_only 종목 표시 | risk/master/event 사유 | Stocks/Config | note/risk flag 확인 | enabled 무조건 true |
| New Season 다음 버튼 비활성 | 이전 단계 조건 미충족 | New Season guidance | 표시된 다음 행동 수행 | disabled 버튼 우회 |

## 32. 새 시즌 준비 UI 사용자 설명

새 시즌 탭에 들어가면 먼저 다음을 본다.

1. OPEN LOT 수
2. 미체결 주문 수
3. 미처리 manual request 수
4. SYNC_REQUIRED / lot mismatch 여부
5. liquidation plan 존재/상태/만료 여부
6. KIS balance snapshot 필요 여부
7. reset 가능 여부

OPEN LOT이 남아 있으면 reset이 막힌다. DB 초기화는 “기록 정리”가 아니라 내부 보유 상태를 새로 시작하는 행위이므로, 실제 계좌에 보유가 남아 있거나 체결 동기화가 끝나지 않았는데 reset하면 이후 모든 주문 판단이 위험해진다.

dry-run은 실행 전 미리보기다. archive, liquidation plan, liquidation requests, reset 모두 dry-run으로 먼저 확인하고, 실제 실행은 confirm과 guard를 통과해야 한다.

liquidation plan은 전량매도 예정표다. 고정 문서가 아니라 현재 DB 상태와 현재 KIS balance snapshot 기준으로 매번 새로 계산되어야 한다.

Plan status 의미:

| status | 사용자 설명 |
| --- | --- |
| ACTIVE | 현재 예정표가 유효하다 |
| EXPIRED | 오래되어 새로 만들어야 한다 |
| SUPERSEDED | 더 최신 예정표가 있어 사용할 수 없다 |
| USED | 이미 전량매도 request 생성에 사용됐다 |
| BLOCKED | 차단 사유가 있어 사용할 수 없다 |

전량매도 요청 생성 버튼이 비활성인 대표 원인:

- KIS balance snapshot 없음
- liquidation plan 없음
- plan 만료
- plan 생성 후 OPEN LOT 변경
- 미체결 주문 존재
- pending manual request 존재
- SYNC_REQUIRED 또는 lot mismatch

DB 초기화 버튼이 비활성인 대표 원인:

- OPEN LOT이 남아 있음
- 미체결 주문이 있음
- 미처리 manual request가 있음
- DB와 KIS 잔고가 일치하지 않음
- 전량매도 체결/reconciliation이 완료되지 않음

새 시즌 시작 준비 완료 조건:

- archive 완료
- 전량매도/reconciliation 완료
- OPEN LOT 0
- 진행 중 order 0
- pending manual request 0
- SYNC_REQUIRED 0
- lot mismatch 0
- expansion_100_safe/KOSPI 100 config 확인

## 33. REVIEW_REQUIRED 처리 상세 사례

### 사례 1. 손실률 -20% 이하

| 항목 | 설명 |
| --- | --- |
| 왜 발생 | 종목 전체 평가손실률이 review threshold 이하로 내려감 |
| UI 위치 | Review 탭, Stocks 상세, Lots |
| 가능한 조치 | 추가매수 중단, 수익권 LOT만 정리, 필요 시 수동 SELL request |
| recheck 시점 | 수동매도/reconciliation 후 손실률이 기준 이상으로 회복됐을 때 |
| acknowledge | 사용자가 상황을 확인했고 추적 메모만 남길 때 |
| 금지 | 조건이 남아 있는데 강제 HOLDING 전환 |

### 사례 2. OPEN LOT 수 10개 초과

| 항목 | 설명 |
| --- | --- |
| 왜 발생 | current_open_lot_count가 허용 범위를 넘음 |
| UI 위치 | Review 탭, Lots, Dashboard risk |
| 가능한 조치 | PROFIT_TAKE 가능한 LOT 정리, 수동매도 후 reconciliation |
| recheck 시점 | OPEN LOT 수가 제한 이하로 줄어든 뒤 |
| 금지 | max_lots만 키워 자동매수를 즉시 재개 |

### 사례 3. 오래된 STALE LOT

| 항목 | 설명 |
| --- | --- |
| 왜 발생 | 손실률, 나이, 가격 괴리 조건을 오래 만족 |
| UI 위치 | Review 탭, Lots stale filter |
| 가능한 조치 | 관찰, 수익권 LOT 정리, cleanup_enabled 정책 재검토 |
| recheck 시점 | stale 조건 해소 또는 LOT 정리 후 |
| 주의 | STALE_LOT은 즉시 손절 신호가 아니다 |

### 사례 4. 수동매도 후에도 REVIEW_REQUIRED 유지

| 항목 | 설명 |
| --- | --- |
| 왜 발생 | 손실률/LOT 수/stale 조건이 아직 남아 있음 |
| UI 위치 | Review 탭 remaining reasons |
| 가능한 조치 | 남은 reason을 확인하고 추가 조치 또는 acknowledge |
| recheck 시점 | 체결과 reconciliation 완료 후 |
| 금지 | “매도했으니 무조건 해제”로 강제 변경 |

### 사례 5. 수동매도 후 SYNC_REQUIRED로 변경

| 항목 | 설명 |
| --- | --- |
| 왜 발생 | DB와 실제 KIS 잔고가 일치하지 않음. 체결 반영 전이거나 수동매매/partial mismatch 가능 |
| UI 위치 | Dashboard warning, Review, Reconciliation, Orders/Fills |
| 가능한 조치 | reconciliation 완료, fills/lots/positions/KIS 잔고 비교 |
| recheck 시점 | sync mismatch가 사라진 뒤 |
| 금지 | SYNC_REQUIRED를 무시하고 BUY 재개 |

## 34. Config 실제값 표 보강

### price_lot_bands

| min 가격 | max 가격 | 1 LOT | 종목당 최대 | enabled | 영향 |
| ---: | ---: | ---: | ---: | --- | --- |
| 0 | 300 | 1,000 | 10,000 | true | 초저가주도 1 LOT 소액 진입 가능 |
| 301 | 1,000 | 3,000 | 30,000 | true | 저가주 소액 LOT |
| 1,001 | 3,000 | 10,000 | 100,000 | true | 저가~중저가 구간 |
| 3,001 | 10,000 | 30,000 | 300,000 | true | 3만원 LOT 구간 |
| 10,001 | 30,000 | 100,000 | 1,000,000 | true | 중가주 10만원 LOT |
| 30,001 | 100,000 | 300,000 | 3,000,000 | true | 고가 진입 구간 |
| 100,001 | 300,000 | 1,000,000 | 10,000,000 | true | 대형 고가주 구간 |
| 300,001 | 1,000,000 | 3,000,000 | 30,000,000 | true | 매우 고가주 구간 |
| 1,000,001 | 3,000,000 | 10,000,000 | 100,000,000 | true | 초고가 구간, 전역 리스크 한도와 함께 운영 주의 |

3,000,000원을 초과하는 가격은 `price_lot_bands` 범위 밖이므로 `price_out_of_lot_sizing_range`로 BUY가 차단된다.

### add_buy_lot_bands

| LOT 구간 | drop_rate | add_lot_count | 변경 영향 |
| --- | ---: | ---: | --- |
| 1~2 | 4% | 1 | 초반 물타기 간격 |
| 3~4 | 6% | 1 | 중간 노출 조절 |
| 5~6 | 8% | 1 | 보수화 |
| 7~8 | 10% | 1 | 더 보수화 |
| 9~10 | 12% | 1 | 마지막 자동 추가매수 구간 |

### target_profit_lot_bands

| LOT 구간 | target_profit_rate | 변경 영향 |
| --- | ---: | --- |
| 1~2 | 6% | 낮은 노출에서는 높은 회전 목표 |
| 3~4 | 5% | 노출 증가 시 목표 완화 |
| 5~6 | 4% | 기존 LOT도 동적 적용 |
| 7~8 | 3% | 포지션 축소 우선 |
| 9~10 | 2% | 고노출 구간 회전 우선 |

### order 위험 설정

| 항목 | 현재값 | 의미 | 위험 | UI 변경 |
| --- | --- | --- | --- | --- |
| `live_trading` | false | true이면 실제 주문 가능 | 매우 높음 | 가능, 이중 확인 필요 |
| `emergency_market_order` | config 확인 필요 | 비상 시장가 관련 설정 | 매우 높음 | 가능하나 강경고 |
| `enable_execution_raw_log` | true | raw execution sample logging | 민감정보 주의 | 가능, 검증 후 false 권장 |
| `cancel_unfilled_on_start` | config 확인 필요 | 시작 시 미체결 취소 | 실거래 영향 | 가능하나 강경고 |

### cleanup 설정

| 항목 | 현재값 | 의미 | 위험 |
| --- | --- | --- | --- |
| `cleanup_enabled` | false | 손실 정리 자동매도 허용 | true 전환 시 손실 확정 가능 |
| `cleanup_min_target_rate` | 기본 -4% 계열 | 최대 손실 정리 허용률 | 너무 낮추면 손실 확대 |
| `cleanup_profit_offset_ratio` | config 확인 필요 | 당일 실현수익 중 cleanup budget 비율 | 손실 상쇄 규모 |
| `cleanup_buy_cooldown_days` | config 확인 필요 | cleanup 후 BUY cooldown, calendar days | 거래일 기준 아님 |
| `cleanup_reentry_cooldown_days` | config 확인 필요 | 전량 cleanup 후 review 전환 대기 | 거래일 기준 아님 |

### runtime/manual 설정

| 항목 | 현재값 | 의미 | 위험 |
| --- | --- | --- | --- |
| `ui_manual_trading_enabled` | false | UI manual request 생성 허용 | true여도 UI 직접 주문 없음 |
| `all_orders_paused` | runtime 파일 기준 | 모든 주문 요청 차단 | 안전장치 |
| `buy_paused` | runtime 파일 기준 | BUY 차단 | 안전장치 |
| `sell_paused` | runtime 파일 기준 | SELL 차단 | 수익실현도 막을 수 있음 |
| `cleanup_paused` | runtime 파일 기준 | CLEANUP_SELL 차단 | 손실 정리 중단 |
| `reentry_paused` | runtime 파일 기준 | 재진입 BUY 차단 | 재진입 중단 |

## 35. 새 세션 작업 시 절대 지켜야 할 실행 금지

이 문서를 넘겨받은 새 Codex/ChatGPT 세션은 다음을 사용자 명시 확인 없이 실행하면 안 된다.

- 실거래 주문
- KIS 주문 API 호출
- DB reset 실행
- OPEN LOT/fills/positions 직접 삭제 또는 수량 수정
- config `live_trading=true` 전환
- archive 없는 기존 DB/log/config 삭제
- manual request pending 상태에서 reset

## 36. 문서 정합성 self-check

최종 문서 정합성 기준:

| 체크 항목 | 기준 |
| --- | --- |
| authoritative source | `docs/project_handoff_full.md`가 최신 전체 기준이다. 세부 참고문서와 충돌하면 full 문서를 우선한다. |
| current state | full/summary/thread prompt 모두 `expansion_100_safe`, 100종목, enabled 97, manual_only 3 기준이다. |
| risk profile | `risk.profile=expansion_100_safe`로 통일한다. |
| 핵심 boolean | `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`로 통일한다. |
| reset 차단 조건 | 진행 중 orders, 진행 중 manual requests, OPEN LOT, SYNC_REQUIRED, lot mismatch, KIS/DB mismatch를 포함한다. |
| manual order 표현 | “KIS 직접 주문 API 없음 / manual request 생성 API는 있음”으로 통일한다. |
| CLI options | `scripts/prepare_new_season.py --help` 기준으로 `--config`, `--archive-root`, `--profile`, `--apply-config`, `--archive`, `--liquidation-plan`, `--create-liquidation-requests`, `--kis-balance-json`, `--liquidation-plan-file`, `--plan-max-age-minutes`, `--reset-db`, `--confirm`, `--dry-run`, `--execute`를 확인했다. 문서에서 `--archive`는 실제 백업 실행 플래그이고, archive root 지정은 `--archive-root`가 맞다. |
| API routes | `src/kis_msj/ui_server.py` 기준으로 `GET /api/status`, `/api/stocks`, `/api/lots`, `/api/orders`, `/api/fills`, `/api/manual-order-requests`, `POST /api/manual-orders/preview`, `POST /api/manual-orders`, review API, new-season API가 존재함을 확인했다. 옛 `/api/manual-order-preview` 표기는 사용하지 않는다. |
| KIS snapshot 표현 | 현재 구현은 snapshot JSON 파일을 입력받아 검증하는 구조다. `prepare_new_season.py`에는 KIS 잔고 snapshot 자동 생성 기능이 없으므로 운영자가 별도 JSON을 준비해야 한다. loader는 `code/pdno/symbol`, `holding_quantity/hldg_qty/quantity`, `sellable_quantity/ord_psbl_qty/available_quantity`를 지원한다. preview/dry-run에서는 `generated_at` 또는 `sellable_quantity` 누락을 warning으로 허용할 수 있지만, 실제 request 생성 단계에서는 둘 다 필수다. |
| Snapshot strict mode | 최신 보강 기준으로 preview/dry-run은 `generated_at` 누락, `sellable_quantity` 누락을 warning으로 plan에 남길 수 있다. 실제 liquidation request 생성 단계에서는 `generated_at`과 실제 `sellable_quantity`가 필수이며, 누락/파싱 실패/age 초과/매도가능수량 부족은 차단한다. |
| 중복/구버전 문서 | `docs` 폴더의 주요 문서 7개를 확인했으며 같은 목적의 구버전/중복 문서는 발견하지 못했다. |
| 문서 링크 | 주요 문서 상단의 상대 링크가 실제 파일명과 일치함을 확인했다. |
| Runbook 명령어 | 저장소 루트 `C:\MSJ\KIS-MSJ`, 필요 시 `$env:PYTHONPATH='src'`, dry-run/execute 구분, confirm text 명시. |
| 테스트 최신성 | `156 passed`, pytest cache warning 1개는 기능 실패 아님. 실행 시점에는 다시 확인 필요. |

문서 작성 범위에서는 실거래 주문, KIS 주문 API, DB reset을 실행하지 않았다.
