"""Offline ARCG safety and state-transition checks; no broker or live storage."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from kis_msj.arcg import (
    ARCGConfig, Bar, Features, Fill, Lot, PendingOrder, Portfolio, RiskContext,
    SymbolState, adaptive_gap, apply_fill, capital_limit, cash_pools,
    compute_features, evaluate, feature_cache_key, lock_cycle_budget, net_profit,
    reference_price,
)
from kis_msj.arcg_state import StateBook, StateIntegrityError


NOW = datetime(2024, 6, 3, 7, tzinfo=timezone.utc)


@pytest.fixture
def config():
    return ARCGConfig(min_price=1, min_average_turnover=0, buy_fee_rate=0,
                      sell_fee_rate=0, sell_tax_rate=0, slippage_rate=0,
                      buy_interval_seconds=86400, confirmation_bars=1)


def position(symbol="AAA", *, price=100.0, quantity=10, budget=10000.0, at=NOW - timedelta(days=2)):
    state = lock_cycle_budget(SymbolState(symbol, sector="TECH"), budget)
    return apply_fill(state, Fill("initial-" + symbol, "initial-order-" + symbol, symbol, "BUY", quantity, price, at))


def context(state, *, now=NOW, cash=100000.0, equity=100000.0, **kwargs):
    return Portfolio(equity, cash, {state.symbol: state}, observed_at=now, **kwargs)


def features(*, at=NOW, close=100.0, regime="RANGE", market="NEUTRAL", entry=True, stabilized=True, atr_fraction=0.01):
    return Features(close=close, atr=close * atr_fraction, atr_fraction=atr_fraction,
                    fast_ma=close, slow_ma=close, average_turnover=100000000,
                    raw_symbol_regime=regime, raw_market_regime=market,
                    entry_signal=entry, stabilized=stabilized, bar_time=at, ready=True)


def evaluation(state, config, *, at=NOW, close=100.0, f=None, mf=None, portfolio=None, risk=RiskContext(), model="F", high=None):
    bar = Bar(at, close, high or close, close, close, 1000000)
    return evaluate(state.symbol, [bar], [bar], portfolio or context(state, now=at), state, at, config, model,
                    features=f or features(at=at, close=close),
                    market_features=mf or features(at=at, close=close), risk=risk)


def test_features_trailing_only_and_classification(config):
    bars = [Bar(NOW + timedelta(days=i), 100 + i, 101 + i, 99 + i, 100 + i, 100000) for i in range(60)]
    upward = compute_features(bars[:50], config)
    assert upward.ready and upward.raw_symbol_regime == "UPTREND"
    assert upward.raw_market_regime == "RISK_ON"
    assert upward.fast_ma == pytest.approx(sum(range(140, 150)) / 10)
    downward = compute_features([replace(bar, open=200 - i, high=201 - i, low=199 - i, close=200 - i) for i, bar in enumerate(bars)], config)
    assert downward.raw_symbol_regime == "DOWNTREND"
    assert downward.raw_market_regime == "RISK_OFF"
    assert compute_features(bars[:10], config).ready is False
    assert feature_cache_key(config) == feature_cache_key(replace(config, trend_fraction=0.8, atr_gap_multiplier=2))
    assert feature_cache_key(config) != feature_cache_key(replace(config, fast_period=8))


@pytest.mark.parametrize("period", [0, 1.5, True])
def test_invalid_periods_rejected(period):
    with pytest.raises(ValueError):
        ARCGConfig(fast_period=period)


@pytest.mark.parametrize("kind", ["future", "incomplete", "duplicate", "invalid_price"])
def test_invalid_observation_is_rejected(config, kind):
    bar = Bar(NOW, 100, 100, 100, 100, 10000)
    bars = [bar]
    if kind == "future":
        bars = [replace(bar, time=NOW + timedelta(seconds=1))]
    elif kind == "incomplete":
        bars = [replace(bar, completed=False)]
    elif kind == "duplicate":
        bars = [bar, bar]
    else:
        bars = [replace(bar, close=float("nan"))]
    state = SymbolState("AAA")
    with pytest.raises(ValueError):
        evaluate("AAA", bars, [bar], context(state), state, NOW, config)


def test_entry_needs_observed_signal_not_watchlist(config):
    state = SymbolState("AAA", sector="TECH")
    result = evaluation(state, config, f=features(entry=False))
    assert result.decision is None and result.reason == "entry_signal_absent"
    result = evaluation(state, config)
    assert result.decision.side == "BUY"
    assert result.decision.cycle_budget == 10000
    assert result.decision.quantity == 15
    assert result.state.cycle_budget == 0  # intent does not lock until accepted


def test_same_completed_bar_neither_confirms_twice_nor_duplicates(config):
    config = replace(config, confirmation_bars=2)
    state = SymbolState("AAA", sector="TECH")
    f = features(regime="UPTREND", market="RISK_ON", entry=False)
    first = evaluation(state, config, f=f, mf=f)
    assert first.state.symbol_confirmations == 1
    repeated = evaluation(first.state, config, f=f, mf=f)
    assert repeated.state.symbol_confirmations == 1
    assert repeated.reason == "bar_already_evaluated"
    later = NOW + timedelta(days=1)
    second = evaluation(repeated.state, config, at=later,
                        f=replace(f, bar_time=later), mf=replace(f, bar_time=later))
    assert second.state.symbol_regime == "UPTREND"
    assert second.state.market_regime == "RISK_ON"


def test_unchanged_market_bar_does_not_confirm_on_new_stock_bar(config):
    config = replace(config, confirmation_bars=2)
    state = SymbolState("AAA")
    f = features(entry=False, market="RISK_OFF")
    first = evaluation(state, config, f=f, mf=f)
    later = NOW + timedelta(days=1)
    stock_bar = Bar(later, 100, 100, 100, 100, 10000)
    index_bar = replace(stock_bar, time=NOW)
    result = evaluate("AAA", [stock_bar], [index_bar], context(first.state, now=later), first.state, later, config,
                      features=replace(f, bar_time=later), market_features=f)
    assert result.state.market_confirmations == 1
    assert result.state.market_regime == "NEUTRAL"


@pytest.mark.parametrize("market,regime", [("RISK_OFF", "RANGE"), ("RISK_ON", "DOWNTREND")])
def test_risk_regime_blocks_all_buys(config, market, regime):
    state = position()
    result = evaluation(state, config, close=80,
                        f=features(close=80, regime=regime), mf=features(close=80, market=market))
    assert result.decision is None
    assert result.reason == "regime_blocks_buy"


def test_adaptive_gap_increases_with_volatility_usage_and_neutral(config):
    f = features(atr_fraction=0.02)
    ordinary = adaptive_gap(f, 0.2, "RISK_ON", config)
    assert adaptive_gap(f, 0.8, "RISK_ON", config) > ordinary
    assert adaptive_gap(f, 0.2, "NEUTRAL", config) > ordinary
    assert adaptive_gap(replace(f, atr_fraction=0.06), 0.2, "RISK_ON", config) > ordinary
    assert adaptive_gap(f, 100, "NEUTRAL", config) == config.max_gap


def test_repeated_additions_use_actual_cycle_low_and_frequency(config):
    state = position(price=100)
    first = evaluation(state, config, close=90)
    assert first.decision.side == "BUY"
    state = apply_fill(first.state, Fill("addition", "o2", "AAA", "BUY", first.decision.quantity, 90, NOW))
    assert state.cycle_low == 90
    next_day = NOW + timedelta(days=1)
    result = evaluation(state, config, at=next_day, close=90)
    assert result.decision is None and result.reason == "addition_price_not_reached"
    result = evaluation(state, config, at=NOW + timedelta(hours=1), close=75)
    assert result.decision is None and result.reason == "buy_frequency_limit"


def test_stabilization_can_be_ablated(config):
    state = position()
    f = features(close=80, stabilized=False)
    result = evaluation(state, config, close=80, f=f)
    assert result.reason == "addition_stabilization_pending"
    immediate = evaluation(state, replace(config, stabilization_required=False), close=80, f=f)
    assert immediate.decision.side == "BUY"


def test_sold_cheapest_lot_preserves_cycle_low_and_last_execution(config):
    state = position(quantity=10, price=100)
    state = apply_fill(state, Fill("low", "o2", "AAA", "BUY", 10, 80, NOW - timedelta(days=1)))
    state = apply_fill(state, Fill("sell-low", "o3", "AAA", "SELL", 10, 90, NOW, lot_quantities=(("low:R", 10),)))
    assert state.quantity == 10 and min(lot.buy_price for lot in state.lots) == 100
    assert state.cycle_low == 80 and reference_price(state, "last_buy") == 80
    assert reference_price(state, "average") == 100


def test_full_exit_resets_low_only_on_actual_new_buy(config):
    state = position(price=100)
    state = apply_fill(state, Fill("exit", "o2", "AAA", "SELL", 10, 110, NOW))
    assert state.quantity == 0 and state.cycle_low == 100 and state.cycle_id == 1
    state = lock_cycle_budget(state, 9000)
    assert state.cycle_low == 100
    state = apply_fill(state, Fill("new-entry", "o3", "AAA", "BUY", 10, 120, NOW + timedelta(days=1)))
    assert state.cycle_id == 2 and state.cycle_low == 120 and state.cycle_budget == 9000


def test_cycle_budget_is_immutable_and_checked_against_actual_fill(config):
    state = position(budget=2000)
    with pytest.raises(ValueError, match="immutable"):
        lock_cycle_budget(state, 3000)
    with pytest.raises(ValueError, match="budget"):
        apply_fill(state, Fill("too-large", "o2", "AAA", "BUY", 11, 100, NOW))


def test_cash_pools_pending_buys_not_subtracted_twice(config):
    state = position()
    pending = PendingOrder("p", "AAA", "BUY", 10, 100, sector="TECH", cycle_budget=10000)
    portfolio = context(state, cash=25000, pending=(pending,))
    pools = cash_pools(portfolio, config)
    assert pools.outstanding_buy_reservations == 1000
    assert pools.logical_addition_reserve == 8000
    assert pools.new_entry_cash == 2000
    addition = capital_limit("AAA", state, portfolio, config, 9000, False, now=NOW)
    assert addition.amount == 8000  # no second debit of the broker's held 1000


def test_new_entry_protects_entire_existing_allocation(config):
    existing = position()
    new = SymbolState("BBB", sector="BANK")
    portfolio = Portfolio(100000, 30000, {"AAA": existing, "BBB": new}, observed_at=NOW)
    allowance = capital_limit("BBB", new, portfolio, config, 1000, True, now=NOW)
    assert allowance.amount == 0 and allowance.reason == "cash_reserved_for_existing_cycles"
    addition = capital_limit("AAA", existing, portfolio, config, 1500, False, now=NOW)
    assert addition.amount == 1500


def test_cash_shortage_shrinks_additions_without_forced_sale(config):
    state = position()
    portfolio = context(state, cash=15500)
    allowance = capital_limit("AAA", state, portfolio, config, 1500, False, now=NOW)
    assert allowance.amount == 500
    result = evaluation(state, config, close=80, portfolio=portfolio)
    assert result.decision.side == "BUY" and result.decision.quantity == 6
    blocked = evaluation(state, config, close=80, portfolio=context(state, cash=14000))
    assert blocked.decision is None


def test_concurrent_new_signals_cannot_spend_same_cash_after_reservation(config):
    one = SymbolState("AAA", sector="TECH")
    two = SymbolState("BBB", sector="BANK")
    portfolio = Portfolio(100000, 30000, {"AAA": one, "BBB": two}, observed_at=NOW)
    first = evaluation(one, config, portfolio=portfolio)
    assert first.decision.side == "BUY"
    reserved_state = lock_cycle_budget(first.state, first.decision.cycle_budget)
    pending = PendingOrder("new-one", "AAA", "BUY", 15, 100, sector="TECH", cycle_budget=10000)
    portfolio = replace(portfolio, buyable_cash=28500, positions={"AAA": reserved_state, "BBB": two}, pending=(pending,))
    second = evaluation(two, config, portfolio=portfolio)
    assert second.decision is None and second.reason == "cash_reserved_for_existing_cycles"


@pytest.mark.parametrize("field,value,reason", [
    ("balance_ok", False, "balance_query_failed"),
    ("orders_ok", False, "order_state_unknown"),
    ("reconciled", False, "position_mismatch"),
    ("observed_at", NOW - timedelta(hours=1), "balance_snapshot_stale"),
    ("observed_at", None, "balance_timestamp_missing"),
])
def test_failed_or_stale_snapshots_block_buys(config, field, value, reason):
    state = SymbolState("AAA")
    result = evaluation(state, config, portfolio=replace(context(state), **{field: value}))
    assert result.decision is None and result.reason == reason


def test_pending_sell_proceeds_never_count_as_cash_and_symbol_is_blocked(config):
    state = position()
    order = PendingOrder("sell", "AAA", "SELL", 10, 1000, sector="TECH")
    portfolio = context(state, cash=1000, pending=(order,))
    assert cash_pools(portfolio, config).new_entry_cash == 0
    result = evaluation(state, config, portfolio=portfolio, close=120)
    assert result.decision is None and result.reason == "symbol_order_pending"


def test_sector_cap_reserves_existing_full_budget(config):
    config = replace(config, sector_cap_fraction=0.15)
    state = position()
    newcomer = SymbolState("BBB", sector="TECH")
    portfolio = Portfolio(100000, 90000, {"AAA": state, "BBB": newcomer}, observed_at=NOW)
    assert capital_limit("BBB", newcomer, portfolio, config, 1000, True, now=NOW).amount == 0


def test_sector_cap_protects_flat_pending_full_cycle_allocation(config):
    config = replace(config, sector_cap_fraction=0.15)
    first = lock_cycle_budget(SymbolState("AAA", sector="TECH"), 10000)
    second = SymbolState("BBB", sector="TECH")
    pending = PendingOrder("one", "AAA", "BUY", 10, 100, sector="TECH", cycle_budget=10000)
    portfolio = Portfolio(100000, 99000, {"AAA": first, "BBB": second}, pending=(pending,), observed_at=NOW)
    allowance = capital_limit("BBB", second, portfolio, config, 1000, True, now=NOW)
    assert allowance.amount == 0 and allowance.reason == "full_cycle_budget_unavailable"


def test_account_cap_counts_actual_plus_reservations_and_full_allocations(config):
    config = replace(config, account_cap_fraction=0.15)
    first = position()
    second = SymbolState("BBB", sector="BANK")
    portfolio = Portfolio(100000, 99000, {"AAA": first, "BBB": second}, observed_at=NOW)
    assert capital_limit("BBB", second, portfolio, config, 1000, True, now=NOW).amount == 0


def test_internal_position_disagreement_cannot_be_overwritten(config):
    real = position()
    empty = SymbolState("AAA", sector="TECH")
    result = evaluation(empty, config, portfolio=context(real))
    assert result.decision is None and result.reason == "position_state_mismatch"


def test_inferred_risk_exit_can_be_ablated_but_thesis_safety_remains(config):
    state = position()
    config = replace(config, trend_risk_exit_enabled=False)
    result = evaluation(state, config, risk=RiskContext(severe_deterioration=True))
    assert result.decision is None
    result = evaluation(state, replace(config, trend_risk_exit_enabled=True), risk=RiskContext(severe_deterioration=True))
    assert result.decision.reason == "risk_trend_deterioration"
    result = evaluation(state, config, risk=RiskContext(thesis_valid=False))
    assert result.decision.reason == "risk_thesis_invalid"


@pytest.mark.parametrize("change", [{"stabilization_required": 1}, {"trend_risk_exit_enabled": "false"}, {"risk_reduction_fraction": 0}])
def test_invalid_boolean_or_disabled_risk_fraction_rejected(change):
    with pytest.raises(ValueError):
        ARCGConfig(**change)


def test_net_costs_prevent_small_or_negative_profit(config):
    state = position()
    config = replace(config, profit_target=0.001, profit_atr_multiplier=0,
                     sell_fee_rate=0.002, sell_tax_rate=0.004, minimum_net_profit=0.005)
    result = evaluation(state, config, close=100.2)
    assert result.decision is None
    assert net_profit(state.lots[0], 100.2, config) < 0
    result = evaluation(state, config, close=102)
    assert result.decision.side == "SELL"


def test_uptrend_rotation_and_trend_tranches_and_trailing_peak(config):
    state = lock_cycle_budget(SymbolState("AAA", sector="TECH"), 10000)
    state = apply_fill(state, Fill("split", "o", "AAA", "BUY", 5, 100, NOW - timedelta(days=2), trend_fraction=0.5))
    assert [(lot.role, lot.quantity) for lot in state.lots] == [("rotation", 3), ("trend", 2)]
    first = evaluation(state, config, close=110, f=features(close=110, regime="UPTREND"), high=115)
    assert first.decision.side == "SELL" and first.decision.quantity == 3
    assert first.decision.lot_quantities == (("split:R", 3),)
    assert all(lot.peak_price == 115 for lot in first.state.lots)
    state = apply_fill(first.state, Fill("rotate-out", "s", "AAA", "SELL", 3, 110, NOW, lot_quantities=first.decision.lot_quantities))
    later = NOW + timedelta(days=1)
    trailing = evaluation(state, config, at=later, close=107, f=features(at=later, close=107, regime="UPTREND"))
    assert trailing.decision.reason == "profit_trailing"
    assert trailing.decision.quantity == 2


def test_small_lot_gets_integer_rotation_role():
    state = lock_cycle_budget(SymbolState("AAA"), 1000)
    state = apply_fill(state, Fill("one", "o", "AAA", "BUY", 1, 100, NOW, trend_fraction=0.5))
    assert len(state.lots) == 1 and state.lots[0].role == "rotation"


def test_risk_sell_precedes_profit_and_buy(config):
    state = position()
    result = evaluation(state, config, close=120, risk=RiskContext(thesis_valid=False))
    assert result.decision.side == "SELL"
    assert result.decision.reason == "risk_thesis_invalid"
    assert result.decision.quantity == 10


def test_long_holding_age_alone_neither_sells_nor_decays(config):
    state = position(at=NOW - timedelta(days=500))
    result = evaluation(state, config, close=90)
    assert result.decision is None or result.decision.side == "BUY"
    assert result.state.last_review_at == NOW
    assert not result.state.paused
    result = evaluation(state, config, close=90, f=features(close=90, regime="DOWNTREND"))
    assert result.decision is None and result.state.paused


def test_full_exit_requires_fresh_reentry_after_cooldown(config):
    state = position()
    state = apply_fill(state, Fill("out", "s", "AAA", "SELL", 10, 110, NOW))
    immediate = evaluation(state, config)
    assert immediate.decision is None and immediate.reason == "reentry_cooldown"
    later = NOW + timedelta(days=2)
    no_signal = evaluation(state, config, at=later, f=features(at=later, entry=False))
    assert no_signal.decision is None
    valid = evaluation(state, config, at=later)
    assert valid.decision.side == "BUY" and valid.decision.reason == "reentry_signal"


def test_state_book_partial_fill_cancel_restart_and_idempotency(tmp_path, config):
    path = tmp_path / "book.json"
    book = StateBook(path, config=config)
    order = PendingOrder("o", "AAA", "BUY", 10, 100, cycle_budget=2000)
    book.reserve_order(order)
    fill = Fill("partial", "o", "AAA", "BUY", 4, 99, NOW)
    state = book.apply_fill(fill)
    assert state.quantity == 4 and state.cycle_low == 99
    assert book.pending["o"].remaining_quantity == 6
    assert book.apply_fill(fill) == state
    with pytest.raises(StateIntegrityError, match="different facts"):
        book.apply_fill(replace(fill, price=98))
    with pytest.raises(StateIntegrityError, match="unconfirmed"):
        book.cancel_order("o", confirmed=False)
    book.cancel_order("o")
    restarted = StateBook(path, config=config)
    assert restarted.states["AAA"].cycle_low == 99
    assert restarted.states["AAA"].cycle_budget == 2000
    assert not restarted.pending
    assert restarted.apply_fill(fill).quantity == 4


def test_state_book_sold_low_restart_and_new_cycle(tmp_path, config):
    book = StateBook(tmp_path / "book.json", config=config)
    book.reserve_order(PendingOrder("one", "AAA", "BUY", 10, 100, cycle_budget=3000))
    book.apply_fill(Fill("one-fill", "one", "AAA", "BUY", 10, 100, NOW))
    book.reserve_order(PendingOrder("two", "AAA", "BUY", 10, 80, cycle_budget=3000))
    book.apply_fill(Fill("two-fill", "two", "AAA", "BUY", 10, 80, NOW + timedelta(days=1)))
    book.reserve_order(PendingOrder("three", "AAA", "SELL", 10, 90, lot_quantities=(("two-fill:R", 10),)))
    book.apply_fill(Fill("three-fill", "three", "AAA", "SELL", 10, 90, NOW + timedelta(days=2)))
    book = StateBook(book.path, config=config)
    assert book.states["AAA"].cycle_low == 80
    assert book.states["AAA"].cycle_last_buy == 80
    book.reserve_order(PendingOrder("four", "AAA", "SELL", 10, 110))
    book.apply_fill(Fill("four-fill", "four", "AAA", "SELL", 10, 110, NOW + timedelta(days=3)))
    book.reserve_order(PendingOrder("five", "AAA", "BUY", 5, 120, cycle_budget=2000))
    assert book.states["AAA"].cycle_low == 80
    state = book.apply_fill(Fill("five-fill", "five", "AAA", "BUY", 5, 120, NOW + timedelta(days=4)))
    assert state.cycle_id == 2 and state.cycle_low == 120


def test_state_book_partial_trend_roles_round_over_whole_order(tmp_path, config):
    book = StateBook(tmp_path / "book.json", config=config)
    book.reserve_order(PendingOrder("o", "AAA", "BUY", 2, 100, trend_fraction=0.5, cycle_budget=1000))
    book.apply_fill(Fill("p1", "o", "AAA", "BUY", 1, 100, NOW))
    state = book.apply_fill(Fill("p2", "o", "AAA", "BUY", 1, 100, NOW))
    assert [(lot.role, lot.quantity) for lot in state.lots] == [("rotation", 1), ("trend", 1)]


def test_state_book_partial_sells_preserve_allocation(tmp_path, config):
    state = position(quantity=5)
    state = apply_fill(state, Fill("second", "o2", "AAA", "BUY", 5, 90, NOW - timedelta(days=1)))
    book = StateBook(tmp_path / "book.json", {"AAA": state}, config=config)
    book.reserve_order(PendingOrder("sell", "AAA", "SELL", 8, 110, sector="TECH", lot_quantities=(("second:R", 5), ("initial-AAA:R", 3))))
    first = book.apply_fill(Fill("s1", "sell", "AAA", "SELL", 6, 110, NOW))
    assert [(lot.lot_id, lot.quantity) for lot in first.lots] == [("initial-AAA:R", 4)]
    second = book.apply_fill(Fill("s2", "sell", "AAA", "SELL", 2, 110, NOW))
    assert second.quantity == 2 and second.cycle_low == 90


def test_observation_updates_persist_peaks_and_reject_fill_rewrites(tmp_path, config):
    state = position()
    book = StateBook(tmp_path / "book.json", {"AAA": state}, config=config)
    observed = evaluation(state, config, close=100, high=110).state
    book.save_state(observed)
    assert StateBook(book.path).states["AAA"].lots[0].peak_price == 110
    with pytest.raises(StateIntegrityError):
        book.save_state(replace(observed, cycle_low=95))
    with pytest.raises(StateIntegrityError):
        book.save_state(state)


def test_mismatch_persists_fail_closed_and_quantity_match_cannot_heal(tmp_path, config):
    state = position()
    book = StateBook(tmp_path / "book.json", {"AAA": state}, config=config)
    assert book.reconcile({"AAA": 9}) is False
    restarted = StateBook(book.path)
    assert not restarted.states["AAA"].history_complete
    assert restarted.reconcile({"AAA": 10}) is False
    assert capital_limit("AAA", restarted.states["AAA"], context(restarted.states["AAA"]), config, 100, False, now=NOW).amount == 0


def test_recovery_requires_complete_history_and_restores_sold_low(tmp_path, config):
    book = StateBook(tmp_path / "book.json", config=config)
    fills = [Fill("b1", "o1", "AAA", "BUY", 5, 100, NOW),
             Fill("b2", "o2", "AAA", "BUY", 5, 80, NOW + timedelta(days=1)),
             Fill("s", "o3", "AAA", "SELL", 5, 90, NOW + timedelta(days=2), lot_quantities=(("b2:R", 5),))]
    with pytest.raises(StateIntegrityError):
        book.recover_history("AAA", fills, {"o1": 2000}, 5)
    with pytest.raises(StateIntegrityError):
        book.recover_history("AAA", fills, {"o1": 2000}, 4, complete_history=True)
    state = book.recover_history("AAA", fills, {"o1": 2000}, 5, complete_history=True)
    assert state.history_complete and state.cycle_low == 80 and state.quantity == 5


def test_journal_corruption_rejected_and_atomic_write_failure_rolls_back(tmp_path, config, monkeypatch):
    path = tmp_path / "book.json"
    book = StateBook(path, {"AAA": SymbolState("AAA")}, config=config)
    old = path.read_bytes()
    import kis_msj.arcg_state as journal
    def fail_replace(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(journal.os, "replace", fail_replace)
    with pytest.raises(OSError):
        book.reserve_order(PendingOrder("o", "AAA", "BUY", 1, 100, cycle_budget=1000))
    assert book.states["AAA"].cycle_budget == 0 and not book.pending
    assert path.read_bytes() == old
    assert not list(tmp_path.glob("*.tmp"))
    corrupt = json.loads(path.read_text())
    corrupt["states"]["AAA"]["cycle_low"] = -1
    path.write_text(json.dumps(corrupt))
    with pytest.raises(StateIntegrityError):
        StateBook(path)
