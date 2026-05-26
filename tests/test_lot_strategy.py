from datetime import datetime, timedelta

import pytest

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import AccountSnapshot, LotState, OrderSide, PositionLifecycle, PositionState, ReentryType, SellReason, TradeFill
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskManager
from kis_msj.strategy import LotGridStrategy


def setup_strategy(strategy_config: StrategyConfig | None = None, daily_profit_loss: int = 0):
    config = BotConfig(order=OrderConfig(price_sample_interval_seconds=0), strategy=strategy_config or StrategyConfig(lot_sizing_mode="legacy_exposure_bands"))
    lots = LotManager(config.strategy)
    positions = PositionManager(config.strategy, lots)
    strategy = LotGridStrategy(config, lots)
    risk = RiskManager(config)
    snapshot = AccountSnapshot(10_000_000, 10_000_000, 0, daily_profit_loss, ())
    return config, lots, positions, strategy, risk, snapshot


def add_lot(positions: PositionManager, code: str, price: int, quantity: int, minutes_ago: int = 0) -> LotState:
    fill = TradeFill(code, "Test", OrderSide.BUY, quantity, price, f"BUY-{price}-{quantity}", datetime.now() - timedelta(minutes=minutes_ago))
    positions.apply_fill(fill)
    return positions.lot_manager.last_buy_lot(code)  # type: ignore[return-value]


def age_lot(lot: LotState, weeks: int) -> None:
    lot.buy_filled_at = (datetime.now() - timedelta(weeks=weeks)).isoformat(timespec="microseconds")


def test_initial_buy_creates_candidate_action() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    account = risk.account_buy_allowed(snapshot, positions.positions)
    symbol = risk.symbol_buy_allowed(position)

    action = strategy.decide(position, 10000, snapshot, account, symbol)

    assert action is not None
    assert action.side is OrderSide.BUY
    assert action.amount == 30000


def test_minus_four_percent_add_buy_under_600k() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 3)
    position = positions.refresh_from_lots("005930", 9600)

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 30000
    assert "4%" in action.reason


def test_minus_five_percent_after_60k_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 7)
    position = positions.refresh_from_lots("005930", 9500)

    action = strategy.decide(position, 9500, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 30000
    assert "5%" in action.reason


def test_minus_six_percent_after_120k_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 13)
    position = positions.refresh_from_lots("005930", 9400)

    action = strategy.decide(position, 9400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 40000
    assert "6%" in action.reason


def test_minus_eight_percent_after_200k_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 21)
    position = positions.refresh_from_lots("005930", 9200)

    action = strategy.decide(position, 9200, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 50000
    assert "8%" in action.reason


def test_exposure_above_300k_blocks_auto_buy_and_marks_review() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 31)
    position = positions.refresh_from_lots("005930", 9000)

    action = strategy.decide(position, 9000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None
    assert position.needs_review
    assert not position.auto_buy_enabled


def test_add_buy_that_would_cross_auto_limit_is_blocked() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 29)
    position = positions.refresh_from_lots("005930", 9200)

    action = strategy.decide(position, 9200, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None


def test_sellable_lot_targets_only_profitable_lot() -> None:
    _, lots, positions, _, _, _ = setup_strategy()
    old = add_lot(positions, "005930", 10000, 10, 20)
    add_lot(positions, "005930", 12000, 10, 10)

    sellable = lots.sellable_lots("005930", 10600, lots.cumulative_invested_amount("005930"))

    assert [lot.lot_id for lot in sellable] == [old.lot_id]


def test_multiple_sellable_lots_sorted_by_profit_rate_first() -> None:
    _, lots, positions, _, _, _ = setup_strategy()
    low = add_lot(positions, "005930", 9000, 10, 20)
    high = add_lot(positions, "005930", 9500, 100, 10)

    sellable = lots.sellable_lots("005930", 10500, lots.cumulative_invested_amount("005930"))

    assert sellable[0].lot_id == low.lot_id
    assert high in sellable


def test_average_loss_but_profitable_lot_sells_only_that_lot() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy()
    profitable = add_lot(positions, "005930", 9000, 10)
    add_lot(positions, "005930", 12000, 100)
    position = positions.refresh_from_lots("005930", 9550)

    assert position.average_price > 9550
    action = strategy.decide(position, 9550, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.lot_id == profitable.lot_id
    assert lots.lots[action.lot_id].buy_price == 9000


def test_sold_out_wait_reentry_blocks_immediate_initial_buy() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy()
    lot = add_lot(positions, "005930", 10000, 1)
    positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10600, "SELL-1", datetime.now(), lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))
    position = positions.refresh_from_lots("005930", 10600)

    assert position.position_state == PositionLifecycle.WAIT_REENTRY.value
    assert position.exit_anchor_price == 10600
    action = strategy.decide(position, 10600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None
    assert lot.status == "CLOSED"


def test_sold_out_wait_reentry_allows_reentry_after_drop() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    lot = add_lot(positions, "005930", 10000, 1)
    positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10600, "SELL-1", datetime.now(), lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))
    position = positions.refresh_from_lots("005930", 10176)

    action = strategy.decide(position, 10176, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.side is OrderSide.BUY
    assert action.reason == "reentry_buy"
    assert action.reentry_type == ReentryType.NORMAL_REENTRY.value


def test_profit_take_full_exit_sets_wait_reentry() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    lot = add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10600)

    action = strategy.decide(position, 10600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    assert action is not None
    assert action.sell_reason == SellReason.PROFIT_TAKE.value

    position = positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10600, "SELL-1", datetime.now(), lot.lot_id, sell_reason=action.sell_reason))
    assert position.position_state == PositionLifecycle.WAIT_REENTRY.value
    assert position.exit_anchor_price == 10600


