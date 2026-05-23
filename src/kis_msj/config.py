"""Configuration for the lot-based conservative grid strategy."""

from __future__ import annotations

import json
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
class BotConfig:
    stocks: tuple[StockConfig, ...] = ()
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    order: OrderConfig = field(default_factory=OrderConfig)
    market_hours: MarketHoursConfig = field(default_factory=MarketHoursConfig)
    kis_account: KisAccountConfig = field(default_factory=KisAccountConfig)
    upstream_watch: UpstreamWatchConfig = field(default_factory=UpstreamWatchConfig)
    storage_path: str = str(PROJECT_ROOT / "data" / "lot_auto_trader_state.sqlite3")
    log_path: str = str(PROJECT_ROOT / "logs" / "lot_auto_trader.log")
    loop_interval_seconds: float = 15.0
    max_loop_count: int | None = None


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
        storage_path=str(raw.get("storage_path", base.storage_path)),
        log_path=str(raw.get("log_path", base.log_path)),
        loop_interval_seconds=float(raw.get("loop_interval_seconds", base.loop_interval_seconds)),
        max_loop_count=raw.get("max_loop_count", base.max_loop_count),
    )


def _stock(item: dict[str, Any]) -> StockConfig:
    data = {key: item.get(key) for key in StockConfig.__dataclass_fields__ if key in item}
    data["code"] = str(item["code"]).zfill(6)
    return StockConfig(**data)


def _strategy(raw: dict[str, Any], base: StrategyConfig) -> StrategyConfig:
    data = {**asdict(base), **raw}
    data["exposure_buy_bands"] = tuple(BuyBand(**item) for item in raw.get("exposure_buy_bands", asdict(base)["exposure_buy_bands"]))
    data["exposure_sell_bands"] = tuple(SellBand(**item) for item in raw.get("exposure_sell_bands", asdict(base)["exposure_sell_bands"]))
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
