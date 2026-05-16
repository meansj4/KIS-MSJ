"""Notification abstraction for alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass


class Notifier:
    def notify(self, title: str, message: str) -> None:
        raise NotImplementedError


@dataclass
class LogNotifier(Notifier):
    logger: logging.Logger

    def notify(self, title: str, message: str) -> None:
        self.logger.warning("notification title=%s message=%s", title, message)
