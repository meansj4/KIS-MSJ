"""Plan and execute fill-based repair for a missing execution.

This script never places, sells, revises, or cancels orders. KIS access, when
enabled, is limited to read-only balance/execution inquiry.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kis_msj.config import config_hash, load_config  # noqa: E402
from kis_msj.kis_client import KisClient  # noqa: E402
from kis_msj.lot_manager import LotManager  # noqa: E402
from kis_msj.models import OrderSide, PositionLifecycle, TradeFill  # noqa: E402
from kis_msj.position_manager import PositionManager  # noqa: E402
from kis_msj.storage import StateStore  # noqa: E402


CONFIRM_EXECUTE = "누락체결 복구 확인"
AUDIT_LOG_PATH = PROJECT_ROOT / "logs" / "sync_repair_audit.jsonl"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    store = StateStore(config.storage_path)
    code = args.code.zfill(6)
    run_dir = _run_dir(args.output_dir, code)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = _collect_state(Path(config.storage_path), code, args.order_no)
    _backup_and_export(Path(config.storage_path), run_dir, code)

    kis_info: dict[str, Any] = {"queried": False}
    candidate = _candidate_from_manual_args(args, code, before)
    if args.kis_readonly:
        kis_info = _query_kis_readonly(config, code, args.order_no, before)
        candidate = candidate or kis_info.get("candidate")

    plan = _build_plan(args, code, before, candidate, kis_info)
    _write_json(run_dir / "before_summary.json", before)
    _write_json(run_dir / "repair_plan.json", plan)
    _audit("sync_repair_plan_created", plan, run_dir)
    if candidate:
        _audit("missing_fill_repair_candidate_found", plan, run_dir)
    else:
        _audit("manual_missing_fill_repair_required", plan, run_dir)

    if not args.execute:
        print(json.dumps({"plan_only": True, "run_dir": str(run_dir), "repair_possible": bool(candidate), "plan": plan}, ensure_ascii=False, indent=2))
        return 0

    if args.confirm != CONFIRM_EXECUTE:
        _audit("missing_fill_repair_failed", {**plan, "result": "confirm_text_required"}, run_dir)
        print(json.dumps({"executed": False, "run_dir": str(run_dir), "error": "confirm_text_required", "required_confirm": CONFIRM_EXECUTE}, ensure_ascii=False, indent=2))
        return 2
    if not candidate:
        _audit("missing_fill_repair_failed", {**plan, "result": "missing_execution_candidate"}, run_dir)
        print(json.dumps({"executed": False, "run_dir": str(run_dir), "error": "missing_execution_candidate"}, ensure_ascii=False, indent=2))
        return 3

    result = _execute_repair(config, store, code, args.order_no, candidate, before, args.kis_quantity)
    after = _collect_state(Path(config.storage_path), code, args.order_no)
    _write_json(run_dir / "after_summary.json", after)
    payload = {**plan, **result, "after": after, "operator_confirmed": True}
    _audit("missing_fill_repair_executed" if result["record_fill_inserted"] else "missing_fill_repair_failed", payload, run_dir)
    _audit("sync_repair_completed" if result["sync_cleared"] else "sync_repair_still_mismatch", payload, run_dir)
    print(json.dumps({"executed": True, "run_dir": str(run_dir), "result": result, "after": after}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair a missing fill by inserting a verified execution and applying it through LotManager/PositionManager.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--order-no", required=True)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "exports")
    parser.add_argument("--kis-readonly", action="store_true", help="Use read-only KIS balance/execution inquiries.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--kis-quantity", type=int, default=None)
    parser.add_argument("--manual-filled-at", default="")
    parser.add_argument("--manual-price", type=int, default=0)
    parser.add_argument("--manual-quantity", type=int, default=0)
    parser.add_argument("--manual-execution-id", default="")
    parser.add_argument("--repair-source", default="manual_operator_repair")
    return parser


def _run_dir(output_dir: Path, code: str) -> Path:
    return output_dir / f"sync_repair_nexteel_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{code}"


def _backup_and_export(db_path: Path, run_dir: Path, code: str) -> None:
    if db_path.exists():
        shutil.copy2(db_path, run_dir / "before_db.sqlite3")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for table in ("positions", "lots", "orders", "fills", "manual_order_requests"):
            try:
                columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
                if not columns:
                    continue
                if "code" in columns:
                    rows = connection.execute(f"SELECT * FROM {table} WHERE code = ?", (code,)).fetchall()
                else:
                    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            _write_csv(run_dir / f"before_{table}.csv", [dict(row) for row in rows])


def _collect_state(db_path: Path, code: str, order_no: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        position = _fetch_one(connection, "SELECT * FROM positions WHERE code = ?", (code,))
        order = _fetch_one(connection, "SELECT * FROM orders WHERE order_id = ?", (order_no,))
        fill_rows = [dict(row) for row in connection.execute("SELECT * FROM fills WHERE order_id = ? ORDER BY filled_at", (order_no,))]
        open_lot = _fetch_one(
            connection,
            "SELECT COALESCE(SUM(remaining_quantity), 0) AS quantity, COUNT(*) AS count FROM lots WHERE code = ? AND status != 'CLOSED'",
            (code,),
        )
        all_fill = _fetch_one(connection, "SELECT COUNT(*) AS count FROM fills WHERE code = ?", (code,))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "code": code,
        "order_no": order_no,
        "position": position,
        "order": order,
        "fills_for_order": fill_rows,
        "fills_for_order_count": len(fill_rows),
        "all_fills_count": int((all_fill or {}).get("count") or 0),
        "open_lot_quantity": int((open_lot or {}).get("quantity") or 0),
        "open_lot_count": int((open_lot or {}).get("count") or 0),
        "quantity_gap": int((position or {}).get("quantity") or 0) - int((open_lot or {}).get("quantity") or 0),
        "safe_block": {
            "sync_status": (position or {}).get("sync_status"),
            "position_state": (position or {}).get("position_state"),
            "lot_quantity_mismatch": bool((position or {}).get("lot_quantity_mismatch")),
            "trading_paused": bool((position or {}).get("trading_paused")),
            "auto_buy_enabled": bool((position or {}).get("auto_buy_enabled")),
        },
    }


def _query_kis_readonly(config: Any, code: str, order_no: str, before: dict[str, Any]) -> dict[str, Any]:
    client = KisClient(config.kis_account, enable_execution_raw_log=True)
    order_date = _order_date(before)
    fills = client.executions(since=order_date)
    snapshot = client.account_snapshot()
    positions = [asdict(item) for item in snapshot.positions if item.code == code]
    candidate = None
    for fill in fills:
        if _normalize_order_id(fill.order_id) == _normalize_order_id(order_no) and fill.code == code and fill.side is OrderSide.BUY:
            candidate = _candidate_from_fill(fill, "kis_execution_inquiry")
            break
    return {
        "queried": True,
        "execution_query_since": order_date.isoformat(),
        "fetched_execution_count": len(fills),
        "candidate": candidate,
        "balance_positions": positions,
        "kis_quantity": positions[0]["quantity"] if positions else None,
        "queried_at": datetime.now().isoformat(timespec="seconds"),
    }


def _candidate_from_manual_args(args: argparse.Namespace, code: str, before: dict[str, Any]) -> dict[str, Any] | None:
    if not (args.manual_filled_at or args.manual_price or args.manual_quantity or args.manual_execution_id):
        return None
    if not args.manual_filled_at or args.manual_price <= 0 or args.manual_quantity <= 0:
        return None
    order = before.get("order") or {}
    return {
        "code": code,
        "name": str(order.get("name") or (before.get("position") or {}).get("name") or ""),
        "side": "BUY",
        "quantity": args.manual_quantity,
        "price": args.manual_price,
        "order_id": args.order_no,
        "filled_at": args.manual_filled_at,
        "execution_id": args.manual_execution_id,
        "repair_source": args.repair_source,
    }


def _candidate_from_fill(fill: TradeFill, source: str) -> dict[str, Any]:
    return {
        "code": fill.code,
        "name": fill.name,
        "side": fill.side.value,
        "quantity": fill.quantity,
        "price": fill.price,
        "order_id": fill.order_id,
        "filled_at": fill.filled_at.isoformat(timespec="seconds"),
        "execution_id": fill.execution_id,
        "repair_source": source,
    }


def _build_plan(args: argparse.Namespace, code: str, before: dict[str, Any], candidate: dict[str, Any] | None, kis_info: dict[str, Any]) -> dict[str, Any]:
    before_position_qty = int((before.get("position") or {}).get("quantity") or 0)
    before_open_qty = int(before.get("open_lot_quantity") or 0)
    candidate_qty = int((candidate or {}).get("quantity") or 0)
    kis_quantity = args.kis_quantity if args.kis_quantity is not None else kis_info.get("kis_quantity")
    return {
        "code": code,
        "name": (before.get("position") or {}).get("name") or (candidate or {}).get("name") or "",
        "order_no": args.order_no,
        "order_id": args.order_no,
        "side": "BUY",
        "before_position_quantity": before_position_qty,
        "before_open_lot_quantity": before_open_qty,
        "quantity_gap": before_position_qty - before_open_qty,
        "candidate": candidate,
        "candidate_found": bool(candidate),
        "expected_after_open_lot_quantity": before_open_qty + candidate_qty,
        "kis_quantity": kis_quantity,
        "repair_source": (candidate or {}).get("repair_source", ""),
        "operator_confirmed": False,
        "execute_requires_confirm": CONFIRM_EXECUTE,
        "kis_readonly": kis_info,
        "safe_block": before.get("safe_block"),
        "result": "plan_created",
    }


def _execute_repair(config: Any, store: StateStore, code: str, order_no: str, candidate: dict[str, Any], before: dict[str, Any], kis_quantity: int | None) -> dict[str, Any]:
    store.set_active_config(config_hash(config), run_id=config.run_id or config.experiment.run_id, experiment_name=config.experiment.experiment_name or config.experiment_name, profile_name=config.risk.profile)
    fill = TradeFill(
        code=code,
        name=str(candidate.get("name") or (before.get("position") or {}).get("name") or ""),
        side=OrderSide.BUY,
        quantity=int(candidate["quantity"]),
        price=int(candidate["price"]),
        order_id=order_no,
        filled_at=datetime.fromisoformat(str(candidate["filled_at"])),
        lot_id="",
        execution_id=str(candidate.get("execution_id") or ""),
    )
    inserted = store.record_fill(fill)
    if not inserted:
        return {"record_fill_inserted": False, "apply_fill_called": False, "sync_cleared": False, "result": "duplicate_or_existing_fill"}

    lot_manager = LotManager(config.strategy, store.load_lots())
    position_manager = PositionManager(config.strategy, lot_manager, store.load_positions())
    updated = position_manager.apply_fill(fill)
    after_open_qty = sum(lot.remaining_quantity for lot in lot_manager.open_lots(code))
    expected_kis_qty = kis_quantity if kis_quantity is not None else int((before.get("position") or {}).get("quantity") or 0)
    sync_cleared = after_open_qty == expected_kis_qty
    if sync_cleared:
        updated.lot_quantity_mismatch = False
        updated.sync_status = "OK"
        updated.trading_paused = False
        if not updated.needs_review and not updated.danger_state:
            updated.auto_buy_enabled = True
        updated.position_state = PositionLifecycle.HOLDING.value if after_open_qty else PositionLifecycle.WAIT_REENTRY.value
        updated.review_reason = updated.review_reason if updated.needs_review else ""
        updated.skip_reason = ""
    store.save_position(updated)
    store.save_lots(lot_manager.lots.values())
    return {
        "record_fill_inserted": True,
        "apply_fill_called": True,
        "created_lot_quantity": fill.quantity,
        "created_lot_price": fill.price,
        "after_open_lot_quantity": after_open_qty,
        "expected_kis_quantity": expected_kis_qty,
        "sync_cleared": sync_cleared,
        "result": "repaired" if sync_cleared else "fill_applied_but_still_mismatch",
    }


def _order_date(before: dict[str, Any]) -> date:
    requested_at = str((before.get("order") or {}).get("requested_at") or "")
    if requested_at:
        try:
            return datetime.fromisoformat(requested_at).date() - timedelta(days=1)
        except ValueError:
            pass
    return date.today() - timedelta(days=1)


def _fetch_one(connection: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = connection.execute(query, params).fetchone()
    return dict(row) if row else None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _audit(event: str, payload: dict[str, Any], run_dir: Path) -> None:
    item = {"event": event, "created_at": datetime.now().isoformat(timespec="seconds"), **payload}
    line = json.dumps(item, ensure_ascii=False, default=str)
    for path in (run_dir / "audit_log.jsonl", AUDIT_LOG_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _normalize_order_id(order_id: str) -> str:
    normalized = str(order_id).strip()
    return normalized.lstrip("0") or normalized


if __name__ == "__main__":
    raise SystemExit(main())
