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
from factor_platform.wind.field_search import FieldSearch

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


def test_unknown_term_returns_bm25_only(search: FieldSearch) -> None:
    """No alias for a nonsense term — all results are BM25 tier."""
    candidates = search.search(make_requirement("zzznotaterm"), limit=3)
    for cand in candidates:
        assert cand.source_tier == "bm25"


def test_search_returns_at_most_limit(search: FieldSearch) -> None:
    candidates = search.search(make_requirement("trade"), limit=4)
    assert len(candidates) <= 4
