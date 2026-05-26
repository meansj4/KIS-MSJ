# 새 시즌 reset/archive 절차

이 절차는 기존 운영 기록을 보존한 뒤 새 후보군과 새 리스크 한도로 다시 시작하기 위한 안전 장치입니다. 스크립트 기본값은 dry-run이며, 실거래 주문 API를 호출하지 않습니다.

## 기본 원칙

- 기존 config, DB, logs는 삭제하지 않고 `archive/reset_YYYYMMDD_HHMMSS/` 아래로 복사합니다.
- DB 초기화는 `RESET 확인` 문구가 있어야 하며, open order 또는 sync mismatch가 있으면 차단됩니다.
- 전량매도는 즉시 주문하지 않고 liquidation plan 파일만 생성합니다.
- 전량매도 요청이 필요하면 별도 확인 후 manual order request 경로로만 처리해야 합니다.
- manual SELL request가 생성되더라도 실제 fill 전에는 lots/positions가 바뀌면 안 됩니다.

## dry-run 점검

```powershell
.\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --liquidation-plan --profile expansion_100_safe
```

이 명령은 어떤 파일도 삭제하거나 변경하지 않고, archive/config/liquidation 계획을 JSON으로 미리 보여줍니다.

## archive + 새 config 적용

```powershell
.\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --profile expansion_100_safe --execute
```

동작:

- 현재 config/DB/log를 archive 폴더에 백업합니다.
- config의 후보 종목을 KOSPI 100 후보군으로 교체합니다.
- `risk.profile=expansion_100_safe`를 적용합니다.
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`로 시작합니다.

## DB 초기화

DB 초기화는 기존 보유/미체결/동기화 상태가 완전히 정리된 뒤에만 수행해야 합니다.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET 확인" --execute
```

차단 조건:

- orders에 `REQUESTED` 또는 `PARTIAL` 주문이 남아 있음
- positions에 `SYNC_REQUIRED` 상태가 있음
- positions에 lot quantity mismatch가 있음

## 전량매도 계획

```powershell
.\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --execute
```

이 명령은 `exports/liquidation_plan_YYYYMMDD_HHMMSS.json` 파일을 만듭니다. 주문 요청은 만들지 않습니다.

계획 확인 항목:

- 종목 코드
- LOT ID
- DB 잔여수량
- 현재가 기준 예상 매도금액
- 예상 손익

실제 매도 전 확인:

- KIS 실제 잔고와 DB OPEN LOT 수량이 일치하는지 확인
- 미체결 주문이 없는지 확인
- 수동매도 요청은 Bot Core/manual_order_requests 경로로만 생성
- 체결 reconciliation 후 lots remaining quantity가 0인지 확인

## 첫 운영 전 체크리스트

- DB 백업 완료
- 기존 logs archive 완료
- 기존 config archive 완료
- 기존 보유 전량매도 완료 여부 확인
- KIS 잔고와 DB positions/lots 불일치 없음
- manual_order_requests 미처리 `REQUESTED` 없음
- orders 중 `REQUESTED`/`PARTIAL` 없음
- fills 중 미반영 항목 없음
- `enable_execution_raw_log=true`
- `live_trading=false` 상태에서 paper/mock 테스트 통과
- live trading 전환 전 사용자 명시 확인
