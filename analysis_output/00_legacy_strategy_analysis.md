# 작업 시작 시점의 기존 전략 분석

작성 기준: 2026-10-22 한국시간. GitHub/HEAD만으로 기존 전략을 정의하지 않고 현재 로컬 소스와 `config/lot_auto_trader.json`을 함께 조사했다. 이 감사는 파일 읽기와 오프라인 설정 해석만 수행했으며 실계좌 API, 주문, 자동매매 프로세스 실행, 운영 DB 변경은 수행하지 않았다. 이 문서는 ARCG 구현 전 기준선의 의미와 재현 제약을 고정한다.

## 1. 기준선과 미커밋 변경

조사 시 기존 브랜치는 `NewMethod`, HEAD는 `490ef3e35c2026acf2ea2f4a7a207be55577ea1c` (`UI 메모리 누적과 화면 갱신 멈춤 수정`)이다. 작업 시작 시 이미 다음 tracked 파일에 수정이 있었다.

- `config/runtime_control.json`
- `src/kis_msj/config.py`, `main.py`, `models.py`, `order_manager.py`, `position_manager.py`, `storage.py`, `strategy.py`, `ui_server.py`, `ui_service.py`

그 외 `cash_rotation.py`, `market_session.py`, `order_identity.py`, 세션 감사/표시 모듈, 여러 오프라인 연구 스크립트/테스트와 reports/data 파일이 untracked 상태였다. 이들 역시 현재 시스템의 일부이므로 HEAD의 코드만 복원하면 다른 전략이 된다. 조사 시 기존 tracked diff는 10개 파일, 약 497줄 추가/66줄 삭제였다. 이 수치는 사용자 기존 작업이며 이번 신규 구현 diff와 구분해야 한다. `.agents` 디렉터리에서는 추가 지침 파일을 찾지 못했다.

기존 미커밋 변경에는 저현금 시 최고원가 LOT 대신 가장 오래된 LOT를 즉시 파는 선택지, 현금연동 추가매수 할인율, 전량 매도 직전 잔존 LOT VWAP 저장, 주문 직전 현금/가격 재검증 및 관련 UI/저장소 확장이 포함된다. 그러나 기능이 코드에 있다는 사실과 현재 설정으로 활성화됐다는 사실은 다르다. 현재 운영 JSON에는 `cash_buy_drop_enabled`와 `cash_target_low_cash_lot_policy`가 없으므로 실제 유효값은 각각 `false`, `highest_cost`이다.

운영 JSON은 `order.live_trading=true`, `extended_hours.enabled=true`, `mode=live`다. 이 파일로 프로그램 진입점을 실행하면 실거래 경로를 사용할 수 있다. 제공하는 `legacy_settings.sanitized.json`은 전략/위험/종목 설정을 유지하되 실행 관련 항목만 연구용으로 비활성화하고 계좌 항목을 제거한 별도 파일이다. 운영 JSON과 런타임 제어 파일은 수정하지 않는다.

## 2. 현재 유효 설정 요약

