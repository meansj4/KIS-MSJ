from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

from kis_msj.config import KisAccountConfig
from kis_msj.kis_client import BALANCE_PATH, KisApiError, KisClient


def _client() -> KisClient:
    client = KisClient.__new__(KisClient)
    client.credentials = SimpleNamespace(base_url="https://example.test", app_key="app-key", app_secret="app-secret", env="real")
    client.access_token = "token"
    client.account_config = KisAccountConfig()
    client.consecutive_errors = 0
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
