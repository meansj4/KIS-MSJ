# KIS-MSJ

한국투자증권(KIS) Open API를 활용한 자동거래 로직을 단계적으로 구축하기 위한 Python 프로젝트입니다.
첫 단계로 KIS가 제공하는 KOSPI 종목 마스터 파일을 내려받아 개별종목 정보를 CSV 또는 JSON으로 저장합니다.

## 현재 구현 범위

- KIS 공식 KOSPI 종목 마스터 다운로드
  - `https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip`
- CP949 고정폭(`.mst`) 파일 파싱
- 기본 필터
  - KOSPI 상장 행만 포함
  - ETF/ETN(ETP), ELW 발행, SPAC 제외
  - 옵션으로 우선주 제외, 거래정지/정리매매 제외 가능
- CSV/JSON 저장 CLI

## 사용 방법

```bash
PYTHONPATH=src python -m kis_msj.kospi_master --output data/kospi_stocks.csv
```

JSON으로 저장하려면 다음처럼 실행합니다.

```bash
PYTHONPATH=src python -m kis_msj.kospi_master --format json --output data/kospi_stocks.json
```

보통주만 가져오고 거래정지/정리매매 종목을 제외하려면 다음처럼 실행합니다.

```bash
PYTHONPATH=src python -m kis_msj.kospi_master \
  --common-stock-only \
  --active-only \
  --output data/kospi_common_active_stocks.csv
```

원본 `kospi_code.mst` 파일도 보관하려면 `--keep-master`를 지정합니다.

```bash
PYTHONPATH=src python -m kis_msj.kospi_master \
  --keep-master data/raw \
  --output data/kospi_stocks.csv
```

## 주요 컬럼

| 컬럼 | 설명 |
| --- | --- |
| `short_code` | 종목 단축코드 |
| `standard_code` | 표준코드 |
| `korean_name` | 한글 종목명 |
| `base_price` | 기준가 |
| `listing_date` | 상장일자(YYYYMMDD) |
| `listed_shares` | 상장주수 |
| `market_cap` | 시가총액 |
| `preferred_stock_yn` | 우선주 여부 |
| `trading_halt_yn` | 거래정지 여부 |
| `kospi_yn` | KOSPI 여부 |

전체 컬럼명은 `src/kis_msj/kospi_master.py`의 `OUTPUT_COLUMNS`와 `KOREAN_COLUMN_LABELS`를 참고하세요.

## 테스트

```bash
python -m pytest
```
