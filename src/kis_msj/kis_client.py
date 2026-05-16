"""KIS Open API client methods used by the automated trading workflow."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import KisAccountConfig
from .domestic_quote import (
    QUOTE_TR_ID,
    fetch_current_quote,
    get_access_token,
    is_rate_limit_error,
    load_credentials,
)
from .models import AccountSnapshot, BalanceItem, OrderRequest, OrderResult, OrderSide, OrderStatus, Quote, TradeFill


ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
ORDER_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
DAILY_FILL_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
OPEN_ORDER_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
HASHKEY_PATH = "/uapi/hashkey"


class KisClient:
    """Small REST wrapper that reuses the existing credential/token flow."""

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
        return Quote(code=code, name=name, price=int(row["current_price"]), timestamp=datetime.now())

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
        output1 = response.get("output1") or []
        output2 = (response.get("output2") or [{}])[0]
        positions = tuple(self._parse_balance_item(item) for item in output1 if int(float(item.get("hldg_qty") or 0)) > 0)
        return AccountSnapshot(
            cash_available=int(float(output2.get("dnca_tot_amt") or output2.get("nass_amt") or 0)),
            total_asset=int(float(output2.get("tot_evlu_amt") or output2.get("nass_amt") or 0)),
            total_evaluation=int(float(output2.get("scts_evlu_amt") or 0)),
            total_profit_loss=int(float(output2.get("evlu_pfls_smtl_amt") or 0)),
            daily_profit_loss=int(float(output2.get("thdt_evlu_pfls_amt") or output2.get("thdt_pfls_amt") or 0)),
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
        if request.side is OrderSide.BUY:
            tr_id = "VTTC0802U" if self.is_demo else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.is_demo else "TTTC0801U"
        response = self._request("POST", ORDER_CASH_PATH, body=body, tr_id=tr_id)
        output = response.get("output") or {}
        order_id = f"{output.get('ODNO', '')}".strip()
        return OrderResult(
            request=request,
            order_id=order_id,
            status=OrderStatus.REQUESTED if order_id else OrderStatus.REJECTED,
            message=str(response.get("msg1", "")),
        )

    def cancel_order(self, order_id: str, code: str, quantity: int) -> OrderStatus:
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
        selected = since or date.today()
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "INQR_STRT_DT": selected.strftime("%Y%m%d"),
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
        rows = response.get("output1") or []
        fills: list[TradeFill] = []
        for row in rows:
            quantity = int(float(row.get("tot_ccld_qty") or row.get("ccld_qty") or 0))
            price = int(float(row.get("avg_prvs") or row.get("ord_unpr") or 0))
            if quantity <= 0 or price <= 0:
                continue
            side_text = str(row.get("sll_buy_dvsn_cd_name") or row.get("trad_dvsn_name") or "")
            side = OrderSide.SELL if "매도" in side_text or "sell" in side_text.lower() else OrderSide.BUY
            fills.append(
                TradeFill(
                    code=str(row.get("pdno") or "").zfill(6),
                    name=str(row.get("prdt_name") or ""),
                    side=side,
                    quantity=quantity,
                    price=price,
                    order_id=str(row.get("odno") or ""),
                    filled_at=datetime.now(),
                )
            )
        return tuple(fills)

    def open_orders(self) -> tuple[dict[str, Any], ...]:
        self._require_account()
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
        }
        response = self._request("GET", OPEN_ORDER_PATH, params=params, tr_id="VTTC8036R" if self.is_demo else "TTTC8036R")
        return tuple(response.get("output") or [])

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        tr_id: str = QUOTE_TR_ID,
    ) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.credentials.base_url}{path}{query}"
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
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("rt_cd") != "0":
                    raise RuntimeError(f"KIS API failed: {payload.get('msg_cd')} {payload.get('msg1')}")
                self.consecutive_errors = 0
                return payload
            except (urllib.error.HTTPError, RuntimeError) as error:
                self.consecutive_errors += 1
                wrapped = RuntimeError(str(error))
                if attempt >= 2 or not is_rate_limit_error(wrapped):
                    raise wrapped from error
                time.sleep(2**attempt)
        raise RuntimeError("KIS API failed after retries")

    def _hashkey(self, body: dict[str, Any]) -> str:
        url = f"{self.credentials.base_url}{HASHKEY_PATH}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "appkey": self.credentials.app_key,
            "appsecret": self.credentials.app_secret,
        }
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload.get("HASH") or payload.get("hash") or "")

    def _require_account(self) -> None:
        if not self.account_number or not self.account_product_code:
            raise RuntimeError(
                f"Missing account environment variables: {self.account_config.account_number_env}, "
                f"{self.account_config.account_product_code_env}"
            )

    @staticmethod
    def _parse_balance_item(item: dict[str, Any]) -> BalanceItem:
        return BalanceItem(
            code=str(item.get("pdno") or "").zfill(6),
            name=str(item.get("prdt_name") or ""),
            quantity=int(float(item.get("hldg_qty") or 0)),
            average_price=float(item.get("pchs_avg_pric") or 0),
            current_price=int(float(item.get("prpr") or 0)),
        )


class MockKisClient:
    """Offline client for paper trading and strategy simulation."""

    def __init__(self, quote_csv_path: str | Path | None = None, cash_available: int = 10_000_000) -> None:
        self.cash_available = cash_available
        self.consecutive_errors = 0
        self._prices = self._load_prices(Path(quote_csv_path)) if quote_csv_path else {}

    def quote(self, code: str, *, name: str = "") -> Quote:
        price = self._prices.get(code, 50_000)
        return Quote(code=code, name=name, price=price, timestamp=datetime.now())

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash_available=self.cash_available,
            total_asset=self.cash_available,
            total_evaluation=0,
            total_profit_loss=0,
            daily_profit_loss=0,
            positions=(),
        )

    def place_order(self, request: OrderRequest) -> OrderResult:
        return OrderResult(request=request, order_id=f"MOCK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}", status=OrderStatus.FILLED)

    def cancel_order(self, order_id: str, code: str, quantity: int) -> OrderStatus:
        return OrderStatus.CANCELED

    def executions(self, *, since: date | None = None) -> tuple[TradeFill, ...]:
        return ()

    def open_orders(self) -> tuple[dict[str, Any], ...]:
        return ()

    @staticmethod
    def _load_prices(path: Path) -> dict[str, int]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8-sig", newline="") as input_file:
            prices: dict[str, int] = {}
            for row in csv.DictReader(input_file):
                code = str(row.get("short_code") or "").zfill(6)
                value = row.get("current_price") or row.get("raw_stck_prpr") or ""
                if code and str(value).strip():
                    prices[code] = int(float(value))
            return prices
