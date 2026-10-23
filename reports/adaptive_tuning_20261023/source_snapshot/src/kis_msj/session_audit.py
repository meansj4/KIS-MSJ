"""Attribution helpers, never used to make trading decisions."""
from datetime import time


def fill_session(fill, order_venue: str) -> tuple[str, str]:
    if fill.timestamp_source == "order_time":
        return "UNKNOWN", "execution_time_unavailable"
    t = fill.filled_at.time()
    if order_venue == "NXT" and time(8) <= t < time(8, 50):
        session = "NXT_PRE"
    elif time(9) <= t <= time(15, 30):
        session = "REGULAR"
    elif order_venue == "NXT" and time(15, 40) <= t <= time(20):
        session = "NXT_AFTER"
    elif order_venue == "KRX" and time(16) <= t <= time(20):
        session = "KRX_AFTER"
    else:
        session = "UNKNOWN"
    return session, "timestamp_and_direct_order_route" if session != "UNKNOWN" else "unknown_session"
