"""Lightweight Bot Core loop profiling helpers."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass
class LoopProfile:
    loop_id: int
    symbols_total: int
    loop_interval_seconds: float
    run_id: str
    experiment_name: str
    active_profile: str
    started_at: datetime = field(default_factory=datetime.now)
    started_perf: float = field(default_factory=time.perf_counter)
    status: str = "running"
    symbols_processed: int = 0
    symbols_skipped: int = 0
    symbol_durations_ms: list[float] = field(default_factory=list)
    symbol_codes: list[str] = field(default_factory=list)
    stage_durations_ms: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add_stage(name, (time.perf_counter() - start) * 1000.0)

    def add_stage(self, name: str, duration_ms: float) -> None:
        self.stage_durations_ms[name] = self.stage_durations_ms.get(name, 0.0) + max(0.0, duration_ms)

    def add_symbol(self, code: str, duration_ms: float) -> None:
        self.symbols_processed += 1
        self.symbol_codes.append(code)
        self.symbol_durations_ms.append(max(0.0, duration_ms))

    def finish(self, status: str = "ok") -> dict[str, object]:
        self.status = status
        finished_at = datetime.now()
        duration_ms = max(0.0, (time.perf_counter() - self.started_perf) * 1000.0)
        avg_symbol = sum(self.symbol_durations_ms) / len(self.symbol_durations_ms) if self.symbol_durations_ms else 0.0
        max_symbol = max(self.symbol_durations_ms) if self.symbol_durations_ms else 0.0
        slowest_symbol = ""
        if self.symbol_durations_ms:
            slowest_symbol = self.symbol_codes[self.symbol_durations_ms.index(max_symbol)]
        bottleneck_stage = ""
        if self.stage_durations_ms:
            bottleneck_stage = max(self.stage_durations_ms.items(), key=lambda item: item[1])[0]
        sleep_duration_ms = max(0.0, self.loop_interval_seconds * 1000.0 - duration_ms)
        return {
            "loop_id": self.loop_id,
            "loop_started_at": self.started_at.isoformat(timespec="seconds"),
            "loop_finished_at": finished_at.isoformat(timespec="seconds"),
            "loop_duration_ms": round(duration_ms, 2),
            "symbols_total": self.symbols_total,
            "symbols_processed": self.symbols_processed,
            "symbols_skipped": self.symbols_skipped,
            "avg_symbol_duration_ms": round(avg_symbol, 2),
            "p50_symbol_duration_ms": round(percentile(self.symbol_durations_ms, 0.50), 2),
            "p95_symbol_duration_ms": round(percentile(self.symbol_durations_ms, 0.95), 2),
            "max_symbol_duration_ms": round(max_symbol, 2),
            "slowest_symbol_last_loop": slowest_symbol,
            "quote_fetch_duration_ms": round(self.stage_durations_ms.get("quote_fetch", 0.0), 2),
            "decision_duration_ms": round(self.stage_durations_ms.get("strategy_decision", 0.0), 2),
            "db_duration_ms": round(self.stage_durations_ms.get("db", 0.0), 2),
            "reconciliation_duration_ms": round(
                self.stage_durations_ms.get("startup_reconciliation", 0.0)
                + self.stage_durations_ms.get("open_order_reconciliation", 0.0),
                2,
            ),
            "manual_request_duration_ms": round(self.stage_durations_ms.get("manual_request", 0.0), 2),
            "order_guard_duration_ms": round(self.stage_durations_ms.get("order_guard", 0.0), 2),
            "runtime_control_duration_ms": round(self.stage_durations_ms.get("runtime_control", 0.0), 2),
            "account_sync_duration_ms": round(self.stage_durations_ms.get("account_sync", 0.0), 2),
            "decision_logging_duration_ms": round(self.stage_durations_ms.get("decision_logging", 0.0), 2),
            "lot_manager_duration_ms": round(self.stage_durations_ms.get("lot_manager", 0.0), 2),
            "sleep_duration_ms": round(sleep_duration_ms, 2),
            "loop_interval_seconds": self.loop_interval_seconds,
            "loop_over_interval": duration_ms > self.loop_interval_seconds * 1000.0,
            "bottleneck_stage": bottleneck_stage,
            "active_profile": self.active_profile,
            "run_id": self.run_id,
            "experiment_name": self.experiment_name,
            "status": self.status,
        }


def key_value_line(event: str, payload: dict[str, object]) -> str:
    parts = [event]
    for key, value in payload.items():
        text = str(value).replace(" ", "_")
        parts.append(f"{key}={text}")
    return " ".join(parts)
