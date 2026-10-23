"""Periodic watcher for the KIS Open API reference repository."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import UpstreamWatchConfig
from .notifier import Notifier
from .upstream import check_open_trading_api_status


class UpstreamWatcher:
    def __init__(self, config: UpstreamWatchConfig, notifier: Notifier) -> None:
        self.config = config
        self.notifier = notifier
        self.next_check = datetime.min
        self.last_remote_head = ""

    def tick(self) -> None:
        if not self.config.enabled or datetime.now() < self.next_check:
            return
        self.next_check = datetime.now() + timedelta(seconds=self.config.interval_seconds)
        try:
            status = check_open_trading_api_status(Path(self.config.repo_path), fetch=self.config.fetch)
        except (FileNotFoundError, RuntimeError, OSError) as error:
            self.notifier.notify("KIS upstream watch failed", str(error))
            return
        if not self.last_remote_head:
            self.last_remote_head = status.remote_head
        elif status.has_updates and status.remote_head != self.last_remote_head:
            self.last_remote_head = status.remote_head
            changed = ", ".join(status.changed_files) if status.changed_files else "remote commits"
            self.notifier.notify("KIS upstream changed", f"{status.remote_ref} {status.remote_head}: {changed}")
