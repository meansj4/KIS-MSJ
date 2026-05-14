# KIS-MSJ

Korea Investment & Securities Open API를 이용한 자동거래 실험용 Python 프로젝트입니다.

첫 단계로 KIS가 제공하는 KOSPI 종목 마스터 파일을 내려받아 개별 종목 정보를 CSV 또는 JSON으로 저장합니다.

## 구조

공식 레퍼런스인 `koreainvestment/open-trading-api`의 `examples_user` 흐름을 따라갑니다.

- `examples_user/domestic_stock/domestic_stock_functions.py`
  - 사용자가 직접 호출할 국내주식 관련 함수 모음
- `examples_user/domestic_stock/domestic_stock_examples.py`
  - 바로 실행해볼 수 있는 예제 스크립트
- `src/kis_msj/kospi_master.py`
  - 테스트 가능한 핵심 파서와 CLI
- `src/kis_msj/upstream.py`
  - 로컬 `open-trading-api` 클론이 upstream과 달라졌는지 확인하는 도구

핵심 로직은 `src/kis_msj`에 두고, 공식 예제와 비교하기 쉬운 얇은 `examples_user` 레이어를 둡니다. 이렇게 하면 공식 repo가 업데이트됐을 때 `examples_user` 변경점을 먼저 확인하고 우리 wrapper에 필요한 부분만 반영하기 쉽습니다.

## 실행 방법

PowerShell에서 프로젝트 루트 기준:

```powershell
$env:PYTHONPATH="src"
python -m kis_msj.kospi_master --output data/kospi_stocks.csv
```

JSON으로 저장:

```powershell
$env:PYTHONPATH="src"
python -m kis_msj.kospi_master --format json --output data/kospi_stocks.json
```

공식 예제 repo 스타일의 entrypoint로 실행:

```powershell
$env:PYTHONPATH="src"
python examples_user/domestic_stock/domestic_stock_examples.py
```

개발 모드로 설치하면 콘솔 명령도 사용할 수 있습니다.

```powershell
pip install -e .
kis-kospi-master --common-stock-only --active-only --output data/kospi_common_active_stocks.csv
```

## Upstream 변경 확인

로컬에 `C:\MSJ\open-trading-api`가 클론되어 있다면:

```powershell
$env:PYTHONPATH="src"
python -m kis_msj.upstream --repo C:\MSJ\open-trading-api
```

원격 최신 정보를 먼저 가져와 비교하려면:

```powershell
$env:PYTHONPATH="src"
python -m kis_msj.upstream --repo C:\MSJ\open-trading-api --fetch
```

개발 모드 설치 후에는:

```powershell
kis-open-trading-api-status --repo C:\MSJ\open-trading-api --fetch
```

이 명령은 `examples_user`, `kis_devlp.yaml`, `pyproject.toml`, `requirements.txt`의 upstream 변경 파일 목록을 보여줍니다.

## 테스트

```powershell
python -m pytest
```