| 항목 | 실제 유효값 / 의미 |
|---|---|
| 종목 목록 | 299개, enabled 298개, retire_after_exit 73개. enabled라도 buy_blocked/manual_only/위험 상태 등에 따라 신규매수가 제한될 수 있음 |
| LOT 단위 | 최초 체결가 구간에 고정. 예: 10,001~30,000원 종목 75,000원/LOT, 종목 최대 900,000원; 30,001~100,000원 종목 225,000원/LOT, 최대 2,700,000원 |
| LOT 개수 | 기본 최대 12개. 가격구간별 별도 max_lots가 없으면 이 기본값 사용 |
| 추가매수 | open LOT 1~12개 모든 구간에서 기준가격 대비 8.8% 하락, 1 LOT씩 |
| 현금연동 매수 | OFF. 소스 기본 할인율 최대 40% 기능은 현재 A의 활성 규칙이 아님 |
| 현금연동 기본 매도목표 | 2026-10-19부터 현금 / 200,000,000 × 0.0072. 현금 2억원에서 0.72%, 0원에서 0% |
| 목표 decay | 정책 활성 후 `LOT 나이(주) × 현재 종목 open LOT 수 × 0.0005` 차감. 그 전은 고정 0.005/주 |
| 저현금 기준 | 주문가능현금 12,000,000원 이하. 현재 기본 정책은 highest_cost |
| 일반 cleanup | `cleanup_enabled=false`; 자동 decay 정리와 deep loss 정리는 별도 경로로 계속 작동 |
| deep loss | 종가 기준 -40% 진입, 최초 반등률 15%, 60일 동안 0%까지 감소, 60일 경과 시 정리 조건 충족 |
| 재진입 | cash_capacity_oldest, 2026-10-19부터 새 LOT 단위의 60배 현금 요구 |
| 익절 부분매도 | 종목 잔존 원가가 600,000원 초과일 때 해당 LOT 수량의 50%, 최소 1주 |
| 추정 비용 | 매도금액의 0.21% 일괄 추정. 매수 수수료와 실제 종목별 세율을 정밀 계산하는 모델은 아님 |
| 계좌 제한 | 현금 최소 30,000원, 총 잔존 원가 200,000,000원, 총 LOT 3,000개, active symbol 300개 |
| 신규매수 일일 제한 | 최초 신규 300회, 금액 30,000,000원. 이 제한을 모든 추가매수/재진입의 일일 한도로 해석하면 안 됨 |
| 계좌 위험 차단 | 일손실 -1.5%, 총손실 -20%, 검토필요 종목 60개, 연속 API 오류 5회, 데이터 불일치 |
| 주문 | 지정가 매수 기본 +0.3%, 매도 기본 -0.5%, 가격 샘플 5회/0.5초, 변동성 한도 1%, 주문 대기 30초 |
| 거래 빈도 | 매수체결 후 동일 종목 매수 600초, 주문요청 간격 10초, 루프 15초 |

비율 값은 소스에서 소수 비율/퍼센트 단위가 혼재한다. `cash_target_reference_rate=0.0072`는 0.72%, `estimated_fee_tax_pct=0.21`은 0.21%이다. 새로운 백테스트에서 이 단위를 혼동하면 100배 오류가 발생한다.

## 3. 최초 매수와 추가매수

`LotGridStrategy.decide` (`strategy.py:202`)는 먼저 `SYNC_REQUIRED`, `RISK_BLOCKED`를 차단한다. 이후 매도 신호를 먼저 찾고, 매도가 없을 때만 계좌/종목 위험과 매수 조건을 평가한다. 따라서 한 번의 판단에서 매수와 매도를 함께 반환하지 않는다.

`NEVER_BOUGHT`이면 별도 추세·시장·가격 진입 신호 없이 LOT 가격구간이 유효하고 1주를 살 수 있는 한 `initial_buy`를 반환한다 (`strategy.py:238`, `1214`). 종목 목록 등재가 사실상 최초 진입의 충분조건이 될 수 있다. 실제 실행에서는 종목 buy_blocked, runtime pause, 계좌 한도 등 후속 가드가 추가된다.

추가매수는 현재 남은 LOT들의 수량가중 VWAP와 LOT별 매수가의 단순 중앙값을 사용한다. 종목 미실현수익률이 +1% 이상인 PLUS 모드에서는 둘 중 큰 값, MINUS/NEUTRAL에서는 작은 값이다 (`strategy.py:841`, `859`, `1333`). 요청문에 언급된 단순평균과 정확히 같지 않다. 현재 설정은 이 기준에서 8.8% 하락할 때 고정 1 LOT 단위 금액을 매수한다 (`strategy.py:307`). LOT 매도 시 남은 LOT 집합이 바뀌면 기준값도 바뀐다. 사이클 내 체결 최저가를 보존하는 별도 상태는 기존에 없다.

최초 체결가에서 LOT 단위와 종목 최대 원가를 잠그며 이후 가격구간 이동에도 유지한다 (`position_manager.py:330`). 추가매수의 `잔존 원가 + 예정 LOT 금액 <= 종목 한도` 및 LOT 개수를 검사한다 (`strategy.py:1279`). 이미 매도한 원가는 한도 사용액에서 빠진다. 총생애 매수 누적금액을 종목 최대예산으로 관리하는 구조는 아니다.

## 4. LOT 목표수익과 매도 대상

