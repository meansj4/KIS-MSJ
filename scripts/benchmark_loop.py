"""Safe Bot Core loop benchmark.

This script never submits buy/sell/cancel orders. It runs against a copied
SQLite DB under exports/ and blocks order submission if a strategy action
unexpectedly reaches that point.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from kis_msj.config import BotConfig, OrderConfig, StockConfig, load_config  # noqa: E402
from kis_msj.kis_client import KisClient  # noqa: E402
from kis_msj.main import AutoTrader  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark one or more read-only Bot Core loops.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "lot_auto_trader.json")
    parser.add_argument("--symbols", type=int, default=120)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--mock-quotes", action="store_true", help="Use MockKisClient quote data. Default.")
    parser.add_argument("--read-only-quotes", action="store_true", help="Use KIS read-only quote/account/order inquiry APIs.")
    parser.add_argument("--price-sample-interval", type=float, default=0.0, help="Override sampling sleep for benchmark. Use -1 to keep config value.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "exports")
    return parser


def benchmark_config(config: BotConfig, args: argparse.Namespace, run_dir: Path) -> BotConfig:
    stocks = tuple(config.stocks[: max(0, args.symbols)])
    source_db = Path(config.storage_path)
    benchmark_db = run_dir / "benchmark_state.sqlite3"
    if source_db.exists():
        shutil.copy2(source_db, benchmark_db)
    order = config.order
    if args.price_sample_interval >= 0:
        order = replace(order, price_sample_interval_seconds=args.price_sample_interval)
    if not args.read_only_quotes:
        order = replace(order, live_trading=False)
    log_path = run_dir / "benchmark_loop.log"
    return replace(config, stocks=stocks, storage_path=str(benchmark_db), log_path=str(log_path), order=order)


def block_order_submission(*_args, **_kwargs):
    raise RuntimeError("benchmark_order_submission_blocked")


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    durations = [float(row.get("loop_duration_ms") or 0) for row in rows]
    symbol_avgs = [float(row.get("avg_symbol_duration_ms") or 0) for row in rows]
    return {
        "iterations": len(rows),
        "loop_duration_ms_avg": round(mean(durations), 2) if durations else 0,
        "loop_duration_ms_p50": percentile(durations, 0.50),
        "loop_duration_ms_p95": percentile(durations, 0.95),
        "loop_duration_ms_max": round(max(durations), 2) if durations else 0,
        "avg_symbol_duration_ms_avg": round(mean(symbol_avgs), 2) if symbol_avgs else 0,
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return round(ordered[index], 2)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_dir / f"loop_benchmark_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    bench_config = benchmark_config(config, args, run_dir)
    use_mock = not args.read_only_quotes
    trader = AutoTrader(bench_config, use_mock_client=use_mock)
    if args.read_only_quotes and not isinstance(trader.client, KisClient):
        raise RuntimeError("read_only_quotes_requested_but_kis_client_not_active")
    original_pre_request_block_reason = trader.pre_request_block_reason

    def benchmark_pre_request_block_reason(position, action, portfolio_preview: str = "") -> str:
        reason = original_pre_request_block_reason(position, action, portfolio_preview)
        return reason or ("benchmark_order_submission_disabled" if action else "")

    trader.pre_request_block_reason = benchmark_pre_request_block_reason
    trader.order_manager.submit_and_confirm = block_order_submission

    rows: list[dict[str, object]] = []
    for _ in range(max(1, args.iterations)):
        trader.run_once()
        rows.append(dict(trader.last_loop_profile))

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "read_only_quotes" if args.read_only_quotes else "mock_quotes",
        "symbols_requested": args.symbols,
        "symbols_configured": len(bench_config.stocks),
        "price_sample_interval_seconds": bench_config.order.price_sample_interval_seconds,
        "order_api_calls": 0,
        "db_reset": False,
        "storage_path": bench_config.storage_path,
        "summary": summarize(rows),
        "rows": rows,
        "notes": [
            "Benchmark uses a copied SQLite DB under exports and does not reset production DB.",
            "Order submission is blocked in-process; buy/sell/cancel APIs are not called.",
            "Mock mode uses local quote data. Read-only mode may call KIS account/quote/execution inquiry APIs only.",
        ],
    }
    json_path = run_dir / "loop_benchmark.json"
    csv_path = run_dir / "loop_benchmark.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(csv_path, rows)
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
