"""Verified whole-LOT completion evidence, independent of order/fill counts.

Historical recovery is read-only. An unknown requested BUY quantity or ambiguous
fill attribution never becomes a guessed completed LOT.
"""
from collections import defaultdict
from datetime import datetime
import json

from .models import OrderSide, TradeFill
from .fill_state import actual_fill_events, fill_order_key, _local_time


def completion_event(lot_id: str, side: str, at: str) -> dict:
    identity = json.dumps(["LOT_COMPLETE", side, lot_id], separators=(",", ":"))
    return {"kind": "LOT_COMPLETE", "key": identity, "order": identity,
            "lot": lot_id, "side": side, "at": _local_time(datetime.fromisoformat(at)).isoformat(timespec="microseconds")}


def merge_completions(position, completions) -> None:
    events = actual_fill_events(position)
    by_key = {event["key"]: index for index, event in enumerate(events)}
    for event in completions:
        index = by_key.get(event["key"])
        if index is None:
            by_key[event["key"]] = len(events)
            events.append(event)
        else:
            events[index] = event
    if completions:
        position.actual_fill_events = json.dumps(events, separators=(",", ":"))


def _order_key(row, timestamp):
    return fill_order_key(TradeFill(row["code"], "", OrderSide(row["side"]),
                                   0, 0, row["order_id"], datetime.fromisoformat(timestamp)))


def reconstruct_completions(lots, fills, orders, allocations=(), *, include_progress=False) -> dict[str, list[dict]]:
    """Require matching book quantities and attributable execution evidence.

    BUY completes at its final fill only if the original requested quantity was
    fully bought. SELL completes once the whole LOT is closed, even across many
    partial-sell orders. Closed historical LOTs remain part of the evidence.
    """
    lots = {row["lot_id"]: dict(row) for row in lots}
    by_buy_key, by_buy_time = defaultdict(list), defaultdict(list)
    for lot in lots.values():
        if lot.get("buy_order_key"):
            by_buy_key[lot["buy_order_key"]].append(lot["lot_id"])
        by_buy_time[(lot["code"], _local_time(datetime.fromisoformat(lot["buy_filled_at"])))].append(lot["lot_id"])
    requests = defaultdict(list)
    for order in orders:
        if order.get("requested_at"):
            requests[_order_key(order, order["requested_at"])].append(order)
    attributed = defaultdict(list)
    for row in allocations:
        if row["applied_quantity"] > 0:
            attributed[row["fill_id"]].append((row["lot_id"], row["applied_quantity"]))
    grouped = defaultdict(list)
    for raw in fills:
        row = dict(raw)
        if row["quantity"] <= 0:
            continue
        key = _order_key(row, row["filled_at"])
        links = attributed.get(row["id"], [])
        if not links and row.get("lot_id") in lots:
            links = [(row["lot_id"], row["quantity"])]
        if not links and row["side"] == "BUY":
            candidates = by_buy_key.get(key, []) or by_buy_time.get(
                (row["code"], _local_time(datetime.fromisoformat(row["filled_at"]))), [])
            if len(candidates) == 1:
                links = [(candidates[0], row["quantity"])]
        # Contradictory allocation is not proof of any LOT completion.
        if sum(quantity for _, quantity in links) != row["quantity"]:
            continue
        for lot_id, quantity in links:
            lot = lots.get(lot_id)
            if lot is not None and lot["code"] == row["code"]:
                grouped[(lot_id, row["side"])].append((row, quantity, key))
    result = defaultdict(list)
    for (lot_id, side), executions in grouped.items():
        lot = lots[lot_id]
        total = sum(quantity for _, quantity, _ in executions)
        if total <= 0:
            continue
        complete = total == lot["buy_quantity"]
        if side == "BUY":
            keys = {key for _, _, key in executions}
            matching = requests.get(next(iter(keys)), []) if len(keys) == 1 else []
            complete = complete and len(matching) == 1 and matching[0]["quantity"] == total
        else:
            complete = complete and (lot["remaining_quantity"] == 0 and lot.get("sell_completed")
                                     and lot["status"] == "CLOSED")
        at = max((row["filled_at"] for row, _, _ in executions),
                 key=lambda value: _local_time(datetime.fromisoformat(value)))
        if complete:
            result[lot["code"]].append(completion_event(lot_id, side, at))
        elif include_progress and total == (lot["buy_quantity"] if side == "BUY" else
                                           lot["buy_quantity"] - lot["remaining_quantity"]):
            # Retain the last attributable execution time for an unfinished LOT.
            # A late older final fill must not move completion into an older day.
            progress = completion_event(lot_id, side, at)
            identity = json.dumps(["LOT_PROGRESS", side, lot_id], separators=(",", ":"))
            progress.update(kind="LOT_PROGRESS", key=identity, order=identity)
            result[lot["code"]].append(progress)
    return dict(result)


def read_completion_history(connection, *, include_progress=False) -> dict[str, list[dict]]:
    """Read only existing small ledger tables; never initialize/migrate a DB."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"lots", "fills", "orders"} <= tables:
        return {}
    lots = [dict(row) for row in connection.execute("SELECT * FROM lots")]
    fills = [dict(row) for row in connection.execute(
        "SELECT id, code, side, quantity, order_id, filled_at, lot_id FROM fills")]
    orders = [dict(row) for row in connection.execute(
        "SELECT order_id, code, side, quantity, requested_at FROM orders")]
    allocations = ([dict(row) for row in connection.execute(
        "SELECT fill_id, lot_id, applied_quantity FROM fill_lot_attribution")]
        if "fill_lot_attribution" in tables else [])
    return reconstruct_completions(lots, fills, orders, allocations, include_progress=include_progress)
