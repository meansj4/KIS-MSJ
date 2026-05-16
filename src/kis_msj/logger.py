"""Trading log helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


def configure_trade_logger(log_path: str | Path) -> logging.Logger:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kis_msj.lot_auto_trader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def log_decision(logger: logging.Logger, **data: Any) -> None:
    logger.info("decision %s", " ".join(f"{key}={value}" for key, value in data.items()))
