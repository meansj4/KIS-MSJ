import json
import sqlite3
from datetime import datetime, timedelta

from kis_msj.config import BotConfig, RiskConfig, StockConfig, StrategyConfig
from kis_msj.models import LotState, LotStatus, OrderRequest, OrderResult, OrderSide, OrderStatus, PositionLifecycle, PositionState, TradeFill
from kis_msj.storage import StateStore
from kis_msj.ui_server import INDEX_HTML
from kis_msj.ui_service import UIService


def _service(tmp_path, *, risk: RiskConfig | None = None) -> tuple[UIService, StateStore]:
    db_path = tmp_path / "state.sqlite3"
    config = BotConfig(
        stocks=(StockConfig("005930", "Samsung"), StockConfig("000660", "Hynix")),
        risk=risk or RiskConfig(
            max_total_invested_amount=200_000,
            max_total_open_lots=4,
            max_active_symbols=3,
            max_new_buy_per_day=2,
            max_new_buy_amount_per_day=100_000,
            max_total_initial_buy_amount_per_day=120_000,
        ),
        strategy=StrategyConfig(estimated_fee_tax_pct=0.25),
        storage_path=str(db_path),
        log_path=str(tmp_path / "bot.log"),
    )
    config_path = tmp_path / "lot_auto_trader.json"
    config_path.write_text(json.dumps(_config_payload(config), ensure_ascii=False), encoding="utf-8")
    return UIService(config_path), StateStore(db_path)


def _config_payload(config: BotConfig) -> dict:
    return {
        "stocks": [stock.__dict__ for stock in config.stocks],
        "risk": config.risk.__dict__,
        "strategy": {"estimated_fee_tax_pct": config.strategy.estimated_fee_tax_pct},
        "storage_path": config.storage_path,
        "log_path": config.log_path,
    }


def _seed_dashboard_data(store: StateStore) -> None:
    store.save_position(
        PositionState(
            "005930",
            "Samsung",
            quantity=8,
            current_price=11000,
            position_state=PositionLifecycle.HOLDING.value,
            max_symbol_amount=100000,
            max_lots_per_symbol=3,
        )
    )
    store.save_position(
        PositionState(
            "000660",
            "Hynix",
            quantity=1,
            current_price=9000,
            position_state=PositionLifecycle.REVIEW_REQUIRED.value,
            needs_review=True,
            max_symbol_amount=50000,
            max_lots_per_symbol=2,
        )
    )
    lot1 = LotState("LOT-1", "005930", "2026-05-28T09:05:00", 10000, 10, 100000, 6, 6.0, 10600, partial_sold=True, realized_profit_loss=7950, estimated_fee_tax=50, status=LotStatus.PARTIAL_SOLD.value)
    lot2 = LotState("LOT-2", "000660", "2026-05-27T09:05:00", 10000, 1, 10000, 1, 6.0, 10600)
    store.save_lot(lot1)
    store.save_lot(lot2)
    store.record_fill(TradeFill("005930", "Samsung", OrderSide.BUY, 10, 10000, "ORDER-BUY-1", datetime(2026, 5, 28, 9, 5), "LOT-1", "EXEC-BUY-1"))
    store.record_fill(TradeFill("000660", "Hynix", OrderSide.BUY, 1, 10000, "ORDER-BUY-2", datetime(2026, 5, 27, 9, 5), "LOT-2", "EXEC-BUY-2"))
    store.record_fill(TradeFill("005930", "Samsung", OrderSide.SELL, 4, 12000, "ORDER-SELL-1", datetime(2026, 5, 28, 10, 5), "LOT-1", "EXEC-SELL-1"))
    request = OrderRequest("005930", "Samsung", OrderSide.BUY, 1, 10000, "initial_buy")
    store.record_order(OrderResult(request, "ORDER-TODAY-1", OrderStatus.REQUESTED, requested_at=datetime.now().isoformat(timespec="seconds")))
    store.record_price_snapshot({"code": "005930", "sampled_at": "2026-05-28T10:00:00", "current_price": 11000})


