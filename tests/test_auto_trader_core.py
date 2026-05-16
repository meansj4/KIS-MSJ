from datetime import datetime

from kis_msj.config import BotConfig, OrderConfig, RiskConfig, StockConfig, StrategyConfig
from kis_msj.models import AccountSnapshot, OrderSide, PositionState, TradeFill
from kis_msj.order_manager import _round_price
from kis_msj.position_manager import PositionManager
from kis_msj.risk_manager import RiskManager
from kis_msj.strategy import GridStrategy


def test_position_updates_only_from_fill() -> None:
    manager = PositionManager()
    manager.mark_order_requested("005930", "ORDER-1", "REQUESTED")

    position = manager.get("005930")
    assert position.quantity == 0
    assert not position.is_filled

    manager.apply_fill(
        TradeFill(
            code="005930",
            name="Samsung",
            side=OrderSide.BUY,
            quantity=4,
            price=70000,
            order_id="ORDER-1",
            filled_at=datetime.now(),
        )
    )

    position = manager.get("005930")
    assert position.quantity == 4
    assert position.average_price == 70000
    assert position.total_buy_amount == 280000
    assert position.is_filled


def test_strategy_add_buy_and_take_profit_thresholds() -> None:
    config = BotConfig(
        stocks=(StockConfig("005930"),),
        order=OrderConfig(price_sample_interval_seconds=0),
        strategy=StrategyConfig(),
    )
    strategy = GridStrategy(config)
    snapshot = AccountSnapshot(10_000_000, 10_000_000, 0, 0, 0, ())
    allowed = RiskManager(config).account_buy_allowed(snapshot, {})
    position = PositionState(code="005930", quantity=10, average_price=10000, last_fill_price=10000)

    add_buy = strategy.decide(position, 9600, snapshot, allowed, allowed)
    assert add_buy is not None
    assert add_buy.side is OrderSide.BUY
    assert add_buy.reason == "add_buy_stage_1"

    take_profit = strategy.decide(position, 10400, snapshot, allowed, allowed)
    assert take_profit is not None
    assert take_profit.side is OrderSide.SELL
    assert take_profit.reason == "take_profit_stage_1"


def test_risk_manager_blocks_market_risk_mode() -> None:
    config = BotConfig(risk=RiskConfig(market_risk_mode=True))
    snapshot = AccountSnapshot(10_000_000, 10_000_000, 0, 0, 0, ())

    decision = RiskManager(config).account_buy_allowed(snapshot, {})

    assert not decision.allowed
    assert "market_risk_mode" in decision.reasons


def test_round_price_uses_domestic_tick_units() -> None:
    assert _round_price(50123) == 50100
    assert _round_price(100321) == 100500
