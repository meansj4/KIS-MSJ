"""Archive/reset helpers for starting a new trading season.

Default mode is dry-run. This script never calls KIS order APIs.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


CONFIRM_RESET = "RESET 확인"
CONFIRM_LIQUIDATION = "전량매도 요청 확인"

KOSPI_100: list[dict[str, Any]] = [
    {"code": "005930", "name": "삼성전자", "sector": "반도체"},
    {"code": "000660", "name": "SK하이닉스", "sector": "반도체"},
    {"code": "005380", "name": "현대차", "sector": "자동차"},
    {"code": "000270", "name": "기아", "sector": "자동차"},
    {"code": "012330", "name": "현대모비스", "sector": "자동차부품"},
    {"code": "005935", "name": "삼성전자우", "sector": "반도체우선주", "enabled": False, "manual_only": True, "liquidity_warning": True, "note": "KIS KOSPI master 검증에서 미확인되어 자동매수 비활성"},
    {"code": "035420", "name": "NAVER", "sector": "플랫폼"},
    {"code": "035720", "name": "카카오", "sector": "플랫폼"},
    {"code": "207940", "name": "삼성바이오로직스", "sector": "바이오"},
    {"code": "068270", "name": "셀트리온", "sector": "바이오"},
    {"code": "051910", "name": "LG화학", "sector": "화학/배터리"},
    {"code": "373220", "name": "LG에너지솔루션", "sector": "2차전지"},
    {"code": "006400", "name": "삼성SDI", "sector": "2차전지"},
    {"code": "003670", "name": "포스코퓨처엠", "sector": "2차전지소재"},
    {"code": "005490", "name": "POSCO홀딩스", "sector": "철강/2차전지"},
    {"code": "066570", "name": "LG전자", "sector": "전자"},
    {"code": "034220", "name": "LG디스플레이", "sector": "디스플레이"},
    {"code": "011070", "name": "LG이노텍", "sector": "전자부품"},
    {"code": "009150", "name": "삼성전기", "sector": "전자부품"},
    {"code": "018260", "name": "삼성SDS", "sector": "IT서비스"},
    {"code": "003550", "name": "LG", "sector": "지주사"},
    {"code": "034730", "name": "SK", "sector": "지주사"},
    {"code": "028260", "name": "삼성물산", "sector": "지주/건설"},
    {"code": "086790", "name": "하나금융지주", "sector": "금융"},
    {"code": "105560", "name": "KB금융", "sector": "금융"},
    {"code": "055550", "name": "신한지주", "sector": "금융"},
    {"code": "316140", "name": "우리금융지주", "sector": "금융"},
    {"code": "024110", "name": "기업은행", "sector": "금융"},
    {"code": "138930", "name": "BNK금융지주", "sector": "지방금융"},
    {"code": "175330", "name": "JB금융지주", "sector": "지방금융"},
    {"code": "139130", "name": "DGB금융지주", "sector": "지방금융"},
    {"code": "032830", "name": "삼성생명", "sector": "보험"},
    {"code": "000810", "name": "삼성화재", "sector": "보험"},
    {"code": "005830", "name": "DB손해보험", "sector": "보험"},
    {"code": "088350", "name": "한화생명", "sector": "보험"},
    {"code": "071050", "name": "한국금융지주", "sector": "증권"},
    {"code": "039490", "name": "키움증권", "sector": "증권"},
    {"code": "006800", "name": "미래에셋증권", "sector": "증권"},
    {"code": "030200", "name": "KT", "sector": "통신"},
    {"code": "017670", "name": "SK텔레콤", "sector": "통신"},
    {"code": "032640", "name": "LG유플러스", "sector": "통신"},
    {"code": "015760", "name": "한국전력", "sector": "유틸리티"},
    {"code": "036460", "name": "한국가스공사", "sector": "유틸리티"},
    {"code": "051600", "name": "한전KPS", "sector": "전력서비스"},
    {"code": "052690", "name": "한전기술", "sector": "원전/엔지니어링"},
    {"code": "010950", "name": "S-Oil", "sector": "정유"},
    {"code": "096770", "name": "SK이노베이션", "sector": "에너지/배터리"},
    {"code": "078930", "name": "GS", "sector": "에너지지주"},
    {"code": "267250", "name": "HD현대", "sector": "조선/에너지지주"},
    {"code": "329180", "name": "HD현대중공업", "sector": "조선"},
    {"code": "010140", "name": "삼성중공업", "sector": "조선"},
    {"code": "042660", "name": "한화오션", "sector": "조선/방산"},
    {"code": "009540", "name": "HD한국조선해양", "sector": "조선지주"},
    {"code": "064350", "name": "현대로템", "sector": "방산/철도"},
    {"code": "012450", "name": "한화에어로스페이스", "sector": "방산"},
    {"code": "047810", "name": "한국항공우주", "sector": "방산/항공"},
    {"code": "079550", "name": "LIG넥스원", "sector": "방산"},
    {"code": "000880", "name": "한화", "sector": "지주/방산"},
    {"code": "009830", "name": "한화솔루션", "sector": "화학/태양광"},
    {"code": "011780", "name": "금호석유", "sector": "화학"},
    {"code": "011170", "name": "롯데케미칼", "sector": "화학"},
    {"code": "010060", "name": "OCI홀딩스", "sector": "화학/태양광"},
    {"code": "010130", "name": "고려아연", "sector": "비철금속"},
    {"code": "004020", "name": "현대제철", "sector": "철강"},
    {"code": "001230", "name": "동국홀딩스", "sector": "철강/지주", "enabled": False, "manual_only": True, "trading_halted": True, "note": "KIS KOSPI master 기준 trading_halt_yn=Y로 자동매수 비활성"},
    {"code": "000720", "name": "현대건설", "sector": "건설"},
    {"code": "006360", "name": "GS건설", "sector": "건설"},
    {"code": "047040", "name": "대우건설", "sector": "건설"},
    {"code": "375500", "name": "DL이앤씨", "sector": "건설"},
    {"code": "294870", "name": "HDC현대산업개발", "sector": "건설"},
    {"code": "180640", "name": "한진칼", "sector": "항공지주"},
    {"code": "003490", "name": "대한항공", "sector": "항공"},
    {"code": "020560", "name": "아시아나항공", "sector": "항공", "enabled": False, "manual_only": True, "administrative_issue": True, "note": "Korean Air 통합/브랜드 종료 일정 관련 이벤트 리스크로 자동매수 비활성"},
    {"code": "086280", "name": "현대글로비스", "sector": "물류"},
    {"code": "000120", "name": "CJ대한통운", "sector": "물류"},
    {"code": "028670", "name": "팬오션", "sector": "해운"},
    {"code": "011200", "name": "HMM", "sector": "해운"},
    {"code": "004990", "name": "롯데지주", "sector": "지주/소비"},
    {"code": "023530", "name": "롯데쇼핑", "sector": "유통"},
    {"code": "004170", "name": "신세계", "sector": "유통"},
    {"code": "139480", "name": "이마트", "sector": "유통"},
    {"code": "282330", "name": "BGF리테일", "sector": "편의점"},
    {"code": "007070", "name": "GS리테일", "sector": "편의점"},
    {"code": "271560", "name": "오리온", "sector": "음식료"},
    {"code": "097950", "name": "CJ제일제당", "sector": "음식료"},
    {"code": "004370", "name": "농심", "sector": "음식료"},
    {"code": "007310", "name": "오뚜기", "sector": "음식료"},
    {"code": "280360", "name": "롯데웰푸드", "sector": "음식료"},
    {"code": "090430", "name": "아모레퍼시픽", "sector": "화장품"},
    {"code": "051900", "name": "LG생활건강", "sector": "화장품/생활용품"},
    {"code": "161890", "name": "한국콜마", "sector": "화장품ODM"},
    {"code": "192820", "name": "코스맥스", "sector": "화장품ODM"},
    {"code": "001040", "name": "CJ", "sector": "지주/소비"},
    {"code": "003240", "name": "태광산업", "sector": "섬유/화학"},
    {"code": "000150", "name": "두산", "sector": "지주/로봇/에너지"},
    {"code": "034020", "name": "두산에너빌리티", "sector": "원전/플랜트"},
    {"code": "241560", "name": "두산밥캣", "sector": "기계"},
    {"code": "042700", "name": "한미반도체", "sector": "반도체장비"},
    {"code": "000990", "name": "DB하이텍", "sector": "반도체"},
    {"code": "112610", "name": "씨에스윈드", "sector": "풍력/신재생"},
]

PROFILES = {
    "expansion_100_safe": {"max_total_invested_amount": 20_000_000, "max_new_buy_per_day": 10, "max_new_buy_amount_per_day": 2_000_000, "max_total_initial_buy_amount_per_day": 2_000_000, "max_total_open_lots": 300, "max_active_symbols": 100},
    "expansion_100_medium": {"max_total_invested_amount": 30_000_000, "max_new_buy_per_day": 15, "max_new_buy_amount_per_day": 3_000_000, "max_total_initial_buy_amount_per_day": 3_000_000, "max_total_open_lots": 450, "max_active_symbols": 100},
    "expansion_100_aggressive": {"max_total_invested_amount": 50_000_000, "max_new_buy_per_day": 20, "max_new_buy_amount_per_day": 5_000_000, "max_total_initial_buy_amount_per_day": 5_000_000, "max_total_open_lots": 700, "max_active_symbols": 100},
}


def build_stock_rows() -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(KOSPI_100, start=1):
        row = {
            "code": str(item["code"]).zfill(6),
            "name": item["name"],
            "enabled": bool(item.get("enabled", True)),
            "market": "KOSPI",
            "sector": item["sector"],
            "note": item.get("note", "KOSPI 100 후보군"),
            "reason": item.get("reason", "expansion_100_candidate"),
            "manual_only": bool(item.get("manual_only", False)),
            "priority": index,
            "group": "expansion_100",
            "trading_halted": bool(item.get("trading_halted", False)),
            "administrative_issue": bool(item.get("administrative_issue", False)),
            "investment_alert": bool(item.get("investment_alert", False)),
            "audit_opinion_issue": bool(item.get("audit_opinion_issue", False)),
            "delisting_risk": bool(item.get("delisting_risk", False)),
            "accounting_issue": bool(item.get("accounting_issue", False)),
            "liquidity_warning": bool(item.get("liquidity_warning", False)),
        }
        rows.append(row)
    return rows


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def archive_current_state(config_path: Path, archive_root: Path, dry_run: bool) -> dict[str, Any]:
    config = load_json(config_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = archive_root / f"reset_{timestamp}"
    storage_path = Path(config.get("storage_path", ""))
    log_path = Path(config.get("log_path", ""))
    files = {
        "config": config_path,
        "db": storage_path,
        "log": log_path,
    }
    result = {"archive_root": str(root), "dry_run": dry_run, "files": {key: str(value) for key, value in files.items() if str(value)}}
    if dry_run:
        return result
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "db").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "exports").mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        shutil.copy2(config_path, root / "config" / config_path.name)
    if storage_path.exists():
        shutil.copy2(storage_path, root / "db" / storage_path.name)
        export_tables(storage_path, root / "exports")
    if log_path.exists():
        shutil.copy2(log_path, root / "logs" / log_path.name)
    return result


def export_tables(db_path: Path, export_dir: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for table in ("manual_order_requests", "orders", "fills", "lots", "positions"):
            try:
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            out = export_dir / f"{table}.csv"
            with out.open("w", newline="", encoding="utf-8") as output:
                writer = None
                for row in rows:
                    data = dict(row)
                    if writer is None:
                        writer = csv.DictWriter(output, fieldnames=list(data))
                        writer.writeheader()
                    writer.writerow(data)


def apply_expansion_config(config_path: Path, profile: str, dry_run: bool) -> dict[str, Any]:
    config = load_json(config_path)
    config["stocks"] = build_stock_rows()
    risk = config.setdefault("risk", {})
    risk.update(PROFILES[profile])
    risk["profile"] = profile
    config.setdefault("strategy", {})["cleanup_enabled"] = False
    config.setdefault("order", {})["live_trading"] = False
    config.setdefault("order", {})["enable_execution_raw_log"] = True
    config["ui_manual_trading_enabled"] = False
    result = {"stock_count": len(config["stocks"]), "enabled_count": sum(1 for item in config["stocks"] if item["enabled"]), "profile": profile, "dry_run": dry_run}
    if not dry_run:
        save_json_atomic(config_path, config)
    return result


def liquidation_plan(config_path: Path, output_dir: Path, dry_run: bool) -> dict[str, Any]:
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    items = []
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT l.lot_id, l.code, p.name, l.remaining_quantity, l.buy_price, p.current_price,
                           (p.current_price - l.buy_price) * l.remaining_quantity AS estimated_pnl
                    FROM lots l
                    LEFT JOIN positions p ON p.code = l.code
                    WHERE l.remaining_quantity > 0 AND l.status != 'CLOSED'
                    ORDER BY l.code, l.buy_filled_at
                    """
                ).fetchall()
                items = [dict(row) for row in rows]
            except sqlite3.Error:
                items = []
    path = output_dir / f"liquidation_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {"dry_run": dry_run, "order_api_called": False, "manual_requests_created": False, "items": items}
    if not dry_run:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"plan_path": str(path), "item_count": len(items), **payload}


