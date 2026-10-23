# KIS-MSJ LOT 자동매매 봇

> 문서 기준: 2026-10-23 변동성·체결 추세·현재 LOT 수 Decay 정책과 `config/lot_auto_trader.json`. 코드 및 실제 로드된 설정이 우선입니다.

한국투자증권(KIS) 국내주식 API를 이용하는 LOT 단위 자동매매 봇입니다. 종목별 최초 진입가에서 LOT 크기와 최대 투자액을 고정하고, 하락 구간에서 LOT을 추가하며, 각 LOT의 목표수익 도달 시 회수합니다. 주문·체결·포지션은 SQLite에 보존하고 로컬 웹 UI에서 조회·통제합니다.

## 빠른 실행

실거래 준비 후에는 프로젝트 폴더의 **`run_live_ready.cmd`를 실행**합니다. 중복 실행을 막고 실계좌 잔고·미체결·LOT·시세를 확인한 뒤 봇을 시작합니다. 준비 작업용 정지는 점검 성공 후 해제하며, 이후 사용자가 지정한 정지는 유지합니다.

```powershell
.\run_live_ready.cmd
```

주문 없이 준비 상태만 확인하려면 다음 명령을 사용합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_live_ready.py --check-only
```

기존 명령은 저장된 일시정지 설정을 그대로 유지하며 실행합니다.

```powershell
.\.venv\Scripts\python.exe main.py --config config\lot_auto_trader.json
```

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

UI: `http://127.0.0.1:8765`

실계좌 설정은 `order.live_trading=true`입니다. 코드 변경은 프로세스 재시작이 필요하며, 설정 변경은 UI의 안전한 reload 절차도 사용할 수 있습니다. [실거래 실행 준비 기록](docs/live_start_ready_20261023.md)에 점검 범위와 실행 시 동작을 정리했습니다.

## 현재 핵심 정책

- 가격대별 LOT 금액과 종목 최대금액은 최초 진입가로 사이클 동안 고정됩니다.
- 추가매수는 현재 보유 LOT 중 최저 매수가를 기준으로 기본 4%에 종목 변동성과 연속 매수 배율을 적용합니다.
- LOT별 매도 목표는 개별 매수가에 기본 3% × 변동성 배율 × 연속 매도 배율을 적용한 뒤, 현재 OPEN LOT 수 × 보유 주수 × 0.1%p를 마지막에 차감합니다. 음수 목표도 허용합니다.
- 매도 후 첫 재매수 체결까지 최근 실제 매도가에서 기본 4% × 변동성 배율 하락 조건도 충족해야 합니다. 재진입 후 종목별 추가 2 LOT 예산과 총 자산의 10% 안전 현금이 남아야 합니다.
- 이익매도는 LOT 단위이며, 고노출 구간에서는 설정에 따라 부분매도가 적용될 수 있습니다.
- `buy_blocked=true`는 신규·추가·재진입 매수만 막고 필요한 매도는 허용합니다.
- 예수금 비례 매수·매도, 현금 확보용 교체매도, 시간 경과 강제 재진입은 새 정책에서 사용하지 않습니다. 기존 손실 회복·투자 한도·일일 신규매수 제한은 유지합니다.
- `retire_after_exit=true` 종목은 남은 LOT이 모두 청산되면 `TRADE_STOPPED_AFTER_EXIT`가 됩니다.
- 심각손실 LOT은 -40% 이하 종가에서 recovery 추적을 시작합니다. 최저 종가 대비 요구 반등률은 +15%에서 60일간 0%까지 선형 감소하고, 60일째에는 강제 정리합니다. 추적 중 -40% 위로 회복해도 상태를 유지합니다.
- 단순 장기보유만으로 `REVIEW_REQUIRED`가 되지는 않습니다.
- 새 정책의 일반·Decay 매도는 목표 도달 후 확인된 현재가를 지정가로 사용합니다. 매수 지정가는 가격 상한과 예산을 넘지 않도록 제한하며, 별도 위험 매도의 기존 가격 정책은 유지합니다.
- UI 손익의 수수료·세금 추정은 매도금액의 0.21%입니다. 실제 증권사 정산과 차이날 수 있습니다.

## 문서 지도

- [2026-10-23 전략 변경·검증 보고서](docs/adaptive_lot_policy_20261023.md)
- [실거래 실행 준비·한 번에 시작](docs/live_start_ready_20261023.md)
- [전략과 LOT 계산](docs/strategy_lot_sizing.md)
- [2026-10-19 예수금 연동 목표·저현금 LOT 매도](docs/cash_target_profit.md)
- [재진입 대기 순서·60 LOT 현금 기준](docs/cash_capacity_reentry.md)
- [2026-10-17 튜닝 결과와 검증·적용 기록](reports/expanded_tuning_20261017/report.md)
- [로컬 UI](docs/local_ui.md)
- [포트폴리오 대시보드](docs/portfolio_dashboard.md)
- [주문 취소·체결 정합성](docs/order_cancel_reconciliation.md)
- [성능과 루프 운영](docs/performance_loop_tuning.md)
- [분석 데이터와 튜닝](docs/analysis_and_tuning.md)
- [신규 시즌 초기화](docs/new_season_reset.md)
- [전체 인수인계](docs/project_handoff_full.md)

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest
```

운영 데이터의 기준 파일은 `config/lot_auto_trader.json`, `data/lot_auto_trader_real_test.sqlite3`, `logs/lot_auto_trader.log`입니다. DB를 직접 수정하지 말고 UI의 보호된 작업 또는 검증된 스크립트를 사용하십시오.
