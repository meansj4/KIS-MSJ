"""Export a sanitized analysis dataset for strategy/config tuning.

This script is read-only with respect to the trading database. It does not call
KIS APIs, does not create orders, and does not reset or mutate DB state.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from kis_msj.config import DEFAULT_CONFIG_PATH, load_config


TABLES = (
    "positions",
    "lots",
    "orders",
    "fills",
    "manual_order_requests",
    "decisions",
)
SENSITIVE_KEYS = ("account", "acct", "cano", "acnt", "appkey", "appsecret", "token", "authorization", "auth")


def export_analysis_dataset(
    config_path: Path = DEFAULT_CONFIG_PATH,
    output_dir: Path | None = None,
    *,
    date_from: str = "",
    date_to: str = "",
    code: str = "",
    config_hash: str = "",
) -> dict[str, Any]:
    config = load_config(config_path)
    db_path = Path(config.storage_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = output_dir or Path("exports") / f"analysis_dataset_{timestamp}"
    target.mkdir(parents=True, exist_ok=True)

    exported: dict[str, int] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for table in TABLES:
            if not _table_exists(connection, table):
                exported[table] = 0
                _write_csv(target / f"{table}.csv", [])
                continue
            rows = _filtered_rows(connection, table, date_from=date_from, date_to=date_to, code=code, config_hash=config_hash)
            _write_csv(target / f"{table}.csv", [_sanitize_row(dict(row)) for row in rows])
            exported[table] = len(rows)
        skipped_actions = _skipped_actions(connection, date_from=date_from, date_to=date_to, code=code, config_hash=config_hash)
        _write_csv(target / "skipped_actions.csv", skipped_actions)
        exported["skipped_actions"] = len(skipped_actions)
        snapshots = _config_snapshots(connection, config_hash=config_hash)
        _write_jsonl(target / "config_snapshots.jsonl", [_sanitize_snapshot(row) for row in snapshots])
        exported["config_snapshots"] = len(snapshots)
        summary = _summary(connection, exported, date_from=date_from, date_to=date_to, code=code, config_hash=config_hash)

    summary.update(
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_db_path": str(db_path),
            "filters": {"date_from": date_from, "date_to": date_to, "code": code, "config_hash": config_hash},
            "exported_files": sorted(path.name for path in target.iterdir() if path.is_file()),
            "kis_order_api_called": False,
            "db_mutated": False,
        }
    )
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata = _metadata(summary)
    (target / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "README.md").write_text(_readme(metadata), encoding="utf-8")
    return {"output_dir": str(target), "summary": summary}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _filtered_rows(connection: sqlite3.Connection, table: str, *, date_from: str, date_to: str, code: str, config_hash: str) -> list[sqlite3.Row]:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    where = []
    params: list[Any] = []
    if code and "code" in columns:
        where.append("code = ?")
        params.append(str(code).zfill(6))
    if config_hash and "config_hash" in columns:
        where.append("config_hash = ?")
        params.append(config_hash)
    time_column = _time_column(columns)
    if date_from and time_column:
        where.append(f"substr({time_column}, 1, 10) >= ?")
        params.append(date_from)
    if date_to and time_column:
        where.append(f"substr({time_column}, 1, 10) <= ?")
        params.append(date_to)
    query = f"SELECT * FROM {table}"
    if where:
        query += " WHERE " + " AND ".join(where)
    if time_column:
        query += f" ORDER BY {time_column}"
    return list(connection.execute(query, params).fetchall())


def _time_column(columns: set[str]) -> str:
    for candidate in ("filled_at", "requested_at", "created_at", "updated_at", "buy_filled_at", "last_update_time"):
        if candidate in columns:
            return candidate
    return ""


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _config_snapshots(connection: sqlite3.Connection, *, config_hash: str) -> list[sqlite3.Row]:
    if not _table_exists(connection, "config_snapshots"):
        return []
    if config_hash:
        return list(connection.execute("SELECT * FROM config_snapshots WHERE config_hash = ? ORDER BY created_at", (config_hash,)).fetchall())
    return list(connection.execute("SELECT * FROM config_snapshots ORDER BY created_at").fetchall())


def _sanitize_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw = str(data.get("full_config_json") or "{}")
    try:
        data["full_config_json"] = json.dumps(_mask_sensitive(json.loads(raw)), ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        data["full_config_json"] = "{}"
    return _sanitize_row(data)


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _mask_sensitive(value) for key, value in row.items()}


def _mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***" if _is_sensitive(key) else _mask_sensitive(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    return value


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEYS)


def _summary(connection: sqlite3.Connection, exported: dict[str, int], *, date_from: str, date_to: str, code: str, config_hash: str) -> dict[str, Any]:
    fills = _filtered_rows(connection, "fills", date_from=date_from, date_to=date_to, code=code, config_hash=config_hash) if _table_exists(connection, "fills") else []
    lots = _filtered_rows(connection, "lots", date_from=date_from, date_to=date_to, code=code, config_hash="") if _table_exists(connection, "lots") else []
    positions = _filtered_rows(connection, "positions", date_from="", date_to="", code=code, config_hash="") if _table_exists(connection, "positions") else []
    buy_fills = [row for row in fills if str(row["side"]) == "BUY"]
    sell_fills = [row for row in fills if str(row["side"]) == "SELL"]
    closed_lots = [row for row in lots if str(row["status"]) == "CLOSED" or int(row["remaining_quantity"] or 0) <= 0]
    open_lots = [row for row in lots if str(row["status"]) != "CLOSED" and int(row["remaining_quantity"] or 0) > 0]
    realized = sum(int(row["realized_profit_loss"] or 0) for row in lots)
    fees = sum(int(row["estimated_fee_tax"] or 0) for row in lots)
    capital_used = sum(int(row["cumulative_invested_amount"] or 0) for row in positions)
    wins = sum(1 for row in closed_lots if int(row["realized_profit_loss"] or 0) >= 0)
    config_hashes = sorted({str(row["config_hash"]) for row in fills if "config_hash" in row.keys() and row["config_hash"]})
    snapshot_rows = _config_snapshots(connection, config_hash=config_hash)
    run_ids = sorted(
        {
            str(row["run_id"])
            for row in (
                (_filtered_rows(connection, "orders", date_from=date_from, date_to=date_to, code=code, config_hash=config_hash) if _table_exists(connection, "orders") else [])
                + list(fills)
                + list(snapshot_rows)
            )
            if "run_id" in row.keys() and row["run_id"]
        }
    )
    return {
        "exported_row_counts": exported,
        "total_buy_fills": len(buy_fills),
        "total_sell_fills": len(sell_fills),
        "total_realized_pnl": realized,
        "total_estimated_fee_tax": fees,
        "net_realized_pnl_estimate": realized - fees,
        "closed_lot_count": len(closed_lots),
        "open_lot_count": len(open_lots),
        "stale_lot_count": sum(1 for row in open_lots if int(row["cleanup_candidate"] or 0)),
        "review_required_count": sum(1 for row in positions if int(row["needs_review"] or 0) or str(row["position_state"]) == "REVIEW_REQUIRED"),
        "win_rate": wins / len(closed_lots) if closed_lots else 0.0,
        "max_capital_used_estimate": capital_used,
        "config_hashes": config_hashes,
        "run_ids": run_ids,
    }


def _skipped_actions(connection: sqlite3.Connection, *, date_from: str, date_to: str, code: str, config_hash: str) -> list[dict[str, Any]]:
    if not _table_exists(connection, "decisions"):
        return []
    rows = _filtered_rows(connection, "decisions", date_from=date_from, date_to=date_to, code=code, config_hash=config_hash)
    skipped = []
    for row in rows:
        data = dict(row)
        try:
            payload = json.loads(str(data.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not (payload.get("action_created") or payload.get("final_block_reason") or payload.get("skip_reason")):
            continue
        skipped.append(
            _sanitize_row(
                {
                    "decision_time": data.get("created_at", ""),
                    "config_hash": data.get("config_hash", ""),
                    "run_id": data.get("run_id", ""),
                    "experiment_name": data.get("experiment_name", ""),
                    "code": data.get("code", ""),
                    "candidate_action_type": payload.get("candidate_action_type") or data.get("action", ""),
                    "block_reason": payload.get("final_block_reason") or payload.get("block_reason", ""),
                    "skip_reason": payload.get("skip_reason", ""),
                    "current_price": payload.get("current_price", data.get("current_price", "")),
                    "position_state": payload.get("position_state", ""),
                    "current_open_lot_count": payload.get("current_open_lot_count", ""),
                    "max_new_buy_per_day": payload.get("max_new_buy_per_day", ""),
                    "max_new_buy_amount_per_day": payload.get("max_new_buy_amount_per_day", ""),
                    "max_total_open_lots": payload.get("max_total_open_lots", ""),
                    "max_total_invested_amount": payload.get("max_total_invested_amount", ""),
                }
            )
        )
    return skipped


def _metadata(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": summary.get("created_at"),
        "source_db_path": summary.get("source_db_path"),
        "filters": summary.get("filters", {}),
        "included_tables": summary.get("exported_row_counts", {}),
        "row_counts": summary.get("exported_row_counts", {}),
        "config_hashes": summary.get("config_hashes", []),
        "run_ids": summary.get("run_ids", []),
        "sensitive_data_policy": "API keys, tokens, account-like config fields are masked in config snapshots.",
        "can_analyze": [
            "realized LOT/order/fill performance",
            "config_hash/run_id segmented outcomes",
            "blocked/skipped action context",
            "manual vs automatic request outcomes",
        ],
        "cannot_fully_analyze_without_price_history": [
            "accurate what-if backtests for configs not used live",
            "post-block opportunity cost over N days",
            "MDD/MFE between fills",
            "limit-order fill probability",
        ],
        "kis_order_api_called": False,
        "db_mutated": False,
    }


def _readme(metadata: dict[str, Any]) -> str:
    return f"""# Analysis Dataset Export

Created at: {metadata.get('created_at')}

This dataset was exported from the local SQLite trading DB for strategy/config analysis.

## Safety

- KIS order API called: false
- DB mutated: false
- Sensitive config fields are masked in `config_snapshots.jsonl`.

## Included Row Counts

```json
{json.dumps(metadata.get('row_counts', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## Config / Run IDs

- config_hashes: {', '.join(metadata.get('config_hashes', [])) or '-'}
- run_ids: {', '.join(metadata.get('run_ids', [])) or '-'}

## Good Uses

- Compare realized performance by `config_hash` or `run_id`.
- Review LOT-level holding period and realized PnL.
- Inspect skipped/blocked action context.

## Limits

This export alone cannot accurately simulate alternative config outcomes. For what-if backtests, add historical daily/minute prices and liquidity/spread data.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export sanitized DB/log context for strategy tuning analysis.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--code", default="")
    parser.add_argument("--config-hash", default="")
    args = parser.parse_args()
    result = export_analysis_dataset(args.config, args.output_dir, date_from=args.date_from, date_to=args.date_to, code=args.code, config_hash=args.config_hash)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
