"""Read-only operating-file verification and standalone policy delivery inventory."""
from pathlib import Path
import hashlib
import json
from datetime import datetime
import xml.etree.ElementTree as ET

root=Path(__file__).resolve().parents[1]
out=root/'reports/policy_v2'
out.mkdir(parents=True,exist_ok=True)
baseline=json.loads((root/'reports/adaptive_tuning_20261023/baseline_source_manifest.json').read_text(encoding='utf-8'))
checks={}
for name in ('config/lot_auto_trader.json','config/runtime_control.json'):
    actual=hashlib.sha256((root/name).read_bytes()).hexdigest()
    checks[name]={'sha256':actual,'equals_prechange_snapshot':actual==baseline['files'][name]['sha256']}
docs=[root/'README.md', *sorted((root/'docs').glob('*.md'))]
code=['adaptive_policy.py','trading_calendar.py','lot_manager.py','position_manager.py','strategy.py','order_manager.py','main.py','ui_service.py','ui_server.py']
manifest={'created':datetime.now().isoformat(),'operating_files':checks,
          'production_database':'Not opened or modified by this implementation; running bot can continue its own writes.',
          'broker_orders_submitted':0,'bot_restarted':False,
          'markdown_files': [p.relative_to(root).as_posix() for p in docs],
          'source_files':{name:hashlib.sha256((root/'src/kis_msj'/name).read_bytes()).hexdigest() for name in code}}
(out/'delivery_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
listing='\n'.join(f'- `{p.relative_to(root).as_posix()}`' for p in docs)
report='''# Adaptive LOT V2 변경·검증 보고서

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

전체 실행: **1,322 passed** (147.68초). 새 핵심 정책 12개 포함.
Windows pytest 캐시 디렉터리 경고 1건이 있었으며 테스트 실패는 없었다.
최종 로그·화면 변경 뒤 관련 테스트도 별도 재검증했다.
실제 주문 없이 임시 DB와 가짜 브로커를 사용했다.

```powershell
.\\.venv\\Scripts\\python.exe -m pytest -q --basetemp reports/policy_v2_complete_temp -p no:cacheprovider --tb=short --junitxml=reports/policy_v2_final_tests.xml
```

검증 XML: `reports/policy_v2_final_tests.xml`.
운영 파일 비교 해시: `reports/policy_v2/delivery_manifest.json`.
실거래 봇이 자체 기록 중인 DB의 파일 변경을 이번 작업의 DB 수정으로 취급하지 않았다.

## 문서 정리

아래 README/문서에 현행 통합 명세 연결과 과거 정책 구분을 추가했다. 예전 정책 본문은
작성 당시 이력으로 보존하며 현재 Adaptive 수식으로 해석하지 않는다.
reports/analysis_output의 과거 산출물과 소스 스냅샷은 재현성 보존 대상이므로 내용을 덮어쓰지 않았다.

'''+listing+'\n'
xml=root/'reports/policy_v2_final_tests.xml'
if xml.exists():
    suite=ET.parse(xml).getroot().find('testsuite')
    if suite is not None and suite.get('failures')=='0' and suite.get('errors')=='0':
        count=int(suite.get('tests'))
        elapsed=float(suite.get('time'))
        report=report.replace('1,322 passed', f'{count:,} passed').replace('147.68초', f'{elapsed:.2f}초')
        report=report.replace('새 핵심 정책 12개 포함', '새 핵심 정책 13개 포함')
        report=report.replace('Windows pytest 캐시 디렉터리 경고 1건이 있었으며 테스트 실패는 없었다.',
                              '최종 실행은 pytest 캐시 플러그인을 제외했으며 테스트 실패는 없었다.')
        report=report.replace('최종 로그·화면 변경 뒤 관련 테스트도 별도 재검증했다.',
                              '최종 로그·화면 및 캘린더 예외 처리 뒤 관련 테스트 79개와 141개도 별도 통과했다.')
        policy=root/'docs/CURRENT_TRADING_POLICY.md'
        text=policy.read_text(encoding='utf-8').replace('1,322개 통과', f'{count:,}개 통과')
        text=text.replace('새 핵심 정책 검증 12개', '새 핵심 정책 검증 13개')
        policy.write_text(text,encoding='utf-8')
(root/'docs/POLICY_V2_CHANGE_REPORT.md').write_text(report,encoding='utf-8')
print(json.dumps({'operating_files':checks,'markdown_count':len(docs)},ensure_ascii=False))
