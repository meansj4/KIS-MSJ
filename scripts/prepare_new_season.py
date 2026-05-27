"""Archive/reset helpers for starting a new trading season.

Default mode is dry-run. This script never calls KIS order APIs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


CONFIRM_RESET = "RESET 확인"
CONFIRM_LIQUIDATION = "전량매도 요청 확인"
PENDING_ORDER_STATUSES = ("REQUESTED", "PARTIAL", "SUBMITTED", "ACCEPTED", "PENDING", "OPEN", "NEW")
TERMINAL_ORDER_STATUSES = ("FILLED", "CANCELED", "REJECTED", "FAILED", "EXPIRED", "PARTIAL_CANCELED", "NONE")
PENDING_MANUAL_STATUSES = ("REQUESTED", "PROCESSING", "ACCEPTED", "SUBMITTED", "PENDING", "OPEN", "NEW", "CREATED", "RETRYING")
TERMINAL_MANUAL_STATUSES = ("FILLED", "CANCELED", "REJECTED", "FAILED", "BLOCKED", "EXPIRED")
PLAN_ACTIVE_STATUSES = ("ACTIVE",)
PLAN_TERMINAL_STATUSES = ("EXPIRED", "SUPERSEDED", "USED", "BLOCKED")
DEFAULT_PLAN_MAX_AGE_MINUTES = 60
SNAPSHOT_WARN_MISSING_GENERATED_AT = "snapshot_generated_at_missing_warning"
SNAPSHOT_WARN_SELLABLE_FALLBACK = "snapshot_sellable_quantity_fallback_warning"

KOSPI_100: list[dict[str, Any]] = [{'code': '175330', 'name': 'JB금융지주', 'sector': '금융', 'note': '저PBR·배당·자본여력 우선'}, {'code': '024110', 'name': '기업은행', 'sector': '금융', 'note': '저PBR·배당·자본여력 우선'}, {'code': '138930', 'name': 'BNK금융지주', 'sector': '지방금융', 'note': '저PBR·배당·자본여력 우선'}, {'code': '032640', 'name': 'LG유플러스', 'sector': '통신', 'note': '안정 현금흐름/방어주 성격'}, {'code': '192400', 'name': '쿠쿠홀딩스', 'sector': '소비재/지주', 'note': '브랜드/현금흐름·소비회복 후보'}, {'code': '030190', 'name': 'NICE평가정보', 'sector': '데이터/신용평가', 'note': '실적·데이터 사업 안정성'}, {'code': '035250', 'name': '강원랜드', 'sector': '카지노/레저', 'note': '독점성/배당·현금흐름'}, {'code': '114090', 'name': 'GKL', 'sector': '카지노/레저', 'note': '카지노 회복/재무 안정성'}, {'code': '300720', 'name': '한일시멘트', 'sector': '건설소재', 'note': '배당/저PBR·건설소재 회복'}, {'code': '007070', 'name': 'GS리테일', 'sector': '편의점/유통', 'note': '편의점 기반 안정 현금흐름'}, {'code': '284740', 'name': '쿠쿠홈시스', 'sector': '렌탈/가전', 'note': '렌탈/가전 현금흐름'}, {'code': '034310', 'name': 'NICE', 'sector': '지주/데이터', 'note': '지주·데이터 자회사 가치'}, {'code': '453340', 'name': '현대그린푸드', 'sector': '식자재/급식', 'note': '식자재/단체급식 안정성'}, {'code': '020000', 'name': '한섬', 'sector': '패션', 'note': '브랜드력/저평가 소비재'}, {'code': '093050', 'name': 'LF', 'sector': '패션', 'note': '저평가 패션/배당 후보'}, {'code': '026960', 'name': '동서', 'sector': '음식료/소비재', 'note': '현금성 자산/방어적 소비재'}, {'code': '105630', 'name': '한세실업', 'sector': '의류 OEM', 'note': '의류 OEM 회복 후보'}, {'code': '248070', 'name': '솔루엠', 'sector': '전자부품', 'note': '전자부품/ESL 성장성'}, {'code': '178920', 'name': 'PI첨단소재', 'sector': 'IT소재', 'note': 'IT소재 회복 후보'}, {'code': '475560', 'name': '더본코리아', 'sector': '외식/소비재', 'note': '브랜드 확장/소비재 모멘텀'}, {'code': '033270', 'name': '유나이티드제약', 'sector': '제약', 'note': '실적형 제약·방어주 성격'}, {'code': '003850', 'name': '보령', 'sector': '제약', 'note': '제약 실적 안정성'}, {'code': '003220', 'name': '대원제약', 'sector': '제약', 'note': '실적형 제약/배당 후보'}, {'code': '005500', 'name': '삼진제약', 'sector': '제약', 'note': '제약 방어주/현금흐름'}, {'code': '009290', 'name': '광동제약', 'sector': '제약/음료', 'note': '음료+제약 안정성'}, {'code': '016580', 'name': '환인제약', 'sector': '제약', 'note': '정신신경계 전문 제약'}, {'code': '004310', 'name': '현대약품', 'sector': '제약/음료', 'note': '제약/음료 방어성'}, {'code': '102460', 'name': '이연제약', 'sector': '제약', 'note': '제약 생산 인프라'}, {'code': '234080', 'name': 'JW생명과학', 'sector': '제약', 'note': '수액/의약품 안정 수요'}, {'code': '214390', 'name': '경보제약', 'sector': '원료의약품', 'note': '원료의약품 회복 후보'}, {'code': '088980', 'name': '맥쿼리인프라', 'sector': '인프라/배당', 'note': '배당형 인프라 대표'}, {'code': '395400', 'name': 'SK리츠', 'sector': '리츠', 'note': '배당형 리츠 후보'}, {'code': '365550', 'name': 'ESR켄달스퀘어리츠', 'sector': '리츠/물류', 'note': '물류센터 리츠'}, {'code': '330590', 'name': '롯데리츠', 'sector': '리츠', 'note': '대형 리츠/배당 후보'}, {'code': '293940', 'name': '신한알파리츠', 'sector': '리츠/오피스', 'note': '오피스 리츠 배당'}, {'code': '357120', 'name': '코람코라이프인프라리츠', 'sector': '리츠/인프라', 'note': '인프라형 리츠'}, {'code': '415640', 'name': 'KB발해인프라', 'sector': '인프라/배당', 'note': '인프라/배당 후보'}, {'code': '088260', 'name': '이리츠코크렙', 'sector': '리츠', 'note': '배당 리츠'}, {'code': '432320', 'name': 'KB스타리츠', 'sector': '리츠', 'note': '배당형 리츠'}, {'code': '400760', 'name': 'NH올원리츠', 'sector': '리츠', 'note': '배당형 리츠'}, {'code': '267290', 'name': '경동도시가스', 'sector': '도시가스', 'note': '도시가스 방어주'}, {'code': '034590', 'name': '인천도시가스', 'sector': '도시가스', 'note': '도시가스 안정성'}, {'code': '117580', 'name': '대성에너지', 'sector': '도시가스/에너지', 'note': '에너지 방어주'}, {'code': '003690', 'name': '코리안리', 'sector': '보험/재보험', 'note': '재보험/배당 후보'}, {'code': '082640', 'name': '동양생명', 'sector': '보험', 'note': '보험주 저PBR'}, {'code': '088350', 'name': '한화생명', 'sector': '보험', 'note': '보험주 저PBR'}, {'code': '001500', 'name': '현대차증권', 'sector': '증권', 'note': '증권주 배당/저평가'}, {'code': '003470', 'name': '유안타증권', 'sector': '증권', 'note': '증권주 저평가'}, {'code': '030610', 'name': '교보증권', 'sector': '증권', 'note': '증권주 배당 후보'}, {'code': '085620', 'name': '미래에셋생명', 'sector': '보험', 'note': '보험주 저평가'}, {'code': '011760', 'name': '현대코퍼레이션', 'sector': '상사', 'note': '상사/자원 트레이딩'}, {'code': '044450', 'name': 'KSS해운', 'sector': '해운', 'note': '해운 안정계약 후보'}, {'code': '028670', 'name': '팬오션', 'sector': '해운', 'note': '벌크 해운 회복 후보'}, {'code': '003490', 'name': '대한항공', 'sector': '항공', 'note': '항공 대형주/수요 회복'}, {'code': '002320', 'name': '한진', 'sector': '물류', 'note': '물류/택배 회복 후보'}, {'code': '004360', 'name': '세방', 'sector': '물류', 'note': '물류 저평가 후보'}, {'code': '009070', 'name': 'KCTC', 'sector': '물류/항만', 'note': '물류/항만 후보'}, {'code': '079430', 'name': '현대리바트', 'sector': '가구/인테리어', 'note': '가구/인테리어 회복'}, {'code': '001680', 'name': '대상', 'sector': '식품', 'note': '식품 방어주'}, {'code': '017810', 'name': '풀무원', 'sector': '식품/건강식', 'note': '식품/건강식 소비재'}, {'code': '003960', 'name': '사조대림', 'sector': '식품/수산', 'note': '식품 실적 후보'}, {'code': '006090', 'name': '사조오양', 'sector': '식품/수산', 'note': '식품/수산 가공'}, {'code': '101530', 'name': '해태제과식품', 'sector': '제과', 'note': '제과 방어주'}, {'code': '264900', 'name': '크라운제과', 'sector': '제과', 'note': '제과 소비재'}, {'code': '005740', 'name': '크라운해태홀딩스', 'sector': '지주/식품', 'note': '지주/식품 가치'}, {'code': '272550', 'name': '삼양패키징', 'sector': '포장재', 'note': '포장재 안정 수요'}, {'code': '122900', 'name': '아이마켓코리아', 'sector': 'B2B 유통', 'note': 'B2B 유통 안정성'}, {'code': '030000', 'name': '제일기획', 'sector': '광고', 'note': '광고 대형주/배당'}, {'code': '214320', 'name': '이노션', 'sector': '광고/마케팅', 'note': '광고/마케팅 안정성'}, {'code': '035000', 'name': 'HS애드', 'sector': '광고', 'note': '광고 회복 후보'}, {'code': '031430', 'name': '신세계인터내셔날', 'sector': '패션/브랜드', 'note': '브랜드/소비 회복'}, {'code': '002790', 'name': '아모레퍼시픽홀딩스', 'sector': '화장품 지주', 'note': '화장품 지주 회복'}, {'code': '007700', 'name': 'F&F홀딩스', 'sector': '지주/브랜드', 'note': '지주/브랜드 가치'}, {'code': '084690', 'name': '대상홀딩스', 'sector': '식품 지주', 'note': '식품 지주 가치'}, {'code': '001800', 'name': '오리온홀딩스', 'sector': '식품 지주', 'note': '식품 지주 안정성'}, {'code': '005440', 'name': '현대지에프홀딩스', 'sector': '지주', 'note': '지주사 할인 관점'}, {'code': '004990', 'name': '롯데지주', 'sector': '지주/소비', 'note': '지주사 저평가'}, {'code': '383800', 'name': 'LX홀딩스', 'sector': '지주', 'note': '지주사 자산가치'}, {'code': '001230', 'name': '동국홀딩스', 'sector': '철강/지주', 'note': '철강 지주 가치'}, {'code': '001940', 'name': 'KISCO홀딩스', 'sector': '지주/철강', 'note': '지주사/자산가치'}, {'code': '000240', 'name': '한국앤컴퍼니', 'sector': '지주/타이어', 'note': '지주/타이어 가치'}, {'code': '002350', 'name': '넥센타이어', 'sector': '타이어', 'note': '타이어 수출 회복'}, {'code': '005720', 'name': '넥센', 'sector': '지주/타이어', 'note': '지주/자산가치'}, {'code': '073240', 'name': '금호타이어', 'sector': '타이어', 'note': '타이어 턴어라운드'}, {'code': '010690', 'name': '화신', 'sector': '자동차부품', 'note': '자동차부품 수출'}, {'code': '200880', 'name': '서연이화', 'sector': '자동차부품', 'note': '자동차부품 저평가'}, {'code': '021820', 'name': '세원정공', 'sector': '자동차부품', 'note': '자동차부품 저평가'}, {'code': '023810', 'name': '인팩', 'sector': '자동차부품', 'note': '자동차부품 후보'}, {'code': '092200', 'name': '디아이씨', 'sector': '자동차부품', 'note': '자동차부품 회복'}, {'code': '000430', 'name': '대원강업', 'sector': '자동차부품', 'note': '자동차부품/스프링'}, {'code': '041650', 'name': '상신브레이크', 'sector': '자동차부품', 'note': '자동차부품 안정성'}, {'code': '123700', 'name': 'SJM', 'sector': '자동차부품', 'note': '자동차부품 저평가'}, {'code': '025530', 'name': 'SJM홀딩스', 'sector': '지주/자동차부품', 'note': '지주/자동차부품'}, {'code': '163560', 'name': '동일고무벨트', 'sector': '산업재/고무', 'note': '산업용 고무/부품'}, {'code': '004840', 'name': 'DRB동일', 'sector': '산업재/고무', 'note': '산업재/고무부품'}, {'code': '004560', 'name': '현대비앤지스틸', 'sector': '철강', 'note': '철강 저PBR 후보'}, {'code': '002710', 'name': 'TCC스틸', 'sector': '철강/2차전지소재', 'note': '2차전지/철강 소재'}, {'code': '058430', 'name': '포스코스틸리온', 'sector': '철강', 'note': '철강 가공 안정성'}, {'code': '084010', 'name': '대한제강', 'sector': '철강', 'note': '철근/배당 후보'}, {'code': '104700', 'name': '한국철강', 'sector': '철강', 'note': '철강 저평가'}, {'code': '460860', 'name': '동국제강', 'sector': '철강', 'note': '철강 저PBR'}, {'code': '460850', 'name': '동국씨엠', 'sector': '철강/컬러강판', 'note': '컬러강판 회복'}, {'code': '075580', 'name': '세진중공업', 'sector': '조선기자재', 'note': '조선기자재 후보'}, {'code': '092790', 'name': '넥스틸', 'sector': '강관/에너지 인프라', 'note': '강관/에너지 인프라'}, {'code': '005010', 'name': '휴스틸', 'sector': '강관', 'note': '강관 저평가'}, {'code': '008260', 'name': 'NI스틸', 'sector': '철강재', 'note': '철강재 안정성'}, {'code': '008420', 'name': '문배철강', 'sector': '철강 유통', 'note': '철강 유통 저평가'}, {'code': '026940', 'name': '부국철강', 'sector': '철강 유통', 'note': '철강 유통 후보'}, {'code': '210540', 'name': '디와이파워', 'sector': '건설기계부품', 'note': '건설기계 부품'}, {'code': '005680', 'name': '삼영전자', 'sector': '전자부품', 'note': '전자부품 현금흐름'}, {'code': '035510', 'name': '신세계 I&C', 'sector': 'IT서비스', 'note': 'IT서비스 안정성'}, {'code': '286940', 'name': '롯데이노베이트', 'sector': 'IT서비스', 'note': 'SI/IT서비스'}, {'code': '078000', 'name': '텔코웨어', 'sector': '통신 SW', 'note': '통신 SW 안정성'}, {'code': '192650', 'name': '드림텍', 'sector': '전자부품', 'note': '전자부품 회복'}, {'code': '097520', 'name': '엠씨넥스', 'sector': '전자부품/카메라', 'note': '카메라모듈 회복'}, {'code': '077500', 'name': '유니퀘스트', 'sector': '반도체 유통/설계', 'note': '반도체 유통/설계'}, {'code': '004710', 'name': '한솔테크닉스', 'sector': '전자부품', 'note': '전자부품 회복'}, {'code': '094280', 'name': '효성ITX', 'sector': 'IT서비스/컨택센터', 'note': 'IT서비스/컨택센터'}, {'code': '100090', 'name': 'SK오션플랜트', 'sector': '해상풍력/플랜트', 'note': '해상풍력/플랜트'}, {'code': '053690', 'name': '한미글로벌', 'sector': 'PM/건설관리', 'note': 'PM/건설관리'}]


PROFILES = {
    "expansion_100_safe": {"max_total_invested_amount": 30_000_000, "max_new_buy_per_day": 120, "max_new_buy_amount_per_day": 30_000_000, "max_total_initial_buy_amount_per_day": 30_000_000, "max_total_open_lots": 500, "max_active_symbols": 120},
    "expansion_100_medium": {"max_total_invested_amount": 50_000_000, "max_new_buy_per_day": 120, "max_new_buy_amount_per_day": 50_000_000, "max_total_initial_buy_amount_per_day": 50_000_000, "max_total_open_lots": 700, "max_active_symbols": 120},
    "expansion_100_aggressive": {"max_total_invested_amount": 100_000_000, "max_new_buy_per_day": 120, "max_new_buy_amount_per_day": 100_000_000, "max_total_initial_buy_amount_per_day": 100_000_000, "max_total_open_lots": 1200, "max_active_symbols": 120},
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
            "note": item.get("note", "KOSPI 120 bootstrap 후보군"),
            "reason": item.get("reason", "bootstrap_120_candidate"),
            "manual_only": bool(item.get("manual_only", False)),
            "priority": index,
            "group": "bootstrap_120_low_price",
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
    config.setdefault("order", {})["enable_execution_raw_log"] = False
    config["ui_manual_trading_enabled"] = True
    result = {"stock_count": len(config["stocks"]), "enabled_count": sum(1 for item in config["stocks"] if item["enabled"]), "profile": profile, "dry_run": dry_run}
    if not dry_run:
        save_json_atomic(config_path, config)
    return result


def _parse_snapshot_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing generated_at")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def validate_kis_balance_snapshot(
    path: Path | None,
    *,
    mode: str = "preview",
    max_age_minutes: int = DEFAULT_PLAN_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    if path is None:
        return {
            "valid": False,
            "reason": "liquidation_kis_balance_fetch_failed",
            "balances": {},
            "warnings": warnings,
            "errors": ["liquidation_kis_balance_fetch_failed"],
            "generated_at": "",
            "snapshot_age_minutes": None,
            "snapshot_validation_mode": mode,
            "request_creation_allowed": False,
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
    result: dict[str, dict[str, int]] = {}
    generated_at = raw.get("generated_at", "") if isinstance(raw, dict) else ""
    snapshot_age_minutes: float | None = None
    try:
        generated_dt = _parse_snapshot_time(generated_at)
        now = datetime.now(generated_dt.tzinfo) if generated_dt.tzinfo else datetime.now()
        snapshot_age_minutes = max(0.0, (now - generated_dt).total_seconds() / 60.0)
        if max_age_minutes >= 0 and snapshot_age_minutes > max_age_minutes:
            errors.append("liquidation_kis_balance_snapshot_stale")
    except ValueError:
        if generated_at:
            errors.append("liquidation_kis_balance_snapshot_invalid_generated_at")
        else:
            warnings.append(SNAPSHOT_WARN_MISSING_GENERATED_AT)
            if mode == "create_request":
                errors.append("liquidation_kis_balance_snapshot_missing_generated_at")
    for row in rows:
        code = str(row.get("code") or row.get("pdno") or row.get("symbol") or "").zfill(6)
        if not code or code == "000000":
            continue
        quantity = int(float(row.get("quantity") or row.get("hldg_qty") or row.get("holding_quantity") or 0))
        sellable_value = row.get("sellable_quantity")
        if sellable_value in (None, ""):
            sellable_value = row.get("ord_psbl_qty")
        if sellable_value in (None, ""):
            sellable_value = row.get("available_quantity")
        if sellable_value in (None, ""):
            warnings.append(SNAPSHOT_WARN_SELLABLE_FALLBACK)
            if mode == "create_request":
                errors.append("liquidation_kis_sellable_quantity_missing")
            sellable_value = quantity
        sellable = int(float(sellable_value))
        result[code] = {"holding_quantity": quantity, "sellable_quantity": sellable}
    reason = errors[0] if errors else ""
    return {
        "valid": not errors,
        "reason": reason,
        "balances": result,
        "warnings": sorted(set(warnings)),
        "errors": sorted(set(errors)),
        "generated_at": str(generated_at or ""),
        "snapshot_age_minutes": snapshot_age_minutes,
        "snapshot_validation_mode": mode,
        "request_creation_allowed": mode == "create_request" and not errors,
    }


def load_kis_balance_json(path: Path | None) -> dict[str, dict[str, int]]:
    if path is None:
        return {}
    return validate_kis_balance_snapshot(path, mode="preview")["balances"]
    return result


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def db_open_lot_snapshot(db_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                result = connection.execute(
                    """
                    SELECT lot_id, code, remaining_quantity, buy_price, status, buy_filled_at
                    FROM lots
                    WHERE remaining_quantity > 0 AND status != 'CLOSED'
                    ORDER BY code, lot_id
                    """
                ).fetchall()
                rows = [dict(row) for row in result]
            except sqlite3.Error:
                rows = []
    return {"rows": rows, "count": len(rows), "hash": _stable_hash(rows)}


def kis_balance_snapshot(kis_balances: dict[str, dict[str, int]] | None) -> dict[str, Any]:
    rows = [
        {"code": code, "holding_quantity": int(item.get("holding_quantity", 0)), "sellable_quantity": int(item.get("sellable_quantity", 0))}
        for code, item in sorted((kis_balances or {}).items())
    ]
    return {"rows": rows, "count": len(rows), "hash": _stable_hash(rows)}


def _plan_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob("liquidation_plan_*.json"))


def _update_plan_status(path: Path, status: str, reason: str = "") -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["status"] = status
    if reason:
        data["status_reason"] = reason
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def supersede_active_plans(output_dir: Path) -> list[str]:
    superseded: list[str] = []
    for path in _plan_files(output_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == "ACTIVE":
            _update_plan_status(path, "SUPERSEDED", "new_plan_created")
            superseded.append(str(path))
    return superseded


def liquidation_plan(
    config_path: Path,
    output_dir: Path,
    dry_run: bool,
    kis_balances: dict[str, dict[str, int]] | None = None,
    kis_balance_path: Path | None = None,
    max_age_minutes: int = DEFAULT_PLAN_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    items = []
    snapshot_validation = validate_kis_balance_snapshot(kis_balance_path, mode="preview", max_age_minutes=max_age_minutes) if kis_balance_path else {
        "valid": False,
        "reason": "liquidation_kis_balance_fetch_required",
        "balances": kis_balances or {},
        "warnings": [],
        "errors": ["liquidation_kis_balance_fetch_required"] if not kis_balances else [],
        "generated_at": "",
        "snapshot_age_minutes": None,
        "snapshot_validation_mode": "preview",
        "request_creation_allowed": False,
    }
    kis_balances = snapshot_validation.get("balances") or kis_balances or {}
    strict_snapshot_validation = (
        validate_kis_balance_snapshot(kis_balance_path, mode="create_request", max_age_minutes=max_age_minutes)
        if kis_balance_path
        else {**snapshot_validation, "valid": False, "request_creation_allowed": False}
    )
    source = "db_and_kis_reconciled" if kis_balances else "db_only_dry_run"
    created_at = datetime.now()
    db_snapshot = db_open_lot_snapshot(db_path)
    kis_snapshot = kis_balance_snapshot(kis_balances)
    blockers = reset_blockers(db_path)
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                lots = connection.execute(
                    """
                    SELECT l.lot_id, l.code, p.name, l.remaining_quantity, l.buy_price, p.current_price,
                           COALESCE(p.quantity, 0) AS db_position_quantity,
                           COALESCE(p.sync_status, '') AS sync_status,
                           COALESCE(p.lot_quantity_mismatch, 0) AS lot_quantity_mismatch,
                           (p.current_price - l.buy_price) * l.remaining_quantity AS estimated_pnl
                    FROM lots l
                    LEFT JOIN positions p ON p.code = l.code
                    WHERE l.remaining_quantity > 0 AND l.status != 'CLOSED'
                    ORDER BY l.code, l.buy_filled_at
                    """
                ).fetchall()
                db_qty = {
                    str(row["code"]).zfill(6): int(row["qty"] or 0)
                    for row in connection.execute("SELECT code, SUM(remaining_quantity) AS qty FROM lots WHERE remaining_quantity > 0 AND status != 'CLOSED' GROUP BY code").fetchall()
                }
                placeholders = ",".join("?" for _ in PENDING_ORDER_STATUSES)
                open_orders: dict[str, int] = {}
                for row in connection.execute(f"SELECT code, SUM(quantity) AS qty FROM orders WHERE status IN ({placeholders}) GROUP BY code", PENDING_ORDER_STATUSES).fetchall():
                    open_orders[str(row["code"]).zfill(6)] = int(row["qty"] or 0)
                manual_placeholders = ",".join("?" for _ in PENDING_MANUAL_STATUSES)
                pending_manual: dict[tuple[str, str], int] = {}
                pending_manual_by_code: dict[str, int] = {}
                for row in connection.execute(f"SELECT code, lot_id, SUM(quantity) AS qty FROM manual_order_requests WHERE side = 'SELL' AND status IN ({manual_placeholders}) GROUP BY code, lot_id", PENDING_MANUAL_STATUSES).fetchall():
                    code = str(row["code"]).zfill(6)
                    lot_id = str(row["lot_id"] or "")
                    qty = int(row["qty"] or 0)
                    pending_manual[(code, lot_id)] = qty
                    pending_manual_by_code[code] = pending_manual_by_code.get(code, 0) + qty
                items = []
                for row in lots:
                    item = dict(row)
                    code = str(item["code"]).zfill(6)
                    lot_id = str(item["lot_id"])
                    db_open_qty = db_qty.get(code, 0)
                    kis = kis_balances.get(code, {})
                    kis_qty = int(kis.get("holding_quantity", 0)) if kis_balances else None
                    kis_sellable = int(kis.get("sellable_quantity", 0)) if kis_balances else None
                    block_reason = ""
                    if not kis_balances:
                        block_reason = "liquidation_kis_balance_fetch_required"
                    elif kis_qty != db_open_qty:
                        block_reason = "liquidation_kis_balance_mismatch"
                    elif kis_sellable < db_open_qty:
                        block_reason = "liquidation_sellable_quantity_insufficient"
                    elif item.get("sync_status") == "SYNC_REQUIRED":
                        block_reason = "liquidation_sync_required"
                    elif bool(item.get("lot_quantity_mismatch")):
                        block_reason = "liquidation_lot_quantity_mismatch"
                    elif open_orders.get(code, 0) > 0:
                        block_reason = "liquidation_open_order_exists"
                    elif pending_manual.get((code, lot_id), 0) > 0 or pending_manual_by_code.get(code, 0) > 0:
                        block_reason = "liquidation_pending_manual_sell_exists"
                    item.update(
                        {
                            "db_open_lot_quantity": db_open_qty,
                            "db_position_quantity": int(item.get("db_position_quantity") or 0),
                            "kis_holding_quantity": kis_qty,
                            "kis_sellable_quantity": kis_sellable,
                            "quantity_diff": None if kis_qty is None else db_open_qty - kis_qty,
                            "open_order_quantity": open_orders.get(code, 0),
                            "pending_manual_sell_quantity": pending_manual_by_code.get(code, 0),
                            "eligible_for_liquidation_request": block_reason == "",
                            "block_reason": block_reason,
                            "expected_sell_quantity": int(item.get("remaining_quantity") or 0),
                            "source": source,
                        }
                    )
                    items.append(item)
            except sqlite3.Error:
                items = []
    plan_blocked = (
        not kis_balances
        or blockers["open_order_count"] > 0
        or blockers["pending_manual_request_count"] > 0
        or blockers["sync_required_count"] > 0
        or blockers["lot_mismatch_count"] > 0
        or any(not item.get("eligible_for_liquidation_request") for item in items)
    )
    status = "BLOCKED" if plan_blocked else "ACTIVE"
    plan_id = f"LIQPLAN-{created_at.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    path = output_dir / f"liquidation_plan_{created_at.strftime('%Y%m%d_%H%M%S')}_{plan_id.rsplit('-', 1)[-1]}.json"
    superseded = [] if dry_run else supersede_active_plans(output_dir)
    payload = {
        "plan_id": plan_id,
        "created_at": created_at.isoformat(timespec="seconds"),
        "db_snapshot_at": created_at.isoformat(timespec="seconds"),
        "kis_balance_snapshot_at": created_at.isoformat(timespec="seconds") if kis_balances else "",
        "source_db_path": str(db_path),
        "db_identity": str(db_path.resolve()) if db_path.exists() else str(db_path),
        "source_kis_snapshot_path": str(kis_balance_path or ""),
        "db_open_lot_hash": db_snapshot["hash"],
        "kis_snapshot_hash": kis_snapshot["hash"] if kis_balances else "",
        "open_lot_count": db_snapshot["count"],
        "pending_order_count": blockers["open_order_count"],
        "pending_manual_request_count": blockers["pending_manual_request_count"],
        "sync_required_count": blockers["sync_required_count"],
        "lot_mismatch_count": blockers["lot_mismatch_count"],
        "status": status,
        "status_reason": "blocked_by_missing_or_ineligible_snapshot" if status == "BLOCKED" else "",
        "expires_at": (created_at + timedelta(minutes=max_age_minutes)).isoformat(timespec="seconds"),
        "max_age_minutes": max_age_minutes,
        "snapshot_warnings": snapshot_validation.get("warnings", []),
        "snapshot_errors": snapshot_validation.get("errors", []),
        "snapshot_generated_at": snapshot_validation.get("generated_at", ""),
        "snapshot_age_minutes": snapshot_validation.get("snapshot_age_minutes"),
        "snapshot_validation_mode": snapshot_validation.get("snapshot_validation_mode", "preview"),
        "request_creation_allowed": status == "ACTIVE" and bool(strict_snapshot_validation.get("valid")),
        "request_creation_block_reason": "" if status == "ACTIVE" and bool(strict_snapshot_validation.get("valid")) else (strict_snapshot_validation.get("reason") or ("blocked_by_missing_or_ineligible_snapshot" if status == "BLOCKED" else "")),
        "dry_run": dry_run,
        "order_api_called": False,
        "manual_requests_created": False,
        "source": source,
        "superseded_plan_paths": superseded,
        "items": items,
    }
    if not dry_run:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"plan_path": str(path), "item_count": len(items), **payload}


def validate_liquidation_plan(
    config_path: Path,
    plan_path: Path | None,
    kis_balance_path: Path | None,
    max_age_minutes: int | None = None,
) -> dict[str, Any]:
    if plan_path is None or not plan_path.exists():
        return {"valid": False, "reason": "liquidation_plan_missing"}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid": False, "reason": "liquidation_plan_missing"}
    if plan.get("status") != "ACTIVE":
        return {"valid": False, "reason": "liquidation_plan_not_active", "status": plan.get("status")}
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    db_snapshot = db_open_lot_snapshot(db_path)
    if db_snapshot["hash"] != plan.get("db_open_lot_hash"):
        return {"valid": False, "reason": "liquidation_plan_db_changed", "current_db_open_lot_hash": db_snapshot["hash"], "plan_db_open_lot_hash": plan.get("db_open_lot_hash")}
    blockers = reset_blockers(db_path)
    if blockers["open_order_count"] or blockers["pending_manual_request_count"]:
        return {"valid": False, "reason": "liquidation_plan_pending_work_created", "blockers": blockers}
    if blockers["sync_required_count"]:
        return {"valid": False, "reason": "liquidation_plan_sync_required", "blockers": blockers}
    if blockers["lot_mismatch_count"]:
        return {"valid": False, "reason": "liquidation_plan_lot_mismatch", "blockers": blockers}
    try:
        expires_at = datetime.fromisoformat(str(plan.get("expires_at") or ""))
    except ValueError:
        expires_at = datetime.fromisoformat(str(plan.get("created_at"))) + timedelta(minutes=max_age_minutes or int(plan.get("max_age_minutes") or DEFAULT_PLAN_MAX_AGE_MINUTES))
    if datetime.now() > expires_at:
        _update_plan_status(plan_path, "EXPIRED", "plan_age_exceeded")
        return {"valid": False, "reason": "liquidation_plan_snapshot_expired", "expires_at": expires_at.isoformat(timespec="seconds")}
    if kis_balance_path is None:
        return {"valid": False, "reason": "liquidation_kis_balance_fetch_failed"}
    try:
        snapshot_validation = validate_kis_balance_snapshot(
            kis_balance_path,
            mode="create_request",
            max_age_minutes=int(plan.get("max_age_minutes") or max_age_minutes or DEFAULT_PLAN_MAX_AGE_MINUTES),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return {"valid": False, "reason": "liquidation_kis_balance_fetch_failed"}
    if not snapshot_validation["valid"]:
        return {"valid": False, "reason": snapshot_validation["reason"], "snapshot_validation": snapshot_validation}
    kis_balances = snapshot_validation["balances"]
    kis_snapshot = kis_balance_snapshot(kis_balances)
    if kis_snapshot["hash"] != plan.get("kis_snapshot_hash"):
        return {"valid": False, "reason": "liquidation_plan_kis_snapshot_changed", "current_kis_snapshot_hash": kis_snapshot["hash"], "plan_kis_snapshot_hash": plan.get("kis_snapshot_hash")}
    current_plan = liquidation_plan(config_path, plan_path.parent, dry_run=True, kis_balances=kis_balances, kis_balance_path=kis_balance_path, max_age_minutes=int(plan.get("max_age_minutes") or DEFAULT_PLAN_MAX_AGE_MINUTES))
    ineligible = [item for item in current_plan["items"] if not item.get("eligible_for_liquidation_request")]
    if ineligible:
        return {"valid": False, "reason": "liquidation_plan_not_eligible", "block_reasons": sorted({str(item.get("block_reason")) for item in ineligible})}
    return {"valid": True, "reason": "", "plan": plan, "current_plan": current_plan}


def reset_blockers(db_path: Path) -> dict[str, Any]:
    blockers = {"open_order_count": 0, "pending_manual_request_count": 0, "open_lot_count": 0, "sync_required_count": 0, "lot_mismatch_count": 0}
    if not db_path.exists():
        return blockers
    with sqlite3.connect(db_path) as connection:
        try:
            placeholders = ",".join("?" for _ in PENDING_ORDER_STATUSES)
            row = connection.execute(f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})", PENDING_ORDER_STATUSES).fetchone()
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
        return {
            "reset": False,
            "reason": "dry_run",
            "db_path": str(db_path),
            "dry_run": True,
            "method": "clear_tables",
        }
    cleared_tables = clear_sqlite_db(db_path)
    return {
        "reset": True,
        "db_path": str(db_path),
        "dry_run": False,
        "method": "clear_tables",
        "cleared_tables": cleared_tables,
    }


def clear_sqlite_db(db_path: Path) -> list[str]:
    """Clear all application tables without deleting the SQLite file.

    Keeping the DB file in place avoids the common Windows unlink failure when the
    UI still has a read connection open. The reset guard must already have
    confirmed that no open lots, pending orders, pending manual requests, or sync
    mismatches remain before this function is called.
    """
    with sqlite3.connect(db_path, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables = [str(row[0]) for row in rows]
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            connection.execute(f'DELETE FROM "{table}"')
        try:
            connection.execute("DELETE FROM sqlite_sequence")
        except sqlite3.Error:
            pass
        connection.commit()
    return tables


def create_liquidation_manual_requests(
    config_path: Path,
    confirm: str,
    dry_run: bool,
    kis_balance_path: Path | None = None,
    plan_path: Path | None = None,
) -> dict[str, Any]:
    if confirm != CONFIRM_LIQUIDATION:
        return {"created": False, "reason": "confirm_required", "required_confirm_text": CONFIRM_LIQUIDATION, "dry_run": dry_run}
    config = load_json(config_path)
    db_path = Path(config.get("storage_path", ""))
    if not db_path.exists():
        return {"created": False, "reason": "db_not_found", "dry_run": dry_run}
    validation = validate_liquidation_plan(config_path, plan_path, kis_balance_path)
    if not validation["valid"]:
        return {"created": False, "reason": validation["reason"], "validation": validation, "dry_run": dry_run}
    kis_balances = load_kis_balance_json(kis_balance_path)
    blockers = reset_blockers(db_path)
    if blockers["open_order_count"] or blockers["pending_manual_request_count"] or blockers["sync_required_count"] or blockers["lot_mismatch_count"]:
        return {"created": False, "reason": "liquidation_request_blocked_by_pending_work", "blockers": blockers, "dry_run": dry_run}
    plan = liquidation_plan(config_path, Path("exports"), dry_run=True, kis_balances=kis_balances)
    items = plan["items"]
    ineligible = [item for item in items if not item.get("eligible_for_liquidation_request")]
    if ineligible:
        return {"created": False, "reason": "liquidation_plan_not_eligible", "ineligible_count": len(ineligible), "block_reasons": sorted({str(item.get("block_reason")) for item in ineligible}), "dry_run": dry_run}
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
    if plan_path is not None:
        _update_plan_status(plan_path, "USED", "manual_sell_requests_created")
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
    parser.add_argument("--kis-balance-json", default="")
    parser.add_argument("--liquidation-plan-file", default="")
    parser.add_argument("--plan-max-age-minutes", type=int, default=DEFAULT_PLAN_MAX_AGE_MINUTES)
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
        balances = load_kis_balance_json(Path(args.kis_balance_json)) if args.kis_balance_json else None
        result["liquidation_plan"] = liquidation_plan(config_path, Path("exports"), dry_run, balances, Path(args.kis_balance_json) if args.kis_balance_json else None, args.plan_max_age_minutes)
    if args.create_liquidation_requests:
        result["liquidation_requests"] = create_liquidation_manual_requests(
            config_path,
            args.confirm,
            dry_run,
            Path(args.kis_balance_json) if args.kis_balance_json else None,
            Path(args.liquidation_plan_file) if args.liquidation_plan_file else None,
        )
    if args.reset_db:
        result["reset_db"] = reset_db(config_path, args.confirm, dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
