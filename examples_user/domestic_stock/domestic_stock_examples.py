"""Runnable domestic stock examples.

Run from the repository root:

    $env:PYTHONPATH = "src"
    python examples_user/domestic_stock/domestic_stock_examples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.extend(["..", "."])

from domestic_stock_functions import get_kospi_master


def main() -> int:
    rows = get_kospi_master(
        output_path=Path("data/kospi_stocks.csv"),
        common_stock_only=True,
        active_only=True,
    )
    print(f"saved {len(rows)} KOSPI rows to data/kospi_stocks.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
