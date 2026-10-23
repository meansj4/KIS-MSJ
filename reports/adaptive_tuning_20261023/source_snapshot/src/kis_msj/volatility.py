"""Adjusted-close volatility and persistent, completed-session snapshots."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import stdev
from typing import Sequence


KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class VolatilityEstimate:
    vol20: float | None = None
    vol60: float | None = None
    weighted_volatility: float | None = None
    gap_pct: float = 4.0
    multiplier: float = 1.0
    close_count: int = 0
    as_of: str = ""
    reason: str = "insufficient_data"


def volatility_multiplier(weighted_volatility: float) -> float:
    """Apply the volatility-only 3..10 percent gap clamp."""
    if not math.isfinite(weighted_volatility) or weighted_volatility < 0:
        return 1.0
    gap = 4.0 * (weighted_volatility / 18.41) ** 0.3707491793
    return max(3.0, min(gap, 10.0)) / 4.0


def calculate_volatility(closes: Sequence[float]) -> VolatilityEstimate:
    """Use 61 chronological, adjusted, completed daily closes, sample std."""
    count = len(closes)
    if count < 61:
        return VolatilityEstimate(close_count=count)
    try:
        prices = [float(value) for value in closes[-61:]]
    except (TypeError, ValueError, OverflowError):
        return VolatilityEstimate(close_count=count, reason="invalid_closes")
    if any(not math.isfinite(value) or value <= 0 for value in prices):
        return VolatilityEstimate(close_count=count, reason="invalid_closes")
    # Subtract logs to avoid overflow/underflow from dividing extreme inputs.
    returns = [math.log(current) - math.log(previous) for previous, current in zip(prices, prices[1:])]
    vol20 = stdev(returns[-20:]) * math.sqrt(252) * 100
    vol60 = stdev(returns) * math.sqrt(252) * 100
    weighted = vol20 * 0.3 + vol60 * 0.7
    multiplier = volatility_multiplier(weighted)
    return VolatilityEstimate(vol20, vol60, weighted, multiplier * 4.0, multiplier, 61, reason="ok")


def completed_close_cutoff(now: datetime, close_time: str = "15:30") -> date:
    """Never include today's unfinished KRX daily bar; use Korean time."""
    local = now.astimezone(KST).replace(tzinfo=None) if now.tzinfo else now
    cutoff = local.date()
    if local.time() < time.fromisoformat(close_time):
        cutoff -= timedelta(days=1)
    while cutoff.weekday() >= 5:
        cutoff -= timedelta(days=1)
    return cutoff


class DailyVolatilityCache:
    """Freeze a symbol's estimate until another completed daily close is due.

    Cache keys refer to the requested cutoff, including fallback results. This
    prevents an insufficient history or transient error from changing intraday
    thresholds or issuing another historical request on every polling loop.
    Exchange holidays simply return the most recent actual dated KIS daily bar.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") == 1 and isinstance(payload.get("symbols"), dict):
                for code, row in payload["symbols"].items():
                    if not isinstance(row, dict):
                        continue
                    try:
                        date.fromisoformat(row["requested_through"])
                        estimate = VolatilityEstimate(**row["estimate"])
                        if not math.isfinite(estimate.multiplier) or not 0.75 <= estimate.multiplier <= 2.5:
                            continue
                        if not math.isfinite(estimate.gap_pct):
                            continue
                    except (KeyError, TypeError, ValueError):
                        continue
                    self._entries[str(code)] = row
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def get(self, code: str) -> VolatilityEstimate:
        row = self._entries.get(code)
        return VolatilityEstimate(**row["estimate"]) if row else VolatilityEstimate()

    def multiplier(self, code: str) -> float:
        return self.get(code).multiplier

    def refresh(self, code: str, client, now: datetime, close_time: str = "15:30") -> VolatilityEstimate:
        cutoff = completed_close_cutoff(now, close_time)
        previous = self._entries.get(code)
        if previous and previous["requested_through"] == cutoff.isoformat():
            return self.get(code)
        try:
            rows = client.adjusted_daily_closes(code, end_date=cutoff, count=61)
            # Reject future and duplicated bars defensively even for adapters.
            dated = {day: close for day, close in rows if isinstance(day, date) and day <= cutoff}
            days = sorted(dated)[-61:]
            estimate = calculate_volatility([dated[day] for day in days])
            if days:
                estimate = replace(estimate, as_of=days[-1].isoformat())
        except Exception:  # A market-data failure must not prevent reconciliation.
            estimate = VolatilityEstimate(reason="daily_data_unavailable")
        self._entries[code] = {"requested_through": cutoff.isoformat(), "estimate": asdict(estimate)}
        self._save()
        return estimate

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "symbols": self._entries}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, self.path)
