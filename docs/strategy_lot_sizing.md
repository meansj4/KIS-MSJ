# 가격대별 LOT sizing

이 문서는 자동거래 봇의 `cycle_locked_by_entry_price` LOT sizing 정책을 설명합니다.

## 목적

기존에는 모든 종목이 거의 같은 `initial_buy_amount`와 누적투입금 절대금액 기준 추가매수 band를 사용했습니다. 이 방식은 1주 가격이 900원인 종목과 90,000원인 종목을 같은 돈 단위로 다루게 되어, 종목별 LOT 개수와 최대 노출이 직관적이지 않았습니다.

새 정책은 현재가 구간별로 1 LOT 금액과 종목당 최대금액을 다르게 정합니다.

## 가격대별 1 LOT 금액

`strategy.price_lot_bands`가 새 사이클 진입 가격 기준으로 적용됩니다.

- `0~300원`: 자동매수 제외
- `301~1,000원`: 1 LOT 3,000원, 종목당 최대 30,000원
- `1,001~10,000원`: 1 LOT 10,000원, 종목당 최대 100,000원
- `10,001~30,000원`: 1 LOT 30,000원, 종목당 최대 300,000원
- `30,001~100,000원`: 1 LOT 100,000원, 종목당 최대 1,000,000원
- `100,001~300,000원`: 1 LOT 300,000원, 종목당 최대 3,000,000원
- `300,001~1,000,000원`: 1 LOT 1,000,000원, 최대 3 LOT
- `1,000,001원 이상`: 자동매수 제외

`enabled=false`인 가격대에서는 자동 initial buy, reentry buy, UI manual BUY request가 차단됩니다.

## 사이클 고정 원칙

`lot_sizing_mode = cycle_locked_by_entry_price`에서는 보유 사이클 최초 진입 시점의 가격대로 LOT sizing을 고정합니다.

예:

- 최초 매수 현재가가 10,100원이면 `lot_unit_amount = 30,000원`
- 이후 주가가 9,000원으로 내려가도 같은 보유 사이클의 추가매수 금액은 계속 30,000원
- OPEN LOT이 모두 사라진 뒤 WAIT_REENTRY 또는 cleanup 이후 새로 진입하면 그때의 가격으로 새 sizing을 계산

positions에는 아래 값이 저장됩니다.

- `entry_price_for_lot_sizing`
- `lot_unit_amount`
- `max_symbol_amount`
- `max_lots_per_symbol`
- `lot_sizing_bucket`
- `lot_sizing_locked_at`
- `lot_sizing_mode`

## 기존 position fallback

기존 DB에 OPEN LOT은 있지만 lot sizing 필드가 비어 있으면 첫 OPEN LOT의 `buy_price` 기준으로 sizing을 채웁니다. 이 migration은 positions의 sizing 기준값만 채우며 lots/positions 수량은 변경하지 않습니다. decision log에는 `lot_sizing_migrated`가 남을 수 있습니다.

## 추가매수 band

새 mode에서는 `exposure_buy_bands` 대신 `add_buy_lot_bands`를 우선 사용합니다.

- 1~2 LOT: 기준가 대비 4% 하락 시 1 LOT 추가
- 3~4 LOT: 기준가 대비 6% 하락 시 1 LOT 추가
- 5~6 LOT: 기준가 대비 8% 하락 시 1 LOT 추가
- 7~8 LOT: 기준가 대비 10% 하락 시 1 LOT 추가
- 9~10 LOT: 기준가 대비 12% 하락 시 1 LOT 추가

추가매수 금액은 `lot_unit_amount * add_lot_count`입니다.

## BUY 차단 사유

- `price_out_of_lot_sizing_range`: 현재가가 어떤 price_lot_band에도 속하지 않음
- `lot_sizing_band_disabled`: 해당 가격대가 자동매수 비활성
- `lot_unit_amount_below_price`: 1 LOT 금액으로 현재가 1주도 살 수 없음
- `max_lots_per_symbol_reached`: 현재 OPEN LOT 수가 최대 LOT 수에 도달
- `max_symbol_amount_reached`: 다음 BUY를 하면 종목당 최대금액 초과
- `lot_sizing_missing`: 보유 사이클 sizing 정보가 없음
- `lot_sizing_migrated`: 기존 OPEN LOT 기준으로 sizing fallback을 적용

## UI 표시

Stocks와 manual order preview에는 아래 항목이 표시됩니다.

- LOT 기준 진입가
- 현재 사이클 1 LOT 금액
- 종목당 최대 금액
- 현재 OPEN LOT 수 / 최대 LOT 수
- LOT 가격 구간
- LOT sizing 고정 여부
- 남은 매수 가능 금액

UI는 KIS 주문 API를 직접 호출하지 않습니다. 수동 매수 요청도 `manual_order_requests` 큐에 저장되고, 실행 중인 Bot Core가 기존 guard와 order_manager 경로를 통해 처리합니다.
