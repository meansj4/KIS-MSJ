from __future__ import annotations

import io
import logging
import urllib.error
from types import SimpleNamespace

import pytest

from kis_msj.config import KisAccountConfig
from kis_msj.kis_client import BALANCE_PATH, DAILY_FILL_PATH, KisApiError, KisClient
from kis_msj.models import OrderSide


def _client() -> KisClient:
    client = KisClient.__new__(KisClient)
    client.credentials = SimpleNamespace(base_url="https://example.test", app_key="app-key", app_secret="app-secret", env="real")
    client.access_token = "token"
    client.account_config = KisAccountConfig()
    client.consecutive_errors = 0
    client.enable_execution_raw_log = False
    client.logger = logging.getLogger("kis_msj.kis_client")
    client.account_number = "12345678"
    client.account_product_code = "01"
    return client


def _http_error(status_code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.test/uapi",
        code=status_code,
        msg="Internal Server Error",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


def test_kis_http_error_includes_endpoint_details_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def fail_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal attempts
        attempts += 1
        raise _http_error(500, '{"rt_cd":"1","msg_cd":"EGW00123","msg1":"upstream exploded"}')

    monkeypatch.setattr("kis_msj.kis_client.urllib.request.urlopen", fail_urlopen)
    monkeypatch.setattr("kis_msj.kis_client.time.sleep", lambda seconds: None)

    with pytest.raises(KisApiError) as error:
        _client()._request("GET", BALANCE_PATH, params={"CANO": "12345678"}, tr_id="TTTC8434R")

    message = str(error.value)
    assert attempts == 3
    assert "method=GET" in message
    assert f"path={BALANCE_PATH}" in message
    assert "tr_id=TTTC8434R" in message
    assert "status=500" in message
    assert "EGW00123" in message
    assert "12345678" not in message


def test_kis_business_error_includes_endpoint_details(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001, ANN204
            return False

        def read(self) -> bytes:
            return b'{"rt_cd":"1","msg_cd":"APBK0919","msg1":"invalid request"}'

    monkeypatch.setattr("kis_msj.kis_client.urllib.request.urlopen", lambda *args, **kwargs: Response())

    with pytest.raises(KisApiError) as error:
        _client()._request("GET", BALANCE_PATH, tr_id="TTTC8434R")

    message = str(error.value)
    assert f"path={BALANCE_PATH}" in message
    assert "tr_id=TTTC8434R" in message
    assert "APBK0919" in message


def test_executions_logs_masked_raw_fields_when_enabled(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.enable_execution_raw_log = True

    def response(method, path, *, params=None, body=None, tr_id=""):  # noqa: ANN001, ANN202
        assert path == DAILY_FILL_PATH
        return {
            "output1": [
                {
                    "CANO": "12345678",
                    "odno": "000001",
                    "ccld_no": "FILL-1",
                    "ccld_dt": "20260523",
                    "ccld_tmd": "093015",
                    "sll_buy_dvsn_cd": "02",
                    "pdno": "005930",
                    "ccld_qty": "3",
                    "ccld_unpr": "10000",
                }
            ]
        }

    client._request = response

    with caplog.at_level(logging.INFO, logger="kis_msj.kis_client"):
        fills = client.executions()

    assert fills[0].side is OrderSide.BUY
    assert fills[0].filled_at.isoformat() == "2026-05-23T09:30:15"
    message = caplog.messages[0]
    assert "raw_execution_count=1" in message
    assert "has_execution_id=True" in message
    assert "has_filled_at=True" in message
    assert "has_side=True" in message
    assert "has_order_no=True" in message
    assert '"CANO": "***"' in message
    assert '"order_no_field": "odno"' in message
    assert '"execution_id_field": "ccld_no"' in message
    assert '"filled_at_field": "ccld_tmd"' in message
    assert '"side_field": "sll_buy_dvsn_cd"' in message
    assert '"code_field": "pdno"' in message
    assert '"quantity_field": "ccld_qty"' in message
    assert '"price_field": "ccld_unpr"' in message
    assert "12345678" not in message


def test_executions_does_not_log_raw_fields_when_disabled(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.enable_execution_raw_log = False
    client._request = lambda *args, **kwargs: {"output1": [{"CANO": "12345678", "odno": "000001", "ccld_qty": "1", "ccld_unpr": "10000", "pdno": "005930"}]}

    with caplog.at_level(logging.INFO, logger="kis_msj.kis_client"):
        client.executions()

    assert not caplog.messages
