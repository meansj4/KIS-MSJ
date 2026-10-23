"""The requested LOT policy, with deterministic time and no broker calls."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from kis_msj.adaptive_policy import final_sell_profit_pct, lot_decay_pp, trend_multiplier
from kis_msj.config import BotConfig, OrderConfig, PriceLotBand, StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import AccountSnapshot, OrderSide, PositionState, TradeFill
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskDecision
from kis_msj.strategy import LotGridStrategy
from kis_msj.volatility import volatility_multiplier

NOW = datetime(2026, 10, 23, 12)
CODE = "111111"


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    from kis_msj import lot_manager, position_manager, strategy

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(tz)

    for module in (lot_manager, position_manager, strategy):
        monkeypatch.setattr(module, "datetime", Clock)


def setup(**settings):
    config = BotConfig(strategy=StrategyConfig(adaptive_lot_enabled=True, estimated_fee_tax_pct=0,
                                               **settings), order=OrderConfig())
    lots = LotManager(config.strategy)
    positions = PositionManager(config.strategy, lots)
    strategy = LotGridStrategy(config, lots)
    strategy.positions = positions.positions
    return config, lots, positions, strategy


def add(positions, price=10_000, days=0, code=CODE, quantity=2):
    index = len(positions.lot_manager.lots)
    fill = TradeFill(code, code, OrderSide.BUY, quantity, price, f"buy{index}",
                     NOW - timedelta(days=days), f"lot{index}", f"execution{index}")
    positions.apply_fill(fill)
    return positions.lot_manager.lots[f"lot{index}"]


def cash(value=10_000_000, total=20_000_000):
    return AccountSnapshot(value, total, 0, 0)


def decide(strategy, position, price, snapshot=None):
    return strategy.decide(position, price, snapshot or cash(), RiskDecision(True), RiskDecision(True))


@pytest.mark.parametrize("count", range(2, 13))
def test_consecutive_buy_gap_expands_beyond_ten_percent(count):
    _, lots, positions, strategy = setup()
    add(positions)
    position = positions.refresh_from_lots(CODE, 8_000)
    position.consecutive_buy_count = count
    details = strategy.adaptive_buy_details(position, 8_000, cash())
    assert details["effective_buy_gap_pct"] == pytest.approx(4)
    assert details["normal_buy_target"] == pytest.approx(10_000 * (1 - details["effective_buy_gap_pct"] / 100))


@pytest.mark.parametrize("count", range(2, 13))
def test_consecutive_sell_target_and_decay_order(count):
    assert final_sell_profit_pct(2, count, 2.4) == pytest.approx(6 * trend_multiplier(count) - 2.4)


@pytest.mark.parametrize("vol20,vol60,buy,sell", [(14.7, 20, 4, 3), (125,117,8,6)])
def test_reference_volatility_examples(vol20, vol60, buy, sell):
    multiplier = volatility_multiplier(vol20 * .3 + vol60 * .7)
    assert 4 * multiplier == pytest.approx(buy, abs=.01)
    assert 3 * multiplier == pytest.approx(sell, abs=.01)


def test_buy_uses_lowest_active_lot_and_recent_sale_minimum():
    _, lots, positions, strategy = setup()
    add(positions, 12_000)
    add(positions, 10_000)
    add(positions, 11_000)
    position = positions.refresh_from_lots(CODE, 9_500)
    position.consecutive_buy_count = 1
    position.rebuy_restriction_active = True
    position.last_sell_price = 9_000
    details = strategy.adaptive_buy_details(position, 9_000, cash())
    assert details["normal_buy_target"] == 9_768
    assert details["reentry_target"] == 8_640
    assert details["buy_target_price"] == 8_640
    assert decide(strategy, position, 9_000) is None
    action = decide(strategy, position, 8_640)
    assert action.side is OrderSide.BUY and action.limit_price == 8_640


def test_reentry_drop_has_no_trend_multiplier_and_initial_rules_still_apply():
    _, lots, positions, strategy = setup()
    position = positions.get(CODE)
    position.rebuy_restriction_active = True
    position.last_sell_price = 10_000
    position.consecutive_buy_count = 12
    lots.set_volatility_multiplier(CODE, 2)
    details = strategy.adaptive_buy_details(position, 9_000, cash())
    assert details["reentry_target"] == 9_200
    assert details["effective_buy_gap_pct"] == 0  # No holdings: initial entry
    assert decide(strategy, position, 9_200).reason == "adaptive_reentry_buy"
    assert decide(strategy, position, 9_210) is None


@pytest.mark.parametrize("count,expected", [(1,.08),(12,.96)])
def test_week_decay(count, expected):
    assert lot_decay_pp(NOW - timedelta(days=7), NOW, count) == pytest.approx(expected)


def test_retroactive_decay_age_and_targets_rise_when_lot_count_falls():
    _, lots, positions, strategy = setup()
    first = add(positions, days=14)
    position = positions.get(CODE)
    position.consecutive_sell_count = 0
    strategy.context(position, 10_000, cash())
    assert first.effective_target_profit_rate == pytest.approx(.0282)
    second = add(positions, days=7)
    third = add(positions)
    strategy.context(position, 10_000, cash())
    assert first.effective_target_profit_rate == pytest.approx(.0246)
    assert second.effective_target_profit_rate == pytest.approx(.0276)
    assert third.effective_target_profit_rate == pytest.approx(.03)
    second.remaining_quantity = third.remaining_quantity = 0
    strategy.context(position, 10_000, cash())
    assert first.effective_target_profit_rate == pytest.approx(.0282)


def test_decay_negative_sells_same_lot_without_cash_priority_override():
    _, lots, positions, strategy = setup(cash_target_profit_enabled=True,
                                         cash_target_low_cash_lot_policy="oldest")
    old = add(positions, 10_000, days=175)
    expensive = add(positions, 20_000, days=0)
    position = positions.refresh_from_lots(CODE, 9_900)
    position.consecutive_sell_count = 2
    action = decide(strategy, position, 9_900, cash(0))
    assert old.effective_target_profit_rate == pytest.approx(-.0196)
    assert action.side is OrderSide.SELL and action.lot_id == old.lot_id
    assert action.limit_price >= 9_810
    assert expensive.remaining_quantity == 2


def test_invalid_negative_price_target_does_not_create_sale():
    _, lots, positions, strategy = setup()
    lot = add(positions, days=8_000)
    position = positions.refresh_from_lots(CODE, 1)
    assert lot_decay_pp(lot.buy_filled_at, NOW, 1) > 100
    assert strategy._sell_candidate(position, 1, cash()) is None


def test_reserve_cash_uses_portfolio_slots_and_buffer_without_double_pending_subtraction():
    _, lots, positions, strategy = setup(reentry_buy_cooldown_minutes=0)
    add(positions, code="222222")
    other = positions.get("222222")
    other.lot_unit_amount = 30_000
    other.max_symbol_amount = 360_000
    other.max_lots_per_symbol = 12
    position = positions.get(CODE)
    position.current_price = 10_000
    position.rebuy_restriction_active = True
    position.last_sell_price = 11_000
    required = strategy.adaptive_reentry_cash_details(position, cash(0, 1_000_000), 30_000)
    # 2 future LOTs on each of two symbols, plus 10% safety, plus order.
    assert required["reentry_required_cash"] == 250_000
    assert not strategy.adaptive_reentry_cash_details(position, cash(249_999, 1_000_000), 30_000)["reentry_cash_condition_met"]
    assert strategy.adaptive_reentry_cash_details(position, cash(250_000, 1_000_000), 30_000)["reentry_cash_condition_met"]
    other.max_lots_per_symbol = 1
    assert strategy.adaptive_reentry_cash_details(position, cash(190_000, 1_000_000), 30_000)["reentry_required_cash"] == 190_000


def test_risk_retirement_max_lots_and_normal_buy_cash_still_block():
    _, lots, positions, strategy = setup()
    add(positions)
    position = positions.refresh_from_lots(CODE, 9_000)
    assert decide(strategy, position, 9_000, cash(1)) is None
    position.max_lots_per_symbol = 1
    assert decide(strategy, position, 9_000) is None
    position.retire_after_exit = True
    position.max_lots_per_symbol = 12
    positions.lot_manager.lots.clear()
    position.quantity = position.cumulative_invested_amount = 0
    assert decide(strategy, position, 9_000) is None


def test_fractional_calendar_age_timezone_and_restart_recalculation():
    aware = NOW.replace(tzinfo=timezone(timedelta(hours=9)))
    bought = (aware - timedelta(days=7, hours=12)).astimezone(timezone.utc)
    assert lot_decay_pp(bought.isoformat(), NOW, 2) == pytest.approx(.16)
    _, lots, positions, strategy = setup()
    lot = add(positions, days=7)
    lots.update_lot_target_metadata(lot, 10_000)
    first = lot.effective_target_profit_rate
    restored = LotManager(lots.config, {lot.lot_id: replace(lot)})
    restored.update_lot_target_metadata(restored.lots[lot.lot_id], 10_000)
    assert restored.lots[lot.lot_id].effective_target_profit_rate == first


def test_partial_buy_session_attribution_tracks_merged_lot_deltas():
    import logging
    from types import SimpleNamespace
    from kis_msj.main import AutoTrader

    config, lots, positions, _ = setup()
    recorded = []
    bot = SimpleNamespace(config=config, _session_audit_active=True, lot_manager=lots,
        position_manager=positions, logger=logging.getLogger(__name__),
        store=SimpleNamespace(record_session_lot_application=lambda *args: recorded.append(args)))
    first = TradeFill(CODE, CODE, OrderSide.BUY, 2, 10_000, "partial-order", NOW,
                      "merged-lot", "part-one")
    second = replace(first, quantity=3, execution_id="part-two", filled_at=NOW + timedelta(seconds=1))
    AutoTrader._apply_fill_with_session_audit(bot, first)
    AutoTrader._apply_fill_with_session_audit(bot, second)
    AutoTrader._apply_fill_with_session_audit(bot, second)
    assert lots.lots["merged-lot"].remaining_quantity == 5
    assert [(row[1], row[2]) for row in recorded] == [("merged-lot", 2), ("merged-lot", 3)]


def test_reentry_reserve_fails_closed_on_unreconciled_holdings_or_invalid_assets():
    _, _, positions, strategy = setup()
    position = positions.get(CODE)
    position.current_price = 10_000
    positions.get("222222").quantity = 5
    assert not strategy.adaptive_reentry_cash_details(position, cash(), 30_000)["reentry_cash_condition_met"]
    positions.get("222222").quantity = 0
    assert not strategy.adaptive_reentry_cash_details(position, cash(total=float("inf")), 30_000)["reentry_cash_condition_met"]
