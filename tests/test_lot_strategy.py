from datetime import datetime, timedelta

from kis_msj.config import BotConfig, OrderConfig, StrategyConfig
from kis_msj.lot_manager import LotManager
from kis_msj.models import AccountSnapshot, LotState, OrderSide, PositionState, TradeFill
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskManager
from kis_msj.strategy import LotGridStrategy


def setup_strategy():
    config = BotConfig(order=OrderConfig(price_sample_interval_seconds=0), strategy=StrategyConfig())
    lots = LotManager(config.strategy)
    positions = PositionManager(config.strategy, lots)
    strategy = LotGridStrategy(config, lots)
    risk = RiskManager(config)
    snapshot = AccountSnapshot(10_000_000, 10_000_000, 0, 0, ())
    return config, lots, positions, strategy, risk, snapshot


def add_lot(positions: PositionManager, code: str, price: int, quantity: int, minutes_ago: int = 0) -> LotState:
    fill = TradeFill(code, "Test", OrderSide.BUY, quantity, price, f"BUY-{price}-{quantity}", datetime.now() - timedelta(minutes=minutes_ago))
    positions.apply_fill(fill)
    return positions.lot_manager.last_buy_lot(code)  # type: ignore[return-value]


def test_initial_buy_creates_candidate_action() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    position = positions.get("005930", "Test")
    account = risk.account_buy_allowed(snapshot, positions.positions)
    symbol = risk.symbol_buy_allowed(position)

    action = strategy.decide(position, 70000, snapshot, account, symbol)

    assert action is not None
    assert action.side is OrderSide.BUY
    assert action.amount == 300000


def test_minus_four_percent_add_buy_under_600k() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 30)
    position = positions.refresh_from_lots("005930", 9600)

    action = strategy.decide(position, 9600, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 300000
    assert "4%" in action.reason


def test_minus_five_percent_after_600k_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 61)
    position = positions.refresh_from_lots("005930", 9500)

    action = strategy.decide(position, 9500, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 300000
    assert "5%" in action.reason


def test_minus_six_percent_after_1_2m_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 121)
    position = positions.refresh_from_lots("005930", 9400)

    action = strategy.decide(position, 9400, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 400000
    assert "6%" in action.reason


def test_minus_eight_percent_after_2m_exposure() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 201)
    position = positions.refresh_from_lots("005930", 9200)

    action = strategy.decide(position, 9200, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is not None
    assert action.amount == 500000
    assert "8%" in action.reason


def test_exposure_above_3m_blocks_auto_buy_and_marks_review() -> None:
    _, _, positions, strategy, risk, snapshot = setup_strategy()
    add_lot(positions, "005930", 10000, 301)
    position = positions.refresh_from_lots("005930", 9000)

    action = strategy.decide(position, 9000, snapshot, risk.account_buy_allowed(snapshot, positions.positions), risk.symbol_buy_allowed(position))

    assert action is None
    assert position.needs_review
    assert not position.auto_buy_enabled


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


def test_lot_quantity_mismatch_blocks_new_buy() -> None:
    config, lots, positions, _, _, _ = setup_strategy()
    position = PositionState(code="005930", quantity=99, lot_quantity_mismatch=True)
    decision = RiskManager(config).symbol_buy_allowed(position)

    assert not decision.allowed
    assert "lot_quantity_mismatch" in decision.reasons
