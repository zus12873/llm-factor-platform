"""Tests for the Wind field catalog builder.

``CatalogBuilder`` parses a Markdown field index into normalized ``FieldRecord``
rows (table+field lowercased). ``FieldCatalog`` round-trips a generated JSONL
file used by the search layer.

The parsing tests below are self-contained: they build their own fixtures with
``tmp_path``. Two further tests exercise the real Wind field index, which is
licensed vendor documentation and therefore *not* distributed with this
repository — those tests skip when it is absent rather than fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.wind.catalog import CatalogBuilder, FieldCatalog, FieldRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_INDEX = REPO_ROOT / "windquery" / "windquery" / "references" / "wind_field_index.md"
GENERATED_JSONL = REPO_ROOT / "backend" / "data" / "generated" / "wind_fields.jsonl"

requires_real_index = pytest.mark.skipif(
    not REAL_INDEX.exists(),
    reason=(
        f"licensed Wind field index not present at {REAL_INDEX}; "
        "supply it locally to run this check"
    ),
)


def test_catalog_parses_table_and_fields(tmp_path: Path) -> None:
    source = tmp_path / "index.md"
    source.write_text(
        "### AShareEODPrices（2个字段）\n\nS_INFO_WINDCODE, S_DQ_CLOSE\n",
        encoding="utf-8",
    )
    records = CatalogBuilder(source).build()
    assert [(r.table, r.field) for r in records] == [
        ("ashareeodprices", "s_info_windcode"),
        ("ashareeodprices", "s_dq_close"),
    ]


def test_builder_skips_section_headers_and_blockquotes(tmp_path: Path) -> None:
    """Doc title, intro blockquote, and category headers must not be parsed as fields."""
    source = tmp_path / "index.md"
    source.write_text(
        "# Wind 数据库字段总索引\n"
        "> 共 678 张表。\n"
        "## 中国A股-指数数据（1张表）\n"
        "### AShareEODPrices（2个字段）\n\n"
        "S_INFO_WINDCODE, S_DQ_CLOSE\n",
        encoding="utf-8",
    )
    records = CatalogBuilder(source).build()
    assert len(records) == 2
    assert {r.table for r in records} == {"ashareeodprices"}


def test_builder_handles_blank_and_malformed_field_lines(tmp_path: Path) -> None:
    """Empty tokens (trailing commas / blank lines) must be skipped, not crashed on."""
    source = tmp_path / "index.md"
    source.write_text(
        "### AShareEODPrices（3个字段）\n\n"
        "S_INFO_WINDCODE, , S_DQ_CLOSE,\n"
        "\n"
        "### (no fields here)\n\n"
        "\n",
        encoding="utf-8",
    )
    records = CatalogBuilder(source).build()
    assert [(r.table, r.field) for r in records] == [
        ("ashareeodprices", "s_info_windcode"),
        ("ashareeodprices", "s_dq_close"),
    ]


def test_builder_lowercases_table_and_field(tmp_path: Path) -> None:
    source = tmp_path / "index.md"
    source.write_text(
        "### AShareEODPrices（1个字段）\n\nS_DQ_CLOSE\n",
        encoding="utf-8",
    )
    records = CatalogBuilder(source).build()
    assert records[0].table == "ashareeodprices"
    assert records[0].field == "s_dq_close"


@requires_real_index
def test_real_index_has_between_7000_and_8000_records() -> None:
    """Sanity check: the real Wind field index yields 7,000-8,000 records."""
    records = CatalogBuilder(REAL_INDEX).build()
    assert 7000 <= len(records) <= 8000, f"got {len(records)} records"


def test_generated_jsonl_round_trips(tmp_path: Path) -> None:
    catalog = FieldCatalog(
        [FieldRecord(table="ashareeodprices", field="s_dq_close")]
    )
    out = tmp_path / "fields.jsonl"
    catalog.save(out)
    reloaded = FieldCatalog.load(out)
    assert [(r.table, r.field) for r in reloaded.records] == [
        ("ashareeodprices", "s_dq_close")
    ]


@requires_real_index
def test_generated_jsonl_matches_real_index() -> None:
    """The locally generated JSONL matches a fresh build from the index."""
    if not GENERATED_JSONL.exists():
        pytest.skip("generated JSONL not built; run `factor-platform build-wind-catalog`")
    expected = CatalogBuilder(REAL_INDEX).build()
    loaded = FieldCatalog.load(GENERATED_JSONL)
    assert len(loaded.records) == len(expected)
    assert loaded.records[:3] == expected[:3]
