import json
import logging
from dataclasses import replace
from pathlib import Path

from kis_msj.config import BotConfig, OrderConfig, StockConfig, config_to_dict
from kis_msj.main import AutoTrader
from kis_msj.models import AccountSnapshot
from kis_msj.risk_manager import RiskDecision

from scripts import benchmark_loop


def _config(tmp_path, *, loop_interval_seconds: float = 3.0) -> BotConfig:
    return BotConfig(
        stocks=(StockConfig("005930", "Test"),),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.sqlite3"),
        log_path=str(tmp_path / "trader.log"),
        loop_interval_seconds=loop_interval_seconds,
    )


def test_loop_profile_log_records_duration_and_symbols(tmp_path, monkeypatch, caplog) -> None:
    config = _config(tmp_path)
    trader = AutoTrader(config, use_mock_client=True)
    trader.startup_sync = lambda: AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    trader.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: None

    with caplog.at_level(logging.INFO, logger="kis_msj.lot_auto_trader"):
        trader.run_once()

    summary = trader.last_loop_profile
    assert float(summary["loop_duration_ms"]) >= 0
    assert summary["symbols_processed"] == 1
    assert float(summary["quote_fetch_duration_ms"]) >= 0
    assert any("loop_profile" in record.message for record in caplog.records)


def test_loop_duration_over_interval_logs_warning(tmp_path, caplog) -> None:
    config = _config(tmp_path, loop_interval_seconds=0)
    trader = AutoTrader(config, use_mock_client=True)
    trader.startup_sync = lambda: AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    trader.strategy.decide = lambda position, current_price, snapshot, account_risk, symbol_risk: None

    with caplog.at_level(logging.WARNING, logger="kis_msj.lot_auto_trader"):
        trader.run_once()

    assert trader.last_loop_profile["loop_over_interval"] is True
    assert any("loop_duration_exceeded_interval" in record.message for record in caplog.records)


def test_benchmark_loop_uses_copied_db_and_blocks_order_submission(tmp_path) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_to_dict(config), ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "exports"

    result = benchmark_loop.main(
        [
            "--config",
            str(config_path),
            "--symbols",
            "1",
            "--iterations",
            "1",
            "--mock-quotes",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result == 0
    run_dirs = list(output_dir.glob("loop_benchmark_*"))
    assert run_dirs
    payload = json.loads((run_dirs[0] / "loop_benchmark.json").read_text(encoding="utf-8"))
    assert payload["order_api_calls"] == 0
    assert payload["db_reset"] is False
    assert Path(payload["storage_path"]).parent == run_dirs[0]
