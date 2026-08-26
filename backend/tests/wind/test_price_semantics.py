"""Price-adjustment ranking is a label + reorder, never an alias rewrite.

``close`` and ``收盘价`` stay aliased to ``s_dq_close``. The semantic layer may
*boost* ``s_dq_adjclose`` for return-like queries and must still list the raw
close so confirmation cannot be skipped or silently remapped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.domain.models import AssetType, DataRequirement, FieldCandidate, Frequency
from factor_platform.wind.catalog import FieldCatalog, FieldRecord
from factor_platform.wind.field_search import AliasEntry, FieldSearch
from factor_platform.wind.metadata_catalog import FieldMetadata, MetadataCatalog
from factor_platform.wind.price_semantics import (
    annotate_candidate,
    apply_price_semantics,
    classify_price_intent,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "backend" / "data" / "generated" / "wind_fields.jsonl"
ALIASES_PATH = REPO_ROOT / "backend" / "data" / "wind_aliases.yaml"


def _requirement(meaning: str, logical_name: str = "close") -> DataRequirement:
    return DataRequirement(logical_name=logical_name, meaning=meaning)


def test_plain_close_does_not_alias_to_adjclose() -> None:
    intent = classify_price_intent(_requirement("收盘价"), True)
    assert intent.preferred_field == "s_dq_adjclose"
    assert intent.explicit is False


def test_adj_close_is_explicit_backward() -> None:
    intent = classify_price_intent(_requirement("后复权收盘价"), True)
    assert intent.preferred_field == "s_dq_adjclose"
    assert intent.explicit is True


def test_forward_adj_prefers_backward_named_field() -> None:
    intent = classify_price_intent(_requirement("前复权收盘价"), True)
    assert intent.preferred_field == "s_dq_adjclose_backward"
    assert intent.explicit is True


def test_explicit_unadjusted_prefers_raw_close() -> None:
    intent = classify_price_intent(_requirement("不复权收盘价"), True)
    assert intent.preferred_field == "s_dq_close"
    assert intent.explicit is True


def test_momentum_infers_adjusted_close() -> None:
    intent = classify_price_intent(
        DataRequirement(logical_name="momentum", meaning="动量"), True
    )
    assert intent.preferred_field == "s_dq_adjclose"
    assert intent.explicit is False


def test_ranking_prior_false_keeps_raw_close_preferred() -> None:
    intent = classify_price_intent(_requirement("收盘价"), False)
    assert intent.preferred_field == "s_dq_close"
    assert intent.explicit is False


def test_unrelated_requirement_has_no_price_preference() -> None:
    intent = classify_price_intent(
        DataRequirement(logical_name="roe_ttm", meaning="净资产收益率"), True
    )
    assert intent.preferred_field is None
    assert intent.explicit is False


def test_open_is_not_reranked_as_close() -> None:
    intent = classify_price_intent(
        DataRequirement(logical_name="open", meaning="后复权开盘价"), True
    )
    assert intent.preferred_field is None


def test_annotate_keeps_source_tier_and_labels_raw_close() -> None:
    intent = classify_price_intent(_requirement("收盘价"), True)
    raw = FieldCandidate(
        table="ashareeodprices",
        field="s_dq_close",
        source_tier="alias",
        meaning_zh="收盘价",
    )
    labelled = annotate_candidate(raw, intent)
    assert labelled.source_tier == "alias"
    assert labelled.price_adjustment == "none"
    assert "不等于" in (labelled.semantic_note or "")


def test_annotate_inferred_adj_explains_the_default() -> None:
    intent = classify_price_intent(_requirement("close"), True)
    adj = FieldCandidate(
        table="ashareeodprices",
        field="s_dq_adjclose",
        source_tier="bm25",
    )
    labelled = annotate_candidate(adj, intent)
    assert labelled.source_tier == "bm25"
    assert labelled.price_adjustment == "backward"
    assert labelled.semantic_note == "动量/收益类默认推荐后复权收盘价，close ≠ adj_close"


def test_apply_boosts_adj_without_dropping_raw() -> None:
    raw = FieldCandidate(
        table="ashareeodprices", field="s_dq_close", source_tier="alias"
    )
    out = apply_price_semantics(
        [raw],
        _requirement("收盘价"),
        True,
        inject=lambda field: FieldCandidate(
            table="ashareeodprices", field=field, source_tier="semantic"
        ),
        limit=5,
    )
    fields = [candidate.field for candidate in out]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert out[0].field == "s_dq_adjclose"
    close = next(candidate for candidate in out if candidate.field == "s_dq_close")
    assert close.source_tier == "alias"
    assert close.price_adjustment == "none"


def _tiny_search() -> FieldSearch:
    catalog = FieldCatalog(
        [
            FieldRecord(table="ashareeodprices", field="s_dq_close"),
            FieldRecord(table="ashareeodprices", field="s_dq_adjclose"),
            FieldRecord(table="ashareeodprices", field="s_dq_adjclose_backward"),
            FieldRecord(table="ashareeodprices", field="s_dq_open"),
        ]
    )
    aliases = {
        "收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_close",
            meaning_zh="收盘价",
        ),
        "后复权收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_adjclose",
            meaning_zh="后复权收盘价",
        ),
        "前复权收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_adjclose_backward",
            meaning_zh="前复权收盘价",
        ),
    }
    return FieldSearch(catalog=catalog, aliases=aliases)


@pytest.fixture
def search() -> FieldSearch:
    """Prefer a tiny catalog so these tests do not depend on the licensed dump."""
    return _tiny_search()


def test_search_for_close_lists_unadjusted_and_prefers_adjusted(
    search: FieldSearch,
) -> None:
    hits = search.search(_requirement("收盘价"), limit=5)
    fields = [hit.field for hit in hits]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert hits[0].field == "s_dq_adjclose"
    close = next(hit for hit in hits if hit.field == "s_dq_close")
    assert close.price_adjustment == "none"
    assert "不等于" in (close.semantic_note or "")
    assert close.source_tier == "alias"


def test_explicit_unadjusted_keeps_s_dq_close_first(search: FieldSearch) -> None:
    hits = search.search(_requirement("不复权收盘价"), limit=5)
    assert hits[0].field == "s_dq_close"


def test_search_does_not_auto_confirm(search: FieldSearch) -> None:
    hits = search.search(_requirement("收盘价"), limit=5)
    assert len(hits) >= 2


@pytest.fixture(scope="module")
def catalog_search() -> FieldSearch:
    if not CATALOG_PATH.exists():
        pytest.skip(
            "generated catalog missing; run "
            "`uv run --project backend factor-platform build-wind-catalog "
            "--source windquery/windquery/references/wind_field_index.md "
            "--output backend/data/generated/wind_fields.jsonl` first"
        )
    return FieldSearch.from_paths(CATALOG_PATH, ALIASES_PATH)


def _daily_stock_price_search() -> FieldSearch:
    """Daily A-share aliases and catalog rows, with metadata so filters bite."""
    catalog = FieldCatalog(
        [
            FieldRecord(table="ashareeodprices", field="s_dq_close"),
            FieldRecord(table="ashareeodprices", field="s_dq_adjclose"),
            FieldRecord(table="ashareeodprices", field="s_dq_adjclose_backward"),
        ]
    )
    aliases = {
        "收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_close",
            asset_type=AssetType.STOCK,
            frequency=Frequency.DAILY,
            meaning_zh="收盘价",
        ),
        "后复权收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_adjclose",
            asset_type=AssetType.STOCK,
            frequency=Frequency.DAILY,
            meaning_zh="后复权收盘价",
        ),
        "前复权收盘价": AliasEntry(
            table="ashareeodprices",
            field="s_dq_adjclose_backward",
            asset_type=AssetType.STOCK,
            frequency=Frequency.DAILY,
            meaning_zh="前复权收盘价",
        ),
    }
    metadata = MetadataCatalog(
        [
            FieldMetadata(
                table="ashareeodprices",
                field="s_dq_close",
                name_zh="收盘价",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                metadata_source="WDS",
            ),
            FieldMetadata(
                table="ashareeodprices",
                field="s_dq_adjclose",
                name_zh="后复权收盘价",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                metadata_source="WDS",
            ),
            FieldMetadata(
                table="ashareeodprices",
                field="s_dq_adjclose_backward",
                name_zh="前复权收盘价",
                asset_type=AssetType.STOCK,
                frequency=Frequency.DAILY,
                metadata_source="WDS",
            ),
        ]
    )
    return FieldSearch(catalog=catalog, aliases=aliases, metadata=metadata)


def test_weekly_requirement_does_not_inject_daily_ashare_close() -> None:
    """Alias and catalog both fail the frequency filter; do not invent a row."""
    hits = _daily_stock_price_search().search(
        DataRequirement(
            logical_name="close",
            meaning="收盘价",
            frequency=Frequency.WEEKLY,
        ),
        limit=5,
    )
    assert not any(hit.table == "ashareeodprices" for hit in hits)


def test_bond_requirement_does_not_inject_stock_ashare_close() -> None:
    hits = _daily_stock_price_search().search(
        DataRequirement(
            logical_name="close",
            meaning="收盘价",
            asset_type=AssetType.BOND,
        ),
        limit=5,
    )
    assert not any(hit.table == "ashareeodprices" for hit in hits)


def test_catalog_search_for_close_lists_both(catalog_search: FieldSearch) -> None:
    hits = catalog_search.search(_requirement("收盘价"), limit=5)
    fields = [hit.field for hit in hits]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert hits[0].field == "s_dq_adjclose"
