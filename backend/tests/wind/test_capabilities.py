"""Tests for the Wind capability catalog (planner tool contracts).

Verifies that ``CapabilityCatalog`` normalizes the ``RQ_WIND_CAPABILITIES``
registry into immutable tool specs, exposes only callable data tools to the
planner / LLM, and resolves Chinese price-term intents to concrete
``get_price`` arguments. Tests use the real registry; no mocks.
"""

from __future__ import annotations

import dataclasses

import pytest

from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog


def test_registry_exports_only_callable_data_tools() -> None:
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    tools = catalog.to_llm_tools()
    assert "get_price" in {tool.name for tool in tools}
    assert "Factor" not in {tool.name for tool in tools}


def test_close_maps_to_get_price() -> None:
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    match = catalog.find_exact("后复权收盘价")
    assert match.tool_name == "get_price"
    assert match.arguments["fields"] == ["close"]


def test_lifecycle_and_expression_entries_are_excluded_from_llm_tools() -> None:
    """init (lifecycle) and Factor/LOG (expression) are not direct data tools."""
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    names = {tool.name for tool in catalog.to_llm_tools()}
    assert "init" not in names
    assert "Factor" not in names
    assert "LOG" not in names
    for expected in (
        "instruments",
        "get_trading_dates",
        "get_next_trading_date",
        "get_previous_trading_date",
        "is_st_stock",
        "is_suspended",
        "get_price",
        "index_components",
        "execute_factor",
        "execute_generic_query_plan",
    ):
        assert expected in names, f"{expected} should be exposed to the planner"


def test_catalog_accounts_for_every_registry_entry() -> None:
    """All 13 registry entries are normalized; none silently dropped."""
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    cataloged = {tool.name for tool in catalog.tools}
    assert cataloged == set(RQ_WIND_CAPABILITIES.keys())
    assert len(catalog.tools) == len(RQ_WIND_CAPABILITIES) == 13


def test_get_price_spec_round_trip() -> None:
    """get_tool returns a normalized spec with purpose/exact_outputs preserved."""
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    spec = catalog.get_tool("get_price")
    assert spec is not None
    assert spec.name == "get_price"
    assert spec.kind == "data"
    assert spec.planner == "price"
    assert spec.purpose  # non-empty
    assert any(
        out.output == "close" and out.argument.get("fields") == ["close"]
        for out in spec.exact_outputs
    )


def test_get_tool_returns_none_for_unknown_name() -> None:
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    assert catalog.get_tool("does_not_exist") is None


def test_find_exact_none_when_nothing_matches() -> None:
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    assert catalog.find_exact("一个完全不存在的意图描述xyz") is None


def test_tool_specs_are_immutable() -> None:
    """Frozen dataclasses: assigning to a field raises FrozenInstanceError."""
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    spec = catalog.get_tool("get_price")
    assert spec is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "tampered"  # type: ignore[misc]