def test_portfolio_dashboard_calculates_overall_daily_and_limits(tmp_path) -> None:
    service, store = _service(tmp_path)
    _seed_dashboard_data(store)

    dashboard = service.portfolio_dashboard()

    overall = dashboard["overall_summary"]
    assert overall["total_buy_amount"] == 110000
    assert overall["total_buy_lot_count"] == 2
    assert overall["current_holding_buy_amount"] == 70000
    assert overall["current_holding_lot_count"] == 2
    assert overall["realized_pnl"] == 7880
    assert round(overall["realized_pnl_rate"], 4) == 0.197
    assert overall["unrealized_pnl"] == 5000
    assert round(overall["unrealized_pnl_rate"], 4) == 0.0714

    today_row = next(row for row in dashboard["daily_summary"] if row["date"] == "2026-05-28")
    assert today_row["buy_amount"] == 100000
    assert today_row["buy_lot_count"] == 1
    assert today_row["sell_amount"] == 48000
    assert today_row["sell_lot_count"] == 1
    assert today_row["realized_pnl"] == 7880

    usage = {row["key"]: row for row in dashboard["limit_usage"]}
    assert usage["max_total_invested_amount"]["usage_pct"] == 35.0
    assert usage["max_total_open_lots"]["usage_pct"] == 50.0
    assert usage["max_new_buy_per_day"]["usage_pct"] == 50.0
    assert usage["max_new_buy_amount_per_day"]["usage_pct"] == 10.0
    assert dashboard["top_symbol_exposures"][0]["code"] == "005930"
    assert dashboard["risk_status_counts"]["review_required_count"] == 1


def test_portfolio_dashboard_is_read_only_and_handles_zero_limits_and_missing_snapshots(tmp_path) -> None:
    service, store = _service(tmp_path, risk=RiskConfig(max_total_invested_amount=0, max_total_open_lots=0, max_active_symbols=0, max_new_buy_per_day=0))
    _seed_dashboard_data(store)
    with sqlite3.connect(store.path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("positions", "lots", "fills", "orders")
        }

    dashboard = service.portfolio_dashboard()

    with sqlite3.connect(store.path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("positions", "lots", "fills", "orders")
        }
    assert before == after
    assert all(row["usage_pct"] is None for row in dashboard["limit_usage"] if row["limit"] == 0)
    assert dashboard["data_quality"]["notes"]
    assert dashboard["pre_after_night_status"]["status"] == "on_hold"


def test_portfolio_dashboard_ui_contains_tab_and_progress_bar() -> None:
    assert "loadPortfolioDashboard" in INDEX_HTML
    assert "/api/portfolio-dashboard" in INDEX_HTML
    assert "${kind}-detail" in INDEX_HTML
    assert "usageBar" in INDEX_HTML
    assert "usage_pct" in INDEX_HTML
    assert "loadPortfolioDetail" in INDEX_HTML


def test_portfolio_realized_detail_returns_sell_fill_lot_pnl_and_pagination(tmp_path) -> None:
    service, store = _service(tmp_path)
    _seed_dashboard_data(store)

    detail = service.portfolio_realized_detail({"limit": "1", "offset": "0"})

    assert detail["read_only"] is True
    assert detail["order_api_called"] is False
    assert detail["total_count"] == 1
    row = detail["rows"][0]
    assert row["code"] == "005930"
    assert row["lot_id"] == "LOT-1"
    assert row["buy_amount"] == 40000
    assert row["sell_amount"] == 48000
    assert row["fee_tax_estimate"] == 120
    assert row["realized_pnl"] == 7880
    assert row["pnl_basis"] == "sell_fill_net_estimate"


def test_portfolio_unrealized_detail_returns_open_lot_targets_and_price_source(tmp_path) -> None:
    service, store = _service(tmp_path)
    _seed_dashboard_data(store)

    detail = service.portfolio_unrealized_detail({"sort": "unrealized_pnl"})

    assert detail["read_only"] is True
    assert detail["total_count"] == 2
    lot2 = next(row for row in detail["rows"] if row["lot_id"] == "LOT-2")
    assert lot2["current_price"] == 9000
    assert lot2["remaining_buy_amount"] == 10000
    assert lot2["current_market_value"] == 9000
    assert lot2["unrealized_pnl"] == -1000
    assert lot2["target_price"] > lot2["buy_price"]
    assert "target" in lot2["data_quality_note"]


def test_daily_detail_filters_sell_fill_date_and_current_basis_unrealized(tmp_path) -> None:
    service, store = _service(tmp_path)
    _seed_dashboard_data(store)

    realized = service.portfolio_realized_detail({"date": "2026-05-28"})
    unrealized = service.portfolio_unrealized_detail({"date": "2026-05-27"})

    assert realized["total_count"] == 1
    assert realized["rows"][0]["sell_filled_at"].startswith("2026-05-28")
    assert unrealized["total_count"] == 1
    assert unrealized["rows"][0]["lot_id"] == "LOT-2"
    assert "현재 기준" in unrealized["calculation_basis"]
    assert unrealized["data_quality_notes"]
