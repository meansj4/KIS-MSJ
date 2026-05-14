from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
DOMESTIC_STOCK_EXAMPLES_PATH = PROJECT_ROOT / "examples_user" / "domestic_stock"

for path in (SRC_PATH, DOMESTIC_STOCK_EXAMPLES_PATH):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from test_kospi_master import build_line

from domestic_stock_functions import get_kospi_master


def test_examples_user_domestic_stock_wrapper_reads_existing_master(tmp_path: Path) -> None:
    master = tmp_path / "kospi_code.mst"
    master.write_text(build_line(), encoding="cp949")

    rows = get_kospi_master(master_path=master)

    assert rows[0]["short_code"] == "005930"