현금정책 활성 시 모든 LOT가 최신 주문가능현금을 기반으로 같은 기본 목표수익률을 공유한다 (`lot_manager.py:68`). 정책 비활성 시 현재 open LOT 개수 구간에 따라 3%, 2.5%, 2%, 1.5%, 1%, 0.5%가 적용된다. 초기 LOT 생성 시 저장한 목표율을 계속 고정 사용하는 방식이 아니다.

일반 익절 후보는 목표 달성과 추정 비용 차감 후 순익 여부를 확인한다. 후보 선택은 오래된 매수체결 LOT 우선이며 동률은 수익률, 원가, 수량, LOT ID로 결정한다 (`strategy.py:616`). 일반 익절에서만 노출 60만원 초과일 때 50% 부분매도가 적용되고 손실 정리 계열은 해당 LOT 전량매도다 (`strategy.py:369`).

목표매도가격은 현금정책 활성 시 `매수가 × (1 + 유효목표율)`을 호가단위로 올림한다 (`strategy.py:414`). 가격과 수량을 실제 체결 기준으로 기록하며 LOT를 지정해 매도 체결을 반영한다 (`lot_manager.py:188`). 주문가격이 양수이면 그 지정가가 실행계층으로 전달된다. deep loss는 limit 0을 반환하여 최신 호가 기준 기본 매도 markdown을 실행계층에서 적용한다.

## 5. 보유기간 decay와 강제 정리

`유효목표 = 현재 기본 목표 - 경과주수 × 현재 open LOT 수 × 0.0005`이다 (`lot_manager.py:223`). 매수 때 고정한 LOT 개수가 아니라 현재 남은 개수를 전체 보유기간에 곱한다. LOT 하나가 팔리면 다음 평가에서 다른 LOT의 과거 전체 기간 decay도 재계산되므로 불연속적으로 목표가 변한다. 하한이 없어 오래 보유하면 목표율이 계속 음수로 내려간다.

`cleanup_enabled=false`라도 유효목표가 음수이고 현재 손실률이 그 목표 이상이면 `AUTO_DECAY_CLEANUP_SELL` 후보가 될 수 있다 (`strategy.py:447`). 현금정책과 LOT수 decay 모드가 활성일 때 이 손실 매도 후보는 최고원가 LOT로 치환되는 `NEGATIVE_TARGET_HIGH_COST_SELL` 경로를 가진다 (`strategy.py:418`). 이 경로에 일반 cleanup의 최소나이·-4% 하한·일익금 30% 손실예산을 그대로 적용했다고 가정하면 실제와 다르다.

현금 1,200만원 이하에서는 최저원가 LOT 목표가격에 도달하면 최고원가 LOT를 전량 매도한다 (`strategy.py:459`). 이때 매도 수량과 손익은 최고원가 LOT, 트리거와 지정가는 최저원가 LOT 기준일 수 있어 경제적 손실을 확정한다. 일반 다른 LOT의 목표달성으로 이 트리거를 우회하지 못하게 되어 있으며 deep loss 회복 경로는 독립적으로 유지된다. 미커밋의 `oldest` 정책은 목표 달성을 기다리지 않고 가장 오래된 LOT를 markdown 가격으로 즉시 파는 선택지이나 현재 운영값은 아니다.

deep loss는 완료된 세션 종가를 LOT당 날짜별 한 번씩 관찰하고 -40% 이하에서 시작한다 (`strategy.py:556`). 이후 관찰된 최저종가와 경과일을 보존한다. 회복 요구율은 `0.15 × max(0, 1 - 경과일/60)`. 현 시세가 최저종가에서 이만큼 반등하거나 60일이 지나면 매도 후보가 된다 (`strategy.py:579`). 60일은 거래일이 아닌 달력 일수다. 당일 아직 완료되지 않은 종가나 미래 최저가를 공급하면 미래정보 오류다.

## 6. 재진입과 종목 은퇴

현재는 `cash_capacity_oldest` 정책이다. 완전 청산되어 `WAIT_REENTRY`이며 정상 과거 exit timestamp가 있고 신규 LOT 크기를 계산할 수 있어야 한다. 현금이 `새 LOT 금액 × 60` 이상이면 별도 추세·과거 매도가 대비 하락 없이 재진입 신호를 낸다 (`strategy.py:1019`). main의 최종 검사와 OrderManager에서는 지정가 주문금액까지 고려하여 60배 요건을 재검증한다 (`main.py:1032`, `order_manager.py:92`). 미체결 주문이 계좌에 하나라도 있으면 이 재진입을 차단한다.