def test_multi_fill_profit_exit_sets_vwap_median_reentry_anchors() -> None:
    _, _, positions, _, _, _ = setup_strategy()
    high_fill_lot = add_lot(positions, "005930", 9000, 1)
    low_fill_lot = add_lot(positions, "005930", 9000, 3)

    positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 12000, "SELL-1", datetime.now(), high_fill_lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))
    position = positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 3, 10000, "SELL-2", datetime.now(), low_fill_lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))

    assert position.position_state == PositionLifecycle.WAIT_REENTRY.value
    assert position.cycle_highest_sell_price == 12000
    assert position.cycle_last_sell_price == 10000
    assert position.cycle_sell_vwap_price == 10500
    assert position.cycle_sell_median_price == 11000
    assert position.normal_exit_anchor_price == 10500
    assert position.trailing_exit_anchor_price == 11000
    assert position.exit_anchor_price == 10500
    assert position.cycle_sell_fill_count == 2
    assert not position.anchor_single_fill
    assert position.anchor_confidence == "NORMAL"


def test_normal_reentry_uses_normal_exit_anchor_not_cycle_highest() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.cycle_highest_sell_price = 12000
    position.normal_exit_anchor_price = 10500
    position.trailing_exit_anchor_price = 11000
    position.post_exit_high_price = 11000
    position.exit_time = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")

    assert strategy.decide(position, 10090, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position)) is None
    action = strategy.decide(position, 10080, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.reentry_type == ReentryType.NORMAL_REENTRY.value


def test_trailing_reentry_activation_uses_trailing_exit_anchor_not_cycle_highest() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.cycle_highest_sell_price = 12000
    position.normal_exit_anchor_price = 10500
    position.trailing_exit_anchor_price = 11000
    position.post_exit_high_price = 11600
    position.exit_time = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")

    action = strategy.decide(position, 10672, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.reentry_type == ReentryType.TRAILING_REENTRY.value


def test_single_sell_fill_anchor_confidence_low() -> None:
    _, _, positions, _, _, _ = setup_strategy()
    lot = add_lot(positions, "005930", 10000, 1)

    position = positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10600, "SELL-1", datetime.now(), lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))

    assert position.normal_exit_anchor_price == 10600
    assert position.trailing_exit_anchor_price == 10600
    assert position.cycle_sell_fill_count == 1
    assert position.anchor_single_fill
    assert position.anchor_confidence == "LOW"


