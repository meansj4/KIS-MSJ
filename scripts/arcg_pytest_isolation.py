"""Offline pytest harness for running the existing suite beside a live setup.

Reproduce with::

    python -m pytest -p scripts.arcg_pytest_isolation tests -q --disable-warnings \
        --continue-on-collection-errors --basetemp tmp/arcg_pytest_isolated

This plugin changes test-process state only. It never changes the operational
runtime-control file. Individual tests may still monkeypatch the trader's
runtime-control function to exercise explicit pause scenarios. External network
connections are denied during collection and execution; loopback UI tests work.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest


def _require_loopback(address) -> None:
    # Non-tuple socket addresses are local IPC (for example AF_UNIX paths).
    if not isinstance(address, tuple) or not address:
        return
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="strict")
    if str(host).lower().rstrip(".") == "localhost":
        return
    try:
        parsed = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        parsed = None
    if parsed is None or not parsed.is_loopback:
        raise OSError("ARCG offline test harness blocked external network")


def pytest_configure(config) -> None:
    """Deny external sockets before pytest starts importing test modules."""
    patcher = pytest.MonkeyPatch()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def connect(sock, address):
        _require_loopback(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        _require_loopback(address)
        return original_connect_ex(sock, address)

    def create_connection(address, *args, **kwargs):
        _require_loopback(address)
        return original_create_connection(address, *args, **kwargs)

    patcher.setattr(socket.socket, "connect", connect)
    patcher.setattr(socket.socket, "connect_ex", connect_ex)
    patcher.setattr(socket, "create_connection", create_connection)
    config._arcg_network_patcher = patcher


def pytest_unconfigure(config) -> None:
    patcher = getattr(config, "_arcg_network_patcher", None)
    if patcher is not None:
        patcher.undo()


@pytest.fixture(autouse=True)
def _arcg_isolate_operational_controls(monkeypatch):
    from kis_msj import cash_rotation
    from kis_msj import main as trader_main
    from kis_msj import runtime_control

    original_runtime = runtime_control.load_runtime_control
    omitted = object()

    def neutral_runtime(path=omitted, now=None):
        # Explicit temporary paths retain their real behavior. Replacing the
        # shared module reference also isolates historical evaluator modules
        # imported dynamically by the existing comparison tests after setup.
        if path is not omitted:
            return original_runtime(path, now=now)
        return runtime_control.RuntimeControl()

    monkeypatch.setattr(runtime_control, "load_runtime_control", neutral_runtime)
    monkeypatch.setattr(trader_main, "load_runtime_control", neutral_runtime)
    monkeypatch.setattr(cash_rotation, "load_runtime_control", neutral_runtime)