기존 포지션을 먼저 평가하고 완전 청산 대기 종목은 실제 exit 시각이 오래된 순으로 처리한다 (`main.py:49`). 이 정렬은 신호의 질 평가가 아니며 기존 목록순 최초 매수 우선순위가 남아 있다. 정책 시행일 전에는 재진입을 허용하지 않는다.

소스에는 정상 재진입 -4%, trailing 활성 +5%, trailing drop -10%, 30일 동안 0%로 완화하는 decay, 30일 후 강제 재진입도 남아 있다. 그러나 cash_capacity 정책에서는 이 조건함수들이 false/0을 반환하여 이 기능을 활성 규칙으로 사용하지 않는다 (`strategy.py:942`, `960`, `985`, `1002`). 따라서 설정에 force timeout enabled가 남아 있어도 현재 효과가 있다고 보고하면 안 된다.

전량 청산 후 `retire_after_exit`이면 `TRADE_STOPPED_AFTER_EXIT`로 이행한다 (`position_manager.py:146`). 목록의 별도 `buy_blocked` 플래그도 main 단계에서 매수를 차단한다. 기존 미커밋 변경은 전량 매도 직전 잔존원가/잔존수량 VWAP를 저장한다. 이는 사이클 최저 체결가와 다르며 현금매수 기능이 OFF인 현재에는 재진입의 가격 기준으로 쓰이지 않는다.

## 7. 계좌·종목 자금관리와 주문가능현금

종목 예산과 총 잔존 원가 한도는 있으나 업종별 투자한도, 최초 진입 전에 확보하는 미래 추가매수 전용 예산, 계좌 현금의 안전/추가매수/신규자금 논리적 분리는 없다. 계좌 총원가 검사에는 현재 총액이 한도 이상인지만 보는 경로가 있어 예정 주문금액까지 더한 엄격한 합산 상한은 독립 백테스트/ARCG에서 검증할 부분이다 (`risk_manager.py:23`, `main.py:1529`).

주문가능현금은 단순 예수금이 아니라 KIS 매수가능 조회의 `nrcvb_buy_amt`, 없으면 `ord_psbl_cash`를 사용한다 (`kis_client.py:112`, `136`). 응답에 둘 다 없으면 예외를 발생시킨다. 잔고는 페이지 끝까지 읽은 완전한 스냅샷만 동기화에 쓴다. 즉 실제 브로커가 이미 보류한 주문자금을 연구모형에서 다시 차감하면 이중차감이 된다. 반대로 오프라인 원장 cash는 가용현금에서 아직 미체결 매수 보류금을 별도로 차감해야 한다. 두 cash 의미를 구분해야 한다.

스냅샷은 기본 30초 캐시되며 제출·체결 후 캐시를 무효화한다 (`main.py:155`, `238`, `877`, `1058`). 현금 재진입은 주문 직전 별도 조회로 다시 검사한다. 조회 rate limit에서는 캐시를 이용하는 경로도 있으므로 모든 시점 현금이 무조건 최신이라고 단정할 수 없다. 신규 전략의 불확실 현금 차단을 별도 검증해야 한다.

`risk.cash_shortfall_rotation_enabled=true`이지만 `cash_rotation.funding_enabled`는 현금매도 정책이 활성 또는 cash_capacity 정책이면 false다 (`cash_rotation.py:21`). 현재 매수재원을 만들기 위한 aggregate 강제매도 루프는 비활성이다. 다만 open order 차단과 과거 rotation 재매수 가격 anchor 검사 등 일부 가드는 이 플래그로 계속 동작한다. 이전 연구의 aggregate 현금 부족 해소 결과를 현재 A의 실효동작으로 옮기면 안 된다.

## 8. 정규장·시간외 실행과 보존 대상

현재 정규장 실효창은 09:02~15:28이며 시간외 라우팅은 NXT 08:00~08:50, NXT 15:40~20:00, 16:00 이후 가능한 경우 KRX 시간외 대안이다 (`market_session.py:40`). 이는 저장된 소스의 동작 설명이며 최신 거래소 규정 확인 결과를 주장하는 내용은 아니다. NXT/시간외에서는 휴장일, 종목 적격성, 거래정지, 연속매매 상태, 같은 거래소 시세/체결시각의 신선도(90초), 세션 변경을 검증한다. 시간외는 지정가만 허용하고 shadow 모드에서 실제 주문을 차단한다 (`market_session.py:120`, `192`).

