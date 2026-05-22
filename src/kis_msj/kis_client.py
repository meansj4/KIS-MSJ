"""KIS Open API client and offline mock client."""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import KisAccountConfig
from .domestic_quote import fetch_current_quote, get_access_token, is_rate_limit_error, load_credentials
from .models import AccountSnapshot, BalanceItem, OrderRequest, OrderResult, OrderSide, OrderStatus, Quote, TradeFill


ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
ORDER_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
DAILY_FILL_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
OPEN_ORDER_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
HASHKEY_PATH = "/uapi/hashkey"


class KisApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        method: str,
        path: str,
        tr_id: str,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        details = [f"method={method}", f"path={path}", f"tr_id={tr_id}"]
        if status_code is not None:
            details.append(f"status={status_code}")
        if body:
            details.append(f"body={_compact_error_body(body)}")
        super().__init__(f"{message} ({' '.join(details)})")
        self.method = method
        self.path = path
        self.tr_id = tr_id
        self.status_code = status_code
        self.body = body


def _compact_error_body(body: str, limit: int = 500) -> str:
    compact = " ".join(body.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _http_error_body(error: urllib.error.HTTPError) -> str:
    body = error.read()
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _is_transient_http_status(status_code: int | None) -> bool:
    return status_code is not None and (status_code == 429 or 500 <= status_code <= 599)


class KisClient:
    def __init__(self, account_config: KisAccountConfig | None = None) -> None:
        self.credentials = load_credentials()
        self.access_token = get_access_token(self.credentials)
        self.account_config = account_config or KisAccountConfig()
        self.account_number = os.environ.get(self.account_config.account_number_env, "").strip()
        self.account_product_code = os.environ.get(self.account_config.account_product_code_env, "").strip()
        self.consecutive_errors = 0

    @property
    def is_demo(self) -> bool:
        return self.credentials.env == "demo"

    def quote(self, code: str, *, name: str = "") -> Quote:
        row = fetch_current_quote(code, korean_name=name, credentials=self.credentials, access_token=self.access_token)
        return Quote(code, int(row["current_price"]), datetime.now(), name)

    def account_snapshot(self) -> AccountSnapshot:
        self._require_account()
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = self._request("GET", BALANCE_PATH, params=params, tr_id="VTTC8434R" if self.is_demo else "TTTC8434R")
        rows = response.get("output1") or []
        summary = (response.get("output2") or [{}])[0]
        positions = tuple(_balance(row) for row in rows if int(float(row.get("hldg_qty") or 0)) > 0)
        return AccountSnapshot(
            cash_available=int(float(summary.get("dnca_tot_amt") or summary.get("nass_amt") or 0)),
            total_asset=int(float(summary.get("tot_evlu_amt") or summary.get("nass_amt") or 0)),
            total_profit_loss=int(float(summary.get("evlu_pfls_smtl_amt") or 0)),
            daily_profit_loss=int(float(summary.get("thdt_evlu_pfls_amt") or summary.get("thdt_pfls_amt") or 0)),
            positions=positions,
        )

    def place_order(self, request: OrderRequest) -> OrderResult:
        self._require_account()
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": request.code,
            "ORD_DVSN": "01" if request.market_order else "00",
            "ORD_QTY": str(request.quantity),
            "ORD_UNPR": "0" if request.market_order else str(request.limit_price),
        }
        tr_id = ("VTTC0802U" if self.is_demo else "TTTC0802U") if request.side is OrderSide.BUY else ("VTTC0801U" if self.is_demo else "TTTC0801U")
        response = self._request("POST", ORDER_CASH_PATH, body=body, tr_id=tr_id)
        output = response.get("output") or {}
        order_id = str(output.get("ODNO") or "").strip()
        return OrderResult(request, order_id, OrderStatus.REQUESTED if order_id else OrderStatus.REJECTED, str(response.get("msg1", "")))

    def cancel_order(self, order_id: str, quantity: int) -> OrderStatus:
        self._require_account()
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        self._request("POST", ORDER_CANCEL_PATH, body=body, tr_id="VTTC0803U" if self.is_demo else "TTTC0803U")
        return OrderStatus.CANCELED

    def executions(self, *, since: date | None = None) -> tuple[TradeFill, ...]:
        self._require_account()
        start = since or date.today()
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "INQR_STRT_DT": start.strftime("%Y%m%d"),
            "INQR_END_DT": date.today().strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = self._request("GET", DAILY_FILL_PATH, params=params, tr_id="VTTC8001R" if self.is_demo else "TTTC8001R")
        fills = []
        for row in response.get("output1") or []:
            quantity = int(float(row.get("tot_ccld_qty") or row.get("ccld_qty") or 0))
            price = int(float(row.get("avg_prvs") or row.get("ord_unpr") or 0))
            if quantity < 1 or price < 1:
                continue
            side_text = str(row.get("sll_buy_dvsn_cd_name") or row.get("trad_dvsn_name") or "")
            side = OrderSide.SELL if "매도" in side_text or "sell" in side_text.lower() else OrderSide.BUY
            order_id = str(row.get("odno") or "").strip()
            execution_id = _execution_id(row, order_id)
            fills.append(
                TradeFill(
                    str(row.get("pdno") or "").zfill(6),
                    str(row.get("prdt_name") or ""),
                    side,
                    quantity,
                    price,
                    order_id,
                    datetime.now(),
                    execution_id=execution_id,
                )
            )
        return tuple(fills)

    def open_orders(self) -> tuple[dict[str, Any], ...]:
        self._require_account()
        params = {"CANO": self.account_number, "ACNT_PRDT_CD": self.account_product_code, "CTX_AREA_FK100": "", "CTX_AREA_NK100": "", "INQR_DVSN_1": "0", "INQR_DVSN_2": "0"}
        response = self._request("GET", OPEN_ORDER_PATH, params=params, tr_id="VTTC8036R" if self.is_demo else "TTTC8036R")
        return tuple(response.get("output") or [])

    def _request(self, method: str, path: str, *, params: dict[str, str] | None = None, body: dict[str, Any] | None = None, tr_id: str) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.credentials.app_key,
            "appsecret": self.credentials.app_secret,
            "tr_id": tr_id,
            "custtype": self.account_config.customer_type,
        }
        if method == "POST" and body is not None:
            headers["hashkey"] = self._hashkey(body)
        request = urllib.request.Request(f"{self.credentials.base_url}{path}{query}", data=json.dumps(body).encode("utf-8") if body is not None else None, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("rt_cd") != "0":
                    raise KisApiError(
                        f"KIS API failed: {payload.get('msg_cd')} {payload.get('msg1')}",
                        method=method,
                        path=path,
                        tr_id=tr_id,
                        body=json.dumps({"rt_cd": payload.get("rt_cd"), "msg_cd": payload.get("msg_cd"), "msg1": payload.get("msg1")}, ensure_ascii=False),
                    )
                self.consecutive_errors = 0
                return payload
            except urllib.error.HTTPError as error:
                self.consecutive_errors += 1
                error_body = _http_error_body(error)
                wrapped = KisApiError(
                    f"KIS HTTP request failed: {error.reason}",
                    method=method,
                    path=path,
                    tr_id=tr_id,
                    status_code=error.code,
                    body=error_body,
                )
                if attempt >= 2 or not _is_transient_http_status(error.code):
                    raise wrapped from error
                time.sleep(2**attempt)
            except urllib.error.URLError as error:
                self.consecutive_errors += 1
                wrapped = KisApiError(f"KIS network request failed: {error.reason}", method=method, path=path, tr_id=tr_id)
                if attempt >= 2:
                    raise wrapped from error
                time.sleep(2**attempt)
            except KisApiError as error:
                self.consecutive_errors += 1
                if attempt >= 2 or not is_rate_limit_error(error):
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("KIS API failed after retries")

    def _hashkey(self, body: dict[str, Any]) -> str:
        headers = {"Content-Type": "application/json; charset=utf-8", "appkey": self.credentials.app_key, "appsecret": self.credentials.app_secret}
        request = urllib.request.Request(f"{self.credentials.base_url}{HASHKEY_PATH}", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise KisApiError(
                f"KIS HTTP request failed: {error.reason}",
                method="POST",
                path=HASHKEY_PATH,
                tr_id="HASHKEY",
                status_code=error.code,
                body=_http_error_body(error),
            ) from error
        return str(payload.get("HASH") or payload.get("hash") or "")

    def _require_account(self) -> None:
        if not self.account_number or not self.account_product_code:
            raise RuntimeError(f"Missing account environment variables: {self.account_config.account_number_env}, {self.account_config.account_product_code_env}")


class MockKisClient:
    def __init__(self, quote_csv_path: str | Path | None = None, cash_available: int = 10_000_000) -> None:
        self.cash_available = cash_available
        self.consecutive_errors = 0
        self._prices = _load_prices(Path(quote_csv_path)) if quote_csv_path else {}

    def quote(self, code: str, *, name: str = "") -> Quote:
        return Quote(code, self._prices.get(code, 50_000), datetime.now(), name)

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(self.cash_available, self.cash_available, 0, 0, ())

    def place_order(self, request: OrderRequest) -> OrderResult:
        return OrderResult(request, f"MOCK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}", OrderStatus.FILLED)

    def cancel_order(self, order_id: str, quantity: int) -> OrderStatus:
        return OrderStatus.CANCELED

    def executions(self, *, since: date | None = None) -> tuple[TradeFill, ...]:
        return ()

    def open_orders(self) -> tuple[dict[str, Any], ...]:
        return ()


def _balance(row: dict[str, Any]) -> BalanceItem:
    return BalanceItem(str(row.get("pdno") or "").zfill(6), str(row.get("prdt_name") or ""), int(float(row.get("hldg_qty") or 0)), float(row.get("pchs_avg_pric") or 0), int(float(row.get("prpr") or 0)))


def _load_prices(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        prices = {}
        for row in csv.DictReader(input_file):
            code = str(row.get("short_code") or "").zfill(6)
            value = row.get("current_price") or row.get("raw_stck_prpr") or ""
            if code and str(value).strip():
                prices[code] = int(float(value))
        return prices


def _execution_id(row: dict[str, Any], order_id: str) -> str:
    for key in ("exec_no", "ccld_no", "odno_seq", "ord_seq", "orgn_odno", "odno"):
        value = str(row.get(key) or "").strip()
        if value:
            return f"EXEC:{value}" if key != "odno" else ""
    code = str(row.get("pdno") or "").zfill(6)
    quantity = str(row.get("tot_ccld_qty") or row.get("ccld_qty") or "").strip()
    price = str(row.get("avg_prvs") or row.get("ord_unpr") or "").strip()
    time_text = str(row.get("ord_tmd") or row.get("ccld_dtime") or row.get("ord_dt") or "").strip()
    return f"AGG:{order_id}:{code}:{quantity}:{price}:{time_text}" if order_id and code and quantity and price else ""
