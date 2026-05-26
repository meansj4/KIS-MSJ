# KIS LOT Bot Project Audit and Cleanup Report

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. This report records the whole-project audit/cleanup pass performed after the handoff docs. If this report conflicts with `project_handoff_full.md`, re-check code first and then update the full handoff.  
> Last updated: 2026-05-27 / Baseline tests before this audit: `147 passed` with one pytest cache warning / Baseline config profile: `expansion_100_safe`.

## 1. 감사 범위

이번 감사는 특정 LOT sizing legacy만 본 것이 아니라 아래 범위를 정적 검색, 코드 읽기, CLI/API 대조, 문서 링크 점검 기준으로 확인했다.

| 범위 | 확인 내용 |
| --- | --- |
| `src/kis_msj/**/*.py` | Bot Core, LOT/position/order/storage, UI service/server, runtime control |
| `scripts/**/*.py` | 새 시즌 archive/reset/liquidation 스크립트와 CLI 옵션 |
| `tests/**/*.py` | 현재 기능 테스트, legacy 테스트의 유지 필요성 |
| `config/*.json`, `config/**/*.json` | live config, example config, backup config의 최신성 |
| `docs/**/*.md`, `README.md` | handoff/local UI/new season/lot sizing/expansion 문서 정합성 |
| UI HTML/CSS/JS | 탭, API 호출, 새 시즌 wizard 문구, manual request 안내 |
| 운영 보조 파일 | archive/export/runtime 관련 문서와 코드 경로 |

실행하지 않은 것:

- 실거래 주문
- KIS 주문 API 호출
- DB reset
- destructive cleanup

## 2. 발견된 불일치 요약

| 분류 | 항목 | 판단 | 조치 |
| --- | --- | --- | --- |
| 오래된 config 예시 | `config/lot_auto_trader.example.json`이 legacy exposure band 중심, 10개 영문 종목 예시, `price_lot_bands` 없음 | 문서/설정 혼동 유발 | `expansion_100_safe` 호환 예시로 갱신. live config는 건드리지 않음 |
| UI metadata | `exposure_buy_bands`, `exposure_sell_bands`, `auto_buy_limit`, `absolute_max_investment` 설명이 현재 cycle-locked LOT sizing보다 legacy 금액 기준처럼 보임 | UI만 수정 필요 | 레거시/호환용임을 명시하고 현재는 `add_buy_lot_bands`, `target_profit_lot_bands`, `max_symbol_amount` 우선임을 설명 |
| UI 새 시즌 화면 | `request_creation_possible` 같은 내부 flag가 기본 화면에 그대로 노출됨 | UI만 수정 필요 | `고급 진단값 보기` details로 접음 |
| 새 시즌 wizard step | “UI에서는 아직 실행 버튼 없이 절차 안내만 제공” 문구가 남아 있음 | UI 문구 오래됨 | 백업 버튼이 존재하는 현재 구조에 맞게 수정 |
| CLI 옵션 문서 | archive root 옵션은 실제로 `--archive-root` | 문서/코드 정합성 확인 | `--help` 기준으로 일치 확인. 문서에서 이미 최신화됨 |
| KIS snapshot strict validation | 예전 “generated_at 권장, sellable fallback” 표현 잔재 가능성 | 문서 확인 필요 | 주요 문서는 preview/create_request 구분으로 최신화되어 있음 |
| Execution Mapping UI | nav 탭은 제거됐지만 `loadExecution()`과 `/api/execution-mapping/status`는 남아 있음 | deprecated 유지 필요 | 내부 진단/API 용도로 유지. 문서에 “탭 제거, API 잔존” 명시됨 |
| Legacy mode | `legacy_exposure_bands`, `exposure_*_bands`, `initial_buy_amount`, `auto_buy_limit` 일부 코드 경로 존재 | deprecated 유지 필요 | 기존 DB/config/test 호환 때문에 삭제하지 않음. UI 설명만 정리 |