주문계층은 동일 종목/방향/LOT의 미체결 중복을 차단하며 제출 전에 durable intent를 원장에 저장한다. 통신 결과가 불명확하면 재전송하지 않고 미결 상태를 유지한다 (`order_manager.py:168`). 세션과 거래소가 달라도 같은 원장 가드를 사용한다. 체결 중복 제거, 누적체결의 delta 반영, 취소 직전/직후 체결 재조회, 부분체결 후 잔량 취소, 취소 실패 재확인 및 재시작 복구가 있다 (`order_manager.py:266`, `427`, `489`, `590`).

main의 최종 가드는 SYNC_REQUIRED/paused, 위험종목, 검토필요, 은퇴, 부분체결, 미체결 중복, runtime pause, 계좌 상한, 요청빈도, 매수 cooldown을 확인한다 (`main.py:1400`). 실잔고와 LOT 수량이 다르면 동기화 필요로 막으며, 위험종목은 매수뿐 아니라 매도도 보수적으로 막는 기존 정책이다. 신규 ARCG의 위험매도가 이 가드를 우회할지 여부는 의도적으로 설계·검증해야 한다.

가격/수량/세션/중복/체결 복구 등 실행 안정장치는 전략 계산과 분리된 기능이다. ARCG가 신호를 바꾸더라도 이를 임의 삭제할 이유가 없다. `OrderManager`의 paper 경로는 요청 즉시 요청 지정가로 전량 체결을 생성하므로 그 자체는 보수적 역사 백테스트 체결 엔진이 아니다.

## 9. 기존 테스트·연구코드의 의미

직접 전략 규칙을 검사하는 주요 테스트는 `test_lot_strategy.py`, `test_cash_target_strategy.py`, `test_lot_count_decay.py`, `test_cash_capacity_reentry.py`, `test_profit_partial_sell_exposure.py`이며 실행 복구는 `test_order_reconciliation.py`, `test_extended_sessions.py`, `test_cash_rotation.py`가 다룬다. 기존 테스트의 mock/urllib 대체 경로를 확인한 뒤, 아래 명령으로 baseline 테스트를 실행했다. 최초 결과는 **47 passed, 1 failed, 2 warnings, 9.15초**이며 `--maxfail=1` 때문에 뒤의 테스트는 실행되지 않았다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests --maxfail=1 -q --basetemp tmp/arcg_before/pytest_baseline
```

실패는 기존 `tests/test_auto_trader_guards.py::test_run_once_skips_manual_only_stock`이다. mock AutoTrader도 전역 운영 `config/runtime_control.json`을 읽어 `bot_paused`가 반환되어 예상값 빈 문자열과 달랐다. 이는 ARCG 소스가 실행된 결과가 아니며 기존 테스트의 환경 의존성이다. 운영 pause 설정을 해제하여 테스트를 통과시키지 않았다. 로컬 로그는 `tmp/arcg_before/baseline_tests.log`이며 cache 쓰기 권한 경고 2건도 기록되어 있다. 후속 격리 실행 여부와 전체 최종 결과는 최종 테스트 로그를 따른다.

후속 전체 격리 실행은 `scripts.arcg_pytest_isolation`을 명시적으로 사용했다. 이 하네스는 테스트 프로세스에서만 기본 runtime 호출을 중립화하고, 명시적 임시 경로는 원래대로 읽으며, 외부 socket 연결을 차단하고 localhost UI 테스트만 허용한다. 운영 제어파일은 변경하지 않았다. 과거 evaluator를 동적으로 import하는 테스트도 같은 기본 중립화를 적용했다. 최종 실행 결과는 **936 passed, 2 failed, 2 warnings, 88.62초**다.

```powershell
.\.venv\Scripts\python.exe -m pytest -p scripts.arcg_pytest_isolation tests -q --disable-warnings --continue-on-collection-errors --basetemp tmp/arcg_pytest_isolated_final
```

남은 실패는 (1) `test_collect_market_data_dry_run_does_not_mutate_db`: 기존 수집기의 dry-run에서 SQLite 헤더 바이트가 바뀜, (2) `test_unchanged_position_risk_and_operating_config_match_baseline`: 기존 미커밋 position_manager 변경 때문에 과거 Git `8dbf925`와 원문 동일성 검사가 실패함이다. 관련 소스나 assertion을 수정해 결과를 숨기지 않았다. 패키지용 UTF-8 로그는 `test_logs/full_isolated_tests.log`; 최초 비격리 실패는 `test_logs/unisolated_baseline_failure.log`; 중간 하네스 실행은 `test_logs/full_isolated_initial_tests.log`에 각각 보존했다. 이 실행 당시 수집된 938개 기존 테스트 결과이며 이후 신규 ARCG 테스트는 별도 실행 로그로 확인한다.

`scripts/research_strategy_tuning.py`, `cash_policy_tuning_model.py`, `week_target_policy_model_20261021.py` 등은 기존 규칙을 별도 구현한 오프라인 모델이다. 특히 week_target 모델은 다음 동일종목/동일세션 관측에서 체결, 매수 미체결 보류금, bucket 끝 시각 노출, 엄격한 지정가 및 지연 민감도 기능이 있다. 하지만 실제 전략 함수를 그대로 부르는 것은 아니며 자체 문서도 호가/거래량/부분체결/배당/기업행위/브로커 지연/가격샘플/일부 위험 가드 부재를 인정한다. 이미 reports에 있는 성과 숫자를 이번 실행 결과로 재사용해서는 안 된다.

## 10. A 기준선의 순수 API와 재현 조건

네트워크 없는 계산 API는 다음과 같다.

```python
config = load_config(Path("analysis_output/legacy_settings.sanitized.json"))
lots = LotManager(config.strategy)
positions = PositionManager(config.strategy, lots)
strategy = LotGridStrategy(config, lots)
risk = RiskManager(config)

