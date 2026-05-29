from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_msj.config import BotConfig
from kis_msj.logger import HourlyKstFileHandler, append_hourly_log_line, hourly_log_path, tail_hourly_logs
from kis_msj.ui_service import UIService


KST = timezone(timedelta(hours=9), "KST")


def test_hourly_log_path_uses_kst_date_and_hour(tmp_path: Path) -> None:
    path = hourly_log_path(tmp_path / "lot_auto_trader.log", datetime(2026, 5, 29, 9, 1, tzinfo=KST))

    assert path == tmp_path / "260529" / "09.log"


def test_hourly_handler_rolls_by_record_without_splitting_multiline(tmp_path: Path) -> None:
    moments = iter(
        (
            datetime(2026, 5, 29, 9, 59, 59, tzinfo=KST),
            datetime(2026, 5, 29, 10, 0, 0, tzinfo=KST),
        )
    )
    logger = logging.getLogger("test.hourly_handler_rolls_by_record")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = HourlyKstFileHandler(tmp_path / "lot_auto_trader.log", time_provider=lambda: next(moments))
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    logger.info("first line\nsecond line")
    logger.info("after rollover")
    handler.close()
    logger.handlers.clear()

    before = (tmp_path / "260529" / "09.log").read_text(encoding="utf-8")
    after = (tmp_path / "260529" / "10.log").read_text(encoding="utf-8")
    assert before == "first line\nsecond line\n"
    assert after == "after rollover\n"


def test_append_hourly_log_line_preserves_existing_legacy_log(tmp_path: Path) -> None:
    legacy = tmp_path / "lot_auto_trader.log"
    legacy.write_text("legacy stays\n", encoding="utf-8")

    append_hourly_log_line(legacy, "new hourly", datetime(2026, 5, 30, 11, 0, tzinfo=KST))

    assert legacy.read_text(encoding="utf-8") == "legacy stays\n"
    assert (tmp_path / "260530" / "11.log").read_text(encoding="utf-8") == "new hourly\n"


def test_tail_hourly_logs_reads_latest_hourly_and_legacy_logs(tmp_path: Path) -> None:
    legacy = tmp_path / "lot_auto_trader.log"
    legacy.write_text("legacy event\n", encoding="utf-8")
    append_hourly_log_line(legacy, "2026-05-29T09:00:00 INFO old event", datetime(2026, 5, 29, 9, 0, tzinfo=KST))
    append_hourly_log_line(legacy, "2026-05-29T10:00:00 ERROR latest event", datetime(2026, 5, 29, 10, 0, tzinfo=KST))

    lines = tail_hourly_logs(legacy, 2)

    assert lines == ["2026-05-29T09:00:00 INFO old event\n", "2026-05-29T10:00:00 ERROR latest event\n"]


def test_ui_logs_tail_filters_new_hourly_logs(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    log_path = tmp_path / "lot_auto_trader.log"
    raw = asdict(BotConfig(storage_path=str(tmp_path / "state.sqlite3"), log_path=str(log_path)))
    config_path.write_text(__import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8")
    append_hourly_log_line(log_path, "2026-05-29T10:00:00 INFO decision code=005930", datetime(2026, 5, 29, 10, 0, tzinfo=KST))
    append_hourly_log_line(log_path, "2026-05-29T10:01:00 ERROR failure token=secret", datetime(2026, 5, 29, 10, 1, tzinfo=KST))

    result = UIService(config_path).logs_tail(limit=10, level="ERROR", keyword="failure")

    assert result["count"] == 1
    assert "failure" in result["lines"][0]
    assert "token=***" in result["lines"][0]