## 3. 제거한 항목

이번 감사에서 소스 코드를 물리적으로 삭제한 항목은 없다. 이유는 다음과 같다.

- legacy exposure band 관련 함수와 config는 `lot_sizing_mode != cycle_locked_by_entry_price`일 때 backward compatibility 경로로 사용된다.
- `exit_anchor_price`, `base_target_profit_rate`, `auto_buy_limit` 등은 기존 DB row와 로그/마이그레이션 호환에 필요하다.
- `/api/execution-mapping/status`와 `loadExecution()`은 일반 nav에서는 제거됐지만 첫 실체결/raw mapping 진단 API로 남길 가치가 있다.

## 4. deprecated로 남긴 항목

| 파일 | 항목 | 유지 이유 | 주의 |
| --- | --- | --- | --- |
| `src/kis_msj/config.py` | `strategy.reentry_drop_rate` | 예전 단일 anchor 설정 호환. UI에서는 숨김 | 실제 reentry 판단은 `normal_reentry_drop_rate`, `trailing_*` 사용 |
| `src/kis_msj/config.py`, `lot_manager.py`, `strategy.py` | `exposure_buy_bands`, `exposure_sell_bands` | `legacy_exposure_bands` 모드와 기존 테스트 호환 | 기본 모드에서는 `add_buy_lot_bands`, `target_profit_lot_bands` 우선 |
| `positions`/`models` | `auto_buy_limit`, `absolute_max_investment` | 기존 position row와 non-cycle mode 호환 | cycle-locked mode에서는 `max_symbol_amount`, `max_lots_per_symbol` 우선 |
| `lots`/`models` | `base_target_profit_rate`, `target_profit_pct` | 과거 LOT 기록/로그 호환 | 실제 SELL 판단은 현재 OPEN LOT 수 기반 `current_base_target_profit_rate` 우선 |
| `positions` | `exit_anchor_price` | 기존 DB row/fallback/log 호환 | 실제 reentry는 `normal_exit_anchor_price`, `trailing_exit_anchor_price` 사용 |
| `src/kis_msj/ui_server.py` | `loadExecution()` | raw execution mapping 내부 진단용 | 일반 nav 탭은 제거된 상태 |

## 5. 문서 수정 항목

| 파일 | 수정/확인 내용 |
| --- | --- |
| `docs/project_handoff_full.md` | 이 감사 보고서 링크 추가 |
| `docs/project_handoff_full.md`, `summary`, `thread_prompt`, `new_season_reset.md`, `local_ui.md` | `147 passed`, strict KIS snapshot policy, manual request 설명 최신화 확인 |
| `docs/new_season_reset.md` | pending order/manual request status, generated_at/sellable_quantity strict policy 최신화 확인 |
| `docs/local_ui.md` | KIS 직접 주문 API 없음과 manual request 생성 API는 있음의 구분 확인 |

추가 확인 결과:

- docs 링크 점검: 깨진 상대 링크 0개
- `project_*`, `local_ui`, `strategy_lot_sizing`, `new_season_reset`, `expansion_100_config` 모두 authoritative source 문구와 최신 테스트 기준 표기 존재

## 6. UI 수정 항목

| 파일 | 위치 | 문제 | 조치 |
| --- | --- | --- | --- |
| `src/kis_msj/ui_service.py` | `CONFIG_METADATA`, `DETAILED_CONFIG_DESCRIPTIONS` | legacy exposure/auto limit 설명이 현재 LOT sizing과 혼동 가능 | 레거시/호환용으로 명확히 수정 |
| `src/kis_msj/ui_service.py` | `_new_season_wizard_steps()` | 백업 단계가 “UI 실행 버튼 없음”이라고 표시 | UI 버튼/CLI 모두 안내하도록 수정 |
| `src/kis_msj/ui_server.py` | New Season 화면 | 내부 flag가 기본 화면에 노출 | `details` 안의 고급 진단값으로 접음 |

