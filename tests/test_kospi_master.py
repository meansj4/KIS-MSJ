from pathlib import Path

from kis_msj.kospi_master import (
    PART2_COLUMNS,
    PART2_WIDTHS,
    KospiFilter,
    parse_kospi_master_line,
    read_kospi_master,
    split_fixed_width,
)


def build_part2(**overrides: str) -> str:
    values = {
        "group_code": "ST",
        "market_cap_scale": "1",
        "sector_large_code": "0001",
        "sector_middle_code": "0002",
        "sector_small_code": "0003",
        "manufacturing_yn": "Y",
        "low_liquidity_yn": "N",
        "governance_index_yn": "N",
        "kospi200_sector_code": "A",
        "kospi100_yn": "Y",
        "kospi50_yn": "N",
        "krx_yn": "Y",
        "etp_yn": "N",
        "elw_issue_yn": "N",
        "krx100_yn": "Y",
        "krx_auto_yn": "N",
        "krx_semiconductor_yn": "Y",
        "krx_bio_yn": "N",
        "krx_bank_yn": "N",
        "spac_yn": "N",
        "krx_energy_chemical_yn": "N",
        "krx_steel_yn": "N",
        "short_term_overheat_yn": "N",
        "krx_media_telecom_yn": "N",
        "krx_construction_yn": "N",
        "reserved_1": " ",
        "krx_securities_yn": "N",
        "krx_shipbuilding_yn": "N",
        "krx_insurance_yn": "N",
        "krx_transport_yn": "N",
        "sri_yn": "N",
        "base_price": "000070000",
        "trading_unit": "00001",
        "after_hours_trading_unit": "00001",
        "trading_halt_yn": "N",
        "liquidation_trading_yn": "N",
        "administrative_issue_yn": "N",
        "market_alert_code": "00",
        "alert_notice_yn": "N",
        "unfaithful_disclosure_yn": "N",
        "backdoor_listing_yn": "N",
        "lock_type_code": "00",
        "par_value_change_code": "00",
        "capital_increase_code": "00",
        "margin_rate": "020",
        "credit_available_yn": "Y",
        "credit_period": "090",
        "previous_day_volume": "000001234567",
        "par_value": "000000005000",
        "listing_date": "19750611",
        "listed_shares": "00000000596978255",
        "capital": "0000000000002990000",
        "settlement_month": "12",
        "public_offering_price": "0005000",
        "preferred_stock_yn": "N",
        "short_sale_overheat_yn": "N",
        "abnormal_surge_yn": "N",
        "krx300_yn": "Y",
        "kospi_yn": "Y",
        "sales": "000012345",
        "operating_profit": "000001234",
        "ordinary_profit": "000001111",
        "net_income": "00009",
        "roe": "000000123",
        "base_year_month": "20240501",
        "market_cap": "012345678",
        "group_company_code": "001",
        "company_credit_limit_exceeded_yn": "N",
        "collateral_loan_available_yn": "Y",
        "stock_loan_available_yn": "Y",
    }
    values.update(overrides)

    return "".join(str(values[column])[:width].rjust(width) for column, width in zip(PART2_COLUMNS, PART2_WIDTHS, strict=True))


def build_line(name: str = "삼성전자", **part2_overrides: str) -> str:
    return f"005930   KR7005930003{name}" + build_part2(**part2_overrides) + "\n"


def test_split_fixed_width_strips_each_field() -> None:
    assert split_fixed_width(" AA001  ", (3, 3, 2)) == ["AA", "001", ""]


def test_parse_kospi_master_line_extracts_identifiers_and_numeric_fields() -> None:
    row = parse_kospi_master_line(build_line())

    assert row["short_code"] == "005930"
    assert row["standard_code"] == "KR7005930003"
    assert row["korean_name"] == "삼성전자"
    assert row["base_price"] == 70000
    assert row["listing_date"] == "19750611"
    assert row["kospi_yn"] == "Y"


def test_read_kospi_master_filters_non_individual_rows(tmp_path: Path) -> None:
    master = tmp_path / "kospi_code.mst"
    master.write_text(
        build_line("삼성전자")
        + build_line("ETF", etp_yn="Y")
        + build_line("스팩", spac_yn="Y")
        + build_line("우선주", preferred_stock_yn="Y"),
        encoding="cp949",
    )

    rows = read_kospi_master(master, KospiFilter(common_stock_only=True))

    assert [row["korean_name"] for row in rows] == ["삼성전자"]