def test_breakeven_old_lot_is_profit_take_when_net_nonnegative() -> None:
    strategy_config = StrategyConfig(estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config)
    lot = add_lot(positions, "005930", 10000, 1)
    age_lot(lot, 12)
    position = positions.refresh_from_lots("005930", 10000)

    action = strategy.decide(position, 10000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.sell_reason == SellReason.PROFIT_TAKE.value


def test_negative_effective_target_but_positive_realized_pnl_is_profit_take() -> None:
    strategy_config = StrategyConfig(estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config)
    lot = add_lot(positions, "005930", 10000, 1)
    age_lot(lot, 20)
    position = positions.refresh_from_lots("005930", 10050)

    action = strategy.decide(position, 10050, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert lot.effective_target_profit_rate < 0
    assert action is not None
    assert action.sell_reason == SellReason.PROFIT_TAKE.value


def test_loss_lot_is_not_classified_as_profit_take() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config, daily_profit_loss=10_000)
    lot = add_lot(positions, "005930", 10000, 1)
    age_lot(lot, 20)
    position = positions.refresh_from_lots("005930", 9800)

    action = strategy.decide(position, 9800, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.sell_reason == SellReason.CLEANUP_SELL.value


def test_trailing_reentry_after_post_exit_high_pullback() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.exit_anchor_price = 10000
    position.reentry_anchor_price = 10000
    position.post_exit_high_price = 11500
    position.exit_time = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")

    action = strategy.decide(position, 10580, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.side is OrderSide.BUY
    assert action.reentry_type == ReentryType.TRAILING_REENTRY.value


def test_context_does_not_update_post_exit_high_price() -> None:
    _, _, positions, strategy, _, _ = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.exit_anchor_price = 10000
    position.post_exit_high_price = 10000
    position.exit_time = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")

    strategy.context(position, 11500)

    assert position.post_exit_high_price == 10000


def test_update_reentry_tracking_only_updates_wait_reentry() -> None:
    _, _, positions, strategy, _, _ = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.WAIT_REENTRY.value
    position.exit_anchor_price = 10000
    position.post_exit_high_price = 10000

    assert strategy.update_reentry_tracking(position, 11500)
    assert position.post_exit_high_price == 11500

    position.position_state = PositionLifecycle.HOLDING.value
    assert not strategy.update_reentry_tracking(position, 12000)
    assert position.post_exit_high_price == 11500


def test_cleanup_sell_partial_keeps_holding_and_sets_buy_cooldown() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config, daily_profit_loss=10_000)
    old = add_lot(positions, "005930", 10000, 1)
    age_lot(old, 20)
    add_lot(positions, "005930", 9500, 1)
    position = positions.refresh_from_lots("005930", 9600)

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    assert action is not None
    assert action.sell_reason == SellReason.CLEANUP_SELL.value

    position = positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 9600, "SELL-1", datetime.now(), old.lot_id, sell_reason=action.sell_reason))
    assert position.position_state == PositionLifecycle.HOLDING.value
    assert position.cleanup_buy_cooldown_until


def test_cleanup_sell_full_exit_sets_cleanup_cooldown() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config, daily_profit_loss=10_000)
    old = add_lot(positions, "005930", 10000, 1)
    age_lot(old, 20)
    position = positions.refresh_from_lots("005930", 9600)

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    assert action is not None
    assert action.sell_reason == SellReason.CLEANUP_SELL.value

    position = positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 9600, "SELL-1", datetime.now(), old.lot_id, sell_reason=action.sell_reason))
    assert position.position_state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
    assert position.cleanup_reentry_cooldown_until


def test_cleanup_cooldown_blocks_reentry_even_when_price_drops() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
    position.exit_anchor_price = 10600
    position.reentry_anchor_price = 10600
    position.cleanup_reentry_cooldown_until = (datetime.now() + timedelta(days=5)).isoformat(timespec="seconds")

    action = strategy.decide(position, 10000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 10000)

    assert action is None
    assert context.skip_reason == "cleanup_cooldown"