## 7. 테스트 수정 항목

테스트 파일은 이번 감사에서 직접 수정하지 않았다. 검색 결과 오래된 이름처럼 보이는 테스트 중 일부는 현재도 명확한 legacy/backward compatibility 목적이 있다.

| 테스트 | 판단 |
| --- | --- |
| `test_legacy_mode_keeps_exposure_based_target_profit_behavior` | legacy mode 보존 검증으로 유지 필요 |
| `test_ui_service.py`의 `strategy.exposure_buy_bands` metadata 확인 | 레거시 항목이 UI에 호환용으로 남아 있는지 확인하는 의미가 있어 유지 가능 |
| snapshot strict validation 테스트 | 최신 정책 검증으로 유지 필요 |

권장 후속:

- legacy 관련 테스트 이름에는 계속 `legacy`/`compat`를 명시한다.
- UI metadata 설명 문자열 존재 테스트는 너무 문구 고정이 강해지지 않게 핵심 keyword 중심으로 유지한다.

## 8. 확인 필요 항목

| 항목 | 현재 판단 | 권장 |
| --- | --- | --- |
| KIS balance snapshot 자동 생성 | `prepare_new_season.py`에는 자동 생성 기능 없음. JSON 파일 입력/검증 구조 | 운영자가 snapshot JSON을 준비하는 절차를 UI에서 더 쉽게 만들지 검토 |
| `config/lot_auto_trader.example.json` 범위 | 현재는 expansion-safe 구조 + 첫 10종목 예시 | 별도 `config/lot_auto_trader.expansion_100.example.json`로 100종목 전체 예시를 둘지 검토 |
| `/api/execution-mapping/status` 노출 | nav 탭은 제거됐지만 API/function 유지 | 필요 없다고 확정되면 deprecated 주석 후 다음 정리 때 제거 가능 |
| `cleanup_enabled=false` 장기 운용 | 초기 안정화에는 적절 | 로그 안정화 뒤 cleanup을 켤지 별도 검토 |
| UI 새 시즌 wizard 단순화 | 내부 flag 노출은 줄였지만 기능이 많음 | 사용자가 계속 헷갈리면 single-action wizard를 더 강하게 자동화 |

## 9. 현재 남은 리스크

| 리스크 | 등급 | 현재 방어장치 | 남은 취약점 | 권장 조치 |
| --- | --- | --- | --- | --- |
| 주문/체결 동기화 | 중간 | open order 기준 reconciliation, startup recent reconciliation, unmatched ignore | 실제 KIS raw 체결 row 변화/누락 가능성 | 첫 실체결 후 raw mapping 재확인 |
| fill dedupe | 낮음~중간 | execution_id 우선, fallback key, duplicate count | KIS가 execution_id 없이 체결시각 품질이 낮으면 fallback 한계 | execution_id 실제 제공 여부 지속 확인 |
| partial fill | 중간 | PARTIAL order status, remaining_quantity 기준 LOT 반영 | 장시간 PARTIAL/order timeout 운영 판단 필요 | open order UI 모니터링 강화 |
| manual order 중복 소비 | 중간 | manual request status, pending status reset guard, Bot Core 소비 경로 | Bot loop 중복 처리 경쟁 가능성은 구현 세부 계속 관찰 필요 | request status transition 로그 확인 |
| DB reset/archive/liquidation | 높음 | confirm text, pending order/request/open lot/sync guard, KIS snapshot strict validation | snapshot 파일을 운영자가 잘못 만들 수 있음 | snapshot 생성 도구 또는 import UI 추가 검토 |
| KIS snapshot stale/mismatch | 높음 | generated_at/sellable strict mode, max age, DB hash, plan freshness guard | 자동 snapshot 생성이 없어 수동 오류 가능 | snapshot validator UI 개선 |
| UI 버튼 오조작 | 중간 | live warning, confirm, disabled guide, no direct KIS order API | 많은 버튼이 있어 초보자 혼동 가능 | wizard UX 지속 단순화 |
| config 저장/검증 | 중간 | backup, atomic save, validation, history | 모든 config 의미를 schema가 완벽히 검증하지는 않음 | schema validation 확대 |
| runtime pause 반영 | 중간 | runtime_control.json, main loop guard | 긴 작업 중 즉시 interrupt 한계 가능 | loop 내 체크포인트 확대 검토 |
| live_trading 전환 | 높음 | UI 경고, config confirm, risk guards | 사용자가 대규모 후보군으로 켤 위험 | 소액/paper 검증 후 단계 전환 |
| raw execution log | 중간 | 기본 마스킹, UI masking | raw log 장기 활성화 시 로그 과다/민감정보 리스크 | 확인 후 `enable_execution_raw_log=false` |
| 100종목 확장 | 중간 | `max_new_buy_per_day=10`, `max_new_buy_amount_per_day=2M`, total limits | 고가 LOT 후보가 섞이면 하루 노출 변동 큼 | daily amount limit 로그 확인 |
| REVIEW/SYNC/RISK guard | 낮음~중간 | 상태별 BUY/SELL 차단 테스트 | 수동매도 후 sync 전 상태 혼동 | Review 탭/recheck/reconciliation 안내 사용 |
| cleanup disabled | 낮음 | `cleanup_enabled=false` | 오래된 손실 LOT 축적 가능 | 안정화 후 cleanup policy 재검토 |

