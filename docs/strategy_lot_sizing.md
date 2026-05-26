# 가격대별 LOT sizing

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `156 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


이 문서는 KIS LOT 자동거래 봇의 `cycle_locked_by_entry_price` LOT sizing 정책을 설명합니다.

## 목적

모든 종목에 같은 `initial_buy_amount`를 쓰면 900원 종목과 90,000원 종목의 LOT 개념이 크게 달라집니다. 그래서 현재가 구간별로 1 LOT 금액과 종목당 최대 금액을 다르게 정하고, 한 번 보유 사이클이 시작되면 그 기준을 고정합니다.

## 가격대별 1 LOT 금액

`strategy.price_lot_bands`가 새 사이클 진입 가격 기준으로 적용됩니다.

- 0~300원: 자동매수 제외
- 301~1,000원: 1 LOT 3,000원, 종목당 최대 30,000원
- 1,001~10,000원: 1 LOT 10,000원, 종목당 최대 100,000원
- 10,001~30,000원: 1 LOT 30,000원, 종목당 최대 300,000원
- 30,001~100,000원: 1 LOT 100,000원, 종목당 최대 1,000,000원
- 100,001~300,000원: 1 LOT 300,000원, 종목당 최대 3,000,000원
- 300,001~1,000,000원: 1 LOT 1,000,000원, 최대 3 LOT
- 1,000,001원 이상: 자동매수 제외

`enabled=false` 구간에서는 자동 initial buy, reentry buy, UI manual BUY request가 차단됩니다.

## 사이클 고정 원칙

`lot_sizing_mode = cycle_locked_by_entry_price`에서는 보유 사이클 최초 진입 시점의 가격으로 LOT sizing을 고정합니다.

예:

- 최초 매수 현재가가 10,100원이면 `lot_unit_amount = 30,000`
- 이후 주가가 9,000원으로 내려가도 같은 보유 사이클의 추가매수 금액은 계속 30,000원
- OPEN LOT이 모두 사라진 뒤 WAIT_REENTRY 또는 cleanup 이후 새로 진입하면 그때의 가격으로 새 sizing 계산

positions에는 아래 값이 저장됩니다.

- `entry_price_for_lot_sizing`
- `lot_unit_amount`
- `max_symbol_amount`
- `max_lots_per_symbol`
- `lot_sizing_bucket`
- `lot_sizing_locked_at`
- `lot_sizing_mode`

## 기존 position fallback

기존 DB에 OPEN LOT은 있는데 lot sizing 필드가 비어 있으면 첫 OPEN LOT의 `buy_price` 기준으로 sizing을 채웁니다. 이 migration은 positions의 sizing 기준값만 채우며 lots/positions 수량은 바꾸지 않습니다. decision log에는 `lot_sizing_migrated`가 남을 수 있습니다.

## 추가매수 band

LOT sizing 모드에서는 기존 절대금액 기준 `exposure_buy_bands` 대신 `add_buy_lot_bands`를 우선 사용합니다.

- 1~2 LOT: 기준가 대비 4% 하락 시 1 LOT 추가
- 3~4 LOT: 기준가 대비 6% 하락 시 1 LOT 추가
- 5~6 LOT: 기준가 대비 8% 하락 시 1 LOT 추가
- 7~8 LOT: 기준가 대비 10% 하락 시 1 LOT 추가
- 9~10 LOT: 기준가 대비 12% 하락 시 1 LOT 추가

추가매수 금액은 `lot_unit_amount * add_lot_count`입니다. OPEN LOT 수는 `remaining_quantity > 0`이고 `status != CLOSED`인 LOT만 계산합니다.

## Target profit LOT band

LOT sizing 모드에서는 매도 목표수익률도 현재 OPEN LOT 수 기준으로 동적으로 재평가합니다.

`strategy.target_profit_lot_bands` 기본값:

- 1~2 LOT: 6%
- 3~4 LOT: 5%
- 5~6 LOT: 4%
- 7~8 LOT: 3%
- 9~10 LOT: 2%

LOT을 살 때 저장된 `base_target_profit_rate`는 과거 데이터 호환과 로그 참고용입니다. 실제 SELL 판단은 매도 판단 시점의 현재 OPEN LOT 수 구간에서 `current_base_target_profit_rate`를 계산해 사용합니다.

공식:

```text
current_base_target_profit_rate = target_profit_rate_for_current_open_lot_count
effective_target_profit_rate = current_base_target_profit_rate - lot_age_weeks * age_decay_rate
```

예를 들어 1~2 LOT 구간에서 산 LOT의 저장 목표가 6%였더라도, 추가매수 후 현재 5~6 LOT 구간이 되면 모든 OPEN LOT은 4%를 기본 목표로 다시 평가받습니다. 그 다음 오래된 LOT 완화값인 `age_decay_rate`가 적용됩니다.

단, `PROFIT_TAKE`와 `CLEANUP_SELL` 분류는 목표수익률이 아니라 실제 예상 손익 기준입니다.

- 예상 순손익이 0 이상이면 `PROFIT_TAKE`
- 예상 순손익이 0 미만이면 `CLEANUP_SELL` 후보
- `CLEANUP_SELL`은 cleanup 조건과 loss budget을 만족할 때만 실제 매도 후보가 됩니다.

Decision log에는 아래 값이 남습니다.

- `original_lot_base_target_profit_rate`
- `current_base_target_profit_rate`
- `target_profit_source`
- `target_profit_lot_band`
- `effective_target_profit_rate`
- `lot_age_weeks`
- `age_decay_rate`

## BUY 차단 사유

- `price_out_of_lot_sizing_range`: 현재가가 어떤 price_lot_band에도 포함되지 않음
- `lot_sizing_band_disabled`: 해당 가격대가 자동매수 비활성
- `lot_unit_amount_below_price`: 1 LOT 금액으로 현재가 1주도 살 수 없음
- `max_lots_per_symbol_reached`: 현재 OPEN LOT 수가 최대 LOT 수에 도달
- `max_symbol_amount_reached`: 다음 BUY를 하면 종목당 최대금액 초과
- `lot_sizing_missing`: 보유 사이클 sizing 정보가 없음
- `lot_sizing_migrated`: 기존 OPEN LOT 기준으로 sizing fallback 적용
- `lot_sizing_changed_after_preview`: manual BUY preview 이후 실제 처리 직전 가격 구간이 바뀌어 재확인이 필요

## UI 표시

Stocks, Position detail, manual order preview에는 아래 항목이 표시됩니다.

- LOT 기준 진입가
- 현재 사이클 1 LOT 금액
- 종목당 최대 금액
- 현재 OPEN LOT 수 / 최대 LOT 수
- LOT 가격 구간
- LOT sizing 고정 여부와 고정 시각
- 남은 매수 가능 금액

UI는 KIS 주문 API를 직접 호출하지 않습니다. 수동 매수 요청은 `manual_order_requests`에 저장되고, 실행 중인 Bot Core가 기존 guard와 order_manager 경로를 통해 처리합니다.