def test_cleanup_cooldown_expiry_returns_review_by_default() -> None:
    _, _, positions, _, _, _ = setup_strategy()
    position = positions.get("005930", "Test")
    position.position_state = PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
    position.cleanup_reentry_cooldown_until = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")

    position = positions.refresh_from_lots("005930", 9500)

    assert position.position_state == PositionLifecycle.REVIEW_REQUIRED.value
    assert position.review_reason == "cleanup_cooldown_complete"


def test_cleanup_loss_budget_blocks_large_loss() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config, daily_profit_loss=10_000)
    old = add_lot(positions, "005930", 10000, 10)
    age_lot(old, 20)
    position = positions.refresh_from_lots("005930", 9600)

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None


def test_minus_mode_add_buy_uses_min_vwap_median_not_lowest_extreme() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 6000, 1)
    add_lot(positions, "005930", 10000, 10)
    position = positions.refresh_from_lots("005930", 5900)

    action = strategy._add_buy_action(position, 5900, snapshot)
    context = strategy.context(position, 5900)

    assert context.pnl_mode == "MINUS"
    assert context.lowest_open_buy_lot_price == 6000
    assert context.open_lot_vwap_buy_price == 9636
    assert context.median_open_buy_price == 8000
    assert context.reference_buy_price == 8000
    assert context.reference_buy_source == "min_vwap_median_for_minus"
    assert action is not None
    assert action.side is OrderSide.BUY


def test_plus_mode_add_buy_uses_max_vwap_median_not_highest_extreme() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 5000, 10)
    add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 7000)

    action = strategy._add_buy_action(position, 7000, snapshot)
    context = strategy.context(position, 7000)

    assert context.pnl_mode == "PLUS"
    assert context.highest_open_buy_lot_price == 10000
    assert context.open_lot_vwap_buy_price == 5455
    assert context.median_open_buy_price == 7500
    assert context.reference_buy_price == 7500
    assert context.reference_buy_source == "max_vwap_median_for_plus"
    assert action is not None
    assert action.reason == "add_buy_drop_4%"


def test_plus_mode_sells_profitable_lot_even_if_highest_lot_is_below_target() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    low = add_lot(positions, "005930", 9000, 10)
    add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 9540)

    action = strategy.decide(position, 9540, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 9540)

    assert context.pnl_mode == "PLUS"
    assert context.reference_sell_price == 10000
    assert context.sell_signal_met
    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.lot_id == low.lot_id


def test_lx_like_plus_mode_sells_low_lot_above_target_before_high_lot_target() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "383800", 9510, 3)
    low = add_lot(positions, "383800", 9150, 3)
    position = positions.refresh_from_lots("383800", 9800)

    action = strategy.decide(position, 9800, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 9800)

    assert context.pnl_mode == "PLUS"
    assert context.reference_sell_price == 9510
    assert context.sell_signal_met
    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.lot_id == low.lot_id


def test_neutral_mode_buy_uses_min_vwap_median_and_sell_uses_individual_lot_profit() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    low = add_lot(positions, "005930", 9000, 1)
    add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 9540)

    action = strategy.decide(position, 9540, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 9540)

    assert context.pnl_mode == "NEUTRAL"
    assert context.open_lot_vwap_buy_price == 9500
    assert context.median_open_buy_price == 9500
    assert context.reference_buy_price == 9500
    assert context.reference_buy_source == "min_vwap_median_for_neutral"
    assert context.sell_signal_met
    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.lot_id == low.lot_id


def test_single_open_lot_reference_buy_price_equals_lot_price() -> None:
    _, _, positions, strategy, _, _ = setup_strategy()
    add_lot(positions, "005930", 10000, 3)
    position = positions.refresh_from_lots("005930", 9700)

    context = strategy.context(position, 9700)

    assert context.open_lot_vwap_buy_price == 10000
    assert context.median_open_buy_price == 10000
    assert context.reference_buy_price == 10000


def test_minus_mode_does_not_sell_loss_lot_even_when_signal_exists() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    loss_lot = add_lot(positions, "005930", 10000, 12)
    profitable = add_lot(positions, "005930", 9200, 1)
    position = positions.refresh_from_lots("005930", 9568)

    action = strategy.decide(position, 9568, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 9568)

    assert context.pnl_mode == "MINUS"
    assert context.sell_signal_met
    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.target_lot is not None
    assert action.target_lot.buy_price == profitable.buy_price
    assert action.lot_id != loss_lot.lot_id


