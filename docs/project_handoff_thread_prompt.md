# 새 ChatGPT / Codex thread 시작용 프롬프트

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `156 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.

> 2026-05-29 loop optimization: automatic scan now uses one quote per enabled symbol, runs 5-sample stability checks only for BUY/SELL candidates, and fetches one final quote before order request construction. Operational changes to accept: non-candidates skip stability sampling, candidates may be blocked more conservatively, and final quote can change quantity/limit/new unlocked LOT bucket. Rollback range: `0e8405d..42c37fe`. Final quote is traceable via `decisions.current_price`, linked `price_snapshots.current_price`, and submitted `orders`; `raw_json` currently stores `sample_count` only, not individual samples or a nested `final_quote`.


Last updated: 2026-05-26  
기준 테스트 결과: `156 passed`  
기준 config profile: `expansion_100_safe`  
관련 문서: [전체 인수인계](project_handoff_full.md), [요약본](project_handoff_summary.md), [로컬 UI](local_ui.md), [LOT sizing](strategy_lot_sizing.md), [새 시즌 reset](new_season_reset.md), [100종목 config](expansion_100_config.md)

아래 내용을 기준으로 `C:\MSJ\KIS-MSJ`의 KIS LOT 자동거래 봇 개발/운영 논의를 이어서 봐줘.

## 프로젝트 요약

이 프로젝트는 KIS API 기반 KOSPI LOT 단위 자동매매 봇이다. 종목 평균단가가 아니라 개별 LOT 기준으로 매수, 추가매수, 매도, 재진입, 손실정리, 수동검토를 관리한다.

핵심 원칙은 **주문 요청이 아니라 실제 fill insert 성공 후에만 lots/positions를 갱신한다**는 것이다.

## 절대 깨면 안 되는 원칙

- 주문 요청만으로 lots/positions를 바꾸지 않는다.
- `store.record_fill(fill)`이 true인 신규 체결만 `position_manager.apply_fill()`로 간다.
- duplicate fill 또는 `record_fill_failed`는 positions/lots에 반영하지 않는다.
- UI 서버는 KIS 주문 API를 직접 호출하지 않는다.
- 수동 주문도 UI가 직접 주문하지 않고 `manual_order_requests` 큐만 생성한다.
- Bot Core만 runtime/risk/open-order/live guard 후 기존 `order_manager` 경로로 주문 요청을 처리한다.
- DB reset은 OPEN LOT 0, 진행 중 order 0, pending manual request 0, sync mismatch 없음일 때만 가능하다.
- REVIEW_REQUIRED는 강제 해제하지 않고 recheck/acknowledge/manual sell/reconciliation 흐름으로 처리한다.

## 현재 상태

- config profile: `expansion_100_safe`
- KOSPI 후보 100종목, enabled 97, disabled/manual_only 3
- `live_trading=false`
- `cleanup_enabled=false`
- `ui_manual_trading_enabled=false`
- `enable_execution_raw_log=true`
- 최신 테스트: `156 passed`
- 현재 DB에는 OPEN LOT이 남아 있는 상태로 이해해야 하며, 이 경우 DB reset 차단은 정상이다.

## 핵심 전략

- `lot_sizing_mode=cycle_locked_by_entry_price`
- 가격대별 1 LOT 금액을 사용하지만, 한 보유 사이클에서는 최초 진입 시 결정된 `lot_unit_amount`, `max_symbol_amount`, `lot_sizing_bucket`을 고정한다.
- 추가매수는 LOT 배수 band 기준이다: 1~2 LOT -4%, 3~4 LOT -6%, 5~6 LOT -8%, 7~8 LOT -10%, 9~10 LOT -12%.
- SELL target은 매수 당시 고정값이 아니라 현재 OPEN LOT 수 기준으로 동적 적용한다: 1~2 LOT 6%, 3~4 LOT 5%, 5~6 LOT 4%, 7~8 LOT 3%, 9~10 LOT 2%.
- PROFIT_TAKE는 실제 손익 0 이상, CLEANUP_SELL은 실제 손익 음수이고 cleanup 조건을 만족해야 한다.
- `cleanup_enabled=false`가 현재 기본이다.

## UI / API

- UI: `src/kis_msj/ui_server.py`
- Service: `src/kis_msj/ui_service.py`
- Runtime control: `config/runtime_control.json`
- UI는 localhost 관제/설정/수동 요청/새 시즌 준비용이며 KIS 주문 API를 직접 호출하지 않는다.
- 수동 주문은 `/api/manual-orders/preview`와 `/api/manual-orders`로 preview/request만 만들고, 실제 주문은 Bot Core가 `manual_order_requests`를 소비한다.

## 새 시즌 다음 단계

1. UI New Season 탭에서 현재 OPEN LOT, pending order, pending manual request, SYNC_REQUIRED 여부 확인.
2. KIS balance snapshot 준비.
3. liquidation plan, 즉 전량매도 예정표 생성.
4. confirm text `전량매도 요청 확인`으로 manual SELL request 생성 여부 결정.
5. Bot Core가 request를 처리하고 reconciliation 완료.
6. OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 확인.
7. confirm text `RESET 확인` 후 DB reset.
8. 새 시즌 준비 완료 확인 후 제한 운용 시작.

## 현재 남은 핵심 리스크

1. 실제 KIS raw execution field mapping은 첫 실체결 row 기준 최종 확인이 필요하다.
2. KIS balance snapshot은 UI New Season 탭에서 읽기 전용 KIS 잔고 조회로 생성할 수 있다. UI/API는 주문 API를 호출하지 않으며, 생성된 JSON 경로를 예정표/전량매도 요청 단계에 사용한다. `scripts/prepare_new_season.py` CLI는 여전히 기존 snapshot JSON 경로를 입력받아 검증한다. 실제 전량매도 request 생성 단계에서는 최신 `generated_at`과 실제 `sellable_quantity`가 포함된 snapshot을 사용해야 한다.
3. OPEN LOT이 남아 있으면 DB reset 차단이 정상이다.
4. `live_trading=false` 유지 상태에서 소액/제한 검증을 먼저 해야 한다.
5. `cleanup_enabled=false` 유지 후 로그 안정화 뒤 cleanup 자동화를 검토한다.

## 절대 하지 말 것

- 실거래 주문 임의 실행 금지.
- KIS 주문 API 직접 호출 금지.
- DB reset 임의 실행 금지.
- OPEN LOT 남은 상태에서 DB reset 금지.
- UI에서 lots/positions/fills 직접 수정 금지.
- KIS balance snapshot 없이 전량매도 request 생성 금지.
- pending manual request/order가 있으면 reset 금지.

## 먼저 확인할 질문

1. 현재 OPEN LOT 수는 몇 개인가?
2. KIS balance snapshot이 준비되어 있는가?
3. liquidation plan은 ACTIVE이고 최신인가?
4. pending order/manual request가 있는가?
5. SYNC_REQUIRED/REVIEW_REQUIRED/RISK_BLOCKED 종목이 있는가?
6. `live_trading=false`인가?
7. `enable_execution_raw_log=true`이고 첫 실체결 raw mapping 확인이 필요한가?
8. 최신 전체 테스트가 통과했는가?
9. 지금 원하는 작업은 UI 개선, 전량매도 준비, 새 시즌 reset, config 튜닝, 로그 검증 중 무엇인가?

## 문서 정합성 self-check

- full/summary/thread prompt의 현재 상태 값이 서로 일치해야 한다.
- 100종목 후보 수, enabled 97, manual_only 3, `risk.profile=expansion_100_safe`가 일치해야 한다.
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`가 일치해야 한다.
- manual order 설명은 “KIS 직접 주문 API 없음 / manual request 생성 API는 있음”으로 읽어야 한다.
- reset 차단 조건과 KIS snapshot 설명은 `project_handoff_full.md`를 우선 기준으로 삼는다.

상세 맥락은 `docs/project_handoff_full.md`를 기준으로 삼아줘.
