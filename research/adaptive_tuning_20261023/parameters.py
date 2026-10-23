"""Experiment-only numeric knobs. Never writes the operating configuration."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math


RANGES = {
    "buy_gap_pct": (3.0, 5.0), "sell_profit_pct": (2.0, 4.0),
    "rebuy_gap_pct": (3.0, 6.0), "volatility_exponent": (0.25, 0.55),
    "vol20_weight": (0.2, 0.5), "trend_step": (0.1, 0.3),
    "decay_pp": (0.025, 0.15), "reserve_lots": (1, 3),
    "cash_buffer_ratio": (0.05, 0.20),
}


@dataclass(frozen=True)
class Parameters:
    buy_gap_pct: float = 4.0
    sell_profit_pct: float = 3.0
    rebuy_gap_pct: float = 4.0
    volatility_exponent: float = 0.3707491793
    vol20_weight: float = 0.3
    trend_step: float = 0.2
    decay_pp: float = 0.1
    reserve_lots: int = 2
    cash_buffer_ratio: float = 0.10

    def __post_init__(self):
        for name, (low, high) in RANGES.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and in [{low}, {high}]")
        if type(self.reserve_lots) is not int:
            raise ValueError("reserve_lots must be an integer")

    @property
    def vol60_weight(self):
        return 1.0 - self.vol20_weight

    def to_dict(self):
        return asdict(self)

    def changed(self, **kwargs):
        return replace(self, **kwargs)

    def trend_multiplier(self, count):
        return 1.0 + self.trend_step * max(0, min(count, 12) - 1)

    def volatility_multiplier(self, vol20, vol60):
        weighted = self.vol20_weight * vol20 + self.vol60_weight * vol60
        if not math.isfinite(weighted) or weighted < 0:
            return 1.0
        # The 4% normalization, 18.41% reference and 3..10% clamp are fixed.
        # Only AFTER this multiplier does the separate tunable buy gap apply.
        return max(3.0, min(4.0 * (weighted / 18.41) ** self.volatility_exponent, 10.0)) / 4.0


BASELINE = Parameters()