def test_lot_quantity_mismatch_blocks_new_buy() -> None:
    config, lots, positions, _, _, _ = setup_strategy()
    position = PositionState(code="005930", quantity=99, lot_quantity_mismatch=True)
    decision = RiskManager(config).symbol_buy_allowed(position)

    assert not decision.allowed
    assert "lot_quantity_mismatch" in decision.reasons


def test_total_invested_account_limit_blocks_new_buy() -> None:
    config = BotConfig(risk=RiskConfig(max_total_invested_amount=50_000))
    positions = {"005930": PositionState(code="005930", quantity=5, cumulative_invested_amount=50_000)}
    snapshot = AccountSnapshot(10_000_000, 10_000_000, 0, 0, ())

    decision = RiskManager(config).account_buy_allowed(snapshot, positions)

    assert not decision.allowed
    assert "max_total_invested_amount_reached" in decision.reasons


def test_profit_take_lots_exclude_loss_cleanup_candidates() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, lots, positions, _, _, _ = setup_strategy(strategy_config)
    loss = add_lot(positions, "005930", 10000, 1)
    age_lot(loss, 20)

    profit_take = lots.profit_take_lots("005930", 9700, lots.cumulative_invested_amount("005930"))
    cleanup_candidates = lots.cleanup_candidate_lots("005930", 9700)

    assert loss not in profit_take
    assert loss in cleanup_candidates


def test_stale_lot_is_marked_without_forced_sell() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, lots, positions, strategy, risk, snapshot = setup_strategy(strategy_config)
    stale = add_lot(positions, "005930", 10000, 1)
    age_lot(stale, 10)
    position = positions.refresh_from_lots("005930", 8500)

    stale_lots = lots.stale_lots("005930", 8500)
    action = strategy.decide(position, 8500, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert stale in stale_lots
    assert stale.cleanup_candidate
    assert action is None or action.side is not OrderSide.SELL


def test_stale_lot_review_age_marks_review_required() -> None:
    _, _, positions, _, _, _ = setup_strategy()
    stale = add_lot(positions, "005930", 10000, 1)
    age_lot(stale, 20)

    position = positions.refresh_from_lots("005930", 8500)

    assert position.position_state == PositionLifecycle.REVIEW_REQUIRED.value
    assert position.review_reason == "stale_lot_review_age"


def test_symbol_loss_marks_review_required() -> None:
    _, _, positions, _, _, _ = setup_strategy()
    add_lot(positions, "005930", 10000, 1)

    position = positions.refresh_from_lots("005930", 7900)

    assert position.position_state == PositionLifecycle.REVIEW_REQUIRED.value
    assert position.review_reason == "symbol_loss_review"


def test_review_required_blocks_buy_but_allows_profit_take_sell() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    lot = add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10600)
    position.needs_review = True
    position.position_state = PositionLifecycle.REVIEW_REQUIRED.value

    action = strategy.decide(position, 10600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.lot_id == lot.lot_id


def test_review_required_blocks_cleanup_sell_conservatively() -> None:
    strategy_config = StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0)
    _, _, positions, strategy, risk, snapshot = setup_strategy(strategy_config, daily_profit_loss=10_000)
    lot = add_lot(positions, "005930", 10000, 1)
    age_lot(lot, 20)
    position = positions.refresh_from_lots("005930", 9600)
    position.needs_review = True
    position.position_state = PositionLifecycle.REVIEW_REQUIRED.value

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None
    assert position.skip_reason == "needs_review"


def test_risk_blocked_blocks_buy_and_sell_conservatively() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10600)
    position.danger_state = True
    position.position_state = PositionLifecycle.RISK_BLOCKED.value

    action = strategy.decide(position, 10600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 10600)

    assert action is None
    assert context.skip_reason == "risk_blocked_buy_sell_blocked"


def test_lot_sizing_new_initial_buy_uses_price_band() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    position = positions.get("005930", "Test")

    action = strategy.decide(position, 10100, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 30000

    position_2 = positions.get("000660", "Test2")
    action_2 = strategy.decide(position_2, 9000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position_2))
    assert action_2 is not None
    assert action_2.amount == 10000


