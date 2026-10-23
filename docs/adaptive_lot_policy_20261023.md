> **현행 정책 안내 (Adaptive V2)**: 현재 소스의 통합 명세는 [CURRENT_TRADING_POLICY](CURRENT_TRADING_POLICY.md)입니다. 아래 본문은 작성 당시 정책·운영 기록이며, 최저 LOT 앵커·연속 횟수·달력 7일 Decay 설명은 현행 Adaptive 판단에 적용되지 않습니다. 운영 프로세스 적용 여부는 통합 문서의 검증·적용 상태를 확인하세요.

# LOT 매매 정책 변경 및 검증 (2026-10-23)

요청한 최저 LOT 앵커, 변동성·체결 추세 보정, 현재 LOT 수 기준 소급 Decay, 보수적 재진입을 실제 코드와 기본 운용 설정에 반영했다. 실제 증권사 주문이나 운영 DB 변경 없이 임시 DB와 가짜 브로커로 검증했다. 기존 전체 일시정지 설정은 유지했다.

## 매매 조건

- 기본 매수/매도/매도 후 재매수 하락폭은 각각 4% / 3% / 4%다.
- 수정종가 61개에서 로그수익률의 표본 표준편차(ddof=1)를 계산한다. 연환산 20일·60일 변동성을 0.3:0.7로 합친다.
- `m = clamp(4 × (V / 18.41)^0.3707491793, 3, 10) / 4`. 이 제한은 변동성 단계에만 적용한다. 61개 미만 또는 유효하지 않은 데이터는 배율 1이다.
- `t(n) = 1 + 0.2 × max(0, min(n, 12) - 1)`. 실제 체결이 있는 주문별로 한 번 계산하며 종목별로 분리한다. 같은 주문의 부분 체결은 횟수를 중복 증가시키지 않는다.
- 추가매수 상한은 `최저 보유 LOT 매수가 × (1 - 4 × m × t(연속매수)/100)`이다. 최근/평균 매수가는 앵커로 사용하지 않는다.
- 매도 후 첫 실제 매수 체결 전까지 `가장 최근 실제 매도가 × (1 - 4 × m/100)`도 충족해야 한다. 두 상한이 있으면 낮은 값을 사용한다. 전량 매도 후에는 기존 최초 진입의 가격별 LOT 금액·허용구간도 함께 확인한다. 매도 후 제한에는 추세 배율·시간 경과 완화·강제 재진입을 적용하지 않는다.
- 각 LOT의 최종 목표수익률(%)은 `3 × m × t(연속매도) - 현재 보유 LOT 수 × 0.1 × 실제 보유일수 / 7`이다. Decay는 퍼센트포인트로 마지막에 차감하며 누적값을 저장하지 않는다. LOT 수 감소로 목표가 다시 상승할 수 있고, 목표수익률이 음수가 되는 것도 허용한다.
- 각 LOT의 개별 매수가에 최종 목표수익률을 적용한다. 목표가격이 0 이하이거나 유효하지 않으면 주문하지 않는다. 매수 상한은 호가 단위로 내림, 매도 목표는 올림 처리한다. 이는 기존 프로젝트 호가 단위를 사용한다. 목표에 도달한 일반·Decay 매도의 실제 지정가는 확인된 현재가를 사용해, 오래된 낮은 목표가격이 당일 가격제한폭을 벗어나지 않도록 한다. 매도 목표는 판단 기준이며 실제 지정가는 그 이상이다.

변동성은 KRX의 완료된 일봉을 기준으로 저장한다. 장중에는 같은 값을 유지하고 장 마감 후 다음 스냅샷으로 갱신한다. 최초 실행 때는 가장 최근 완료 일봉을 조회한다. 주말·휴일에는 API가 반환한 실제 마지막 거래일의 데이터가 사용된다. 조회 실패의 기본값도 해당 완료일 기준으로 고정해 장중 조건이 반복 변경되지 않도록 한다.

## 재진입 예수금

최근 매도 후 제한이 활성화된 매수만 강화된 예비자금 검사를 받는다. 일반 추가매수는 기존 주문 가능 현금·투자 한도 검사를 유지한다.

재진입 주문 비용을 지불한 뒤, 재진입 종목을 포함한 보유 종목마다 추가 2 LOT 예산과 총 평가자산의 10%가 남아야 한다. 종목별 최대 LOT 수와 최대 투자금액을 초과하는 가상 슬롯은 제외한다. 현금·총 평가자산이 없거나 유효하지 않거나, 보유수량을 LOT으로 확인할 수 없으면 재진입을 보류한다. 계획 시에는 LOT 예산 전액으로 보수적으로 검사하고, 제출 직전에는 실제 지정가 주문 금액도 다시 검사한다.

