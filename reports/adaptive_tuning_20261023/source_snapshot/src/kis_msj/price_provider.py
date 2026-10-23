"""Price sampling layer kept separate for a future websocket implementation."""

from __future__ import annotations

import time
from typing import Protocol

from .models import Quote


class QuoteClient(Protocol):
    def quote(self, code: str, *, name: str = "") -> Quote: ...


class PriceSampler:
    def __init__(self, client: QuoteClient, sample_count: int, interval_seconds: float) -> None:
        self.client = client
        self.sample_count = sample_count
        self.interval_seconds = interval_seconds

    def sample(self, code: str, name: str = "") -> tuple[Quote, ...]:
        samples = []
        for index in range(self.sample_count):
            samples.append(self.client.quote(code, name=name))
            if index + 1 < self.sample_count:
                time.sleep(self.interval_seconds)
        return tuple(samples)

    @staticmethod
    def stable(samples: tuple[Quote, ...], max_volatility_pct: float) -> tuple[bool, str]:
        if not samples:
            return False, "current_price_lookup_failed"
        prices = [quote.price for quote in samples]
        low = min(prices)
        high = max(prices)
        if low <= 0:
            return False, "invalid_price_sample"
        volatility = (high - low) / low * 100.0
        if volatility >= max_volatility_pct:
            return False, f"price_volatility_{volatility:.2f}%"
        return True, "stable"
