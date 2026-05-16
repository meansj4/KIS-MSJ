"""Configuration for the risk-limited domestic stock trading bot."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "auto_trader.json"

ReferencePriceMode = Literal["last_fill", "average_price"]


@dataclass(frozen=True)
class StockConfig:
    code: str
    name: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class StrategyConfig:
    initial_buy_amount: int = 300_000
    max_position_amount: int = 5_000_000
    grid_interval_pct: float = 4.0
    reference_price_mode: ReferencePriceMode = "last_fill"
    add_buy_amounts: tuple[int, ...] = (300_000, 500_000, 700_000)
    add_buy_drop_pcts: tuple[float, ...] = (4.0, 8.0, 12.0)
    sell_rise_pcts: tuple[float, ...] = (4.0, 8.0, 12.0)
    sell_portion_pct: float = 30.0
    final_sell_portion_pct: float = 100.0


@dataclass(frozen=True)
class RiskConfig:
    market_risk_mode: bool = False
    daily_account_loss_limit_pct: float = -1.5
    total_account_loss_limit_pct: float = -5.0
    warning_loss_pct: float = -8.0
    block_add_buy_loss_pct: float = -12.0
    half_stop_loss_pct: float = -15.0
    full_stop_loss_pct: float = -20.0
    max_stop_waiting_positions: int = 3
    max_total_exposure: int = 20_000_000
    max_total_exposure_pct: float = 70.0
    max_consecutive_api_errors: int = 5
    max_price_sample_volatility_pct: float = 1.0
    block_on_state_mismatch: bool = True


@dataclass(frozen=True)
class OrderConfig:
    live_trading: bool = False
    allow_market_order: bool = False
    buy_limit_markup_pct: float = 0.3
    sell_limit_markdown_pct: float = 0.3
    price_sample_count: int = 5
    price_sample_interval_seconds: float = 2.0
    limit_order_timeout_seconds: int = 60
    order_cooldown_seconds: int = 300
    cancel_unfilled_on_start: bool = False


@dataclass(frozen=True)
class MarketHoursConfig:
    open_time: str = "09:00"
    close_time: str = "15:30"
    block_after_open_minutes: int = 5
    block_before_close_minutes: int = 10
    timezone: str = "Asia/Seoul"


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
    storage_path: str = str(PROJECT_ROOT / "data" / "auto_trader_state.sqlite3")
    log_path: str = str(PROJECT_ROOT / "logs" / "auto_trader.log")
    loop_interval_seconds: float = 15.0
    max_loop_count: int | None = None


def _merge_dataclass(cls: type[Any], data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        value = data[item.name]
        current = item.default
        if current is None or current is item.default_factory:  # type: ignore[comparison-overlap]
            kwargs[item.name] = value
        elif is_dataclass(item.type):
            kwargs[item.name] = _merge_dataclass(item.type, value)
        else:
            kwargs[item.name] = value
    return cls(**kwargs)


def _stock_from_dict(data: dict[str, Any]) -> StockConfig:
    return StockConfig(code=str(data["code"]).zfill(6), name=str(data.get("name", "")), enabled=bool(data.get("enabled", True)))


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> BotConfig:
    """Load JSON config, falling back to conservative paper-trading defaults."""

    if not path.exists():
        return BotConfig()

    raw = json.loads(path.read_text(encoding="utf-8"))
    config = BotConfig()
    return BotConfig(
        stocks=tuple(_stock_from_dict(item) for item in raw.get("stocks", [])),
        strategy=_merge_dataclass(StrategyConfig, raw.get("strategy", asdict(config.strategy))),
        risk=_merge_dataclass(RiskConfig, raw.get("risk", asdict(config.risk))),
        order=_merge_dataclass(OrderConfig, raw.get("order", asdict(config.order))),
        market_hours=_merge_dataclass(MarketHoursConfig, raw.get("market_hours", asdict(config.market_hours))),
        kis_account=_merge_dataclass(KisAccountConfig, raw.get("kis_account", asdict(config.kis_account))),
        upstream_watch=_merge_dataclass(UpstreamWatchConfig, raw.get("upstream_watch", asdict(config.upstream_watch))),
        storage_path=str(raw.get("storage_path", config.storage_path)),
        log_path=str(raw.get("log_path", config.log_path)),
        loop_interval_seconds=float(raw.get("loop_interval_seconds", config.loop_interval_seconds)),
        max_loop_count=raw.get("max_loop_count", config.max_loop_count),
    )


def write_default_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create an example config file without secrets or account numbers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    sample = asdict(
        BotConfig(
            stocks=(
                StockConfig(code="005930", name="Samsung Electronics"),
                StockConfig(code="000660", name="SK hynix"),
            ),
            max_loop_count=1,
        )
    )
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
