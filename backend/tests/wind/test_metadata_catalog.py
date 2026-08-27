"""Tests for the localized Wind field metadata catalog.

The Wind data dictionary answers *what should I query*; the Wind MySQL replica
answers *how do I get the rows*. This module owns the first half, entirely
offline: the dictionary Markdown is parsed locally and never fetched at runtime.

Two properties matter more than coverage:

* **Never guess.** Frequency is derived from the declared business key, not from
  the table name; a unit that is not in the reviewable overlay stays ``None``.
  A market-cap field silently assumed to be in yuan rather than ten-thousand
  yuan is a 10,000x error that no downstream check would catch.
* **Never drop.** The dictionary is machine-extracted from PDFs and a large
  minority of rows are damaged. Fields we cannot describe are still returned,
  marked ``metadata_source=None``, so a missing description degrades ranking
  instead of hiding the field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.domain.models import AssetType, Frequency
from factor_platform.wind.catalog import FieldRecord
from factor_platform.wind.metadata_catalog import FieldMetadata, MetadataCatalog
from factor_platform.wind.metadata_repository import MetadataRepository
from factor_platform.wind.wds_sync import DictionaryBuilder

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_DICTIONARY = REPO_ROOT / "windquery" / "windquery" / "references" / "wind字典"

requires_real_dictionary = pytest.mark.skipif(
    not REAL_DICTIONARY.exists(),
    reason=(
        f"licensed Wind data dictionary not present at {REAL_DICTIONARY}; "
        "supply it locally to run this check"
    ),
)


# --------------------------------------------------------------------------- fixtures


def _write_dictionary(
    root: Path,
    name: str,
    *,
    module: str = "中国A股-股票信息",
    business_key: str = "Wind代码 , 交易日期",
    rows: str,
) -> Path:
    path = root / f"{name}_数据字典.md"
    path.write_text(
        f"# {name}_数据字典\n\n"
        "## 数据字典信息\n\n"
        "- **所属数据库**: ：\n"
        f"- **所属模块**: {module}\n"
        "- **历史长度**: ：\n"
        f"- **注释说明**: 数据字典 业务主键： {business_key} 所属数据库： 中国A股数据库\n\n"
        "## 字段列表\n\n"
        "| 序号 | 字段中文名 | 字段名 | 字段类型 | 枚举/外部引用 | 有值率 | 释义 |\n"
        "|------|------------|--------|----------|---------------|--------|------|\n"
        f"{rows}\n",
        encoding="utf-8",
    )
    return path


_EOD_ROWS = (
    "| 1 | Wind代码 | S_INFO_WINDCODE | VARCHAR2(40) | WindCustomCode | 100.00% | 证券唯一编码 |\n"
    "| 2 | 交易日期 | TRADE_DT | VARCHAR2(8) | | 100.00% | 该证券的交易日期 |\n"
    "| 3 | 收盘价 | S_DQ_CLOSE | NUMBER(20,4) | | 99.00% | 当日最后一个成交价 |"
)


# --------------------------------------------------------------------------- parsing


def test_field_rows_become_metadata(tmp_path: Path) -> None:
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}

    meta = records[("ashareeodprices", "s_dq_close")]
    assert meta.name_zh == "收盘价"
    assert meta.data_type == "NUMBER(20,4)"
    assert meta.description_zh.startswith("当日最后一个成交价")
    assert meta.metadata_source == "WDS"


def test_asset_type_comes_from_the_module_header(tmp_path: Path) -> None:
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    _write_dictionary(
        tmp_path,
        "CBondDescription",
        module="中国债券-债券信息",
        business_key="Wind代码",
        rows="| 1 | Wind代码 | S_INFO_WINDCODE | VARCHAR2(40) | | 100.00% | 编码 |",
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}

    assert records[("ashareeodprices", "s_dq_close")].asset_type is AssetType.STOCK
    assert records[("cbonddescription", "s_info_windcode")].asset_type is AssetType.BOND


def test_daily_frequency_is_derived_from_the_business_key(tmp_path: Path) -> None:
    """The business key is the observation grain; the table name is a guess."""
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}
    assert records[("ashareeodprices", "s_dq_close")].frequency is Frequency.DAILY


def test_quarterly_frequency_is_derived_from_the_business_key(tmp_path: Path) -> None:
    _write_dictionary(
        tmp_path,
        "AShareIncome",
        business_key="Wind代码 , 报告期 , 报表类型",
        rows=(
            "| 1 | 首次公告日期 | ANN_DT | VARCHAR2(8) | | 99.00% | 首次披露日期 |\n"
            "| 2 | 报告期 | REPORT_PERIOD | VARCHAR2(8) | | 100.00% | 报告截止时点 |\n"
            "| 3 | 营业收入 | OPER_REV | NUMBER(20,4) | | 98.00% | 主营及其他业务收入 |"
        ),
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}
    assert records[("ashareincome", "oper_rev")].frequency is Frequency.QUARTERLY


def test_report_period_wins_over_a_generic_date_in_a_financial_key(
    tmp_path: Path,
) -> None:
    _write_dictionary(
        tmp_path,
        "AShareTTMHis",
        business_key="Wind代码 , 报告期 , 公告日期",
        rows="| 1 | 报告期 | REPORT_PERIOD | VARCHAR2(8) | | 100.00% | 报告期 |",
    )
    record = DictionaryBuilder(tmp_path).build()[0]

    assert record.frequency is Frequency.QUARTERLY


def test_an_unrecognised_business_key_leaves_frequency_unset(tmp_path: Path) -> None:
    """Guessing daily for a static description table would corrupt every filter."""
    _write_dictionary(
        tmp_path,
        "AShareDescription",
        business_key="Wind代码",
        rows="| 1 | 公司中文名 | S_INFO_COMPNAME | VARCHAR2(100) | | 100.00% | 名称 |",
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}
    assert records[("asharedescription", "s_info_compname")].frequency is None


def test_date_role_fields_are_identified_from_the_field_list(tmp_path: Path) -> None:
    _write_dictionary(
        tmp_path,
        "AShareIncome",
        business_key="Wind代码 , 报告期",
        rows=(
            "| 1 | 首次公告日期 | ANN_DT | VARCHAR2(8) | | 99.00% | 首次披露日期 |\n"
            "| 2 | 报告期 | REPORT_PERIOD | VARCHAR2(8) | | 100.00% | 报告截止时点 |\n"
            "| 3 | 营业收入 | OPER_REV | NUMBER(20,4) | | 98.00% | 收入 |"
        ),
    )
    meta = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}[
        ("ashareincome", "oper_rev")
    ]
    assert meta.announcement_date_field == "ann_dt"
    assert meta.report_period_field == "report_period"
    assert meta.observation_date_field is None


def test_trade_date_becomes_the_observation_date_field(tmp_path: Path) -> None:
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    meta = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}[
        ("ashareeodprices", "s_dq_close")
    ]
    assert meta.observation_date_field == "trade_dt"
    assert meta.security_code_field == "s_info_windcode"


def test_fill_rate_is_parsed_when_present(tmp_path: Path) -> None:
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    meta = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}[
        ("ashareeodprices", "s_dq_close")
    ]
    assert meta.fill_rate == pytest.approx(0.99)


# --------------------------------------------------------------------------- damaged rows


def test_a_row_folded_into_its_neighbour_is_recovered(tmp_path: Path) -> None:
    """The dictionaries are PDF extractions; whole rows collapse into one cell.

    ``S_VAL_MV`` — the market-cap field — is folded this way in the real
    AShareEODDerivativeIndicator dictionary. Losing it would silently remove the
    single most-requested field from the metadata tier.
    """
    _write_dictionary(
        tmp_path,
        "AShareEODDerivativeIndicator",
        rows=(
            "| 1 | 交易日期 | TRADE_DT | VARCHAR2(8) | | 100.00% | 交易日期 |\n"
            "| 2 | 交易货币代码 | CRNCY_CODE VARCHAR2(10) | 区分币种 | 3 | | "
            "当日总市值 S_VAL_MV NUMBER(20 ,4) 当日总股本与当日收盘价的乘积 |"
        ),
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}

    assert ("ashareeodderivativeindicator", "s_val_mv") in records
    assert records[("ashareeodderivativeindicator", "s_val_mv")].name_zh == "当日总市值"


def test_an_unparseable_row_is_skipped_rather_than_guessed(tmp_path: Path) -> None:
    """Wrong metadata is worse than missing metadata: missing is visible."""
    _write_dictionary(
        tmp_path,
        "AShareEODPrices",
        rows=(
            "| 1 | 收盘价 | S_DQ_CLOSE | NUMBER(20,4) | | 99.00% | 收盘价 |\n"
            "| 2 | 乱码行 | 释义被截断 zhangqi@xq zhangqi@xq |"
        ),
    )
    records = DictionaryBuilder(tmp_path).build()
    assert [r.field for r in records] == ["s_dq_close"]


@pytest.mark.parametrize(
    ("row", "field", "name_zh"),
    [
        (
            "| 39 | 净资产收益率 | (TTM) | S_FA_ROE_TTM | NUMBER(20,4) | "
            "95.00% | ROE 描述 |",
            "s_fa_roe_ttm",
            "净资产收益率 (TTM)",
        ),
        (
            "| 32 | 经营活动产生 | 的现金流量净额 | NET_CASH_FLOWS_OPER_ACT | "
            "NUMBER(20,4) | 99.00% | 现金流描述 |",
            "net_cash_flows_oper_act",
            "经营活动产生 的现金流量净额",
        ),
        (
            "| 156 | 营业收入同比 | 增长率(%) | S_FA_YOY_OR NUMBER(20,4) | "
            "91.00% | | 同比描述 |",
            "s_fa_yoy_or",
            "营业收入同比 增长率(%)",
        ),
    ],
)
def test_a_name_split_across_cells_does_not_shift_the_field_column(
    tmp_path: Path, row: str, field: str, name_zh: str
) -> None:
    _write_dictionary(tmp_path, "SplitCells", rows=row)
    records = DictionaryBuilder(tmp_path).build()

    assert len(records) == 1
    assert records[0].field == field
    assert records[0].name_zh == name_zh
    assert records[0].data_type == "NUMBER(20,4)"


def test_type_fill_rate_and_description_folded_into_one_cell_are_separated(
    tmp_path: Path,
) -> None:
    row = (
        "| 148 | 同比增长率-归 | 属母公司股东 | 的净利润(%) | "
        "S_FA_YOYNETPROFIT | | NUMBER(20 ,4) 91.00% 净利润同比增长率； |"
    )
    _write_dictionary(tmp_path, "FoldedTail", rows=row)
    record = DictionaryBuilder(tmp_path).build()[0]

    assert record.field == "s_fa_yoynetprofit"
    assert record.data_type == "NUMBER(20,4)"
    assert record.fill_rate == pytest.approx(0.91)
    assert record.description_zh == "净利润同比增长率；"


# --------------------------------------------------------------------------- market


def test_market_is_read_from_the_source_database(tmp_path: Path) -> None:
    """A-share and HK tables are both ``asset_type=stock`` and look identical.

    The platform's scope is daily A-shares. Without the market, searching 市值
    surfaces ``hkshareeodderivativeindex`` at rank 1, the user picks it, and the
    query returns nothing for A-share codes — with no error anywhere.
    """
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    hk = tmp_path / "HKShareEODDerivativeIndex_数据字典.md"
    hk.write_text(
        "# HKShareEODDerivativeIndex_数据字典\n\n"
        "## 数据字典信息\n\n"
        "- **所属模块**: 中国A股-股票信息\n"
        "- **注释说明**: 数据字典 业务主键： Wind代码 , 交易日期 所属数据库： 中国香港股票数据\n\n"
        "## 字段列表\n\n"
        "| 序号 | 字段中文名 | 字段名 | 字段类型 | 枚举/外部引用 | 有值率 | 释义 |\n"
        "|------|------------|--------|----------|---------------|--------|------|\n"
        "| 1 | 当日总市值 | S_VAL_MV | NUMBER(20,4) | | 100.00% | 总市值 |",
        encoding="utf-8",
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}

    assert records[("ashareeodprices", "s_dq_close")].market == "cn_a"
    assert records[("hkshareeodderivativeindex", "s_val_mv")].market == "hk"


def test_an_undeclared_source_database_leaves_the_market_unset(tmp_path: Path) -> None:
    _write_dictionary(
        tmp_path,
        "MysteryTable",
        business_key="Wind代码",
        rows="| 1 | 某字段 | SOME_FIELD | VARCHAR2(8) | | 100.00% | 说明 |",
    )
    dictionary = tmp_path / "MysteryTable_数据字典.md"
    dictionary.write_text(
        dictionary.read_text(encoding="utf-8").replace(" 所属数据库： 中国A股数据库", ""),
        encoding="utf-8",
    )
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}
    assert records[("mysterytable", "some_field")].market is None


# --------------------------------------------------------------------------- units


def test_unit_comes_from_the_reviewable_overlay(tmp_path: Path) -> None:
    """Units are not in the dictionary, so they come from a file a human signs off."""
    _write_dictionary(
        tmp_path,
        "AShareEODDerivativeIndicator",
        rows=(
            "| 1 | 交易日期 | TRADE_DT | VARCHAR2(8) | | 100.00% | 交易日期 |\n"
            "| 2 | 当日总市值 | S_VAL_MV | NUMBER(20,4) | | 100.00% | 总股本乘收盘价 |"
        ),
    )
    overlay = tmp_path / "units.yaml"
    overlay.write_text(
        "units:\n"
        "  ashareeodderivativeindicator.s_val_mv:\n"
        "    unit: ten_thousand_cny\n"
        "    verified: false\n",
        encoding="utf-8",
    )
    records = {
        (m.table, m.field): m for m in DictionaryBuilder(tmp_path, units_path=overlay).build()
    }
    assert records[("ashareeodderivativeindicator", "s_val_mv")].unit == "ten_thousand_cny"


def test_a_unit_absent_from_the_overlay_stays_unset(tmp_path: Path) -> None:
    """Assuming yuan where Wind means ten-thousand yuan is a silent 10,000x error."""
    _write_dictionary(tmp_path, "AShareEODPrices", rows=_EOD_ROWS)
    records = {(m.table, m.field): m for m in DictionaryBuilder(tmp_path).build()}
    assert records[("ashareeodprices", "s_dq_close")].unit is None


# --------------------------------------------------------------------------- merge


def _index_records() -> list[FieldRecord]:
    return [
        FieldRecord(table="ashareincome", field="oper_rev"),
        FieldRecord(table="ashareeodprices", field="s_dq_close"),
        FieldRecord(table="obscuretable", field="rare_field"),
    ]


def _wds_records() -> list[FieldMetadata]:
    return [
        FieldMetadata(
            table="ashareincome",
            field="oper_rev",
            name_zh="营业收入",
            frequency=Frequency.QUARTERLY,
            metadata_source="WDS",
        ),
        FieldMetadata(
            table="ashareeodprices",
            field="s_dq_close",
            name_zh="收盘价",
            frequency=Frequency.DAILY,
            metadata_source="WDS",
        ),
    ]


def test_merge_prefers_wds_over_index_for_overlapping_fields() -> None:
    merged = MetadataRepository.merge(_index_records(), _wds_records())
    assert merged[("ashareincome", "oper_rev")].metadata_source == "WDS"
    assert merged[("ashareincome", "oper_rev")].name_zh == "营业收入"


def test_a_field_the_dictionary_missed_is_kept_and_marked() -> None:
    """38% of dictionary rows are damaged; dropping their fields would hide them."""
    merged = MetadataRepository.merge(_index_records(), _wds_records())
    rare = merged[("obscuretable", "rare_field")]
    assert rare.metadata_source is None
    assert rare.frequency is None


def test_merge_covers_every_index_field() -> None:
    merged = MetadataRepository.merge(_index_records(), _wds_records())
    assert all(
        (record.table, record.field) in merged for record in _index_records()
    )


def test_a_field_only_the_dictionary_knows_is_kept() -> None:
    """Both sources are damaged PDF extractions; either one may be the survivor.

    The field index lost 74 fields the dictionary still has — including
    ``ashareeodderivativeindicator.s_val_mv``, the market-cap column. Letting the
    index decide what exists would delete them. Recall layers over-include on
    purpose; ``information_schema`` and sample verification prune later.
    """
    merged = MetadataRepository.merge(
        _index_records(),
        [
            *_wds_records(),
            FieldMetadata(
                table="ashareeodderivativeindicator",
                field="s_val_mv",
                name_zh="当日总市值",
                frequency=Frequency.DAILY,
                metadata_source="WDS",
            ),
        ],
    )
    assert ("ashareeodderivativeindicator", "s_val_mv") in merged
    assert merged[("ashareeodderivativeindicator", "s_val_mv")].name_zh == "当日总市值"


def test_merge_stamps_a_metadata_version() -> None:
    merged = MetadataRepository.merge(_index_records(), _wds_records(), version=7)
    assert all(m.metadata_version == 7 for m in merged.values())


# --------------------------------------------------------------------------- catalog io


def test_catalog_round_trips_through_jsonl(tmp_path: Path) -> None:
    catalog = MetadataCatalog(_wds_records())
    out = tmp_path / "metadata.jsonl"
    catalog.save(out)
    reloaded = MetadataCatalog.load(out)
    assert reloaded.get("ashareeodprices", "s_dq_close") is not None
    assert reloaded.get("ashareeodprices", "s_dq_close").frequency is Frequency.DAILY


def test_catalog_lookup_is_case_insensitive() -> None:
    catalog = MetadataCatalog(_wds_records())
    assert catalog.get("AShareEODPrices", "S_DQ_CLOSE") is not None


def test_catalog_returns_none_for_an_unknown_field() -> None:
    assert MetadataCatalog(_wds_records()).get("nosuchtable", "nosuchfield") is None


# --------------------------------------------------------------------------- real data


@requires_real_dictionary
def test_real_dictionary_yields_metadata_for_thousands_of_fields() -> None:
    """A real WDS dump is large; never cap how many fields it may contain."""
    records = DictionaryBuilder(REAL_DICTIONARY).build()
    assert len(records) >= 6000, f"got {len(records)} records"
    assert all(record.metadata_source == "WDS" for record in records)
    by_key = {(record.table.lower(), record.field.lower()): record for record in records}
    close = by_key.get(("ashareeodprices", "s_dq_close"))
    adj = by_key.get(("ashareeodprices", "s_dq_adjclose"))
    assert close is not None, "s_dq_close missing from dictionary"
    assert adj is not None, "s_dq_adjclose missing from dictionary"
    assert close.name_zh
    assert close.frequency is Frequency.DAILY or close.frequency is None


@requires_real_dictionary
def test_market_cap_is_recovered_from_a_folded_row_in_the_real_dictionary() -> None:
    """Regression: ``S_VAL_MV`` is both folded in the dictionary *and* missing
    from the field index. It only survives if row recovery works and the merge
    unions the two sources."""
    table = "ashareeodderivativeindicator"
    described = DictionaryBuilder(REAL_DICTIONARY).build()
    merged = MetadataRepository.merge([], described)

    market_cap = merged.get((table, "s_val_mv"))
    assert market_cap is not None, "market cap lost between dictionary and index"
    assert market_cap.frequency is Frequency.DAILY
    assert market_cap.observation_date_field == "trade_dt"
