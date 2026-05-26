"""Configuration for the lot-based conservative grid strategy."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "lot_auto_trader.json"


@dataclass(frozen=True)
class StockConfig:
    code: str
    name: str = ""
    enabled: bool = True
    market: str = "KOSPI"
    sector: str = ""
    note: str = ""
    reason: str = ""
    manual_only: bool = False
    priority: int = 0
    group: str = ""
    trading_halted: bool = False
    administrative_issue: bool = False
    investment_alert: bool = False
    audit_opinion_issue: bool = False
    delisting_risk: bool = False
    accounting_issue: bool = False
    liquidity_warning: bool = False

    @property
    def danger_state(self) -> bool:
        return any(
            (
                self.trading_halted,
                self.administrative_issue,
                self.investment_alert,
                self.audit_opinion_issue,
                self.delisting_risk,
                self.accounting_issue,
                self.liquidity_warning,
            )
        )


@dataclass(frozen=True)
class BuyBand:
    min_exposure: int
    max_exposure: int
    drop_pct: float
    amount: int


@dataclass(frozen=True)
class SellBand:
    min_exposure: int
    max_exposure: int
    target_profit_pct: float


@dataclass(frozen=True)
class PriceLotBand:
    min_price: int
    max_price: int
    lot_unit_amount: int
    max_symbol_amount: int
    enabled: bool = True
    max_lots: int = 0
    note: str = ""


@dataclass(frozen=True)
class AddBuyLotBand:
    min_lots: int
    max_lots: int
    drop_rate: float
    add_lot_count: int = 1


@dataclass(frozen=True)
class TargetProfitLotBand:
    min_lots: int
    max_lots: int
    target_profit_rate: float


@dataclass(frozen=True)
class StrategyConfig:
    initial_buy_amount: int = 30_000
    auto_buy_limit: int = 300_000
    absolute_max_investment: int = 500_000
    review_loss_pct: float = -20.0
    max_open_lots_before_review: int = 12
    pnl_minus_threshold: float = -0.01
    pnl_plus_threshold: float = 0.01
    reentry_drop_rate: float = 0.04
    normal_reentry_drop_rate: float = 0.04
    trailing_activation_gain: float = 0.05
    trailing_reentry_drop_rate: float = 0.08
    min_reentry_wait_minutes: int = 60
    max_trailing_reentry_per_day: int = 1
    reentry_buy_cooldown_minutes: int = 60
    age_decay_rate: float = 0.005
    cleanup_enabled: bool = False
    cleanup_min_age_weeks: int = 12
    cleanup_min_target_rate: float = -0.04
    cleanup_profit_offset_ratio: float = 0.3
    cleanup_buy_cooldown_days: int = 3
    cleanup_reentry_cooldown_days: int = 5
    cleanup_auto_return_to_wait_reentry: bool = False
    stale_lot_loss_rate: float = -0.15
    stale_lot_min_age_weeks: int = 8
    stale_lot_price_gap_rate: float = -0.10
    review_symbol_loss_rate: float = -0.20
    stale_lot_review_age_weeks: int = 20
    lot_sizing_mode: str = "cycle_locked_by_entry_price"
    price_lot_bands: tuple[PriceLotBand, ...] = (
        PriceLotBand(0, 300, 1_000, 10_000, True),
        PriceLotBand(301, 1_000, 3_000, 30_000, True),
        PriceLotBand(1_001, 3_000, 10_000, 100_000, True),
        PriceLotBand(3_001, 10_000, 30_000, 300_000, True),
        PriceLotBand(10_001, 30_000, 100_000, 1_000_000, True),
        PriceLotBand(30_001, 100_000, 300_000, 3_000_000, True),
        PriceLotBand(100_001, 300_000, 1_000_000, 10_000_000, True),
        PriceLotBand(300_001, 1_000_000, 3_000_000, 30_000_000, True),
        PriceLotBand(1_000_001, 3_000_000, 10_000_000, 100_000_000, True),
    )
    add_buy_lot_bands: tuple[AddBuyLotBand, ...] = (
        AddBuyLotBand(1, 2, 0.04, 1),
        AddBuyLotBand(3, 4, 0.06, 1),
        AddBuyLotBand(5, 6, 0.08, 1),
        AddBuyLotBand(7, 8, 0.10, 1),
        AddBuyLotBand(9, 10, 0.12, 1),
    )
    target_profit_lot_bands: tuple[TargetProfitLotBand, ...] = (
        TargetProfitLotBand(1, 2, 0.06),
        TargetProfitLotBand(3, 4, 0.05),
        TargetProfitLotBand(5, 6, 0.04),
        TargetProfitLotBand(7, 8, 0.03),
        TargetProfitLotBand(9, 10, 0.02),
    )
    max_lots_per_symbol_default: int = 10
    exposure_buy_bands: tuple[BuyBand, ...] = (
        BuyBand(1, 60_000, 4.0, 30_000),
        BuyBand(60_001, 120_000, 5.0, 30_000),
        BuyBand(120_001, 200_000, 6.0, 40_000),
        BuyBand(200_001, 300_000, 8.0, 50_000),
    )
    exposure_sell_bands: tuple[SellBand, ...] = (
        SellBand(1, 60_000, 6.0),
        SellBand(60_001, 120_000, 5.0),
        SellBand(120_001, 200_000, 4.0),
        SellBand(200_001, 300_000, 3.0),
        SellBand(300_001, 500_000, 2.5),
    )
    high_exposure_partial_sell_pct: float = 50.0
    estimated_fee_tax_pct: float = 0.25


@dataclass(frozen=True)
class RiskConfig:
    market_risk_mode: bool = False
    daily_account_loss_limit_pct: float = -1.5
    total_account_loss_limit_pct: float = -5.0
    max_review_positions: int = 3
    min_cash_available: int = 300_000
    max_consecutive_api_errors: int = 5
    max_price_sample_volatility_pct: float = 1.0
    block_on_lot_mismatch: bool = True
    max_active_symbols: int = 20
    max_total_open_lots: int = 40
    max_total_invested_amount: int = 1_500_000
    max_new_buy_per_day: int = 5
    max_new_buy_amount_per_day: int = 0
    max_total_initial_buy_amount_per_day: int = 0
    profile: str = "default"


@dataclass(frozen=True)
class OrderConfig:
    live_trading: bool = False
    emergency_market_order: bool = False
    buy_limit_markup_pct: float = 0.3
    sell_limit_markdown_pct: float = 0.3
    price_sample_count: int = 5
    price_sample_interval_seconds: float = 2.0
    limit_order_timeout_seconds: int = 60
    order_cooldown_seconds: int = 300
    min_order_request_interval_seconds: int = 10
    cancel_unfilled_on_start: bool = False
    execution_query_buffer_minutes: int = 60
    include_previous_day_for_open_orders: bool = True
    enable_execution_raw_log: bool = False
    reconcile_recent_executions_on_startup: bool = True
    startup_execution_lookup_days: int = 1


@dataclass(frozen=True)
class MarketHoursConfig:
    open_time: str = "09:00"
    close_time: str = "15:30"
    block_after_open_minutes: int = 5
    block_before_close_minutes: int = 10


@dataclass(frozen=True)
class KisAccountConfig:
    account_number_env: str = "KIS_ACCOUNT_NUMBER"
    account_product_code_env: str = "KIS_ACCOUNT_PRODUCT_CODE"
    customer_type: str = "P"


@dataclass(frozen=True)
class UpstreamWatchConfig:
    enabled: bool = True
    interval_seconds: int = 3600
    repo_path: str = r"C:\MSJ\open-trading-api"
    fetch: bool = False


@dataclass(frozen=True)
class ExperimentConfig:
    run_id: str = ""
    experiment_name: str = ""
    operator_note: str = ""
    purpose: str = ""


@dataclass(frozen=True)
class BotConfig:
    stocks: tuple[StockConfig, ...] = ()
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    order: OrderConfig = field(default_factory=OrderConfig)
    market_hours: MarketHoursConfig = field(default_factory=MarketHoursConfig)
    kis_account: KisAccountConfig = field(default_factory=KisAccountConfig)
    upstream_watch: UpstreamWatchConfig = field(default_factory=UpstreamWatchConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    storage_path: str = str(PROJECT_ROOT / "data" / "lot_auto_trader_state.sqlite3")
    log_path: str = str(PROJECT_ROOT / "logs" / "lot_auto_trader.log")
    loop_interval_seconds: float = 15.0
    max_loop_count: int | None = None
    ui_manual_trading_enabled: bool = False
    run_id: str = ""
    experiment_name: str = ""
    operator_note: str = ""
    purpose: str = ""


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> BotConfig:
    if not path.exists():
        return BotConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = BotConfig()
    return BotConfig(
        stocks=tuple(_stock(item) for item in raw.get("stocks", [])),
        strategy=_strategy(raw.get("strategy", {}), base.strategy),
        risk=RiskConfig(**{**asdict(base.risk), **raw.get("risk", {})}),
        order=OrderConfig(**{**asdict(base.order), **raw.get("order", {})}),
        market_hours=MarketHoursConfig(**{**asdict(base.market_hours), **raw.get("market_hours", {})}),
        kis_account=KisAccountConfig(**{**asdict(base.kis_account), **raw.get("kis_account", {})}),
        upstream_watch=UpstreamWatchConfig(**{**asdict(base.upstream_watch), **raw.get("upstream_watch", {})}),
        experiment=ExperimentConfig(**{**asdict(base.experiment), **raw.get("experiment", {})}),
        storage_path=str(raw.get("storage_path", base.storage_path)),
        log_path=str(raw.get("log_path", base.log_path)),
        loop_interval_seconds=float(raw.get("loop_interval_seconds", base.loop_interval_seconds)),
        max_loop_count=raw.get("max_loop_count", base.max_loop_count),
        ui_manual_trading_enabled=bool(raw.get("ui_manual_trading_enabled", base.ui_manual_trading_enabled)),
        run_id=str(raw.get("run_id", base.run_id)),
        experiment_name=str(raw.get("experiment_name", base.experiment_name)),
        operator_note=str(raw.get("operator_note", base.operator_note)),
        purpose=str(raw.get("purpose", base.purpose)),
    )


def _stock(item: dict[str, Any]) -> StockConfig:
    data = {key: item.get(key) for key in StockConfig.__dataclass_fields__ if key in item}
    data["code"] = str(item["code"]).zfill(6)
    return StockConfig(**data)


def _strategy(raw: dict[str, Any], base: StrategyConfig) -> StrategyConfig:
    data = {**asdict(base), **raw}
    data["exposure_buy_bands"] = tuple(BuyBand(**item) for item in raw.get("exposure_buy_bands", asdict(base)["exposure_buy_bands"]))
    data["exposure_sell_bands"] = tuple(SellBand(**item) for item in raw.get("exposure_sell_bands", asdict(base)["exposure_sell_bands"]))
    data["price_lot_bands"] = tuple(PriceLotBand(**item) for item in raw.get("price_lot_bands", asdict(base)["price_lot_bands"]))
    data["add_buy_lot_bands"] = tuple(AddBuyLotBand(**item) for item in raw.get("add_buy_lot_bands", asdict(base)["add_buy_lot_bands"]))
    data["target_profit_lot_bands"] = tuple(TargetProfitLotBand(**item) for item in raw.get("target_profit_lot_bands", asdict(base)["target_profit_lot_bands"]))
    return StrategyConfig(**data)


def write_default_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = asdict(
        BotConfig(
            stocks=(
                StockConfig("005930", "Samsung Electronics"),
                StockConfig("000660", "SK hynix"),
                StockConfig("005380", "Hyundai Motor"),
                StockConfig("035420", "NAVER"),
                StockConfig("051910", "LG Chem"),
                StockConfig("006400", "Samsung SDI"),
                StockConfig("068270", "Celltrion"),
                StockConfig("105560", "KB Financial"),
                StockConfig("055550", "Shinhan Financial"),
                StockConfig("012330", "Hyundai Mobis"),
            ),
            max_loop_count=1,
        )
    )
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def config_to_dict(config: BotConfig) -> dict[str, Any]:
    """Return a JSON-serializable config dictionary for snapshots/analysis."""
    return asdict(config)


def config_hash(config: BotConfig) -> str:
    """Stable hash used to connect decisions/orders/fills to a config snapshot."""
    payload = json.dumps(config_to_dict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