PENDING_MANUAL_STATUSES = ("REQUESTED", "PROCESSING", "ACCEPTED", "SUBMITTED")


def reset_blockers(db_path: Path) -> dict[str, Any]:
    blockers = {"open_order_count": 0, "pending_manual_request_count": 0, "open_lot_count": 0, "sync_required_count": 0, "lot_mismatch_count": 0}
    if not db_path.exists():
        return blockers
    with sqlite3.connect(db_path) as connection:
        try:
            row = connection.execute("SELECT COUNT(*) FROM orders WHERE status IN ('REQUESTED', 'PARTIAL')").fetchone()
            blockers["open_order_count"] = int(row[0] or 0)
        except sqlite3.Error:
            blockers["open_order_count"] = 0
        try:
            placeholders = ",".join("?" for _ in PENDING_MANUAL_STATUSES)
            row = connection.execute(f"SELECT COUNT(*) FROM manual_order_requests WHERE status IN ({placeholders})", PENDING_MANUAL_STATUSES).fetchone()
            blockers["pending_manual_request_count"] = int(row[0] or 0)
        except sqlite3.Error:
            blockers["pending_manual_request_count"] = 0
        try:
            row = connection.execute("SELECT COUNT(*) FROM lots WHERE remaining_quantity > 0 AND status != 'CLOSED'").fetchone()
            blockers["open_lot_count"] = int(row[0] or 0)
        except sqlite3.Error:
            blockers["open_lot_count"] = 0
        try:
            row = connection.execute("SELECT COUNT(*) FROM positions WHERE sync_status = 'SYNC_REQUIRED'").fetchone()
            blockers["sync_required_count"] = int(row[0] or 0)
        except sqlite3.Error:
            blockers["sync_required_count"] = 0
        try:
            row = connection.execute("SELECT COUNT(*) FROM positions WHERE lot_quantity_mismatch = 1").fetchone()
            blockers["lot_mismatch_count"] = int(row[0] or 0)
        except sqlite3.Error:
            blockers["lot_mismatch_count"] = 0
    return blockers