KIS 주문가능조회 `nrcvb_buy_amt`(없으면 `ord_psbl_cash`)를 사용한다. 이미 브로커 주문가능금액에 반영된 예약금을 다시 차감하지 않는다. 재진입은 다른 종목을 포함해 미해결 매수 주문·접수 결과 불명확 예약이 남아 있으면 대기하며, 주문 예약 트랜잭션에서도 다시 확인한다.

근거: [한국투자증권 주문가능조회 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py)는 현금 주문 금액에 `nrcvb_buy_amt`를 안내한다. [공식 실시간잔고 도움말](https://securities.koreainvestment.com/pro_help/2100.html)은 당일 매수 정산을 주문 기준으로 반영한다고 설명한다. API의 주문가능 현금을 미체결 매수 예약 차감 후 금액으로 취급하는 것은 두 설명에 근거한 해석이다. 실제 계좌 조회를 통한 대조는 수행하지 않았다.

수정종가 조회는 [한국투자증권 기간별 시세 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py)의 `FHKST03010100`, 일봉 `D`, 수정주가 `FID_ORG_ADJ_PRC=0`을 사용한다. 필요한 데이터가 한 번에 오지 않으면 이전 날짜로 이어서 조회한다.

## 수정 파일과 주요 함수

| 파일 | 주요 함수 / 변경 |
|---|---|
| `src/kis_msj/adaptive_policy.py` | `trend_multiplier`, `lot_decay_pp`, `final_sell_profit_pct`, `lowest_lot_buy_target`, `reentry_buy_target`, `floor_order_price`: 계산 단위 분리 |
| `src/kis_msj/volatility.py` | `calculate_volatility`, `volatility_multiplier`, `DailyVolatilityCache`: 수정종가 계산과 영속 캐시 |
| `src/kis_msj/kis_client.py` | `adjusted_daily_closes`: 완료된 수정종가 조회, 모의 클라이언트는 오프라인 기본값 |
| `src/kis_msj/strategy.py` | `adaptive_buy_details`, `adaptive_reentry_cash_details`, `_adaptive_decide`, `_adaptive_sell_candidate`, `_adaptive_context`: 새 정책 판단·설명 |
| `src/kis_msj/lot_manager.py` | `current_target_profit_info`, `age_decay_rate_for`, `update_lot_target_metadata`, `recalculate_symbol_targets`, `create_buy_lot`: 현재 LOT 수 소급 계산, 부분 매수 병합, 최초 체결시각 보존 |
| `src/kis_msj/fill_state.py` | `record_actual_fill_state`, `fill_order_key`, `actual_fill_key`: 실제 체결 순서·주문별 횟수·최근 매도가·매수 제한 복원 |
| `src/kis_msj/models.py`, `position_manager.py`, `storage.py` | 종목별 연속 횟수·제한 상태 저장, 기존 이력 복원, 같은 시각의 서로 다른 체결 보존, 원자적 주문 예약 |
| `src/kis_msj/order_manager.py` | `_adaptive_submission_guard`, `build_request`: 최신 현금·저장된 앵커 재확인, 주문 예산·수량·가격·상충 주문 검증, 접수 결과 불명확 시 예약 유지 |
| `src/kis_msj/main.py` | `refresh_daily_volatility`, `_evaluate_strategy`, `_apply_fill_with_session_audit`: 장 마감 갱신, 전체 보유종목 예비자금, 최종 재판단, 병합 부분 체결 기록 |
| `src/kis_msj/cash_rotation.py` | 새 정책에서 현금 부족 매도와 기존 재매수 앵커 경로 차단 |
| `src/kis_msj/ui_service.py`, `ui_server.py` | 저장된 변동성·횟수로 LOT 목표·매수 상한·재진입 예비자금 표시, 현금 미확인 시에도 매도 목표 계산 |
| `src/kis_msj/config.py`, `config/lot_auto_trader.json` | `adaptive_lot_enabled`, `reentry_reserve_lots_per_symbol=2`, `reentry_cash_buffer_ratio=0.10` |

## 기존 정책과의 관계

기존의 예수금 비례 목표수익률, 예수금 비례 평균단가 매수, LOT 구간별 매수 간격·기본 목표수익률, 청산 사이클 고가/VWAP 재진입, 시간 완화·강제 재진입은 새 공식과 충돌하므로 새 정책 활성화 중 사용하지 않는다. 기존 설정값은 호환성을 위해 읽을 수 있게 남겼다. 운용 설정에서는 예수금 비례 매도와 현금 부족 교체매도를 명시적으로 비활성화했다.

수익 목표 충족 LOT의 기존 선택 순서와 별도 심층 손실 회복 조건은 유지했다. 기존 이익실현→손실 회복→음수 Decay 매도 순서, 고노출 부분 매도 비율, 최대 LOT·투자 한도, 거래중지·검토·동기화·가격 안정성·주문 간격·종목 제외 조건도 유지한다. 현금 부족을 이유로 추가 강제매도·교체매도·우선순위 변경을 넣지 않았다.

같은 매수 주문의 부분 체결은 하나의 LOT으로 병합하고 실제 체결 금액의 가중 매수가와 최초 체결시각을 보존한다. 일부 매도 후 수량이 남으면 보유 LOT 수에 포함한다. 서로 다른 체결 ID의 동일 시각·동일 수량·동일 가격 체결을 누락하지 않도록 `fills` 고유 제약을 보완했다. 기존 DB는 다음 초기화 시 온라인 백업 후 열·제약을 마이그레이션하며 기존 ID·추가 열·인덱스·트리거·체결 귀속을 보존한다.

기존 코드에는 체결 저널 기록과 LOT 반영 사이의 프로세스 중단을 하나의 트랜잭션으로 묶지 않는 구간이 있다. 이번 이력 복원에서는 ‘기록된 체결’과 ‘수량에 반영한 체결’을 구분하여 기존 누락 체결 복구를 막지 않도록 했다. 잔고 불일치 시에는 기존 동기화 차단·복구 경로를 유지한다.

KIS가 누적 수량·평균가로 응답하는 부분 체결은 누적 체결금액(`tot_ccld_amt`)에서 이전 기록 금액을 빼서 새 체결분의 금액과 가격을 계산한다. 평균가가 반올림되어도 원가가 누적 왜곡되지 않도록 정확한 체결금액과 소수 평균단가를 보존한다. 한 번의 조회 사이에 여러 체결이 있었으면 새 체결분의 수량 가중 평균가를 사용한다. 개별 체결 식별자·시각이 없는 주문 집계 응답만으로 마지막 하위 체결 하나의 가격·시각을 재구성할 수는 없다. 실제 체결시각 필드가 제공되면 사용하고, 없으면 기존 주문시각 대체 경로와 시각 출처를 유지한다.

## 검증

실제 주문 없는 테스트에서 아래 항목을 검증했다.

1. 두 변동성 예시의 4/3/4%, 약 8/6/8%, 표본 표준편차·61개 데이터 부족 기본값.
2. 연속 매수·매도 2~12회, 최종 매수 간격의 10% 초과 허용, 재매수 하락폭에는 추세 미적용.
3. 1·12 LOT의 7일 Decay, 다른 매수시각, 소수 일수·시간대·주말 포함 실제 경과시간.
4. LOT 증가·감소 시 전체 기간 소급 및 목표가 재상승, 음수 목표, 배율 뒤 Decay 차감, 재시작 후 중복 누적 없음.
5. 최저 보유 LOT 앵커와 실제 최근 매도가 상한의 동시 적용·영속 복원, 첫 재매수 체결 후 해제.
6. 예비자금 경계와 최대 LOT 제외, 미확인 현금·자산·보유 LOT 차단, 주문 직전 최신 현금 확인, 예약금 이중 차감 방지.
7. 부분 체결·동일 시각 복수 체결·주문/체결 ID 날짜별 재사용·누락 체결 복구·세션별 체결 귀속.
8. 같은 종목 양방향 주문 충돌, 불명확 접수 결과의 재시작 후 재전송 차단, 취소 거절 예약 유지, 무효 가격·예산 초과 차단.
9. 장 마감 이후 갱신·장중 고정·재시작 캐시·모의 클라이언트 오프라인 동작, 화면 계산 일치.

추가 테스트는 `tests/test_adaptive_strategy.py`, `test_adaptive_volatility.py`, `test_adaptive_fill_state.py`, `test_adaptive_order_guards.py`, `test_adaptive_ui.py`, `test_aggregate_fill_amount.py`에 있다. 기존 테스트의 실제 운영 일시정지 의존은 임시 런타임 설정으로 격리했고, 이전 버전 소스 바이트 고정 검사는 의도적으로 변경한 부분을 제외하고 위험 계산·운용 설정 보존 검사를 유지했다.

최종 전체 실행 결과: **1,156개 통과, 실패 0개 (133.42초)**. 실행 명령은 `.venv/Scripts/python.exe -m pytest -q --tb=short --maxfail=1 --junitxml=reports/adaptive_lot_20261023_pytest.xml`이다. `git diff --check`도 통과했다. 운영 DB는 읽기 전용으로 `fills` 스키마만 확인하여 마이그레이션 형식의 호환성을 검증했다. 운영 DB의 마이그레이션·수량 변경은 실행하지 않았다. 설정 원본과의 비교에서는 요청한 8개 항목 이외의 종목 목록·한도·주문 설정이 보존됐고, 기존 일시정지도 유지된 것을 확인했다.

최종 전체 결과는 `reports/adaptive_lot_20261023_pytest.xml`에 기록했다. 설정 적용 전 원본은 `tmp/adaptive_lot_policy_before_20261023.json`이다. 실행 중인 프로세스의 Python 모듈은 파일 편집만으로 갱신되지 않으므로, 새 코드는 다음 봇 프로세스 재시작 시 사용된다. 현재 전체 일시정지는 해제하지 않았다.
