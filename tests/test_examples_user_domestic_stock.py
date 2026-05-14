from pathlib import Path
import sys

from test_kospi_master import build_line

sys.path.append(str(Path(__file__).resolve().parents[1] / "examples_user" / "domestic_stock"))
from domestic_stock_functions import get_kospi_master


def test_examples_user_domestic_stock_wrapper_reads_existing_master(tmp_path: Path) -> None:
    master = tmp_path / "kospi_code.mst"
    master.write_text(build_line(), encoding="cp949")

    rows = get_kospi_master(master_path=master)

    assert rows[0]["short_code"] == "005930"
