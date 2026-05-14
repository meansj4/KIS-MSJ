"""Download and parse Korea Investment & Securities KOSPI master data.

KIS publishes the KOSPI master file as a CP949 encoded fixed-width file inside
``kospi_code.mst.zip``.  This module keeps the parser dependency-free so the
first data-ingestion step can run in a fresh Python environment.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Sequence

KOSPI_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSPI_MASTER_FILE_NAME = "kospi_code.mst"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "kospi_common_stocks.csv"

PART2_WIDTHS: tuple[int, ...] = (
    2,
    1,
    4,
    4,
    4,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
)

PART2_COLUMNS: tuple[str, ...] = (
    "group_code",
    "market_cap_scale",
    "sector_large_code",
    "sector_middle_code",
    "sector_small_code",
    "manufacturing_yn",
    "low_liquidity_yn",
    "governance_index_yn",
    "kospi200_sector_code",
    "kospi100_yn",
    "kospi50_yn",
    "krx_yn",
    "etp_yn",
    "elw_issue_yn",
    "krx100_yn",
    "krx_auto_yn",
    "krx_semiconductor_yn",
    "krx_bio_yn",
    "krx_bank_yn",
    "spac_yn",
    "krx_energy_chemical_yn",
    "krx_steel_yn",
    "short_term_overheat_yn",
    "krx_media_telecom_yn",
    "krx_construction_yn",
    "reserved_1",
    "krx_securities_yn",
    "krx_shipbuilding_yn",
    "krx_insurance_yn",
    "krx_transport_yn",
    "sri_yn",
    "base_price",
    "trading_unit",
    "after_hours_trading_unit",
    "trading_halt_yn",
    "liquidation_trading_yn",
    "administrative_issue_yn",
    "market_alert_code",
    "alert_notice_yn",
    "unfaithful_disclosure_yn",
    "backdoor_listing_yn",
    "lock_type_code",
    "par_value_change_code",
    "capital_increase_code",
    "margin_rate",
    "credit_available_yn",
    "credit_period",
    "previous_day_volume",
    "par_value",
    "listing_date",
    "listed_shares",
    "capital",
    "settlement_month",
    "public_offering_price",
    "preferred_stock_yn",
    "short_sale_overheat_yn",
    "abnormal_surge_yn",
    "krx300_yn",
    "kospi_yn",
    "sales",
    "operating_profit",
    "ordinary_profit",
    "net_income",
    "roe",
    "base_year_month",
    "market_cap",
    "group_company_code",
    "company_credit_limit_exceeded_yn",
    "collateral_loan_available_yn",
    "stock_loan_available_yn",
)

COLUMN_LABELS: dict[str, str] = {column: column for column in ("short_code", "standard_code", "korean_name", *PART2_COLUMNS)}
KOREAN_COLUMN_LABELS = COLUMN_LABELS

NUMERIC_COLUMNS = {
    "base_price",
    "trading_unit",
    "after_hours_trading_unit",
    "margin_rate",
    "credit_period",
    "previous_day_volume",
    "par_value",
    "listed_shares",
    "capital",
    "public_offering_price",
    "sales",
    "operating_profit",
    "ordinary_profit",
    "net_income",
    "roe",
    "market_cap",
}

OUTPUT_COLUMNS: tuple[str, ...] = ("short_code", "standard_code", "korean_name", *PART2_COLUMNS)
OutputFormat = Literal["csv", "json"]


@dataclass(frozen=True)
class KospiFilter:
    """Options for narrowing master data to listed individual stocks."""

    individual_only: bool = True
    common_stock_only: bool = False
    active_only: bool = False

    def includes(self, row: dict[str, str | int]) -> bool:
        if row.get("kospi_yn") != "Y":
            return False
        if self.individual_only and (row.get("etp_yn") == "Y" or row.get("elw_issue_yn") == "Y"):
            return False
        if self.individual_only and row.get("spac_yn") == "Y":
            return False
        if self.common_stock_only and row.get("preferred_stock_yn") == "Y":
            return False
        if self.active_only and row.get("trading_halt_yn") == "Y":
            return False
        if self.active_only and row.get("liquidation_trading_yn") == "Y":
            return False
        return True


def download_kospi_master(destination_dir: Path, url: str = KOSPI_MASTER_URL) -> Path:
    """Download and extract the KIS KOSPI master file, returning its path."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    archive_path = destination_dir / "kospi_code.mst.zip"
    urllib.request.urlretrieve(url, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        archive.extract(KOSPI_MASTER_FILE_NAME, destination_dir)

    archive_path.unlink(missing_ok=True)
    return destination_dir / KOSPI_MASTER_FILE_NAME


def split_fixed_width(text: str, widths: Sequence[int]) -> list[str]:
    """Split a fixed-width string according to ``widths`` and strip fields."""

    values: list[str] = []
    start = 0
    for width in widths:
        values.append(text[start : start + width].strip())
        start += width
    return values


def normalize_value(column: str, value: str) -> str | int | float:
    """Convert blank-padded numeric fields to integers while preserving dates."""

    cleaned = value.strip()
    if column not in NUMERIC_COLUMNS:
        return cleaned
    if cleaned in {"", "-"}:
        return ""
    if "." in cleaned:
        return float(cleaned)
    return int(cleaned)


def parse_kospi_master_line(line: str) -> dict[str, str | int]:
    """Parse one line from ``kospi_code.mst``."""

    row = line.rstrip("\r\n")
    part1 = row[: len(row) - sum(PART2_WIDTHS)]
    part2 = row[-sum(PART2_WIDTHS) :]

    values = {
        "short_code": part1[0:9].strip(),
        "standard_code": part1[9:21].strip(),
        "korean_name": part1[21:].strip(),
    }
    values.update(
        {
            column: normalize_value(column, value)
            for column, value in zip(PART2_COLUMNS, split_fixed_width(part2, PART2_WIDTHS), strict=True)
        }
    )
    return values


def read_kospi_master(path: Path, kospi_filter: KospiFilter | None = None) -> list[dict[str, str | int]]:
    """Read and parse ``kospi_code.mst`` rows."""

    selected_filter = kospi_filter or KospiFilter()
    with path.open("r", encoding="cp949") as master_file:
        return [row for row in map(parse_kospi_master_line, master_file) if selected_filter.includes(row)]


def fetch_kospi_stocks(kospi_filter: KospiFilter | None = None) -> list[dict[str, str | int]]:
    """Download the current KOSPI master file and return filtered stock rows."""

    with tempfile.TemporaryDirectory() as temp_dir:
        master_path = download_kospi_master(Path(temp_dir))
        return read_kospi_master(master_path, kospi_filter)


def write_rows(rows: Iterable[dict[str, str | int]], output_path: Path, output_format: OutputFormat) -> None:
    """Write parsed rows as CSV or JSON."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows_list = list(rows)
    if output_format == "json":
        output_path.write_text(json.dumps(rows_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return

    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download KIS KOSPI master data and save it as CSV/JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output file path")
    parser.add_argument("--format", choices=("csv", "json"), default="csv", help="Output format")
    parser.add_argument("--include-etp-elw-spac", action="store_true", help="Include ETF/ETN, ELW, and SPAC rows")
    parser.add_argument("--include-preferred-stocks", action="store_true", help="Include preferred stocks")
    parser.add_argument("--active-only", action="store_true", help="Exclude trading-halt and liquidation-trading rows")
    parser.add_argument("--keep-master", type=Path, help="Directory where the raw kospi_code.mst file is kept")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kospi_filter = KospiFilter(
        individual_only=not args.include_etp_elw_spac,
        common_stock_only=not args.include_preferred_stocks,
        active_only=args.active_only,
    )

    print("Downloading KIS KOSPI master data...")
    if args.keep_master:
        master_path = download_kospi_master(args.keep_master)
        rows = read_kospi_master(master_path, kospi_filter)
    else:
        rows = fetch_kospi_stocks(kospi_filter)

    write_rows(rows, args.output, args.format)
    print(f"saved {len(rows)} KOSPI rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
