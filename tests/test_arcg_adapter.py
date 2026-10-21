from dataclasses import replace
from datetime import datetime, timedelta

from kis_msj.arcg import ARCGConfig, Bar, Lot, PendingOrder, Portfolio, SymbolState
from kis_msj.arcg_adapter import MarketEvidence, preview_order
from kis_msj.market_session import NXT_AFTER, REGULAR
from kis_msj.models import OrderSide
from kis_msj.runtime_control import RuntimeControl


def setup(holding=False):
    start = datetime(2024, 1, 1, 15, 30)
    prices = [100.0] * 49 + [102.0]
    bars = [Bar(start + timedelta(days=i), p, p + 1, p - 1, p, 1_000_000) for i, p in enumerate(prices)]
    now = bars[-1].time + timedelta(seconds=1)
    state = SymbolState("005930")
    if holding:
        state = replace(state, cycle_low=70, cycle_budget=1000,
                        lots=(Lot("lot", 10, 70, start, buy_costs=1),), cycle_id=1)
    portfolio = Portfolio(100_000, 90_000, {state.symbol: state}, observed_at=now, session="REGULAR")
    evidence = MarketEvidence(now, True, True, True, state.quantity, prices[-1])
    return bars, now, state, portfolio, evidence


def preview(holding=False, **overrides):
    bars, now, state, portfolio, evidence = setup(holding)
    return preview_order(state.symbol, bars, bars, overrides.get("portfolio", portfolio), state, now,
                         overrides.get("evidence", evidence), control=overrides.get("control", RuntimeControl()),
                         route=overrides.get("route", REGULAR), config=ARCGConfig())


def test_shared_core_builds_limit_buy_without_client():
    result = preview()
    assert result.requests
    assert result.requests[0].side is OrderSide.BUY
    assert not result.requests[0].market_order
    assert result.requests[0].session == "REGULAR"


def test_sell_allocations_translate_to_existing_order_lot_ids():
    result = preview(True)
    assert result.requests[0].side is OrderSide.SELL
    assert result.requests[0].lot_id == "lot"
    assert result.requests[0].quantity == 10


def test_stale_balance_blocks_sells_too():
    bars, now, state, portfolio, evidence = setup(True)
    result = preview(True, portfolio=replace(portfolio, observed_at=now - timedelta(minutes=10)))
    assert result.blocked_reason == "balance_snapshot_stale"
    assert not result.requests


def test_pending_sale_never_funds_buy_and_blocks_duplicate():
    bars, now, state, portfolio, evidence = setup()
    pending = PendingOrder("sale", state.symbol, "SELL", 1, 100)
    result = preview(portfolio=replace(portfolio, pending=(pending,)))
    assert result.blocked_reason == "symbol_order_pending"
    assert not result.requests


def test_balance_query_failure_blocks_preview():
    bars, now, state, portfolio, evidence = setup()
    result = preview(portfolio=replace(portfolio, balance_ok=False))
    assert result.blocked_reason == "broker_state_untrusted"
    assert not result.requests


def test_session_transition_and_stale_venue_quote_block():
    assert preview(route=NXT_AFTER).blocked_reason == "session_changed"
    bars, now, state, portfolio, evidence = setup()
    assert preview(evidence=replace(evidence, quote_time=now-timedelta(seconds=100))).blocked_reason == "quote_stale"


def test_runtime_control_and_confirmed_sellable_quantity():
    assert not preview(control=RuntimeControl(bot_paused=True)).requests
    assert not preview(True, control=RuntimeControl(sell_paused=True)).requests
    bars, now, state, portfolio, evidence = setup(True)
    assert preview(True, evidence=replace(evidence, sellable_quantity=0)).blocked_reason == "broker_sellable_quantity_insufficient"


def test_verified_extended_route_retains_existing_metadata():
    bars, now, state, portfolio, evidence = setup()
    result = preview(portfolio=replace(portfolio, session="NXT_AFTER"), route=NXT_AFTER)
    assert result.requests[0].venue == "NXT"
    assert result.requests[0].quote_market == "NX"
    assert result.requests[0].order_division == "00"