def reset_db(config_path: Path, confirm: str, dry_run: bool) -> dict[str, Any]:
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    if confirm != CONFIRM_RESET:
        return {"reset": False, "reason": "confirm_required", "dry_run": dry_run}
    if not db_path.exists():
        return {"reset": False, "reason": "db_not_found", "dry_run": dry_run}
    blockers = reset_blockers(db_path)
    if any(blockers.values()):
        return {"reset": False, "reason": "reset_blocked_by_open_order_or_sync_mismatch", "blockers": blockers, "dry_run": dry_run}
    if dry_run:
        return {"reset": False, "reason": "dry_run", "db_path": str(db_path), "dry_run": True}
    db_path.unlink()
    return {"reset": True, "db_path": str(db_path), "dry_run": False}


def create_liquidation_manual_requests(config_path: Path, confirm: str, dry_run: bool) -> dict[str, Any]:
    if confirm != CONFIRM_LIQUIDATION:
        return {"created": False, "reason": "confirm_required", "required_confirm_text": CONFIRM_LIQUIDATION, "dry_run": dry_run}
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    if not db_path.exists():
        return {"created": False, "reason": "db_not_found", "dry_run": dry_run}
    blockers = reset_blockers(db_path)
    if blockers["open_order_count"] or blockers["pending_manual_request_count"] or blockers["sync_required_count"] or blockers["lot_mismatch_count"]:
        return {"created": False, "reason": "liquidation_request_blocked_by_pending_work", "blockers": blockers, "dry_run": dry_run}
    plan = liquidation_plan(config_path, Path("exports"), dry_run=True)
    items = plan["items"]
    if dry_run:
        return {"created": False, "reason": "dry_run", "request_count": len(items), "dry_run": True}
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as connection:
        for index, item in enumerate(items, start=1):
            request_id = f"LIQ-{datetime.now().strftime('%Y%m%d%H%M%S')}-{index:04d}"
            preview = {"liquidation_plan": True, **item}
            runtime_snapshot = {"source": "prepare_new_season", "confirm_text": CONFIRM_LIQUIDATION}
            connection.execute(
                """
                INSERT INTO manual_order_requests (
                    request_id, source, requested_by, requested_at, code, side, current_price, amount, quantity, lot_id,
                    order_type, preview_json, runtime_snapshot_json, live_trading, confirm_text_verified,
                    status, block_reason, linked_order_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    "local_script_liquidation",
                    "prepare_new_season",
                    now,
                    str(item["code"]).zfill(6),
                    "SELL",
                    int(item.get("current_price") or 0),
                    int(item.get("current_price") or 0) * int(item.get("remaining_quantity") or 0),
                    int(item.get("remaining_quantity") or 0),
                    str(item.get("lot_id") or ""),
                    "LIMIT",
                    json.dumps(preview, ensure_ascii=False),
                    json.dumps(runtime_snapshot, ensure_ascii=False),
                    int(bool(config.get("order", {}).get("live_trading", False))),
                    1,
                    "REQUESTED",
                    "",
                    "",
                    now,
                    now,
                ),
            )
    return {"created": True, "request_count": len(items), "dry_run": False}


def validate_candidates() -> dict[str, Any]:
    rows = build_stock_rows()
    codes = [row["code"] for row in rows]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    invalid_format = [code for code in codes if len(code) != 6 or not code.isdigit()]
    risk_disabled = [row for row in rows if not row["enabled"] or row["manual_only"] or any(row[key] for key in ("trading_halted", "administrative_issue", "investment_alert", "audit_opinion_issue", "delisting_risk", "accounting_issue", "liquidity_warning"))]
    return {"count": len(rows), "duplicates": duplicates, "invalid_format": invalid_format, "risk_disabled": risk_disabled}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/lot_auto_trader.json")
    parser.add_argument("--archive-root", default="archive")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="expansion_100_safe")
    parser.add_argument("--apply-config", action="store_true")
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--liquidation-plan", action="store_true")
    parser.add_argument("--create-liquidation-requests", action="store_true")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", help="perform selected non-order file operations")
    args = parser.parse_args()

    dry_run = not args.execute
    config_path = Path(args.config)
    result: dict[str, Any] = {"candidate_validation": validate_candidates(), "dry_run": dry_run}
    if args.archive:
        result["archive"] = archive_current_state(config_path, Path(args.archive_root), dry_run)
    if args.apply_config:
        result["config"] = apply_expansion_config(config_path, args.profile, dry_run)
    if args.liquidation_plan:
        result["liquidation_plan"] = liquidation_plan(config_path, Path("exports"), dry_run)
    if args.create_liquidation_requests:
        result["liquidation_requests"] = create_liquidation_manual_requests(config_path, args.confirm, dry_run)
    if args.reset_db:
        result["reset_db"] = reset_db(config_path, args.confirm, dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