# 각 관측 시각에 미래정보 없이 평가한다.
position = positions.refresh_from_lots(code, observed_price)
account_gate = risk.account_buy_allowed(snapshot, positions.positions)
symbol_gate = risk.symbol_buy_allowed(position)
action = strategy.decide(position, observed_price, snapshot, account_gate, symbol_gate)

# 별도 모의 체결 엔진이 후속 시세에서 체결을 확정한 다음에만 반영한다.
positions.apply_fill(simulated_fill)
```

`strategy.decide`는 주문을 보내지 않지만 position/LOT 메타데이터를 수정할 수 있으므로 완전한 무상태 함수는 아니다. 후보마다 독립 상태를 사용해야 한다. LotManager에 빈 dict를 넘기면 `lots or {}` 때문에 전달한 빈 dict와 내부 dict가 같은 객체가 아닐 수 있다는 점도 모형 연결 시 유의한다.

`config.py`, `strategy.py`, `lot_manager.py`, `position_manager.py`는 여러 곳에서 `datetime.now()`를 부른다. 과거 데이터에 현재 날짜를 적용하면 LOT 나이, 시행일, deep loss 경과일, 재진입 판단이 틀어진다. 기존 `tests/test_lot_count_decay.py:31`은 모듈 datetime을 통제하는 frozen clock 예제를 제공한다. 순차 연구에서 같은 방식의 가역적인 시간 주입을 사용하거나 동등한 명시적 clock adapter가 필요하다. 여러 스레드에서 전역 모듈 시계를 동시에 바꾸면 안 된다.

순수 Strategy만 부르는 A는 main의 자동 review 재검사, 종목별 buy_blocked/manual_only, 최초매수 일일 한도, 미체결/부분체결, cooldown, 가격 샘플, 시간외 적격성/신선도, runtime pause, 브로커 cash 스냅샷과 rotation anchor를 자동 재현하지 않는다. 포함한 항목과 생략한 항목을 결과에 명시해야 하며 이를 완전한 실계좌 실행 재현이라고 표시하면 안 된다. 운영 DB 전체 상태 없이 실제 보유계좌를 과거로 역산하지도 않는다.

현재 현금정책 시행일은 2026-10-19이므로 연구기간이 그 이전이면 두 실험 정의를 구분해야 한다. (1) 당시 날짜대로 기존 설정의 시행일을 적용한 replay, (2) 현재 정책을 과거 전 구간에 고정 적용하는 counterfactual 비교. 이번 연구의 A는 **두 번째 정의를 사용한다**. `legacy_strategy_settings.json`에는 strategy/risk/paper order/market_hours를 포함하며 `cash_target_start_date`와 `reentry_start_date`를 빈 문자열로 설정하여 현재 규칙을 전 기간에 적용한다. 이후 업종예산 실험의 공개 입력으로 종목 code/name/sector 299건만 추가했다. enabled/위험/계좌정보는 넣지 않고 시세 표본의 종목 선택에도 사용하지 않는다. 이 업종값은 2026년 현재 테마/업종 문자열을 과거에 정적으로 투영한 것으로 당시 산업분류가 아니다. 미등록/업종 없는 종목만 UNKNOWN으로 묶는다. 이는 실제 당시 운영성과 재현이 아니다. 원래 시행일과 전체 유효 기본값을 보존한 `legacy_settings.sanitized.json`과 역할이 다르다. B~F와 같은 날짜·종목·시세·체결·비용 조건에서 비교해야 한다. 첫 번째를 선택하고 시행일 전 재진입이 안 된 결과를 정책 성능으로 일반화하면 안 된다.

추가 보존: 원본 소스/설정 전체는 로컬 전용 `tmp/arcg_before/local_baseline.zip`, 기존 diff는 `tmp/arcg_before/preexisting.patch`, 상태와 해시는 같은 디렉터리에 저장되었다. 원본 압축에는 계좌 설정이 포함될 수 있으므로 배포 연구 ZIP에 포함하지 않는다. 신규 연구 작업 브랜치는 `arcg-research-20261022`이며 원래 브랜치명/HEAD는 이 문서 1절의 시작 시점을 뜻한다.

## 11. 변경 전 주요 위험과 신규 연구 과제

기존 장점은 체결 기반 LOT 회계, 지정가와 세션 분리, 미체결·취소·재시작 복구, 종목 예산 잠금, 정교한 실행 가드다. 주요 연구 과제는 가격 신호 없는 첫 진입, 현재 잔존평균에 의존하는 추가매수 기준 변화, 업종/미래 추가매수 준비금 부재, 현재 LOT 수를 과거 전체 보유기간에 곱하는 무제한 decay, 저현금 시 고원가 LOT 손실확정, 추세와 무관한 현금용량 재진입이다.

이는 논리적 위험 진단이며 ARCG의 우월함을 검증한 결론이 아니다. 급락에서 매수 중단이 손실을 줄여도 반등 수익을 놓칠 수 있고 추세매도도 횡보장에서 거래비용과 whipsaw를 늘릴 수 있다. 데이터 확보, 동일 체결가정, 미사용 검증구간, 비용 반영 A~F 비교로 실제 기여를 판단해야 한다.

## 12. 조사 소스 SHA-256

다음은 조사 시점 작업트리 바이트 기준이다. HEAD 해시와 별개이며 이후 연구 파일 추가와 구분한다.

| 파일 | SHA-256 |
|---|---|
| src/kis_msj/strategy.py | 68f87422ec69c857a5d0464f586cc0a289e75f74d1601a492cc6099e77648af9 |
| src/kis_msj/lot_manager.py | acb408ce1473ac4b9da7e8992544e00d1e4d560673bf3c19353247171d78eb10 |
| src/kis_msj/config.py | 85eb02b51b294f4208b086c66daee166d1d4016df0bccbe0588e6878b702e39b |
| src/kis_msj/models.py | 6f974dd5ff075d4df4148e17fb183e6913dd50ba4fd1e342599c82b85bd09c22 |
| src/kis_msj/position_manager.py | 6ae84708f2d8df960fef154c2589204a5488dc81de6b7a35eeedbf4cc7fe3777 |
| src/kis_msj/order_manager.py | 42876e244073b0de45b90600bdf5d711bde3d367fe944e2342bee3e48e078f5c |
| src/kis_msj/main.py | cb936a8015ee72a58e1788ef6b8e31f3ced7f517d62ed85bc9cb9f78bcb2eacf |
| src/kis_msj/cash_rotation.py | da018c8c411ef308a0fedd6863db984466901c29604003a8e883bfb734d47a34 |
| src/kis_msj/market_session.py | 0ce0d6a095e77b396a371e2e17e04921da5c9080b5ad794aa0e154ad96b7d555 |
