# Market Data Tuning Guide

Last updated: 2026-05-27

Authoritative overview: `docs/project_handoff_full.md`

## 오늘 저장 상태 확인

2026-05-27 장 종료 후 `Save market data now` 실행 결과를 DB 기준으로 확인했다.

| 항목 | 확인값 |
| --- | ---: |
| DB | `data/lot_auto_trader_real_test.sqlite3` |
| `price_snapshots` 누적 | 2,525 |
| `daily_prices` 누적 | 119 |
| `liquidity_snapshots` 누적 | 0 |
| `market_data_collection_runs` 누적 | 1 |
| 최신 수집 run | `market_data_20260527_162101_631694` |
| 수집 모드 | `snapshot_daily` |
| 요청 종목 | 119 |
| 성공 종목 | 119 |
| 실패 종목 | 0 |
| 저장 row | 238 |
| KIS 주문 API 호출 | false |
| DB reset | false |

즉, 오늘 `Save market data now`는 정상 저장되었다. 119종목인 이유는 거래정지로 제외한 종목이 있어서이며, 저장된 값은 장마감 후 현재가 조회 기반의 당일 OHLCV 유사 데이터다.

## 계속 쌓이는 데이터와 직접 수집해야 하는 데이터

| 데이터 | 봇만 켜면 자동 누적 | 별도 실행 필요 | 용도 |
| --- | --- | --- | --- |
| decisions | 예 | 아니오 | 매수/매도/skip/block 판단 기록 |
| orders/fills/lots/positions | 예 | 아니오 | 실제 주문/체결/LOT 성과 분석 |
| config_snapshots | 예 | 아니오 | config 버전별 성과 비교 |
| price_snapshots | 예, decision 시점 | 선택적으로 수동 추가 가능 | 판단 당시 가격 맥락 |
| daily_prices | 아니오 | 예, `Save market data now` 또는 `collect_market_data.py` | +1/+3/+5/+10일 사후 성과, MFE/MDD 근사 |
| liquidity_snapshots | 아니오 | 아직 future work | 스프레드/호가 기반 정밀 분석 |
| minute_prices | 아니오 | 아직 future work | intraday 정밀 백테스트 |

핵심은 `daily_prices`다. 봇이 매매할 때의 판단 가격은 자동으로 남지만, “그 뒤 며칠 동안 올랐는지/내렸는지”를 보려면 장마감 후 가격 데이터가 매일 쌓여야 한다.

## 장마감 후 권장 운영

매일 장 종료 후 아래 둘 중 하나를 수행한다.

1. UI Dashboard에서 `Save market data now` 클릭
2. PowerShell에서 직접 실행

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe scripts\collect_market_data.py --config config\lot_auto_trader.json --symbols-from-config --snapshot --daily --execute
```

장중에 실행하면 `daily_prices.close`가 확정 종가가 아니라 실행 시점 현재가 성격이 될 수 있다. Level 2 튜닝용으로는 장마감 후 16:10~16:30 사이 실행을 권장한다.

## 나중에 튜닝할 때 쓰는 방식

1. 충분한 기간 동안 봇을 운용한다.
   - 최소 2~4주
   - 최소 100 fills
   - 최소 30 closed LOT
   - config별 비교를 하려면 각 config/run_id별 표본이 충분해야 한다.

2. 매일 장마감 후 market data를 저장한다.
   - `daily_prices`가 없으면 blocked/skipped 후보의 이후 성과를 제대로 볼 수 없다.

3. 분석 데이터셋을 export한다.

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe scripts\export_analysis_dataset.py --config config\lot_auto_trader.json
```

4. 생성된 `exports/analysis_dataset_YYYYMMDD_HHMMSS/` 폴더를 확인한다.
   - `summary.json`
   - `metadata.json`
   - `decisions.csv`
   - `skipped_actions.csv`
   - `price_snapshots.csv`
   - `daily_prices.csv`
   - `orders.csv`, `fills.csv`, `lots.csv`, `positions.csv`

5. Codex/ChatGPT에 넘길 때는 위 export 폴더와 현재 config를 함께 제공한다.

## Level 2에서 가능한 분석

`daily_prices`가 며칠 이상 쌓이면 다음 분석이 가능해진다.

| 분석 | 필요한 데이터 |
| --- | --- |
| add_buy_lot_bands 성과 | decisions, lots, fills, daily_prices |
| target_profit_lot_bands 성과 | decisions, lots, fills |
| price_lot_band별 성과 | decisions, price_snapshots, fills |
| blocked action 기회손실 | skipped_actions, daily_prices |
| +1/+3/+5/+10일 이후 수익률 | skipped_actions 또는 fills + daily_prices |
| MFE/MDD 근사 | daily_prices high/low |
| 보유기간/회전율 | lots, fills |
| stale/review 기준 튜닝 | lots, positions, decisions |

## 아직 한계

- 현재 `daily_prices`는 historical 60일 일봉 다운로드가 아니라, 수집 시점의 당일 quote를 일봉처럼 누적하는 방식이다.
- 분봉/호가 데이터가 없으므로 정밀 체결 품질, 스프레드 비용, intraday MDD는 제한적이다.
- `liquidity_snapshots`는 아직 0개이며 future work다.
- 다른 config였으면 실제로 어떤 주문이 체결됐을지까지 완전 재현하려면 더 촘촘한 가격 경로와 호가 데이터가 필요하다.

## 안전 원칙

- market data 저장은 조회 API만 사용해야 하며 KIS 주문 API를 호출하지 않는다.
- export는 DB를 수정하지 않는다.
- 튜닝 결과를 바로 live config에 크게 반영하지 말고 paper/mock 또는 소액 제한 운용으로 검증한다.
- 수익률만 보지 말고 stale LOT, REVIEW_REQUIRED, max capital used, turnover, manual intervention count를 함께 본다.