## 10. 다음 권장 작업

1. KIS balance snapshot JSON을 사람이 덜 실수하게 만드는 import/validator UI를 강화한다.
2. 새 시즌 wizard에서 “다음 안전 단계 하나만 실행” 버튼을 더 강하게 단순화한다.
3. 첫 실체결 이후 raw execution mapping 결과를 다시 보고 `enable_execution_raw_log=false`로 되돌린다.
4. `config/lot_auto_trader.example.json`과 별도로 full 100-stock example/profile 파일을 둘지 결정한다.
5. `legacy_exposure_bands`를 장기적으로 계속 유지할지, 명확한 deprecation timeline을 정한다.

## 11. 실행한 정적 점검 결과

| 점검 | 결과 |
| --- | --- |
| Python AST parse: `src`, `scripts`, `tests` | parse error 0 |
| UI route inventory | `/api/status`, `/api/stocks`, `/api/lots`, `/api/orders`, `/api/fills`, `/api/manual-order-requests`, `/api/manual-orders/preview`, `/api/manual-orders`, review API, new-season API 등 실제 route 확인 |
| CLI help | `scripts/prepare_new_season.py --help` 기준 옵션 확인: `--config`, `--archive-root`, `--profile`, `--apply-config`, `--archive`, `--liquidation-plan`, `--create-liquidation-requests`, `--kis-balance-json`, `--liquidation-plan-file`, `--plan-max-age-minutes`, `--reset-db`, `--confirm`, `--dry-run`, `--execute` |
| docs link check | missing relative links 0 |
| KOSPI 100 config count | stocks 100, enabled 97, manual_only 3 |
| reset pending statuses | orders: `REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW`; manual: `REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING` |

## 12. 실행한 테스트 결과

이번 보고서 작성 시점에 아래 전체 테스트를 실행했다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_audit_cleanup_check
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

결과:

| 명령 | 결과 |
| --- | --- |
| `.\\.venv\\Scripts\\python.exe -m pytest -q --basetemp .pytest_tmp_audit_cleanup_check` | `147 passed`, pytest cache warning 1개 |
| `.\\.venv\\Scripts\\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check` | `147 passed`, pytest cache warning 1개 |

warning은 `.pytest_cache` cache write 관련 `PytestCacheWarning`이며 기능 실패는 아니다.
