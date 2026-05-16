from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from kis_msj.domestic_quote import is_rate_limit_error, normalize_quote_output, read_master_codes


def test_normalize_quote_output_extracts_current_price_and_volume() -> None:
    row = normalize_quote_output(
        "005930",
        "Samsung",
        {
            "stck_prpr": "72000",
            "acml_vol": "1234567",
            "acml_tr_pbmn": "88888888",
            "stck_oprc": "71000",
            "stck_hgpr": "73000",
            "stck_lwpr": "70000",
            "prdy_vrss": "-500",
            "prdy_ctrt": "-0.69",
        },
    )

    assert row["current_price"] == 72000
    assert row["accumulated_volume"] == 1234567
    assert row["previous_day_diff_rate"] == -0.69


def test_read_master_codes_reads_short_code_and_name(tmp_path: Path) -> None:
    master = tmp_path / "master.csv"
    master.write_text("short_code,korean_name\n005930,Samsung\n", encoding="utf-8-sig")

    rows = read_master_codes(master)

    assert rows == [{"short_code": "005930", "korean_name": "Samsung"}]


def test_is_rate_limit_error_detects_common_messages() -> None:
    assert is_rate_limit_error(RuntimeError("KIS request failed (429): too many requests"))
    assert is_rate_limit_error(RuntimeError("초당 거래건수를 초과했습니다"))
    assert not is_rate_limit_error(RuntimeError("Missing environment variable: KIS_APP_KEY"))
