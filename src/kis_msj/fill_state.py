"""Persistent, execution-based symbol streaks and post-sale restrictions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .models import OrderSide, PositionState, TradeFill


KOREA = timezone(timedelta(hours=9))


def fill_order_key(fill: TradeFill) -> str:
    """Broker numbers are reused daily; one split order remains one trade."""
    day = _local_time(fill.filled_at).date().isoformat()
    identity = fill.order_id.strip() or fill.lot_id or fill.execution_id
    if identity.isdigit():
        identity = identity.lstrip("0") or "0"
    if not identity:
        identity = [_local_time(fill.filled_at).isoformat(timespec="microseconds"), fill.quantity, fill.price]
    return json.dumps([day, fill.code, fill.side.value, identity], separators=(",", ":"))


def actual_fill_key(fill: TradeFill) -> str:
    if fill.execution_id:
        identity = [fill_order_key(fill), fill.execution_id]
    else:
        identity = [fill_order_key(fill), fill.lot_id, fill.quantity, fill.price,
                    _local_time(fill.filled_at).isoformat(timespec="microseconds")]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def actual_fill_events(position: PositionState) -> list[dict]:
    events = json.loads(position.actual_fill_events or "[]")
    if not isinstance(events, list):
        raise ValueError("Invalid persisted actual-fill ledger")
    return events


def fill_already_applied(position: PositionState, fill: TradeFill) -> bool:
    key = actual_fill_key(fill)
    return any(event["key"] == key and event.get("applied", True) for event in actual_fill_events(position))


def record_actual_fill_state(position: PositionState, fill: TradeFill, *, applied: bool = True) -> bool:
    """Rebuild chronology from actual fills; ties retain persisted arrival order.

    An order contributes to the direction streak at its first real execution.
    All executions contribute to the latest-sale price and rebuy restriction.
    """
    events = actual_fill_events(position)
    key = actual_fill_key(fill)
    existing = next((event for event in events if event["key"] == key), None)
    if existing is not None:
        if not applied or existing.get("applied", True):
            return False
        existing["applied"] = True
    else:
        events.append({"key": key, "order": fill_order_key(fill), "side": fill.side.value,
                       "at": _local_time(fill.filled_at).isoformat(timespec="microseconds"),
                       "price": fill.price, "applied": applied})
    seen_orders = set()
    buys = sells = 0
    for _, event in sorted(enumerate(events), key=lambda pair: (pair[1]["at"], pair[0])):
        if event["order"] not in seen_orders:
            if event["side"] == OrderSide.BUY.value:
                buys, sells = buys + 1, 0
            else:
                buys, sells = 0, sells + 1
            seen_orders.add(event["order"])
        position.rebuy_restriction_active = event["side"] == OrderSide.SELL.value
        if event["side"] == OrderSide.SELL.value:
            position.last_sell_price = event["price"]
    position.consecutive_buy_count = buys
    position.consecutive_sell_count = sells
    position.actual_fill_events = json.dumps(events, separators=(",", ":"))
    return True


def _local_time(value: datetime) -> datetime:
    return value.replace(tzinfo=KOREA) if value.tzinfo is None else value.astimezone(KOREA)
