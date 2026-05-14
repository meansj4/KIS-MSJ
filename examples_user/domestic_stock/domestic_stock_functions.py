"""User-facing domestic stock helpers.

This file intentionally follows the role of Korea Investment's
``examples_user/domestic_stock/domestic_stock_functions.py``: keep callable
functions here, and keep runnable scripts in ``domestic_stock_examples.py``.
The actual parser lives in ``kis_msj.kospi_master`` so it can be tested and
reused without depending on the examples layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from kis_msj.kospi_master import (
    KospiFilter,
    download_kospi_master,
    fetch_kospi_stocks,
    read_kospi_master,
    write_rows,
)

OutputFormat = Literal["csv", "json"]


def get_kospi_master(
    *,
    master_path: Path | None = None,
    output_path: Path | None = None,
    output_format: OutputFormat = "csv",
    include_etp_elw_spac: bool = False,
    common_stock_only: bool = False,
    active_only: bool = False,
    keep_master: Path | None = None,
) -> list[dict[str, str | int]]:
    """Fetch or read KOSPI master rows and optionally save them.

    Args:
        master_path: Existing ``kospi_code.mst`` path. Useful for offline tests.
        output_path: Optional CSV/JSON destination.
        output_format: ``csv`` or ``json``.
        include_etp_elw_spac: Include ETF/ETN, ELW, and SPAC rows.
        common_stock_only: Exclude preferred stocks.
        active_only: Exclude trading-halt and liquidation-trading rows.
        keep_master: Directory where the downloaded raw master file is kept.

    Returns:
        Parsed rows matching the filter options.
    """

    kospi_filter = KospiFilter(
        individual_only=not include_etp_elw_spac,
        common_stock_only=common_stock_only,
        active_only=active_only,
    )

    if master_path is not None:
        rows = read_kospi_master(master_path, kospi_filter)
    elif keep_master is not None:
        rows = read_kospi_master(download_kospi_master(keep_master), kospi_filter)
    else:
        rows = fetch_kospi_stocks(kospi_filter)

    if output_path is not None:
        write_rows(rows, output_path, output_format)

    return rows
