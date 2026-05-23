# KIS-MSJ

## Local LOT Bot UI

로컬 관제/제어 UI는 자동매매 루프와 분리되어 있으며, UI 실행만으로 KIS 주문 API를 호출하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

자세한 기능, 안전 정책, runtime control 파일 설명은 [docs/local_ui.md](docs/local_ui.md)를 참고하세요.

한국투자증권(KIS) Open API를 이용한 자동거래 실험용 Python 프로젝트입니다.

## 데이터 수집 코드

- `src/kis_msj/kospi_master.py`
  - KOSPI 종목 마스터 파일을 내려받습니다.
  - 종목코드, 종목명, 상장일, 시가총액, 우선주 여부 같은 정적인 기본 정보를 저장합니다.
  - KIS 인증키 없이 실행됩니다.

- `src/kis_msj/domestic_quote.py`
  - KIS REST API로 현재가와 누적 거래량을 조회합니다.
  - `stck_prpr` 현재가, `acml_vol` 누적 거래량 등을 저장합니다.
  - KIS API 인증키가 필요합니다.

## 1. KOSPI 보통주 목록 저장

VS Code에서 `src/kis_msj/kospi_master.py`를 `Run Python File`로 실행하면 됩니다.

PowerShell에서는:

```powershell
py src\kis_msj\kospi_master.py
```

결과:

```text
data/kospi_common_stocks.csv
```

## 2. 현재가/거래량 조회

먼저 KIS 모의투자 API 키를 설정합니다. 권장 방식은 `.env` 파일을 `C:\MSJ\.env`에 두는 것입니다.

```env
KIS_APP_KEY=내_모의투자_APP_KEY
KIS_APP_SECRET=내_모의투자_APP_SECRET
KIS_ENV=demo
```

PowerShell 환경변수로 직접 설정해도 됩니다.

```powershell
$env:KIS_APP_KEY="내_APP_KEY"
$env:KIS_APP_SECRET="내_APP_SECRET"
$env:KIS_ENV="demo"
```

삼성전자 한 종목만 조회:

```powershell
$env:PYTHONPATH="src"
py -m kis_msj.domestic_quote --code 005930 --name 삼성전자
```

KOSPI 보통주 CSV에 있는 전체 종목을 조회:

```powershell
$env:PYTHONPATH="src"
py -m kis_msj.domestic_quote --sleep 1.0
```

처음에는 API 호출량을 줄이기 위해 일부만 테스트하는 것을 추천합니다.

```powershell
$env:PYTHONPATH="src"
py -m kis_msj.domestic_quote --limit 10 --sleep 0.5
```

결과:

```text
data/kospi_current_quotes.csv
```

## 공식 예제 스타일 wrapper

공식 `koreainvestment/open-trading-api`의 `examples_user` 구조와 비교하기 쉽도록 아래 wrapper를 둡니다.

```text
examples_user/domestic_stock/domestic_stock_functions.py
examples_user/domestic_stock/domestic_stock_examples.py
```

핵심 로직은 `src/kis_msj`에 두고, `examples_user`는 실행 예시와 얇은 연결 계층으로 사용합니다.

## Upstream 변경 확인

로컬에 `C:\MSJ\open-trading-api`가 클론되어 있다면:

```powershell
$env:PYTHONPATH="src"
py -m kis_msj.upstream --repo C:\MSJ\open-trading-api --fetch
```

공식 repo의 `examples_user` 변경점을 빠르게 확인하기 위한 명령입니다.

## 테스트

```powershell
py -m pip install pytest
$env:PYTHONPATH="src"
py -m pytest
```
