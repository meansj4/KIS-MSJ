"""Actual-fill streaks, restart recovery, partial LOTs and retroactive decay."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import sqlite3

import pytest

from kis_msj.config import StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import OrderSide, PositionState, TradeFill
from kis_msj.position_manager import PositionManager
from kis_msj.storage import StateStore


NOW = datetime(2026, 10, 23, 10)
CODE = "005930"


def managers(**options):
    config = StrategyConfig(adaptive_lot_enabled=True, estimated_fee_tax_pct=0, **options)
    lots = LotManager(config)
    return config, lots, PositionManager(config, lots)


def buy(positions, order="buy", *, at=NOW, quantity=2, price=10_000, execution="", code=CODE, lot_id=""):
    fill = TradeFill(code, "Test", OrderSide.BUY, quantity, price, order, at, lot_id, execution)
    return positions.apply_fill(fill), fill


def sell(positions, lot, order="sell", *, at=NOW, quantity=1, price=10_500, execution=""):
    fill = TradeFill(lot.code, "Test", OrderSide.SELL, quantity, price, order, at, lot.lot_id, execution)
    return positions.apply_fill(fill), fill


def test_streaks_follow_actual_orders_and_are_independent_per_symbol():
    _, lots, positions = managers()
    position = positions.get(CODE)
    positions.mark_order_requested(CODE, "unfilled", "REQUESTED")
    assert position.consecutive_buy_count == position.consecutive_sell_count == 0
    buy(positions, "one", at=NOW)
    buy(positions, "two", at=NOW + timedelta(seconds=1))
    buy(positions, "another-symbol", code="000660")
    assert position.consecutive_buy_count == 2
    assert positions.get("000660").consecutive_buy_count == 1
    lot = lots.open_lots(CODE)[0]
    sell(positions, lot, at=NOW + timedelta(seconds=2))
    assert (position.consecutive_buy_count, position.consecutive_sell_count) == (0, 1)
    assert position.rebuy_restriction_active
    assert position.last_sell_price == 10_500
    buy(positions, "three", at=NOW + timedelta(seconds=3))
    assert (position.consecutive_buy_count, position.consecutive_sell_count) == (1, 0)
    assert not position.rebuy_restriction_active


def test_partial_buy_is_one_order_one_lot_with_original_time_and_weighted_cost():
    _, lots, positions = managers()
    position, first = buy(positions, execution="first", quantity=1)
    lot = lots.open_lots(CODE)[0]
    position, second = buy(positions, execution="second", quantity=3, price=9_600,
                           at=NOW + timedelta(minutes=1))
    assert len(lots.open_lots(CODE)) == 1
    assert lot.buy_quantity == lot.remaining_quantity == 4
    assert lot.buy_amount == 38_800
    assert lot.buy_price == 9_700
    assert lot.buy_filled_at == NOW.isoformat(timespec="microseconds")
    assert position.daily_buy_amount == 38_800
    assert position.consecutive_buy_count == 1
    positions.apply_fill(first)
    positions.apply_fill(second)
    assert lot.buy_quantity == 4
    assert position.daily_buy_amount == 38_800


def test_partial_sell_counts_once_and_renews_latest_actual_sell_price():
    _, lots, positions = managers()
    position, _ = buy(positions, quantity=4)
    lot = lots.open_lots(CODE)[0]
    sell(positions, lot, at=NOW + timedelta(minutes=1), execution="s1")
    _, partial = sell(positions, lot, at=NOW + timedelta(minutes=2), price=10_600, execution="s2")
    assert position.consecutive_sell_count == 1
    assert position.rebuy_restriction_active
    assert position.last_sell_price == 10_600
    positions.apply_fill(partial)
    assert lot.remaining_quantity == 2
    assert position.daily_sell_amount == 21_100


def test_restriction_and_partial_order_deduplication_survive_restart(tmp_path):
    config, lots, positions = managers()
    position, _ = buy(positions, quantity=4, execution="b1")
    lot = lots.open_lots(CODE)[0]
    _, partial = sell(positions, lot, at=NOW + timedelta(minutes=1), execution="s1")
    store = StateStore(tmp_path / "state.db")
    store.save_positions(positions.positions.values())
    store.save_lots(lots.lots.values())
    restored_lots = LotManager(config, store.load_lots())
    restored = PositionManager(config, restored_lots, store.load_positions())
    position = restored.get(CODE)
    assert position.rebuy_restriction_active is True
    assert position.consecutive_sell_count == 1
    restored.apply_fill(partial)
    assert restored_lots.open_lots(CODE)[0].remaining_quantity == 3
    sell(restored, restored_lots.open_lots(CODE)[0], at=NOW + timedelta(minutes=2), execution="s2")
    assert position.consecutive_sell_count == 1
    buy(restored, "rebuy", at=NOW + timedelta(minutes=3))
    assert not position.rebuy_restriction_active
    assert position.consecutive_buy_count == 1


def test_first_upgrade_recovers_streaks_and_restriction_from_fill_history(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.save_position(PositionState(CODE))
    history = [
        TradeFill(CODE, "Test", OrderSide.BUY, 2, 10_000, "b", NOW, execution_id="b1"),
        TradeFill(CODE, "Test", OrderSide.BUY, 2, 10_000, "b", NOW + timedelta(seconds=1), execution_id="b2"),
        TradeFill(CODE, "Test", OrderSide.SELL, 1, 10_500, "s1", NOW + timedelta(seconds=2), execution_id="s1"),
        TradeFill(CODE, "Test", OrderSide.SELL, 1, 10_600, "s2", NOW + timedelta(seconds=3), execution_id="s2"),
    ]
    for fill in history:
        assert store.record_fill(fill)
    restored = store.load_positions()[CODE]
    assert (restored.consecutive_buy_count, restored.consecutive_sell_count) == (0, 2)
    assert restored.rebuy_restriction_active
    assert restored.last_sell_price == 10_600
    store.save_position(restored)
    assert store.load_positions()[CODE].actual_fill_events == restored.actual_fill_events


def test_late_reported_older_sell_does_not_override_more_recent_sale_or_rebuy():
    _, lots, positions = managers()
    position, _ = buy(positions, quantity=6)
    lot = lots.open_lots(CODE)[0]
    sell(positions, lot, "newer", at=NOW + timedelta(minutes=3), price=10_600)
    sell(positions, lot, "older", at=NOW + timedelta(minutes=2), price=10_500)
    assert position.last_sell_price == 10_600
    assert position.consecutive_sell_count == 2
    buy(positions, "rebuy", at=NOW + timedelta(minutes=4))
    sell(positions, lot, "delayed", at=NOW + timedelta(minutes=1), price=10_400)
    assert position.last_sell_price == 10_600
    assert not position.rebuy_restriction_active
    assert (position.consecutive_buy_count, position.consecutive_sell_count) == (1, 0)
    assert position.quantity == 5


def test_equal_timestamps_keep_recorded_execution_sequence():
    _, lots, positions = managers()
    position, _ = buy(positions)
    lot = lots.open_lots(CODE)[0]
    sell(positions, lot)
    buy(positions, "rebuy")
    assert not position.rebuy_restriction_active
    assert (position.consecutive_buy_count, position.consecutive_sell_count) == (1, 0)


def test_broker_order_number_reused_next_day_is_a_new_trade():
    _, lots, positions = managers()
    position, _ = buy(positions, "123", at=NOW)
    buy(positions, "123", at=NOW + timedelta(days=1))
    assert len(lots.open_lots(CODE)) == 2
    assert position.consecutive_buy_count == 2


def test_decay_recomputes_retroactively_from_current_count_and_surviving_lots():
    _, lots, positions = managers()
    buy(positions, "old", at=NOW - timedelta(days=14), quantity=1)
    old = lots.open_lots(CODE)[0]
    assert lots.effective_target_profit_rate(old, NOW) == pytest.approx(0.0282)
    buy(positions, "middle", at=NOW - timedelta(days=7), quantity=1)
    assert lots.effective_target_profit_rate(old, NOW) == pytest.approx(0.0264)
    buy(positions, "new", at=NOW, quantity=1)
    assert lots.effective_target_profit_rate(old, NOW) == pytest.approx(0.0246)
    middle, new = lots.open_lots(CODE)[1:]
    sell(positions, middle, "s-middle", at=NOW + timedelta(seconds=1))
    sell(positions, new, "s-new", at=NOW + timedelta(seconds=2))
    # Two sells lift the base to 3.6%; one surviving LOT decays by 0.2 pp.
    assert lots.effective_target_profit_rate(old, NOW) == pytest.approx(0.0282)


@pytest.mark.parametrize(("volatility", "sell_count", "weeks", "expected"), [
    (1.0, 2, 8, 0.0222),
    (2.0, 3, 24, 0.0362),
    (1.0, 2, 50, -0.0198),
])
def test_decay_is_subtracted_after_all_positive_multipliers(volatility, sell_count, weeks, expected):
    _, lots, positions = managers()
    buy(positions, at=NOW - timedelta(weeks=weeks))
    lot = lots.open_lots(CODE)[0]
    lots.set_volatility_multiplier(CODE, volatility)
    lots.set_symbol_trade_state(CODE, sell_count)
    assert lots.effective_target_profit_rate(lot, NOW) == pytest.approx(expected)


def test_negative_profit_target_is_sellable_but_nonpositive_target_is_invalid():
    from kis_msj.arcg_backtest import historical_clock
    # Include the final completed session independently of the test run's time.
    end = NOW.replace(hour=16)
    with historical_clock(end):
        _, lots, positions = managers()
        buy(positions, at=end - timedelta(weeks=50), quantity=1)
        lot = lots.open_lots(CODE)[0]
        assert lots.profit_take_lots(CODE, 9_800, 10_000) == [lot]
        lot.buy_filled_at = (end - timedelta(weeks=2_000)).isoformat()
        assert lots.profit_take_lots(CODE, 9_800, 10_000) == []
        assert lot.target_sell_price == 0


def test_lot_holding_time_uses_real_elapsed_time_across_timezone_offsets():
    _, lots, positions = managers()
    buy(positions, at=NOW.replace(tzinfo=timezone(timedelta(hours=9))))
    lot = lots.open_lots(CODE)[0]
    utc_one_week_later = (NOW + timedelta(days=7, hours=-9)).replace(tzinfo=timezone.utc)
    assert lots.age_weeks(lot, utc_one_week_later) == pytest.approx(0.8)


def test_distinct_same_timestamp_executions_survive_storage_and_count_once(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _, lots, positions = managers()
    first = TradeFill(CODE, "Test", OrderSide.BUY, 1, 10_000, "one", NOW, execution_id="exec1")
    second = replace(first, execution_id="exec2")
    assert store.record_fill(first)
    assert store.record_fill(second)
    assert not store.record_fill(first)
    positions.apply_fill(first)
    position = positions.apply_fill(second)
    assert len(lots.open_lots(CODE)) == 1
    assert lots.open_lots(CODE)[0].remaining_quantity == 2
    assert position.consecutive_buy_count == 1
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2


def test_reused_execution_number_next_day_is_a_new_fill(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _, lots, positions = managers()
    first = TradeFill(CODE, "Test", OrderSide.BUY, 1, 10_000, "123", NOW, execution_id="1")
    second = replace(first, filled_at=NOW + timedelta(days=1))
    assert store.record_fill(first)
    assert store.record_fill(second)
    positions.apply_fill(first)
    position = positions.apply_fill(second)
    assert len(lots.open_lots(CODE)) == 2
    assert position.consecutive_buy_count == 2


def test_journal_history_is_counted_without_assuming_lot_application(tmp_path):
    config, lots, _ = managers()
    store = StateStore(tmp_path / "state.db")
    store.save_position(PositionState(CODE))
    fill = TradeFill(CODE, "Test", OrderSide.BUY, 2, 10_000, "123", NOW, execution_id="e")
    assert store.record_fill(fill)
    positions = PositionManager(config, lots, store.load_positions())
    assert positions.get(CODE).consecutive_buy_count == 1
    positions.apply_fill(fill)
    assert positions.get(CODE).quantity == 2
    assert positions.get(CODE).consecutive_buy_count == 1
    positions.apply_fill(fill)
    assert positions.get(CODE).quantity == 2


def test_repairing_older_journal_sale_preserves_newer_sale_price_and_rebuy(tmp_path):
    config, lots, positions = managers()
    position, buy_fill = buy(positions, quantity=6)
    lot = lots.open_lots(CODE)[0]
    position.actual_fill_events = ""
    store = StateStore(tmp_path / "state.db")
    store.save_position(position)
    store.save_lots(lots.lots.values())
    old_sell = TradeFill(CODE, "Test", OrderSide.SELL, 1, 10_500, "old", NOW + timedelta(seconds=1), lot.lot_id, "s1")
    new_sell = replace(old_sell, order_id="new", price=10_600, filled_at=NOW + timedelta(seconds=2), execution_id="s2")
    rebuy_fill = replace(buy_fill, order_id="rebuy", filled_at=NOW + timedelta(seconds=3), execution_id="b2")
    for fill in (buy_fill, old_sell, new_sell, rebuy_fill):
        store.record_fill(fill)
    restored = PositionManager(config, LotManager(config, store.load_lots()), store.load_positions())
    restored.apply_fill(old_sell)
    position = restored.get(CODE)
    assert position.quantity == 5
    assert position.last_sell_price == 10_600
    assert not position.rebuy_restriction_active
    assert (position.consecutive_buy_count, position.consecutive_sell_count) == (1, 0)


def test_legacy_fill_constraint_migration_preserves_ids_metadata_and_custom_columns(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, name TEXT NOT NULL,
            side TEXT NOT NULL, quantity INTEGER NOT NULL, price INTEGER NOT NULL,
            order_id TEXT NOT NULL, filled_at TEXT NOT NULL, lot_id TEXT NOT NULL,
            execution_id TEXT NOT NULL DEFAULT '', sell_reason TEXT NOT NULL DEFAULT 'UNKNOWN',
            reentry_type TEXT NOT NULL DEFAULT 'NONE', operator_note TEXT DEFAULT '',
            UNIQUE(order_id,code,side,quantity,price,filled_at,lot_id))""")
        connection.execute("""INSERT INTO fills
            (id,code,name,side,quantity,price,order_id,filled_at,lot_id,execution_id,operator_note)
            VALUES (7,?,'Test','BUY',1,10000,'one',?,'','exec1','keep me')""", (CODE, NOW.isoformat()))
        StateStore._ensure_session_metadata(connection)
        connection.execute("INSERT INTO fill_session_metadata VALUES (7,'REGULAR','REGULAR','KRX','','','known')")
        connection.execute("INSERT INTO fill_lot_attribution VALUES (7,'lot',1,0,0)")
        connection.execute("CREATE INDEX custom_fills_note ON fills(operator_note)")
    store = StateStore(path)
    new_fill = TradeFill(CODE, "Test", OrderSide.BUY, 1, 10_000, "one", NOW, execution_id="exec2")
    assert store.record_fill(new_fill)
    with store._connect() as connection:
        previous = connection.execute("SELECT * FROM fills WHERE id=7").fetchone()
        assert previous["operator_note"] == "keep me"
        assert connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2
        assert connection.execute("SELECT fill_id FROM fill_session_metadata").fetchone()[0] == 7
        assert connection.execute("SELECT fill_id FROM fill_lot_attribution").fetchone()[0] == 7
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='custom_fills_note'").fetchone()
    assert list((tmp_path / "backups").glob("*.db"))


