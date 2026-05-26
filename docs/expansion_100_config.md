# KOSPI 100 확장 운용 config

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `153 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


기본 프로파일은 `expansion_100_safe`입니다. 후보 종목은 100개로 관리하지만, 첫날부터 모두 매수되지 않도록 신규 진입 수량과 금액을 별도로 제한합니다.

## 적용 프로파일

| profile | max_total_invested_amount | max_new_buy_per_day | max_new_buy_amount_per_day | max_total_open_lots |
|---|---:|---:|---:|---:|
| expansion_100_safe | 20,000,000 | 10 | 2,000,000 | 300 |
| expansion_100_medium | 30,000,000 | 15 | 3,000,000 | 450 |
| expansion_100_aggressive | 50,000,000 | 20 | 5,000,000 | 700 |

`max_new_buy_amount_per_day`와 `max_total_initial_buy_amount_per_day`는 하루 initial buy 주문 금액 합계를 제한합니다. 주문 난사 방지를 위해 체결 기준이 아니라 주문 요청 기준으로 계산합니다.

## 가격대별 LOT sizing

| 현재가 구간 | 1 LOT 금액 | 종목당 최대금액 | 자동매수 |
|---|---:|---:|---|
| 0~300 | 0 | 0 | 비활성 |
| 301~1,000 | 3,000 | 30,000 | 활성 |
| 1,001~10,000 | 10,000 | 100,000 | 활성 |
| 10,001~30,000 | 30,000 | 300,000 | 활성 |
| 30,001~100,000 | 100,000 | 1,000,000 | 활성 |
| 100,001~300,000 | 300,000 | 3,000,000 | 활성 |
| 300,001~1,000,000 | 1,000,000 | 3,000,000 | 활성, 최대 3 LOT |
| 1,000,001~3,000,000 | 0 | 0 | 비활성 |

LOT sizing은 `cycle_locked_by_entry_price` 방식입니다. 최초 진입 시 현재가 기준으로 1 LOT 금액과 최대금액을 정하고, 같은 보유 사이클 동안에는 주가가 다른 가격 구간으로 이동해도 다시 계산하지 않습니다.

## 추가매수 LOT band

| 현재 OPEN LOT 수 | 추가매수 하락률 | 추가 LOT |
|---|---:|---:|
| 1~2 | 4% | 1 |
| 3~4 | 6% | 1 |
| 5~6 | 8% | 1 |
| 7~8 | 10% | 1 |
| 9~10 | 12% | 1 |

`max_lots_per_symbol_default=10`이 기본입니다. 가격대 band에 `max_lots`가 있으면 그 값을 우선합니다.

## 목표수익률 LOT band

목표수익률은 매수 당시 고정값이 아니라 현재 OPEN LOT 수 기준으로 동적으로 재평가됩니다.

| 현재 OPEN LOT 수 | 목표수익률 |
|---|---:|
| 1~2 | 6% |
| 3~4 | 5% |
| 5~6 | 4% |
| 7~8 | 3% |
| 9~10 | 2% |

이후 LOT age decay가 적용됩니다. PROFIT_TAKE와 CLEANUP_SELL 분류는 target이 아니라 실제 손익 기준입니다.

## 초기 확장 운용 권장값

- `cleanup_enabled=false`: 신규 확장 초반에는 체결/동기화/log 안정화가 우선입니다.
- `ui_manual_trading_enabled=false`: 수동 주문 요청은 필요 시 명시적으로 켭니다.
- `enable_execution_raw_log=true`: 첫 실체결 field mapping 확인 후 false로 되돌립니다.
- `live_trading=false`: 새 config 적용 직후에는 paper/mock 테스트를 먼저 수행합니다.

## 후보 종목 검증 메모

스크립트는 코드 형식, 중복, 기본 위험 플래그를 검증합니다. 실시간 KRX 거래정지/관리종목 여부는 첫 live 운용 전 별도 확인이 필요합니다.

현재 후보 중 아래 종목은 자동매수를 비활성화합니다.

- `005935 삼성전자우`: KIS KOSPI master 검증에서 미확인되어 `enabled=false`, `manual_only=true`, `liquidity_warning=true`
- `001230 동국홀딩스`: KIS KOSPI master 기준 `trading_halt_yn=Y`라서 `enabled=false`, `manual_only=true`, `trading_halted=true`
- `020560 아시아나항공`: 대한항공 통합 관련 이벤트 리스크가 있어 `enabled=false`, `manual_only=true`, `administrative_issue=true`
