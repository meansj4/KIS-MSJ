"""Fetch domestic stock quotes from the KIS Open API."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MASTER_PATH = PROJECT_ROOT / "data" / "kospi_common_stocks.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "kospi_current_quotes.csv"
TOKEN_CACHE_PATH = PROJECT_ROOT / ".kis" / "token.json"
DEFAULT_ENV_PATHS = (
    PROJECT_ROOT / ".env",
    PROJECT_ROOT.parent / ".env",
)

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
DEMO_BASE_URL = "https://openapivts.koreainvestment.com:29443"
QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
QUOTE_TR_ID = "FHKST01010100"
DEFAULT_SLEEP_SECONDS = 0.35
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BASE_SECONDS = 2.0

QUOTE_COLUMNS = (
    "short_code",
    "korean_name",
    "current_price",
    "accumulated_volume",
    "accumulated_trade_amount",
    "open_price",
    "high_price",
    "low_price",
    "previous_day_diff",
    "previous_day_diff_rate",
    "raw_stck_prpr",
    "raw_acml_vol",
)


@dataclass(frozen=True)
class KisCredentials:
    app_key: str
    app_secret: str
    base_url: str
    env: str


def _read_required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def load_dotenv(paths: Sequence[Path] = DEFAULT_ENV_PATHS) -> None:
    """Load simple KEY=VALUE lines from .env files without overwriting env vars."""

    configured_path = os.environ.get("KIS_ENV_FILE", "").strip()
    selected_paths = (Path(configured_path),) if configured_path else paths

    for path in selected_paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_credentials() -> KisCredentials:
    """Load KIS credentials from environment variables."""

    load_dotenv()
    env = os.environ.get("KIS_ENV", "demo").strip().lower()
    base_url = DEMO_BASE_URL if env in {"demo", "paper", "vps"} else REAL_BASE_URL
    return KisCredentials(
        app_key=_read_required_env("KIS_APP_KEY"),
        app_secret=_read_required_env("KIS_APP_SECRET"),
        base_url=os.environ.get("KIS_BASE_URL", base_url).rstrip("/"),
        env="demo" if env in {"demo", "paper", "vps"} else "real",
    )


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"KIS token request failed ({error.code}): {body}") from error


def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"KIS request failed ({error.code}): {body}") from error


def is_rate_limit_error(error: RuntimeError) -> bool:
    """Return True when a KIS error looks like a temporary rate-limit response."""

    message = str(error).lower()
    rate_limit_markers = (
        "too many",
        "rate",
        "limit",
        "초당",
        "거래건수",
        "egw",
        "429",
    )
    return any(marker in message for marker in rate_limit_markers)


def _read_cached_token(cache_key: str, path: Path = TOKEN_CACHE_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(cached["expires_at"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None
    if cached.get("cache_key") != cache_key:
        return None
    if expires_at <= datetime.now() + timedelta(minutes=5):
        return None
    return str(cached["access_token"])


def _write_cached_token(access_token: str, expires_in: int, cache_key: str, path: Path = TOKEN_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    path.write_text(
        json.dumps({"access_token": access_token, "expires_at": expires_at.isoformat(), "cache_key": cache_key}, indent=2) + "\n",
        encoding="utf-8",
    )


def get_access_token(credentials: KisCredentials, *, use_cache: bool = True) -> str:
    """Return a KIS OAuth token, using a local cache when possible."""

    cache_key = f"{credentials.env}:{credentials.base_url}:{credentials.app_key[:8]}"
    if use_cache:
        cached = _read_cached_token(cache_key)
        if cached:
            return cached

    body = {
        "grant_type": "client_credentials",
        "appkey": credentials.app_key,
        "appsecret": credentials.app_secret,
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}
    token_response = _post_json(f"{credentials.base_url}/oauth2/tokenP", body, headers)
    access_token = str(token_response["access_token"])
    expires_in = int(token_response.get("expires_in", 86400))
    _write_cached_token(access_token, expires_in, cache_key)
    return access_token


def to_int(value: str | int | None) -> int | str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return int(text)


def fetch_current_quote(
    short_code: str,
    *,
    korean_name: str = "",
    credentials: KisCredentials | None = None,
    access_token: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
) -> dict[str, str | int | float]:
    """Fetch current price and accumulated volume for one domestic stock."""

    selected_credentials = credentials or load_credentials()
    token = access_token or get_access_token(selected_credentials)
    params = urllib.parse.urlencode({"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": short_code})
    url = f"{selected_credentials.base_url}{QUOTE_PATH}?{params}"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": selected_credentials.app_key,
        "appsecret": selected_credentials.app_secret,
        "tr_id": QUOTE_TR_ID,
        "custtype": "P",
    }
    response: dict[str, Any] = {}
    for attempt in range(max_retries + 1):
        try:
            response = _get_json(url, headers)
            if response.get("rt_cd") == "0":
                break
            raise RuntimeError(f"KIS quote failed for {short_code}: {response.get('msg_cd')} {response.get('msg1')}")
        except RuntimeError as error:
            if attempt >= max_retries or not is_rate_limit_error(error):
                raise
            delay = retry_base_seconds * (2**attempt)
            print(f"rate limit for {short_code}; retrying in {delay:.1f}s...")
            time.sleep(delay)

    output = response.get("output") or {}
    return normalize_quote_output(short_code, korean_name, output)


def normalize_quote_output(short_code: str, korean_name: str, output: dict[str, Any]) -> dict[str, str | int | float]:
    """Convert KIS quote response fields into a compact row."""

    return {
        "short_code": short_code,
        "korean_name": korean_name,
        "current_price": to_int(output.get("stck_prpr")),
        "accumulated_volume": to_int(output.get("acml_vol")),
        "accumulated_trade_amount": to_int(output.get("acml_tr_pbmn")),
        "open_price": to_int(output.get("stck_oprc")),
        "high_price": to_int(output.get("stck_hgpr")),
        "low_price": to_int(output.get("stck_lwpr")),
        "previous_day_diff": to_int(output.get("prdy_vrss")),
        "previous_day_diff_rate": float(output.get("prdy_ctrt") or 0),
        "raw_stck_prpr": str(output.get("stck_prpr", "")),
        "raw_acml_vol": str(output.get("acml_vol", "")),
    }


def read_master_codes(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return [
            {"short_code": row["short_code"], "korean_name": row.get("korean_name", "")}
            for row in csv.DictReader(input_file)
        ]


def write_quote_rows(rows: Iterable[dict[str, str | int | float]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=QUOTE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def fetch_quotes_from_master(
    master_path: Path = DEFAULT_MASTER_PATH,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    limit: int | None = None,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[dict[str, str | int | float]]:
    credentials = load_credentials()
    access_token = get_access_token(credentials)
    stocks = read_master_codes(master_path)
    if limit is not None:
        stocks = stocks[:limit]

    rows: list[dict[str, str | int | float]] = []
    for index, stock in enumerate(stocks, start=1):
        row = fetch_current_quote(
            stock["short_code"],
            korean_name=stock["korean_name"],
            credentials=credentials,
            access_token=access_token,
            max_retries=max_retries,
        )
        rows.append(row)
        print(f"[{index}/{len(stocks)}] {row['short_code']} {row['korean_name']} {row['current_price']}")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    write_quote_rows(rows, output_path)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch current KOSPI stock quotes from the KIS Open API.")
    parser.add_argument("--code", help="Fetch one stock code, e.g. 005930")
    parser.add_argument("--name", default="", help="Optional stock name when --code is used")
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER_PATH, help="Input master CSV path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output quote CSV path")
    parser.add_argument("--limit", type=int, help="Fetch only the first N rows from the master CSV")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS, help="Delay between quote requests")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Retries for temporary rate limits")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.code:
            row = fetch_current_quote(args.code, korean_name=args.name)
            write_quote_rows([row], args.output)
            print(f"saved current quote for {args.code} to {args.output}")
            return 0

        rows = fetch_quotes_from_master(
            args.master,
            output_path=args.output,
            limit=args.limit,
            sleep_seconds=args.sleep,
            max_retries=args.max_retries,
        )
        print(f"saved {len(rows)} current quote rows to {args.output}")
        return 0
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        print('set KIS_APP_KEY and KIS_APP_SECRET first, e.g. $env:KIS_APP_KEY="..."', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