def test_exact_partial_fill_notionals_preserve_fractional_lot_basis_and_latest_sale(tmp_path):
    config, lots, positions = managers()
    store = StateStore(tmp_path / "state.db")
    first = TradeFill(CODE, "Test", OrderSide.BUY, 3, 10_000, "buy", NOW,
                      execution_id="b1", filled_amount=30_001)
    second = replace(first, quantity=2, price=9_000, filled_at=NOW + timedelta(seconds=1),
                     execution_id="b2", filled_amount=18_001)
    for fill in (first, second):
        assert store.record_fill(fill)
        positions.apply_fill(fill)
    lot = lots.open_lots(CODE)[0]
    assert lot.buy_amount == 48_002
    assert lot.buy_price == pytest.approx(48_002 / 5)
    assert positions.get(CODE).daily_buy_amount == 48_002
    store.save_positions(positions.positions.values())
    store.save_lots(lots.lots.values())
    restored = PositionManager(config, LotManager(config, store.load_lots()), store.load_positions())
    assert restored.lot_manager.open_lots(CODE)[0].buy_price == pytest.approx(48_002 / 5)
    assert store.recorded_fill_amount(CODE, "buy", OrderSide.BUY, filled_at=NOW) == 48_002
    sell_one = TradeFill(CODE, "Test", OrderSide.SELL, 2, 10_500.5, "sell", NOW + timedelta(seconds=2),
                         lot.lot_id, "s1", filled_amount=21_001)
    sell_two = replace(sell_one, quantity=1, price=11_000, filled_at=NOW + timedelta(seconds=3),
                       execution_id="s2", filled_amount=11_000)
    for fill in (sell_one, sell_two):
        assert store.record_fill(fill)
        restored.apply_fill(fill)
    position = restored.get(CODE)
    assert position.last_sell_price == 11_000
    assert position.daily_sell_amount == position.cycle_sell_value_amount == 32_001
    assert position.consecutive_sell_count == 1
    assert position.rebuy_restriction_active
    assert store.recorded_fill_amount(CODE, "sell", OrderSide.SELL, filled_at=NOW, lot_id=lot.lot_id) == 32_001
    with store._connect() as connection:
        assert connection.execute("SELECT price FROM fills WHERE execution_id='s1'").fetchone()[0] == 10_500.5


def test_recorded_fill_totals_use_exact_amount_and_normalized_day_order_scope(tmp_path):
    store = StateStore(tmp_path / "state.db")
    day_one = TradeFill(CODE, "Test", OrderSide.BUY, 3, 10_000, "000123", NOW,
                        execution_id="one", filled_amount=30_001)
    legacy = replace(day_one, quantity=1, price=10_050, execution_id="legacy", filled_amount=0,
                     filled_at=NOW + timedelta(seconds=1))
    day_two = replace(day_one, quantity=2, price=9_000.5, filled_at=NOW + timedelta(days=1),
                      execution_id="two", filled_amount=18_001)
    for fill in (day_one, legacy, day_two):
        assert store.record_fill(fill)
    assert store.recorded_fill_amount(CODE, "123", OrderSide.BUY, filled_at=NOW) == 40_051
    assert store.filled_quantity_for_order("123", code=CODE, side=OrderSide.BUY, filled_at=NOW) == 4
    assert store.recorded_fill_amount(CODE, "123", OrderSide.BUY, filled_at=day_two.filled_at) == 18_001
    assert store.filled_quantity_for_order("123", code=CODE, side=OrderSide.BUY, filled_at=day_two.filled_at) == 2
