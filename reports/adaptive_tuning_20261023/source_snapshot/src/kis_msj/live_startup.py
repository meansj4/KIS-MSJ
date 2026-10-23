"""Process ownership used before opening a live trader's database."""

from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from typing import BinaryIO


class TradingInstanceAlreadyRunning(RuntimeError):
    """Another process owns the trader for this database."""


def _try_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _contended(error: OSError) -> bool:
    return error.errno in {errno.EACCES, errno.EAGAIN} or getattr(error, "winerror", None) in {32, 33, 36}


class FileMutex:
    """Serialize short writes to a shared local file across processes."""

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._handle: BinaryIO | None = None

    def __enter__(self) -> FileMutex:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b", buffering=0)
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                try:
                    _try_lock(handle)
                    break
                except OSError as error:
                    if not _contended(error):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"file_write_lock_timeout path={self.path}") from error
                    time.sleep(0.05)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def __exit__(self, *_exc: object) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                _unlock(handle)
            finally:
                handle.close()


def instance_lock_path(storage_path: str | Path) -> Path:
    database_path = Path(storage_path).resolve()
    return database_path.with_name(f".{database_path.name}.trader.lock")


PREPARATION_PAUSE_REASON = "adaptive_live_preparation"


def resume_preparation_pause(snapshot_bytes: bytes | None, runtime_path: str | Path) -> bool:
    """Release only our preparation pause, without overwriting a later update."""
    from dataclasses import replace
    from datetime import datetime

    from .runtime_control import RuntimeControl, save_runtime_control

    if snapshot_bytes is None:
        return False
    raw = json.loads(snapshot_bytes.decode("utf-8"))
    if raw.get("reason") != PREPARATION_PAUSE_REASON:
        return False
    pause_fields = ("bot_paused", "all_orders_paused", "buy_paused", "sell_paused", "cleanup_paused", "reentry_paused")
    if not all(raw.get(name) is True for name in pause_fields):
        raise RuntimeError("preparation_pause_inconsistent")
    control = RuntimeControl(**{key: raw[key] for key in RuntimeControl.__dataclass_fields__ if key in raw})
    resumed = replace(
        control,
        **{name: False for name in pause_fields},
        reentry_resume_on="",
        expires_at="",
        reason="adaptive_live_ready",
        updated_at=datetime.now().isoformat(timespec="seconds"),
        updated_by="live_ready_launcher",
    )
    save_runtime_control(resumed, runtime_path, expected_bytes=snapshot_bytes)
    return True


def launch_live_trader(argv: list[str] | None = None) -> int:
    """Validate fresh broker state, then enter the ordinary trader loop."""
    import argparse
    import sys

    from .config import DEFAULT_CONFIG_PATH, config_hash, load_config
    from .main import AutoTrader, run_trader
    from .readiness import run_readiness
    from .runtime_control import DEFAULT_RUNTIME_CONTROL_PATH, load_runtime_control

    parser = argparse.ArgumentParser(description="Check live readiness and start the KIS trader.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Check readiness without resuming or running orders.")
    args = parser.parse_args(argv)
    args.mock = False
    try:
        config = load_config(args.config)
        if not config.order.live_trading:
            raise RuntimeError("live_trading_disabled_in_config")
        with TradingInstanceLock(config.storage_path):
            runtime_path = DEFAULT_RUNTIME_CONTROL_PATH
            runtime_before = runtime_path.read_bytes() if runtime_path.exists() else None
            print("실거래 시작 전 계좌·미체결·LOT·목표가를 점검합니다.", flush=True)
            trader = AutoTrader(config)
            result = run_readiness(trader, refresh_volatility=True)
            if not result.ready:
                print("실거래 준비 점검 실패: " + ", ".join(result.reasons), file=sys.stderr, flush=True)
                return 1
            if config_hash(load_config(args.config)) != config_hash(config):
                raise RuntimeError("config_changed_during_preflight")
            if args.check_only:
                print("실거래 준비 점검 통과. 점검 전용 실행을 마칩니다.", flush=True)
                return 0
            resumed = resume_preparation_pause(runtime_before, runtime_path)
            if resumed:
                print("준비 작업용 일시정지를 해제했습니다.", flush=True)
            control = load_runtime_control(runtime_path)
            paused = [label for field, label in (
                ("bot_paused", "봇 전체"), ("all_orders_paused", "모든 주문"),
                ("buy_paused", "매수"), ("sell_paused", "매도"),
                ("cleanup_paused", "정리매도"), ("reentry_paused", "재진입"),
            ) if getattr(control, field)]
            if paused:
                print("점검 통과. 봇을 시작합니다. 현재 일시정지된 기능: " + ", ".join(paused), flush=True)
            else:
                print("점검 통과. 일시정지 없이 자동매매를 시작합니다. 매매 조건에 따라 주문합니다.", flush=True)
            return run_trader(trader, config, args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001
        print(f"실거래 시작 중단: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


class TradingInstanceLock:
    """Hold an OS lock for the lifetime of one trader process.

    The lock file deliberately remains on disk: removing it after unlocking
    could let concurrent processes lock different file objects at that path.
    OS ownership, rather than the recorded PID, decides whether it is in use;
    an interrupted or killed process therefore does not leave a stale lock.
    """

    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.path = instance_lock_path(self.storage_path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> TradingInstanceLock:
        if self._handle is not None:
            raise RuntimeError("trader_instance_lock_already_acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            handle.seek(0)
            try:
                _try_lock(handle)
            except OSError as error:
                if _contended(error):
                    raise TradingInstanceAlreadyRunning(
                        f"trader_already_running storage={self.storage_path} lock={self.path}"
                    ) from error
                raise
            handle.seek(0)
            handle.write(json.dumps({"pid": os.getpid(), "storage": str(self.storage_path)}).encode("utf-8"))
            handle.truncate()
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> TradingInstanceLock:
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()
