"""Runtime safety switches shared by the trader and local UI."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .models import OrderSide


DEFAULT_RUNTIME_CONTROL_PATH = PROJECT_ROOT / "config" / "runtime_control.json"


@dataclass(frozen=True)
class RuntimeControl:
    all_orders_paused: bool = False
    buy_paused: bool = False
    sell_paused: bool = False
    cleanup_paused: bool = False
    reentry_paused: bool = False
    reason: str = ""
    updated_at: str = ""
    updated_by: str = "local_ui"
    expires_at: str = ""


def load_runtime_control(path: str | Path = DEFAULT_RUNTIME_CONTROL_PATH) -> RuntimeControl:
    control_path = Path(path)
    if not control_path.exists():
        return RuntimeControl()
    raw = json.loads(control_path.read_text(encoding="utf-8"))
    data = {key: raw.get(key) for key in RuntimeControl.__dataclass_fields__ if key in raw}
    control = RuntimeControl(**data)
    if _expired(control):
        return RuntimeControl(updated_at=control.updated_at, updated_by=control.updated_by)
    return control


def save_runtime_control(control: RuntimeControl, path: str | Path = DEFAULT_RUNTIME_CONTROL_PATH) -> None:
    control_path = Path(path)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(control)
    if not data.get("updated_at"):
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temp_path = control_path.with_suffix(control_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, control_path)


def runtime_block_reason(control: RuntimeControl, action: Any) -> str:
    if action is None:
        return ""
    if control.all_orders_paused:
        return "runtime_all_orders_paused"
    side = getattr(action, "side", None)
    if side is OrderSide.BUY and control.buy_paused:
        return "runtime_buy_paused"
    if side is OrderSide.SELL and control.sell_paused:
        return "runtime_sell_paused"
    if getattr(action, "cleanup_flag", False) and control.cleanup_paused:
        return "runtime_cleanup_paused"
    if side is OrderSide.BUY and getattr(action, "reentry_type", "NONE") != "NONE" and control.reentry_paused:
        return "runtime_reentry_paused"
    return ""


def updated_control(**updates: Any) -> RuntimeControl:
    base = asdict(load_runtime_control())
    base.update(updates)
    base["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return RuntimeControl(**{key: base.get(key) for key in RuntimeControl.__dataclass_fields__})


def _expired(control: RuntimeControl) -> bool:
    if not control.expires_at:
        return False
    try:
        return datetime.now() >= datetime.fromisoformat(control.expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return False
