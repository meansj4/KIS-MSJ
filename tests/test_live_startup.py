"""Live startup ownership tests never open a broker or production database."""

import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kis_msj.live_startup import (
    FileMutex, TradingInstanceAlreadyRunning, TradingInstanceLock,
    instance_lock_path, launch_live_trader, resume_preparation_pause,
)
from kis_msj.runtime_control import (
    RuntimeControl, RuntimeControlChangedError, load_runtime_control, save_runtime_control,
)


def test_trader_lock_blocks_another_owner_before_database_creation(tmp_path):
    database = tmp_path / "live.sqlite3"
    with TradingInstanceLock(database):
        with pytest.raises(TradingInstanceAlreadyRunning, match="trader_already_running"):
            TradingInstanceLock(database).acquire()
        assert not database.exists()
    with TradingInstanceLock(database):
        assert instance_lock_path(database).is_file()


def test_exception_releases_trader_lock(tmp_path):
    database = tmp_path / "live.sqlite3"
    with pytest.raises(ValueError):
        with TradingInstanceLock(database):
            raise ValueError("startup failed")
    with TradingInstanceLock(database):
        pass


def test_process_exit_releases_trader_lock_without_deleting_file(tmp_path):
    database = tmp_path / "live.sqlite3"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    script = (
        "import os, sys; from kis_msj.live_startup import TradingInstanceLock; "
        "lock = TradingInstanceLock(sys.argv[1]).acquire(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script, str(database)], env=env, check=True, timeout=15)
    assert instance_lock_path(database).exists()
    with TradingInstanceLock(database):
        pass


def test_database_alias_resolves_to_same_lock(tmp_path):
    database = tmp_path / "live.sqlite3"
    assert instance_lock_path(tmp_path / "unused" / ".." / "live.sqlite3") == instance_lock_path(database)


def preparation_pause():
    return RuntimeControl(
        bot_paused=True, all_orders_paused=True, buy_paused=True,
        sell_paused=True, cleanup_paused=True, reentry_paused=True,
        reason="adaptive_live_preparation", updated_by="readiness_preparation",
    )


def test_preparation_pause_is_released_after_unchanged_snapshot(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(preparation_pause(), runtime_path)
    assert resume_preparation_pause(runtime_path.read_bytes(), runtime_path)
    runtime = load_runtime_control(runtime_path)
    assert not any((runtime.bot_paused, runtime.all_orders_paused, runtime.buy_paused,
                    runtime.sell_paused, runtime.cleanup_paused, runtime.reentry_paused))
    assert runtime.reason == "adaptive_live_ready"


def test_preparation_resume_never_overwrites_new_operator_pause(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(preparation_pause(), runtime_path)
    snapshot = runtime_path.read_bytes()
    save_runtime_control(replace(preparation_pause(), reason="operator_stop"), runtime_path)
    newer_bytes = runtime_path.read_bytes()
    with pytest.raises(RuntimeControlChangedError):
        resume_preparation_pause(snapshot, runtime_path)
    assert runtime_path.read_bytes() == newer_bytes


def test_preparation_resume_preserves_unrelated_pause(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(replace(preparation_pause(), reason="operator_stop"), runtime_path)
    snapshot = runtime_path.read_bytes()
    assert not resume_preparation_pause(snapshot, runtime_path)
    assert runtime_path.read_bytes() == snapshot


def test_runtime_cas_is_serialized_with_other_writers(tmp_path):
    lock_path = tmp_path / ".runtime.json.write.lock"
    with FileMutex(lock_path):
        with pytest.raises(TimeoutError, match="file_write_lock_timeout"):
            with FileMutex(lock_path, timeout_seconds=0):
                pytest.fail("another writer acquired the lock")


def test_standard_main_rejects_duplicate_before_trader_initialization(tmp_path, monkeypatch):
    from kis_msj import main as trader_main
    config = SimpleNamespace(storage_path=str(tmp_path / "live.sqlite3"))
    monkeypatch.setattr(trader_main, "load_config", lambda _path: config)

    def forbidden_constructor(*_args, **_kwargs):
        pytest.fail("duplicate startup constructed AutoTrader")

    monkeypatch.setattr(trader_main, "AutoTrader", forbidden_constructor)
    with TradingInstanceLock(config.storage_path):
        assert trader_main.main(["--once"]) == 1


@pytest.fixture
def launcher_setup(tmp_path, monkeypatch):
    from kis_msj import config as config_module
    from kis_msj import main as trader_main
    from kis_msj import readiness
    from kis_msj import runtime_control
    from kis_msj.config import BotConfig, OrderConfig

    config = BotConfig(order=OrderConfig(live_trading=True), storage_path=str(tmp_path / "live.sqlite3"))
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(preparation_pause(), runtime_path)
    monkeypatch.setattr(runtime_control, "DEFAULT_RUNTIME_CONTROL_PATH", runtime_path)
    monkeypatch.setattr(config_module, "load_config", lambda _path: config)
    state = SimpleNamespace(config=config, runtime_path=runtime_path, ran=False, ready=True, during_readiness=None)
    bot = object()

    def constructor(*_args, **_kwargs):
        with pytest.raises(TradingInstanceAlreadyRunning):
            TradingInstanceLock(config.storage_path).acquire()
        return bot

    def check(trader, **_kwargs):
        assert trader is bot
        assert load_runtime_control(runtime_path).all_orders_paused
        if state.during_readiness:
            state.during_readiness()
        return SimpleNamespace(ready=state.ready, reasons=() if state.ready else ("cash_unknown",))

    def loop(trader, loop_config, args):
        assert trader is bot
        assert loop_config is config
        with pytest.raises(TradingInstanceAlreadyRunning):
            TradingInstanceLock(config.storage_path).acquire()
        state.ran = True
        return 0

    monkeypatch.setattr(trader_main, "AutoTrader", constructor)
    monkeypatch.setattr(trader_main, "run_trader", loop)
    monkeypatch.setattr(readiness, "run_readiness", check)
    return state


def test_launcher_checks_before_resume_and_reuses_trader(launcher_setup):
    assert launch_live_trader([]) == 0
    assert launcher_setup.ran
    assert not load_runtime_control(launcher_setup.runtime_path).all_orders_paused


def test_failed_readiness_never_resumes_or_runs(launcher_setup):
    launcher_setup.ready = False
    before = launcher_setup.runtime_path.read_bytes()
    assert launch_live_trader([]) == 1
    assert not launcher_setup.ran
    assert launcher_setup.runtime_path.read_bytes() == before


def test_check_only_never_resumes_or_runs(launcher_setup):
    before = launcher_setup.runtime_path.read_bytes()
    assert launch_live_trader(["--check-only"]) == 0
    assert not launcher_setup.ran
    assert launcher_setup.runtime_path.read_bytes() == before


def test_launcher_preserves_operator_pause_during_readiness(launcher_setup):
    state = launcher_setup
    state.during_readiness = lambda: save_runtime_control(
        replace(preparation_pause(), reason="operator_stop"), state.runtime_path,
    )
    assert launch_live_trader([]) == 1
    assert not state.ran
    assert load_runtime_control(state.runtime_path).reason == "operator_stop"


def test_launcher_rejects_config_changed_during_readiness(launcher_setup, monkeypatch):
    from kis_msj import config as config_module
    state = launcher_setup
    loaded_configs = iter((state.config, replace(state.config, loop_interval_seconds=999)))
    monkeypatch.setattr(config_module, "load_config", lambda _path: next(loaded_configs))
    assert launch_live_trader([]) == 1
    assert not state.ran
    assert load_runtime_control(state.runtime_path).all_orders_paused
