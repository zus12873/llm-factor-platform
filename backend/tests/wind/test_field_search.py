"""Tests for the Wind field search (alias tier + BM25).

``FieldSearch`` resolves a ``DataRequirement`` to ranked ``FieldCandidate``
rows. Exact Chinese alias hits (``source_tier="alias"``) outrank BM25 matches
(``source_tier="bm25"``); asset-type and frequency constraints from the
requirement are applied before ranking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.domain.models import AssetType, DataRequirement, Frequency
from factor_platform.wind.catalog import FieldCatalog, FieldRecord
from factor_platform.wind.field_search import FieldSearch
from factor_platform.wind.metadata_catalog import FieldMetadata, MetadataCatalog

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "backend" / "data" / "generated" / "wind_fields.jsonl"
ALIASES_PATH = REPO_ROOT / "backend" / "data" / "wind_aliases.yaml"


def make_requirement(
    term: str,
    *,
    asset_type: AssetType | None = None,
    frequency: Frequency | None = None,
) -> DataRequirement:
    """Build a DataRequirement whose ``meaning`` carries the search term."""
    return DataRequirement(
        logical_name=term,
        meaning=term,
        asset_type=asset_type,
        frequency=frequency,
    )


@pytest.fixture(scope="module")
def search() -> FieldSearch:
    """Load the real generated catalog and committed alias YAML."""
    if not CATALOG_PATH.exists():
        pytest.skip(
            "generated catalog missing; run "
            "`uv run --project backend factor-platform build-wind-catalog "
            "--source windquery/windquery/references/wind_field_index.md "
            "--output backend/data/generated/wind_fields.jsonl` first"
        )
    return FieldSearch.from_paths(CATALOG_PATH, ALIASES_PATH)


def test_alias_beats_bm25_for_exact_business_term(search: FieldSearch) -> None:
    candidates = search.search(make_requirement("后复权收盘价"), limit=3)
    assert candidates[0].field == "s_dq_adjclose"
    assert candidates[0].source_tier == "alias"


def test_bm25_ranks_relevant_field(search: FieldSearch) -> None:
    """A token drawn from a field name surfaces that field via BM25.

    ``pctchange`` is not in the alias YAML, so this exercises the lexical
    ranking path rather than the alias tier.
    """
    candidates = search.search(make_requirement("pctchange"), limit=5)
    assert candidates, "expected at least one BM25 hit"
    assert any("pctchange" in c.field for c in candidates)


def test_results_respect_asset_type_filter(search: FieldSearch) -> None:
    """When the requirement pins asset_type, alias hits must obey the filter."""
    candidates = search.search(
        make_requirement("开盘价", asset_type=AssetType.STOCK),
        limit=5,
    )
    assert candidates
    for cand in candidates:
        if cand.asset_type is not None:
            assert cand.asset_type == AssetType.STOCK


def test_results_respect_frequency_filter(search: FieldSearch) -> None:
    candidates = search.search(
        make_requirement("收盘价", frequency=Frequency.DAILY),
        limit=5,
    )
    assert candidates
    for cand in candidates:
        if cand.frequency is not None:
            assert cand.frequency == Frequency.DAILY


def test_results_are_deduplicated(search: FieldSearch) -> None:
    """An alias hit and a BM25 hit for the same (table, field) coalesce."""
    candidates = search.search(make_requirement("收盘价"), limit=10)
    keys = [(c.table, c.field) for c in candidates]
    assert len(keys) == len(set(keys))


def test_search_for_close_lists_unadjusted_and_prefers_adjusted(
    search: FieldSearch,
) -> None:
    hits = search.search(
        DataRequirement(logical_name="close", meaning="收盘价"), limit=5
    )
    fields = [hit.field for hit in hits]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert hits[0].field == "s_dq_adjclose"
    close = next(hit for hit in hits if hit.field == "s_dq_close")
    assert close.price_adjustment == "none"
    assert "不等于" in (close.semantic_note or "")
    assert close.source_tier == "alias"


def test_explicit_unadjusted_keeps_s_dq_close_first(search: FieldSearch) -> None:
    hits = search.search(
        DataRequirement(logical_name="close", meaning="不复权收盘价"), limit=5
    )
    assert hits[0].field == "s_dq_close"


def test_unknown_term_returns_bm25_only(search: FieldSearch) -> None:
    """No alias for a nonsense term — all results are BM25 tier."""
    candidates = search.search(make_requirement("zzznotaterm"), limit=3)
    for cand in candidates:
        assert cand.source_tier == "bm25"


def test_search_returns_at_most_limit(search: FieldSearch) -> None:
    candidates = search.search(make_requirement("trade"), limit=4)
    assert len(candidates) <= 4


# --------------------------------------------------------------------------- metadata tier


@pytest.fixture
def metadata_search() -> FieldSearch:
    """A small, self-contained search wired to a metadata catalog.

    Three fields deliberately differ in what is known about them: a daily one, a
    quarterly one, and one the dictionary could not describe at all.
    """
    catalog = FieldCatalog(
        [
            FieldRecord(table="ashareeodderivativeindicator", field="s_val_mv"),
            FieldRecord(table="ashareincome", field="mv_quarterly"),
            FieldRecord(table="obscuretable", field="mv_undocumented"),
        ]
    )
    metadata = MetadataCatalog(
        [
            FieldMetadata(
                table="ashareeodderivativeindicator",
                field="s_val_mv",
                name_zh="当日总市值",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                unit="ten_thousand_cny",
                metadata_source="WDS",
            ),
            FieldMetadata(
                table="ashareincome",
                field="mv_quarterly",
                name_zh="季度市值",
                asset_type=AssetType.STOCK,
                frequency=Frequency.QUARTERLY,
                metadata_source="WDS",
            ),
        ]
    )
    return FieldSearch(catalog=catalog, aliases={}, metadata=metadata)


def test_quarterly_field_is_filtered_out_for_daily_requirement(
    metadata_search: FieldSearch,
) -> None:
    """Before metadata existed this filter silently passed everything through."""
    candidates = metadata_search.search(
        make_requirement("mv", frequency=Frequency.DAILY), limit=10
    )
    assert candidates
    assert all(c.frequency is not Frequency.QUARTERLY for c in candidates)


def test_asset_type_filter_reaches_bm25_hits(metadata_search: FieldSearch) -> None:
    candidates = metadata_search.search(
        make_requirement("mv", asset_type=AssetType.BOND), limit=10
    )
    assert all(c.asset_type is not AssetType.STOCK for c in candidates)


def test_field_without_metadata_is_marked_not_dropped(
    metadata_search: FieldSearch,
) -> None:
    """A field the dictionary missed must stay findable, flagged as undescribed."""
    candidates = metadata_search.search(
        make_requirement("mv", frequency=Frequency.DAILY), limit=10
    )
    undescribed = [c for c in candidates if c.metadata_source is None]
    assert undescribed, "an undocumented field was dropped by the metadata filter"
    assert any(c.field == "mv_undocumented" for c in undescribed)


def test_metadata_enriches_the_candidate_with_its_chinese_name(
    metadata_search: FieldSearch,
) -> None:
    candidates = metadata_search.search(make_requirement("mv"), limit=10)
    described = next(c for c in candidates if c.field == "s_val_mv")
    assert described.meaning_zh == "当日总市值"
    assert described.unit == "ten_thousand_cny"
    assert described.metadata_source == "WDS"


def test_a_chinese_query_finds_a_field_through_its_metadata_name(
    metadata_search: FieldSearch,
) -> None:
    """Metadata is a recall layer, not just a filter.

    Users write research ideas in Chinese, but the catalog documents are English
    identifiers (``ashareeodderivativeindicator s_val_mv``). Without the Chinese
    name in the index, "总市值" matches nothing and the whole metadata tier only
    ever narrows results it never helped find — leaving the 32 hand-written
    aliases as the only Chinese entry point into 7,478 fields.
    """
    candidates = metadata_search.search(make_requirement("总市值"), limit=5)
    assert any(c.field == "s_val_mv" for c in candidates)


def test_a_shares_outrank_hong_kong_for_the_same_business_term() -> None:
    """Scope is daily A-shares, and the two markets are otherwise identical.

    Both tables are ``asset_type=stock`` with the same Chinese field name, so
    nothing but the market separates them. Ranking rather than filtering keeps HK
    reachable — existence is settled later, against the database.
    """
    catalog = FieldCatalog(
        [
            FieldRecord(table="hkshareeodderivativeindex", field="s_val_mv"),
            FieldRecord(table="ashareeodderivativeindicator", field="s_val_mv"),
        ]
    )
    metadata = MetadataCatalog(
        [
            FieldMetadata(
                table="hkshareeodderivativeindex",
                field="s_val_mv",
                name_zh="当日总市值",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                market="hk",
                metadata_source="WDS",
            ),
            FieldMetadata(
                table="ashareeodderivativeindicator",
                field="s_val_mv",
                name_zh="当日总市值",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                market="cn_a",
                metadata_source="WDS",
            ),
        ]
    )
    search = FieldSearch(catalog=catalog, aliases={}, metadata=metadata)

    candidates = search.search(make_requirement("当日总市值"), limit=5)
    assert candidates[0].table == "ashareeodderivativeindicator"
    # Demoted, not removed.
    assert any(c.table == "hkshareeodderivativeindex" for c in candidates)
