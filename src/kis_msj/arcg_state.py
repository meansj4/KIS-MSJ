"""Crash-safe, execution-idempotent ARCG state and reservation journal.

This is an isolated JSON book for simulations or a future explicitly selected
adapter. It never reads production credentials, SQLite files, or broker APIs.
One process owns a book; multi-process writers require an external lock.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

from .arcg import ARCGConfig, Fill, Lot, PendingOrder, SymbolState, apply_fill, instant, lock_cycle_budget


class StateIntegrityError(ValueError):
    """Ambiguous persisted, broker or execution facts; buys must remain blocked."""


def _jsonable(value):
    if isinstance(value, datetime):
        return instant(value).isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _signature(fill: Fill) -> dict:
    return _jsonable(asdict(fill))


def _state_from_json(data: dict) -> SymbolState:
    values = dict(data)
    for name in ("last_observation", "last_buy_at", "last_exit_at", "last_signal_at", "last_review_at", "last_market_observation"):
        if values.get(name) is not None:
            values[name] = instant(values[name])
    lots = []
    for item in values.get("lots", []):
        lot = dict(item)
        lot["opened_at"] = instant(lot["opened_at"])
        lots.append(Lot(**lot))
    values["lots"] = tuple(lots)
    return SymbolState(**values)


def _pending_from_json(data: dict) -> PendingOrder:
    values = dict(data)
    values["lot_quantities"] = tuple((key, quantity) for key, quantity in values.get("lot_quantities", []))
    return PendingOrder(**values)


def _validate_state(state: SymbolState) -> None:
    if not state.symbol or not math.isfinite(state.cycle_budget) or state.cycle_budget < 0:
        raise StateIntegrityError("invalid symbol or cycle budget")
    if state.cycle_id < 0 or type(state.cycle_id) is not int:
        raise StateIntegrityError("invalid cycle id")
    if state.cycle_low is not None and (not math.isfinite(state.cycle_low) or state.cycle_low <= 0):
        raise StateIntegrityError("invalid cycle low")
    if state.cycle_last_buy is not None and (not math.isfinite(state.cycle_last_buy) or state.cycle_last_buy <= 0):
        raise StateIntegrityError("invalid cycle last buy")
    ids = set()
    for lot in state.lots:
        if not lot.lot_id or lot.lot_id in ids or type(lot.quantity) is not int or lot.quantity <= 0 or not math.isfinite(lot.buy_price) or lot.buy_price <= 0 or not math.isfinite(lot.buy_costs) or lot.buy_costs < 0:
            raise StateIntegrityError("invalid or duplicate lot")
        if lot.role not in {"rotation", "trend"} or not math.isfinite(lot.peak_price) or lot.peak_price < 0:
            raise StateIntegrityError("invalid lot role/peak")
        instant(lot.opened_at)
        ids.add(lot.lot_id)
    if state.quantity and (state.cycle_low is None or state.cycle_budget <= 0):
        raise StateIntegrityError("held position missing its cycle history/budget")
    if state.cycle_low is not None and any(lot.buy_price < state.cycle_low - 1e-8 for lot in state.lots):
        raise StateIntegrityError("cycle low is higher than a confirmed lot purchase")


def _validate_pending(order: PendingOrder) -> None:
    if not order.order_id or not order.symbol or order.side not in {"BUY", "SELL"} or type(order.quantity) is not int or order.quantity <= 0 or type(order.filled_quantity) is not int or not 0 <= order.filled_quantity < order.quantity:
        raise StateIntegrityError("invalid pending order")
    if not math.isfinite(order.limit_price) or order.limit_price <= 0 or not 0 <= order.trend_fraction <= 1:
        raise StateIntegrityError("invalid order limit/role fraction")
    if order.side == "SELL" and order.lot_quantities and (sum(qty for _, qty in order.lot_quantities) != order.quantity or len({key for key, _ in order.lot_quantities}) != len(order.lot_quantities)):
        raise StateIntegrityError("invalid pending sell allocation")


def _partial_allocations(allocations: tuple[tuple[str, int], ...], already_filled: int, quantity: int) -> tuple[tuple[str, int], ...]:
    result = []
    skip = already_filled
    need = quantity
    for key, size in allocations:
        skipped = min(size, skip)
        skip -= skipped
        available = size - skipped
        take = min(available, need)
        if take:
            result.append((key, take))
            need -= take
    if skip or need:
        raise StateIntegrityError("partial execution exceeds original lot allocations")
    return tuple(result)


class StateBook:
    """Atomic snapshot including positions, pending reservations and fill IDs.

    Mutations write and fsync a temporary file, atomically replace the target,
    then publish the new in-memory snapshot. A failed write leaves the previous
    in-memory state intact. Callers must retain exclusive ownership of the path.
    """

    schema_version = 1

    def __init__(self, path: str | Path, states: Mapping[str, SymbolState] | None = None, *, config: ARCGConfig = ARCGConfig()):
        self.path = Path(path)
        self.config = config
        self.states: dict[str, SymbolState] = {}
        self.pending: dict[str, PendingOrder] = {}
        self.executions: dict[str, dict] = {}
        if self.path.exists():
            if states:
                raise StateIntegrityError("cannot seed an existing state book")
            self._load()
        elif states:
            self._commit(dict(states), {}, {})

    @property
    def positions(self) -> Mapping[str, SymbolState]:
        return dict(self.states)

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != self.schema_version:
                raise StateIntegrityError("unsupported or missing journal schema")
            states = {key: _state_from_json(value) for key, value in data["states"].items()}
            pending = {key: _pending_from_json(value) for key, value in data["pending"].items()}
            executions = data["executions"]
            self._validate_snapshot(states, pending, executions)
            self.states, self.pending, self.executions = states, pending, executions
        except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
            raise StateIntegrityError(f"state journal cannot be trusted: {exc}") from exc

    @staticmethod
    def _validate_snapshot(states: dict, pending: dict, executions: dict) -> None:
        if not isinstance(executions, dict):
            raise StateIntegrityError("execution journal must be a mapping")
        for key, state in states.items():
            if key != state.symbol:
                raise StateIntegrityError("state key mismatch")
            _validate_state(state)
        for key, order in pending.items():
            if key != order.order_id or order.symbol not in states:
                raise StateIntegrityError("pending order has no matching symbol state")
            _validate_pending(order)
        for key, signature in executions.items():
            if not isinstance(signature, dict) or signature.get("execution_id") != key:
                raise StateIntegrityError("execution signature key mismatch")
            values = dict(signature)
            values["time"] = instant(values["time"])
            values["lot_quantities"] = tuple(tuple(item) for item in values.get("lot_quantities", []))
            fill = Fill(**values)
            if fill.side not in {"BUY", "SELL"} or fill.quantity <= 0 or not math.isfinite(fill.price) or fill.price <= 0 or fill.symbol not in states:
                raise StateIntegrityError("invalid execution signature")

    def _commit(self, states: dict, pending: dict, executions: dict) -> None:
        self._validate_snapshot(states, pending, executions)
        document = {"schema_version": self.schema_version,
                    "states": {key: _jsonable(asdict(value)) for key, value in states.items()},
                    "pending": {key: _jsonable(asdict(value)) for key, value in pending.items()},
                    "executions": executions}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(document, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.states, self.pending, self.executions = dict(states), dict(pending), dict(executions)

    def save_state(self, state: SymbolState) -> None:
        """Persist observations/peaks without allowing accidental fill rewrites."""
        previous = self.states.get(state.symbol)
        if previous is not None:
            for name in ("cycle_id", "cycle_low", "cycle_last_buy", "cycle_budget", "last_buy_at", "last_exit_at"):
                if getattr(previous, name) != getattr(state, name):
                    raise StateIntegrityError("fill/budget fields require a journal transition")
            previous_lots = [(lot.lot_id, lot.quantity, lot.buy_price, lot.opened_at, lot.role, lot.buy_costs) for lot in previous.lots]
            next_lots = [(lot.lot_id, lot.quantity, lot.buy_price, lot.opened_at, lot.role, lot.buy_costs) for lot in state.lots]
            if previous_lots != next_lots or any(after.peak_price < before.peak_price for before, after in zip(previous.lots, state.lots)):
                raise StateIntegrityError("observations cannot rewrite lots or decrease peaks")
        states = dict(self.states)
        states[state.symbol] = state
        self._commit(states, self.pending, self.executions)

    def reserve_order(self, order: PendingOrder) -> None:
        _validate_pending(order)
        if order.filled_quantity:
            raise StateIntegrityError("new reservations must start unfilled")
        if order.order_id in self.pending:
            if order == self.pending[order.order_id]:
                return
            raise StateIntegrityError("order id reused with different facts")
        if any(signature.get("order_id") == order.order_id for signature in self.executions.values()):
            raise StateIntegrityError("completed order id cannot be reused")
        if any(item.symbol == order.symbol for item in self.pending.values()):
            raise StateIntegrityError("only one outstanding order per symbol is permitted")
        state = self.states.get(order.symbol, SymbolState(order.symbol, sector=order.sector))
        if not state.history_complete:
            raise StateIntegrityError("incomplete history blocks reservations")
        if state.sector != order.sector:
            raise StateIntegrityError("order/state sector mismatch")
        if order.side == "BUY":
            if order.cycle_budget <= 0:
                raise StateIntegrityError("buy reservation requires an approved cycle budget")
            state = lock_cycle_budget(state, order.cycle_budget)
            worst_cost = order.quantity * order.limit_price * (1 + self.config.buy_fee_rate + self.config.slippage_rate)
            if state.invested + worst_cost > state.cycle_budget + 1e-6:
                raise StateIntegrityError("order exceeds locked cycle budget")
        elif order.quantity > state.quantity:
            raise StateIntegrityError("sell reservation exceeds confirmed shares")
        elif order.lot_quantities:
            by_id = {lot.lot_id: lot.quantity for lot in state.lots}
            if any(qty <= 0 or qty > by_id.get(key, 0) for key, qty in order.lot_quantities):
                raise StateIntegrityError("sell reservation exceeds confirmed lot")
        states, pending = dict(self.states), dict(self.pending)
        states[state.symbol], pending[order.order_id] = state, order
        self._commit(states, pending, self.executions)

    def cancel_order(self, order_id: str, *, confirmed: bool = True) -> None:
        """Release only a broker-confirmed terminal remainder; no synthetic fill."""
        if not confirmed:
            raise StateIntegrityError("unconfirmed cancellation must retain its reservation")
        if order_id not in self.pending:
            return
        order = self.pending[order_id]
        states, pending = dict(self.states), dict(self.pending)
        del pending[order_id]
        state = states[order.symbol]
        if order.side == "BUY" and state.quantity == 0 and order.filled_quantity == 0:
            # No actual execution ever started this allocation; release it.
            states[order.symbol] = replace(state, cycle_budget=0.0)
        self._commit(states, pending, self.executions)

    def apply_fill(self, fill: Fill) -> SymbolState:
        signature = _signature(fill)
        if fill.execution_id in self.executions:
            if self.executions[fill.execution_id] != signature:
                raise StateIntegrityError("execution id reused with different facts")
            return self.states[fill.symbol]
        order = self.pending.get(fill.order_id)
        if order is None or order.symbol != fill.symbol or order.side != fill.side:
            raise StateIntegrityError("execution has no matching reserved order")
        if fill.quantity > order.remaining_quantity:
            raise StateIntegrityError("execution exceeds outstanding quantity")
        if (fill.side == "BUY" and fill.price > order.limit_price + 1e-8) or (fill.side == "SELL" and fill.price < order.limit_price - 1e-8):
            raise StateIntegrityError("execution violates reserved limit price")
        transition = fill
        if fill.side == "BUY":
            # Allocate integer roles over the original order, so one-share
            # partial executions do not erase the intended trend tranche.
            trend_quantity = math.floor((order.filled_quantity + fill.quantity) * order.trend_fraction) - math.floor(order.filled_quantity * order.trend_fraction)
            transition = replace(fill, trend_fraction=order.trend_fraction, trend_quantity=trend_quantity)
        elif order.lot_quantities:
            expected = _partial_allocations(order.lot_quantities, order.filled_quantity, fill.quantity)
            if fill.lot_quantities and fill.lot_quantities != expected:
                raise StateIntegrityError("execution lot allocation differs from reservation")
            transition = replace(fill, lot_quantities=expected)
        try:
            updated = apply_fill(self.states[fill.symbol], transition)
        except ValueError as exc:
            raise StateIntegrityError(str(exc)) from exc
        states, pending, executions = dict(self.states), dict(self.pending), dict(self.executions)
        states[fill.symbol] = updated
        if order.filled_quantity + fill.quantity == order.quantity:
            del pending[order.order_id]
        else:
            pending[order.order_id] = replace(order, filled_quantity=order.filled_quantity + fill.quantity)
        executions[fill.execution_id] = signature
        self._commit(states, pending, executions)
        return updated

    def reconcile(self, quantities: Mapping[str, int], *, complete_snapshot: bool = True) -> bool:
        """Quantity mismatch persists a fail-closed flag; matching cannot heal it.

        A matching quantity proves neither a cycle low nor sold-lot history.
        Only explicit complete history replay can clear an ambiguous state.
        """
        if any(type(quantity) is not int or quantity < 0 for quantity in quantities.values()):
            raise StateIntegrityError("invalid broker quantities")
        states = dict(self.states)
        matched = complete_snapshot
        for symbol in set(states) | set(quantities):
            state = states.get(symbol, SymbolState(symbol, history_complete=False))
            same = complete_snapshot and state.quantity == quantities.get(symbol, 0)
            if not same:
                matched = False
                state = replace(state, history_complete=False)
            if not state.history_complete:
                matched = False
            states[symbol] = state
        self._commit(states, self.pending, self.executions)
        return matched

    def recover_history(self, symbol: str, fills: Sequence[Fill], cycle_budgets: Mapping[str, float], broker_quantity: int, *, sector: str = "UNKNOWN", complete_history: bool = False) -> SymbolState:
        """Replay complete flat-start history, explicitly proving the final quantity.

        cycle_budgets maps each first-buy order ID to its approved cycle budget.
        This method never guesses the missing lowest price from remaining lots.
        """
        if not complete_history or type(broker_quantity) is not int or broker_quantity < 0:
            raise StateIntegrityError("complete flat-start history and broker quantity are required")
        if any(order.symbol == symbol for order in self.pending.values()):
            raise StateIntegrityError("recovery requires reconciled terminal orders")
        state = SymbolState(symbol, sector=sector)
        signatures = {}
        prior_time = None
        try:
            for fill in fills:
                if fill.symbol != symbol:
                    raise StateIntegrityError("mixed symbols in recovery history")
                stamp = instant(fill.time)
                if prior_time is not None and stamp < prior_time:
                    raise StateIntegrityError("history is not in actual execution order")
                prior_time = stamp
                signature = _signature(fill)
                if fill.execution_id in signatures:
                    if signatures[fill.execution_id] != signature:
                        raise StateIntegrityError("ambiguous duplicate execution in recovery")
                    continue
                if fill.side == "BUY" and state.quantity == 0:
                    if fill.order_id not in cycle_budgets:
                        raise StateIntegrityError("recovery missing approved cycle budget")
                    state = lock_cycle_budget(state, cycle_budgets[fill.order_id])
                state = apply_fill(state, fill)
                signatures[fill.execution_id] = signature
        except ValueError as exc:
            raise StateIntegrityError(str(exc)) from exc
        if state.quantity != broker_quantity:
            raise StateIntegrityError("recovered history does not match broker position")
        states = dict(self.states)
        states[symbol] = state
        executions = {key: value for key, value in self.executions.items() if value.get("symbol") != symbol}
        for key, signature in signatures.items():
            if key in executions:
                raise StateIntegrityError("execution id collision with another symbol")
            executions[key] = signature
        self._commit(states, self.pending, executions)
        return state
