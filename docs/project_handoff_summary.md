# KIS LOT 자동거래 봇 인수인계 요약

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `156 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.

> 2026-05-29 loop optimization: automatic scan now uses one quote per enabled symbol, runs 5-sample stability checks only for BUY/SELL candidates, and fetches one final quote before order request construction. Operational changes to accept: non-candidates skip stability sampling, candidates may be blocked more conservatively, and final quote can change quantity/limit/new unlocked LOT bucket. Rollback range: `0e8405d..42c37fe`. Final quote is traceable via `decisions.current_price`, linked `price_snapshots.current_price`, and submitted `orders`; `raw_json` currently stores `sample_count` only, not individual samples or a nested `final_quote`.

> 2026-05-29 SELL LOT priority: if multiple OPEN LOTs satisfy a SELL condition, the bot now sells the oldest valid `buy_filled_at` first. `PROFIT_TAKE` keeps profit/exposure/quantity/`lot_id` as tie-breakers; `CLEANUP_SELL` keeps expected-loss/profit/`lot_id` as tie-breakers. Eligibility, guards, reconciliation, manual request routing, and fill-driven DB updates are unchanged.


관련 문서:

- [전체 인수인계](project_handoff_full.md)
- [요약본](project_handoff_summary.md)
- [새 thread 첫 메시지용 프롬프트](project_handoff_thread_prompt.md)
- [로컬 UI 문서](local_ui.md)
- [LOT sizing 전략](strategy_lot_sizing.md)
- [새 시즌 reset](new_season_reset.md)
- [100종목 확장 config](expansion_100_config.md)

Last updated: 2026-05-26  
기준 테스트 결과: `156 passed` (`.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check`)  
기준 config profile: `expansion_100_safe`  
주의: 실제 값은 실행 시점의 config/DB/log/KIS 계좌 상태를 다시 확인해야 한다.

이 프로젝트는 `C:\MSJ\KIS-MSJ`의 KIS API 기반 KOSPI LOT 단위 자동매매 봇이다. 평균단가가 아니라 개별 LOT 기준으로 매수, 추가매수, 매도, 재진입, 손실정리, 수동검토를 관리한다. 가장 중요한 원칙은 **주문 요청이 아니라 신규 fill insert 성공 후에만 lots/positions를 갱신한다**는 것이다.

## 절대 원칙

- 주문 요청만으로 lots/positions를 바꾸지 않는다.
- `store.record_fill(fill)`이 true인 신규 체결만 `position_manager.apply_fill()`로 간다.
- duplicate fill, `record_fill_failed`는 positions/lots에 반영하지 않는다.
- UI 서버는 KIS 주문 API를 직접 호출하지 않는다.
- 수동 주문도 UI가 직접 주문하지 않고 `manual_order_requests` 큐만 생성한다.
- Bot Core만 runtime/risk/open-order/live guard 후 기존 `order_manager` 경로로 주문 요청을 처리한다.
- DB reset은 OPEN LOT 0, 진행 중 order 0, pending manual request 0, sync mismatch 없음일 때만 가능하다.
- REVIEW_REQUIRED는 강제 해제하지 않고 recheck/acknowledge/manual sell/reconciliation 흐름으로 처리한다.

## 현재 핵심 상태

- config profile: `expansion_100_safe`
- KOSPI 후보: 100종목
- enabled/manual_only: enabled 97, disabled/manual_only 3
- `max_active_symbols=100`
- `max_total_invested_amount=20,000,000`
- `max_new_buy_per_day=10`
- `max_new_buy_amount_per_day=2,000,000`
- `max_total_open_lots=300`
- `lot_sizing_mode=cycle_locked_by_entry_price`
- `cleanup_enabled=false`
- `ui_manual_trading_enabled=false`
- `live_trading=false`
- `enable_execution_raw_log=true`
- 현재 DB에는 OPEN LOT이 남아 있으므로 DB reset 차단은 정상이다.

## LOT sizing

가격대별 1 LOT 금액을 사용하지만 한 보유 사이클에서는 최초 진입 시 결정된 sizing을 고정한다. HOLDING 중 주가가 다른 가격대로 이동해도 `lot_unit_amount`, `max_symbol_amount`, `lot_sizing_bucket`은 재계산하지 않는다.

| 가격대 | 1 LOT | 종목당 최대 | 비고 |
| --- | ---: | ---: | --- |
| 0~300원 | 1,000 | 10,000 | enabled |
| 301~1,000원 | 3,000 | 30,000 | enabled |
| 1,001~3,000원 | 10,000 | 100,000 | enabled |
| 3,001~10,000원 | 30,000 | 300,000 | enabled |
| 10,001~30,000원 | 100,000 | 1,000,000 | enabled |
| 30,001~100,000원 | 300,000 | 3,000,000 | enabled |
| 100,001~300,000원 | 1,000,000 | 10,000,000 | enabled |
| 300,001~1,000,000원 | 3,000,000 | 30,000,000 | enabled |
| 1,000,001~3,000,000원 | 10,000,000 | 100,000,000 | enabled |

3,000,000원을 초과하는 가격은 price_lot_bands 범위 밖이므로 BUY가 차단된다.

추가매수는 LOT 배수 band를 쓴다: 1~2 LOT -4%, 3~4 LOT -6%, 5~6 LOT -8%, 7~8 LOT -10%, 9~10 LOT -12%. 9 LOT에서 1 LOT 추가는 허용되어 10 LOT까지 가능하지만 10 LOT에서는 차단된다.

SELL target은 매수 당시 고정값이 아니라 현재 OPEN LOT 수 기준으로 동적 적용한다: 1~2 LOT 6%, 3~4 LOT 5%, 5~6 LOT 4%, 7~8 LOT 3%, 9~10 LOT 2%.

## 주요 상태

| 상태 | 의미 | 정책 |
| --- | --- | --- |
| NEVER_BOUGHT | 신규 후보 | initial_buy 가능, guard 필요 |
| HOLDING | OPEN LOT 보유 | add buy/PROFIT_TAKE/CLEANUP 조건부 가능 |
| WAIT_REENTRY | 전량 PROFIT_TAKE 후 재진입 대기 | initial_buy 금지, NORMAL/TRAILING_REENTRY만 |
| COOLDOWN_AFTER_CLEANUP | cleanup 전량 종료 후 대기 | BUY 금지, 자동 재진입 금지 |
| REVIEW_REQUIRED | 수동검토 필요 | BUY 금지, PROFIT_TAKE 허용, CLEANUP 차단 |
| RISK_BLOCKED | 위험 플래그 | BUY/SELL 모두 차단 |
| SYNC_REQUIRED | DB/KIS 불일치 | 신규 주문 차단, reconciliation 우선 |

## UI/API

- UI: `src/kis_msj/ui_server.py`, service: `src/kis_msj/ui_service.py`
- localhost 전용 관제 UI이며 외부 공개 금지.
- UI는 KIS 주문 API를 직접 호출하지 않는다.
- Runtime Control은 `config/runtime_control.json`을 통해 즉시 적용된다.
- Manual Order Request 탭은 preview/request 큐 생성만 한다.
- New Season 탭은 archive/liquidation plan/manual SELL request/reset guard wizard를 제공한다.
- Review 탭은 REVIEW_REQUIRED 종목의 reason, recheck, acknowledge, 수동매도 안내를 제공한다.

## 새 시즌 준비 흐름

1. 이전 시즌 백업 archive 생성.
2. KIS balance snapshot 준비.
3. liquidation plan, 즉 전량매도 예정표 생성.
4. confirm text `전량매도 요청 확인` 후 manual SELL request 생성. UI/script는 KIS 주문 API를 호출하지 않음.
5. Bot Core가 request를 처리하고 fills/reconciliation 완료.
6. OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 확인.
7. confirm text `RESET 확인` 후 DB reset.
8. expansion_100_safe/KOSPI 100 config 확인 후 새 시즌 시작.

현재 OPEN LOT이 남아 있으므로 reset 차단은 정상이다.

## 테스트 현황

최신 전체 회귀 기준:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

결과는 `156 passed`였고, pytest cache warning 1개는 기능 실패가 아니다.

## 새 thread에서 먼저 확인할 것

- 현재 OPEN LOT 수
- KIS balance snapshot 존재 여부
- liquidation plan ACTIVE/최신 여부
- pending order/manual request 여부
- SYNC_REQUIRED/REVIEW_REQUIRED/RISK_BLOCKED 종목 여부
- `live_trading=false` 유지 여부
- `enable_execution_raw_log=true` 상태와 첫 실체결 raw mapping 확인 여부
- 최신 테스트 통과 여부

## 현재 남은 핵심 리스크

1. 실제 KIS raw execution field mapping은 첫 실체결 row 기준 최종 확인이 필요하다.
2. KIS balance snapshot은 UI New Season 탭에서 읽기 전용 KIS 잔고 조회로 생성할 수 있다. UI/API는 주문 API를 호출하지 않으며, 생성된 JSON 경로를 예정표/전량매도 요청 단계에 사용한다. `scripts/prepare_new_season.py` CLI는 여전히 기존 snapshot JSON 경로를 입력받아 검증하는 구조다. 실제 전량매도 request 생성 단계에서는 최신 `generated_at`과 실제 `sellable_quantity`가 포함된 snapshot을 사용해야 한다.
3. OPEN LOT이 남아 있으면 DB reset 차단이 정상이다.
4. `live_trading=false`를 유지한 상태에서 소액/제한 검증을 먼저 해야 한다.
5. `cleanup_enabled=false`를 유지하고 로그/동기화 안정화 후 cleanup 자동화를 검토한다.

## 문서 정합성 self-check

- full/summary/thread prompt의 현재 상태 값은 `expansion_100_safe`, 100종목, enabled 97, manual_only 3 기준으로 맞춘다.
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true` 표현을 유지한다.
- manual order 설명은 “KIS 직접 주문 API 없음 / manual request 생성 API는 있음”으로 통일한다.
- KIS balance snapshot 설명은 “UI New Season 탭에서 읽기 전용 잔고 조회로 생성 가능 / CLI는 기존 JSON path 검증 구조 / 실제 request 생성 단계에서는 `generated_at`과 `sellable_quantity` 필수”로 통일한다.
- reset 차단 조건은 진행 중 orders/manual requests, OPEN LOT, SYNC_REQUIRED, lot mismatch, KIS/DB mismatch를 포함한다.

## 금지

- 실거래 주문 임의 실행 금지
- KIS 주문 API 직접 호출 금지
- OPEN LOT 남은 상태 DB reset 금지
- UI에서 lots/positions/fills 직접 수정 금지
- KIS snapshot 없이 전량매도 request 생성 금지
- pending manual request/order가 있으면 reset 금지

상세 문서는 `docs/project_handoff_full.md`를 읽으면 된다.
