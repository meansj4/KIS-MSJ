# Adaptive LOT V2 변경·검증 보고서

## 전달 문서와 상태

독립 검토에 업로드할 파일: `docs/CURRENT_TRADING_POLICY.md`.
코드는 수정했으며 실행 중인 봇의 재시작·운영 설정/DB 변경·일시정지 변경·실제 주문은 실행하지 않았다.
캘린더 API의 실제 운영 연결 및 특수 장 종료시각은 별도 운영 검증이 필요하다.

## 변경 코드

`src/kis_msj/adaptive_policy.py`, `trading_calendar.py`, `lot_manager.py`, `position_manager.py`,
`strategy.py`, `order_manager.py`, `main.py`, `ui_service.py`, `ui_server.py`.
이력 저장 형식은 기존 actual_fill_events를 재사용하며 새 DB 열은 추가하지 않는다.
구버전 실험 재현을 위해 `research/adaptive_tuning_20261023/legacy_policy.py` 및 engine의 import를
당시 동결 소스로 연결했다. 구버전 실험은 V2 성과 검증이 아니다.

검증·보조 코드: `scripts/verify_policy_v2_artifacts.py`, `tests/conftest.py`,
`tests/test_trading_policy_v2.py`, `tests/test_adaptive_strategy.py`,
`tests/test_adaptive_fill_state.py`, `tests/test_adaptive_ui.py`,
`tests/test_adaptive_tuning_engine.py`, `tests/test_adaptive_tuning_runner.py`.
새 정책 테스트는 실제 달력의 추정값을 사용하지 않고 명시한 개장일·휴장일 자료로 검증한다.

## 구현과 수치

추가매수는 최저 LOT 대신 잔여 수량 VWAP와 N별 -(4+2*(N-1))%를 사용한다.
추세는 실제 고유 체결 주문의 최근 5 KRX 거래일 BUY−SELL 점수(±12)다.
Decay는 매수일 다음부터 완료된 세션 D에 대해 N*0.1*D/5 %p를 마지막에 뺀다.
연속 횟수 필드는 보존하지만 판단 배율에 사용하지 않는다.

예: 10,000원·12,000원 각 1 LOT이면 VWAP 11,000원이다. m=1, 최근점수0일 때
이전 최저 LOT 기준은 9,600원, V2의 2 LOT 평가PnL -6% 목표는 10,340원이다.
거래 이력이 실제 창 안에 있으면 새 점수 배율을 추가로 적용한다.
최근 매도가 10,000원과 m=1이면 추가 상한 9,600원을 함께 충족해야 한다.
매수일 이후 완료 세션 4개라면 1 LOT Decay는 0.08%p이고, 5개면 0.1%p다.

VWAP 변동은 실제 주문 간격을 일정하게 만들지 않는다. 같은 가격 연쇄 매수 차단 사례와
매도 직후 최근 매도가 제한을 검사했다. 극단 배율로 -100% 이하가 되면 완화 없이 차단한다.
로그의 adaptive_v2와 adaptive_pnl_threshold_unreachable로 사유를 확인할 수 있다.
실거래 발생 빈도는 측정하지 않았으며 상한·감쇠를 임의로 추가하지 않았다.

## 테스트

전체 실행: **1,323 passed** (195.30초). 새 핵심 정책 13개 포함.
최종 실행은 pytest 캐시 플러그인을 제외했으며 테스트 실패는 없었다.
최종 로그·화면 및 캘린더 예외 처리 뒤 관련 테스트 79개와 141개도 별도 통과했다.
실제 주문 없이 임시 DB와 가짜 브로커를 사용했다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp reports/policy_v2_complete_temp -p no:cacheprovider --tb=short --junitxml=reports/policy_v2_final_tests.xml
```

검증 XML: `reports/policy_v2_final_tests.xml`.
운영 파일 비교 해시: `reports/policy_v2/delivery_manifest.json`.
실거래 봇이 자체 기록 중인 DB의 파일 변경을 이번 작업의 DB 수정으로 취급하지 않았다.

## 문서 정리

아래 README/문서에 현행 통합 명세 연결과 과거 정책 구분을 추가했다. 예전 정책 본문은
작성 당시 이력으로 보존하며 현재 Adaptive 수식으로 해석하지 않는다.
reports/analysis_output의 과거 산출물과 소스 스냅샷은 재현성 보존 대상이므로 내용을 덮어쓰지 않았다.

- `README.md`
- `docs/adaptive_lot_policy_20261023.md`
- `docs/addtime.md`
- `docs/analysis_and_tuning.md`
- `docs/cash_adaptive_v1.md`
- `docs/cash_capacity_reentry.md`
- `docs/cash_linear_policy_20261021.md`
- `docs/cash_rotation_aggregate.md`
- `docs/cash_shortfall_rotation.md`
- `docs/cash_target_profit.md`
- `docs/CURRENT_TRADING_POLICY.md`
- `docs/expansion_100_config.md`
- `docs/kis_api_update_20260922.md`
- `docs/live_start_ready_20261023.md`
- `docs/local_ui.md`
- `docs/market_data_tuning_guide.md`
- `docs/new_season_reset.md`
- `docs/order_cancel_reconciliation.md`
- `docs/order_intent_rejection_20261008.md`
- `docs/performance_loop_tuning.md`
- `docs/POLICY_V2_CHANGE_REPORT.md`
- `docs/portfolio_dashboard.md`
- `docs/project_audit_cleanup_report.md`
- `docs/project_handoff_full.md`
- `docs/project_handoff_summary.md`
- `docs/project_handoff_thread_prompt.md`
- `docs/strategy_lot_sizing.md`
- `docs/today_bootstrap120_runbook.md`