def test_lot_sizing_disabled_bands_block_initial_buy() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    low = positions.get("000001", "Low")
    high = positions.get("999999", "High")

    assert strategy.decide(low, 250, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(low)) is None
    assert low.skip_reason == "lot_sizing_band_disabled"
    assert strategy.decide(high, 1_000_001, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(high)) is None
    assert high.skip_reason == "lot_sizing_band_disabled"


def test_lot_sizing_cycle_lock_keeps_original_lot_unit_after_price_moves() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    add_lot(positions, "005930", 10100, 3)
    position = positions.refresh_from_lots("005930", 9500)

    action = strategy.decide(position, 9500, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert position.entry_price_for_lot_sizing == 10100
    assert position.lot_unit_amount == 30000
    assert position.max_symbol_amount == 300000
    assert action is not None
    assert action.amount == 30000


def test_lot_sizing_reentry_starts_new_cycle_from_reentry_price() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    lot = add_lot(positions, "005930", 10100, 1)
    positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10600, "SELL-1", datetime.now() - timedelta(minutes=70), lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))
    position = positions.refresh_from_lots("005930", 9000)

    action = strategy.decide(position, 9000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.side is OrderSide.BUY
    assert action.amount == 10000


def test_lot_sizing_lot_count_bands_and_max_lots_block() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    for _ in range(3):
        add_lot(positions, "005930", 10100, 1)
    position = positions.refresh_from_lots("005930", 9400)

    action = strategy.decide(position, 9400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 30000
    assert "6%" in action.reason
    assert strategy.context(position, 9400).add_buy_lot_band == "3-4"

    for _ in range(9):
        add_lot(positions, "000660", 10100, 1)
    position_9 = positions.refresh_from_lots("000660", 8700)
    allowed_10th = strategy.decide(position_9, 8700, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position_9))

    assert allowed_10th is not None
    assert strategy.context(position_9, 8700).current_open_lot_count == 9

    for _ in range(7):
        add_lot(positions, "005930", 10100, 1)
    position = positions.refresh_from_lots("005930", 8700)
    blocked = strategy.decide(position, 8700, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert blocked is None
    assert position.skip_reason == "max_lots_per_symbol_reached"


def test_lot_sizing_open_lot_count_excludes_closed_lots_for_max_lot_boundary() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    for _ in range(10):
        add_lot(positions, "005930", 10100, 1)
    closed = next(iter(lots.lots.values()))
    closed.remaining_quantity = 0
    closed.status = "CLOSED"
    position = positions.refresh_from_lots("005930", 8700)

    action = strategy.decide(position, 8700, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 8700)

    assert context.current_open_lot_count == 9
    assert action is not None
    assert action.side is OrderSide.BUY


def test_lot_sizing_high_price_band_max_lots_three_blocks_after_three_lots() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    for _ in range(3):
        add_lot(positions, "005930", 400000, 1)
    position = positions.refresh_from_lots("005930", 360000)

    action = strategy.decide(position, 360000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None
    assert position.max_lots_per_symbol == 3
    assert position.skip_reason == "max_lots_per_symbol_reached"


def test_lot_sizing_migrates_existing_open_lot_without_quantity_change() -> None:
    _, _, positions, strategy, _, _ = setup_strategy(StrategyConfig())
    lot = add_lot(positions, "005930", 10100, 3)
    position = positions.refresh_from_lots("005930", 9500)
    position.lot_unit_amount = 0
    position.max_symbol_amount = 0

    sizing = strategy.ensure_lot_sizing(position, 9500)

    assert sizing["lot_unit_amount"] == 30000
    assert position.skip_reason == "lot_sizing_migrated"
    assert lot.remaining_quantity == 3


def test_lot_sizing_sell_target_uses_current_lot_band_not_original_lot_target() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    first = add_lot(positions, "005930", 10000, 1)
    for _ in range(4):
        add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10400)

    action = strategy.decide(position, 10400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 10400)

    assert first.base_target_profit_rate == pytest.approx(0.06)
    assert context.target_profit_source == "current_lot_band"
    assert context.target_profit_lot_band == "5-6"
    assert context.current_base_target_profit_rate == pytest.approx(0.04)
    assert all(lot.effective_target_profit_rate <= 0.04 for lot in lots.open_lots("005930"))
    assert action is not None
    assert action.side is OrderSide.SELL
    assert action.sell_reason == SellReason.PROFIT_TAKE.value


def test_target_profit_lot_band_uses_current_six_open_lots_for_old_lot() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig())
    first = add_lot(positions, "005930", 10000, 1)
    for _ in range(5):
        add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10400)

    action = strategy.decide(position, 10400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 10400)

    assert context.current_open_lot_count == 6
    assert context.target_profit_lot_band == "5-6"
    assert context.current_base_target_profit_rate == pytest.approx(0.04)
    assert context.original_lot_base_target_profit_rate == pytest.approx(first.base_target_profit_rate)
    assert context.target_profit_source == "current_lot_band"
    assert action is not None
    assert action.side is OrderSide.SELL


