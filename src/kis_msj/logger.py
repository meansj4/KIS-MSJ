"""Logging helpers for trading decisions and order lifecycle events."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


def configure_trade_logger(log_path: str | Path) -> logging.Logger:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kis_msj.auto_trader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def log_decision(logger: logging.Logger, **data: Any) -> None:
    parts = [f"{key}={value}" for key, value in data.items()]
    logger.info("decision %s", " ".join(parts))
