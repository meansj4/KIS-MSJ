"""Trading log helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

KST = timezone(timedelta(hours=9), "KST")


def _kst_now() -> datetime:
    return datetime.now(KST)


def hourly_log_path(log_path: str | Path, now: datetime | None = None) -> Path:
    base = Path(log_path).parent
    current = now or _kst_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)
    return base / current.strftime("%y%m%d") / f"{current:%H}.log"


class HourlyKstFileHandler(logging.Handler):
    """Write complete logging records into KST date/hour files."""

    terminator = "\n"

    def __init__(self, log_path: str | Path, *, time_provider: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self.log_path = Path(log_path)
        self.time_provider = time_provider or _kst_now
        self.current_path: Path | None = None
        self.stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            path = hourly_log_path(self.log_path, self.time_provider())
            self._open_for(path)
            assert self.stream is not None
            self.stream.write(message + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

    def _open_for(self, path: Path) -> None:
        if self.current_path == path and self.stream is not None:
            return
        if self.stream is not None:
            self.stream.flush()
            self.stream.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a", encoding="utf-8")
        self.current_path = path

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        try:
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
        finally:
            self.stream = None
            self.current_path = None
            super().close()


def configure_trade_logger(log_path: str | Path) -> logging.Logger:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kis_msj.lot_auto_trader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (HourlyKstFileHandler(path), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def log_decision(logger: logging.Logger, **data: Any) -> None:
    logger.info("decision %s", " ".join(f"{key}={value}" for key, value in data.items()))


def append_hourly_log_line(log_path: str | Path, line: str, now: datetime | None = None) -> Path:
    path = hourly_log_path(log_path, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(line.rstrip("\n") + "\n")
    return path


def discover_log_files(log_path: str | Path) -> list[Path]:
    configured = Path(log_path)
    base = configured.parent
    candidates: set[Path] = set()
    if configured.exists():
        candidates.add(configured)
    if base.exists():
        candidates.update(path for path in base.glob("*.log") if path.is_file())
        candidates.update(path for path in base.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/[0-9][0-9].log") if path.is_file())
    return sorted(candidates, key=_log_sort_key)


def tail_hourly_logs(log_path: str | Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    collected: list[str] = []
    for path in reversed(discover_log_files(log_path)):
        if len(collected) >= limit:
            break
        try:
            lines = _tail_lines(path, limit - len(collected))
        except OSError:
            continue
        collected[0:0] = lines
    return collected[-limit:]


def _tail_lines(path: Path, limit: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as input_file:
        lines = input_file.readlines()
    return lines[-limit:]


def _log_sort_key(path: Path) -> tuple[int, str, str, float, str]:
    parent = path.parent.name
    stem = path.stem
    if parent.isdigit() and len(parent) == 6 and stem.isdigit() and len(stem) == 2:
        return (1, parent, stem, path.stat().st_mtime, str(path))
    return (0, "", "", path.stat().st_mtime, str(path))