def test_target_profit_recalculates_up_after_partial_lot_sales_reduce_open_count() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig(estimated_fee_tax_pct=0))
    created = [add_lot(positions, "005930", 10000, 1) for _ in range(6)]
    position = positions.refresh_from_lots("005930", 10400)
    context_before = strategy.context(position, 10400)

    assert context_before.current_open_lot_count == 6
    assert context_before.target_profit_lot_band == "5-6"
    assert context_before.current_base_target_profit_rate == pytest.approx(0.04)

    for index, lot in enumerate(created[:2], start=1):
        positions.apply_fill(TradeFill("005930", "Test", OrderSide.SELL, 1, 10400, f"SELL-{index}", datetime.now(), lot.lot_id, sell_reason=SellReason.PROFIT_TAKE.value))
    position = positions.refresh_from_lots("005930", 10400)
    context_after = strategy.context(position, 10400)
    action_at_four_percent = strategy.decide(position, 10400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert len(lots.open_lots("005930")) == 4
    assert context_after.current_open_lot_count == 4
    assert context_after.target_profit_lot_band == "3-4"
    assert context_after.current_base_target_profit_rate == pytest.approx(0.05)
    assert all(lot.effective_target_profit_rate == pytest.approx(0.05, abs=0.001) for lot in lots.open_lots("005930"))
    assert action_at_four_percent is None or action_at_four_percent.side is not OrderSide.SELL

    action_at_five_percent = strategy.decide(position, 10500, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    assert action_at_five_percent is not None
    assert action_at_five_percent.side is OrderSide.SELL


def test_lot_sizing_age_decay_applies_to_current_lot_band_target() -> None:
    _, lots, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig(estimated_fee_tax_pct=0))
    old = add_lot(positions, "005930", 10000, 1)
    age_lot(old, 4)
    for _ in range(4):
        add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10200)

    action = strategy.decide(position, 10200, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert old.effective_target_profit_rate == pytest.approx(0.02, abs=0.001)
    assert action is not None
    assert action.lot_id == old.lot_id
    assert action.sell_reason == SellReason.PROFIT_TAKE.value


def test_lot_sizing_dynamic_target_does_not_change_realized_pnl_sell_reason_rules() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig(cleanup_enabled=True, estimated_fee_tax_pct=0), daily_profit_loss=10_000)
    lot = add_lot(positions, "005930", 10000, 1)
    age_lot(lot, 12)
    for _ in range(4):
        add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 9800)

    action = strategy.decide(position, 9800, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.sell_reason == SellReason.CLEANUP_SELL.value


def test_legacy_mode_keeps_exposure_based_target_profit_behavior() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy(StrategyConfig(lot_sizing_mode="legacy_exposure_bands"))
    for _ in range(5):
        add_lot(positions, "005930", 10000, 1)
    position = positions.refresh_from_lots("005930", 10400)

    action = strategy.decide(position, 10400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))
    context = strategy.context(position, 10400)

    assert context.target_profit_source == "exposure_sell_band"
    assert context.target_profit_rate == pytest.approx(0.06)
    assert action is None or action.side is not OrderSide.SELL
