"""Collect read-only market data for later strategy analysis.

This script uses quote/market-data lookup paths only. It never places, revises,
or cancels KIS orders and it never resets the trading database.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from kis_msj.config import DEFAULT_CONFIG_PATH, config_hash, load_config
from kis_msj.domestic_quote import DEFAULT_SLEEP_SECONDS, fetch_current_quote
from kis_msj.storage import StateStore


QuoteFetcher = Callable[[str], dict[str, Any]]


def collect_market_data(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    codes: Sequence[str] = (),
    symbols_from_config: bool = False,
    snapshot: bool = False,
    daily: bool = False,
    days: int = 1,
    execute: bool = False,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    fetcher: QuoteFetcher | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    selected_codes = _selected_codes(config, codes, symbols_from_config)
    active_hash = config_hash(config)
    run_id = config.experiment.run_id or config.run_id or f"{config.risk.profile}_{active_hash}"
    fetch = fetcher or (lambda code: fetch_current_quote(code, korean_name=_name_for_code(config, code)))
    result: dict[str, Any] = {
        "execute": execute,
        "dry_run": not execute,
        "kis_order_api_called": False,
        "db_reset": False,
        "requested_days": days,
        "daily_history_supported": False,
        "warnings": [],
        "stored_price_snapshots": 0,
        "stored_daily_prices": 0,
        "errors": [],
        "codes": selected_codes,
    }
    if days > 1 and daily:
        result["warnings"].append("Only current-day OHLCV can be collected through the existing quote path; historical daily lookup is future work.")
    store = StateStore(config.storage_path) if execute else None
    if store is not None:
        store.set_active_config(active_hash, run_id=run_id, experiment_name=config.experiment.experiment_name or config.experiment_name or config.risk.profile, profile_name=config.risk.profile)

    for index, code in enumerate(selected_codes):
        try:
            row = fetch(code)
            normalized = _quote_to_market_rows(code, row, active_hash, run_id)
            if execute and store is not None:
                if snapshot:
                    store.record_price_snapshot(normalized["price_snapshot"])
                    result["stored_price_snapshots"] = int(result["stored_price_snapshots"]) + 1
                if daily:
                    store.upsert_daily_price(normalized["daily_price"])
                    result["stored_daily_prices"] = int(result["stored_daily_prices"]) + 1
            else:
                if snapshot:
                    result["stored_price_snapshots"] = int(result["stored_price_snapshots"]) + 1
                if daily:
                    result["stored_daily_prices"] = int(result["stored_daily_prices"]) + 1
        except Exception as error:  # noqa: BLE001
            result["errors"].append({"code": code, "error": type(error).__name__, "message": str(error)})
        if index + 1 < len(selected_codes) and sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return result


def _selected_codes(config, codes: Sequence[str], symbols_from_config: bool) -> list[str]:
    selected = [str(code).zfill(6) for code in codes]
    if symbols_from_config:
        selected.extend(stock.code for stock in config.stocks if stock.enabled and not stock.manual_only)
    return sorted(set(selected))


def _name_for_code(config, code: str) -> str:
    stock = next((item for item in config.stocks if item.code == str(code).zfill(6)), None)
    return stock.name if stock else ""


def _quote_to_market_rows(code: str, row: dict[str, Any], config_hash_value: str, run_id: str) -> dict[str, dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    current_price = _to_int(row.get("current_price"))
    open_price = _to_int(row.get("open_price"))
    high_price = _to_int(row.get("high_price"))
    low_price = _to_int(row.get("low_price"))
    volume = _to_int(row.get("accumulated_volume"))
    trading_value = _to_int(row.get("accumulated_trade_amount"))
    previous_close = current_price - _to_int(row.get("previous_day_diff")) if current_price and row.get("previous_day_diff") not in ("", None) else None
    missing = [
        name
        for name, value in {
            "previous_close": previous_close,
            "day_open": open_price,
            "day_high": high_price,
            "day_low": low_price,
            "volume": volume,
            "trading_value": trading_value,
            "bid_price": None,
            "ask_price": None,
            "spread": None,
            "spread_rate": None,
        }.items()
        if value in (None, "")
    ]
    safe_raw = {key: value for key, value in row.items() if "token" not in key.lower() and "app" not in key.lower()}
    return {
        "price_snapshot": {
            "code": code,
            "sampled_at": now,
            "current_price": current_price,
            "previous_close": previous_close,
            "day_open": open_price,
            "day_high": high_price,
            "day_low": low_price,
            "volume": volume,
            "trading_value": trading_value,
            "source": "kis_inquire_price",
            "run_id": run_id,
            "config_hash": config_hash_value,
            "collected_at": now,
            "missing_fields": missing,
            "raw_json": safe_raw,
        },
        "daily_price": {
            "code": code,
            "date": datetime.now().date().isoformat(),
            "open": open_price or 0,
            "high": high_price or 0,
            "low": low_price or 0,
            "close": current_price or 0,
            "volume": volume or 0,
            "trading_value": trading_value or 0,
            "source": "kis_inquire_price_snapshot",
            "collected_at": now,
        },
    }


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect read-only quote data for analysis.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--symbols-from-config", action="store_true")
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    args = parser.parse_args(argv)
    execute = bool(args.execute and not args.dry_run)
    result = collect_market_data(
        args.config,
        codes=args.code,
        symbols_from_config=args.symbols_from_config,
        snapshot=args.snapshot,
        daily=args.daily,
        days=args.days,
        execute=execute,
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
