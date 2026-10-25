"""The dashboard reconstructs adaptive targets from persisted, read-only data."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kis_msj.config import BotConfig, StockConfig, StrategyConfig
from kis_msj.lot_manager import ceil_price
from kis_msj.models import LotState, PositionState
from kis_msj.runtime_control import RuntimeControl, save_runtime_control
from kis_msj.storage import StateStore
from kis_msj.ui_service import UIService
from kis_msj.volatility import VolatilityEstimate


NOW = datetime(2026, 10, 23, 10)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 23, 10, tzinfo=tz)
    monkeypatch.setattr("kis_msj.ui_service.datetime", Clock)
    monkeypatch.setattr("kis_msj.lot_manager.datetime", Clock)
    monkeypatch.setattr("kis_msj.strategy.datetime", Clock)
    monkeypatch.setattr("kis_msj.ui_service.KisClient", lambda *args, **kwargs: pytest.fail("UI must stay offline"))


def make_service(tmp_path, *, current_price=9_000, restricted=False):
    config = BotConfig(
        stocks=(StockConfig("005930", "Test"),),
        strategy=StrategyConfig(
            adaptive_lot_enabled=True, cash_target_profit_enabled=True,
            cash_buy_drop_enabled=True, reentry_policy="cash_capacity_oldest",
            estimated_fee_tax_pct=0,
        ),
        storage_path=str(tmp_path / "state.sqlite3"), log_path=str(tmp_path / "bot.log"),
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(asdict(config)), encoding="utf-8")
    store = StateStore(config.storage_path)
    lots = [LotState(f"lot-{index}", "005930", (NOW - timedelta(days=7)).isoformat(),
                     price, 1, price, 1, 50, price * 2)
            for index, price in enumerate((10_000, 11_000))]
    store.save_lots(lots)
    position = PositionState(
        "005930", "Test", quantity=2, current_price=current_price,
        cumulative_invested_amount=21_000, position_state="HOLDING",
        consecutive_sell_count=3, rebuy_restriction_active=restricted,
        last_sell_price=11_000 if restricted else 0,
        lot_unit_amount=30_000, max_symbol_amount=360_000, max_lots_per_symbol=12,
        entry_price_for_lot_sizing=10_000, lot_sizing_mode="cycle_locked_by_entry_price", lot_sizing_bucket="3001-10000",
    )
    store.save_position(position)
    cache = {"version": 1, "symbols": {"005930": {
        "requested_through": "2026-10-22",
        "estimate": asdict(VolatilityEstimate(multiplier=2, gap_pct=8, reason="ok")),
    }}}
    Path(config.storage_path).with_suffix(".volatility.json").write_text(json.dumps(cache), encoding="utf-8")
    runtime_path = tmp_path / "runtime.json"
    save_runtime_control(RuntimeControl(), runtime_path)
    return UIService(config_path, runtime_path), store


def cash_log(monkeypatch, *, include_assets=True):
    suffix = " total_asset=200000000" if include_assets else ""
    stamp = NOW.isoformat(timespec="seconds")
    monkeypatch.setattr("kis_msj.ui_service.tail_hourly_logs", lambda *_: [
        f"{stamp} INFO cash_target_account_snapshot cash_available=100000000 snapshot_at={stamp}{suffix}",
    ])


def test_adaptive_lot_and_portfolio_targets_use_cached_volatility_streak_decay(tmp_path, monkeypatch):
    service, store = make_service(tmp_path)
    cash_log(monkeypatch)
    stored_before = {key: asdict(value) for key, value in store.load_lots().items()}
    rows = service.lots()
    expected = 0.03 * 2 - 2 * 0.001 * 4 / 5
    for row in rows:
        assert row["volatility_multiplier"] == 2
        assert row["consecutive_sell_count"] == 3
        assert row["effective_target_profit_rate"] == pytest.approx(expected)
        assert row["sell_trigger_price"] == ceil_price(row["buy_price"] * (1 + expected))
        assert not row["cash_target_sell_candidate"]
        assert not row["low_cash_oldest_lot_sell"]
    detail = service.portfolio_unrealized_detail()
    for row in detail["rows"]:
        assert row["effective_target_profit_rate"] == pytest.approx(expected)
        assert row["target_price"] == ceil_price(row["buy_price"] * (1 + expected))
    assert {key: asdict(value) for key, value in store.load_lots().items()} == stored_before


def test_stock_context_and_list_use_lowest_lot_with_cached_multiplier(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    cash_log(monkeypatch)
    detail = service.stock_detail("005930")
    context = detail["strategy_context"]
    assert context["volatility_multiplier"] == 2
    assert context["reference_buy_price"] == 10_500
    assert context["target_buy_drop_rate"] == pytest.approx(.12)
    assert context["adaptive_buy_target_price"] == 9_240
    assert context["current_base_target_profit_rate"] == pytest.approx(.06)
    assert detail["cash_buy_details"] == {}
    assert detail["adaptive_buy_details"]["buy_target_price"] == 9_240
    assert service.stocks()[0]["buy_target_price"] == 9_240


def test_unknown_cash_hides_adaptive_sell_preview(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, current_price=12_000)
    monkeypatch.setattr("kis_msj.ui_service.tail_hourly_logs", lambda *_: [])
    preview = service.decision_preview("005930")["previews"][0]
    assert not preview["action_created"]
    assert preview["final_block_reason"] == "cash_snapshot_unavailable"
    assert service.stock_detail("005930")["strategy_context"] == {}


def test_reentry_preview_reads_asset_buffer_and_uses_reserve_conditions(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, restricted=True)
    cash_log(monkeypatch)
    policy = service.cash_target_policy()
    assert policy["adaptive_lot_enabled"]
    assert not policy["cash_buy_drop_enabled"]
    assert not policy["cash_target_profit_enabled"]
    assert policy["total_asset_for_reserve"] == 200_000_000
    details = service.stock_detail("005930")["adaptive_buy_details"]
    assert details["reentry_safety_cash_buffer"] == 20_000_000
    assert details["reentry_future_lot_reserve"] == 60_000
    assert details["reentry_required_cash"] == 20_090_000
    assert details["reentry_cash_condition_met"]
    assert service.decision_preview("005930")["previews"][0]["side"] == "BUY"


def test_missing_asset_evidence_blocks_reentry_with_unknown_buffer(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, restricted=True)
    cash_log(monkeypatch, include_assets=False)
    details = service.stock_detail("005930")["adaptive_buy_details"]
    assert not details["reentry_cash_condition_met"]
    assert details["skip_reason"] == "adaptive_reentry_cash_insufficient"


def test_config_editor_exposes_adaptive_mode_and_reserve_units(tmp_path):
    service, _ = make_service(tmp_path)
    metadata = {item["key"]: item for item in service.config_schema()["metadata"]}
    assert metadata["strategy.adaptive_lot_enabled"]["type"] == "boolean"
    assert metadata["strategy.reentry_reserve_lots_per_symbol"]["config_format"] == "integer"
    assert metadata["strategy.reentry_cash_buffer_ratio"]["config_format"] == "decimal_rate"
    assert "이전 정책" in metadata["strategy.cash_buy_drop_enabled"]["description_ko"]


def test_fractional_actual_cost_and_last_sell_anchor_survive_ui_recomputation(tmp_path, monkeypatch):
    service, store = make_service(tmp_path, restricted=True)
    lots = store.load_lots()
    lots["lot-0"].buy_price = 10_000.5
    lots["lot-0"].buy_amount = 10_000.5
    store.save_lots(lots.values())
    position = store.load_positions()["005930"]
    position.last_sell_price = 9_950.75
    store.save_position(position)
    cash_log(monkeypatch)
    detail = service.stock_detail("005930")
    assert detail["strategy_context"]["reference_buy_price"] == 10_500.25
    assert detail["adaptive_buy_details"]["normal_buy_target"] == pytest.approx(10_500.25 * .88)
    assert detail["adaptive_buy_details"]["reentry_target"] == pytest.approx(9_950.75 * .92)
    row = next(row for row in detail["lots"] if row["lot_id"] == "lot-0")
    assert row["buy_price"] == 10_000.5
    assert row["unrealized_pnl"] == -1_000.5
    expected_target = ceil_price(10_000.5 * (1 + .03 * 2 - .0016))
    assert row["sell_trigger_price"] == expected_target
    portfolio = service.portfolio_unrealized_detail()["rows"]
    row = next(row for row in portfolio if row["lot_id"] == "lot-0")
    assert row["buy_price"] == 10_000.5
    assert row["target_price"] == expected_target
    assert service.stocks()[0]["invested_amount"] == 21_000.5


def test_unknown_cash_does_not_create_a_synthetic_adaptive_buy_preview(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    monkeypatch.setattr("kis_msj.ui_service.tail_hourly_logs", lambda *_: [])
    preview = service.decision_preview("005930")["previews"][0]
    assert not preview["action_created"]
    assert preview["final_block_reason"] == "cash_snapshot_unavailable"
