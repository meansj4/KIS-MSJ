"""Local order identity is distinct from the broker's reusable order number.

The database is account-scoped. New session orders additionally include date,
venue, symbol and side. Existing orders/fills are never renamed or overwritten.
"""
from datetime import date


def broker_order_id(local_id: str) -> str:
    if local_id.startswith("SESSION:"):
        parts = local_id.split(":")
        if len(parts) != 6 or not parts[-1].isdigit():
            raise ValueError("Invalid session order identity")
        return parts[-1]
    return local_id


def session_order_id(request, broker_id: str, requested_at: str) -> str:
    number = broker_order_id(broker_id)
    if not number.isdigit() or not number.lstrip("0"):
        raise ValueError("Session acknowledgment requires a numeric broker order number")
    day = date.fromisoformat(requested_at[:10]).isoformat()
    return f"SESSION:{day}:{request.venue}:{request.code}:{request.side.value}:{number.lstrip('0')}"
