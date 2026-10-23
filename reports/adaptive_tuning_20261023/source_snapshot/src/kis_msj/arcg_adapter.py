"""ARCG preview bridge to existing order models; contains no submit capability.

The unchanged production trader is deliberately not wired to this bridge. A
future executor must refresh broker/session evidence, reserve each accepted
intent in StateBook, and use the existing order reconciliation lifecycle. This
preview makes that integration reviewable without activating a live strategy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .arcg import ARCGConfig, Bar, Evaluation, Portfolio, RiskContext, SymbolState, evaluate, instant
from .lot_manager import ceil_price
from .market_session import KRX_AFTER, NXT_AFTER, NXT_PRE, REGULAR, Route
from .models import OrderRequest, OrderSide, SellReason
from .runtime_control import RuntimeControl


@dataclass(frozen=True)
class MarketEvidence:
    """Facts from a session adapter, never inferred from a local wall clock."""
    quote_time: datetime
    calendar_open: bool
    session_open: bool
    symbol_tradable: bool
    sellable_quantity: int
    quote_price: float


@dataclass(frozen=True)
class OrderPreview:
    evaluation: Evaluation
    requests: tuple[OrderRequest, ...] = ()
    blocked_reason: str = ""


def _floor_price(price: float) -> int:
    value = math.floor(price)
    tick = (1 if value < 1000 else 5 if value < 5000 else 10 if value < 10000 else
            50 if value < 50000 else 100 if value < 100000 else 500 if value < 500000 else 1000)
    return max(tick, math.floor(value / tick) * tick)


def preview_order(
    symbol: str, bars: Sequence[Bar], market_bars: Sequence[Bar],
    portfolio: Portfolio, state: SymbolState, now: datetime, evidence: MarketEvidence,
    *, config: ARCGConfig = ARCGConfig(), model: str = "F", route: Route = REGULAR,
    control: RuntimeControl = RuntimeControl(), risk: RiskContext = RiskContext(), name: str = "",
) -> OrderPreview:
    """Evaluate the shared core then build only limit-order preview objects.

    Multiple sell allocations become separate existing OrderRequest objects.
    They are an ordered preview, not authorisation for simultaneous submission:
    the current OrderManager serializes same-symbol orders. Never net pending
    sale proceeds into ``Portfolio.buyable_cash``. That field must already be
    the broker's cash after its outstanding-buy reservations.
    """
    result = evaluate(symbol, bars, market_bars, portfolio, state, now, config, model, risk=risk)

    def block(reason):
        return OrderPreview(result, (), reason)

    if route not in (REGULAR, NXT_PRE, NXT_AFTER, KRX_AFTER):
        return block("unknown_session_route")
    if portfolio.session.upper() != route.session:
        return block("session_changed")
    if not evidence.calendar_open or not evidence.session_open or not evidence.symbol_tradable:
        return block("session_or_tradability_unverified")
    if control.bot_paused or control.all_orders_paused:
        return block("runtime_all_orders_paused")
    if not portfolio.balance_ok or not portfolio.orders_ok or not portfolio.reconciled:
        return block("broker_state_untrusted")
    if portfolio.observed_at is None:
        return block("balance_timestamp_missing")
    balance_age = (instant(now) - instant(portfolio.observed_at)).total_seconds()
    quote_age = (instant(now) - instant(evidence.quote_time)).total_seconds()
    if not 0 <= balance_age <= config.max_snapshot_age_seconds:
        return block("balance_snapshot_stale")
    if not 0 <= quote_age <= min(90, config.max_snapshot_age_seconds):
        return block("quote_stale")
    if not math.isfinite(evidence.quote_price) or evidence.quote_price <= 0:
        return block("invalid_quote")
    if any(order.symbol == symbol and order.remaining_quantity > 0 for order in portfolio.pending):
        return block("symbol_order_pending")
    action = result.decision
    if action is None:
        return block(result.reason)
    if action.quantity <= 0 or type(action.quantity) is not int:
        return block("invalid_order_quantity")
    if action.side == "BUY":
        if control.buy_paused or (state.quantity == 0 and state.last_exit_at and control.reentry_paused):
            return block("runtime_buy_or_reentry_paused")
        price = _floor_price(action.limit_price)
        required = action.quantity * price * (1 + config.buy_fee_rate + config.slippage_rate)
        if not math.isfinite(portfolio.buyable_cash) or required > portfolio.buyable_cash + 1e-6:
            return block("latest_buyable_cash_insufficient")
        request = OrderRequest(symbol, name, OrderSide.BUY, action.quantity, price, action.reason,
                               session=route.session, venue=route.venue,
                               order_division=route.order_division, quote_market=route.quote_market)
        return OrderPreview(result, (request,))
    if action.side != "SELL":
        return block("unknown_order_side")
    if control.sell_paused:
        return block("runtime_sell_paused")
    if type(evidence.sellable_quantity) is not int or action.quantity > evidence.sellable_quantity:
        return block("broker_sellable_quantity_insufficient")
    holdings = {lot.lot_id: lot.quantity for lot in result.state.lots}
    allocations = action.lot_quantities
    if (not allocations or sum(q for _, q in allocations) != action.quantity or
            len({key for key, _ in allocations}) != len(allocations) or
            any(type(q) is not int or q <= 0 or q > holdings.get(key, 0) for key, q in allocations)):
        return block("invalid_sell_lot_allocation")
    reason = SellReason.RISK_EXIT.value if action.reason.startswith("risk_") else SellReason.PROFIT_TAKE.value
    requests = tuple(OrderRequest(symbol, name, OrderSide.SELL, quantity, ceil_price(action.limit_price),
                                  action.reason, lot_id=lot_id, sell_reason=reason,
                                  session=route.session, venue=route.venue,
                                  order_division=route.order_division, quote_market=route.quote_market)
                     for lot_id, quantity in allocations)
    return OrderPreview(result, requests)
