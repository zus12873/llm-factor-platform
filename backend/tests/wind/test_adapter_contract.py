"""Contract tests for factor_platform.wind.adapter.

These are behavioral assertions that the public surface of the Wind adapter
survived the migration + connection-factory injection unchanged: capability
registry, public callables, generic-query shape set, identifier validation,
code mapping, factor expressions, and key function signatures. They do NOT
touch a real database.
"""

from __future__ import annotations

import inspect

import pytest

import factor_platform.wind.adapter as wind

# Re-exported for parametrize readability.
ADAPTER_PUBLIC_NAMES = [
    "init",
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
    "Factor",
    "LOG",
    "rq_to_wind",
    "wind_to_rq",
    "query_df",
]


def test_capability_registry_keys_survived() -> None:
    expected = {
        "init",
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
        "Factor",
        "LOG",
    }
    assert expected <= set(wind.RQ_WIND_CAPABILITIES)


def test_capability_registry_has_no_unexpected_extra_kinds() -> None:
    """All capability entries carry a ``kind`` from the documented vocabulary."""
    allowed_kinds = {
        "lifecycle",
        "data",
        "calendar",
        "status",
        "membership",
        "factor",
        "expression",
    }
    for name, entry in wind.RQ_WIND_CAPABILITIES.items():
        assert entry["kind"] in allowed_kinds, f"{name} has unexpected kind {entry['kind']!r}"


def test_capability_version_is_one() -> None:
    assert wind.RQ_WIND_CAPABILITY_VERSION == 1


@pytest.mark.parametrize("name", ADAPTER_PUBLIC_NAMES)
def test_public_callables_survived(name: str) -> None:
    assert hasattr(wind, name), f"adapter lost public attribute: {name}"
    assert callable(getattr(wind, name)), f"{name} is not callable"


def test_get_price_capability_shape() -> None:
    entry = wind.RQ_WIND_CAPABILITIES["get_price"]
    assert entry["kind"] == "data"
    assert entry["return_schema"]["kind"] == "dataframe"
    assert entry["planner"] == "price"
    assert "order_book_ids" in entry["parameters"]


def test_execute_generic_query_plan_capability_shape() -> None:
    entry = wind.RQ_WIND_CAPABILITIES["execute_generic_query_plan"]
    assert entry["kind"] == "data"
    assert entry["planner"] == "generic_table"
    assert "plan" in entry["parameters"]


def test_generic_query_shapes_are_the_six_expected() -> None:
    assert {
        "point_range",
        "report_period",
        "announcement_range",
        "interval_overlap",
        "static_lookup",
        "cross_section_asof",
    } == wind._GENERIC_QUERY_SHAPES


def test_safe_query_identifier_accepts_valid_names() -> None:
    assert wind._safe_query_identifier("ashareeodprices", "table_name") == "ashareeodprices"
    assert wind._safe_query_identifier("S_INFO_WINDCODE", "field") == "s_info_windcode"
    assert wind._safe_query_identifier("  Trade_Dt  ", "field") == "trade_dt"
    assert wind._safe_query_identifier("a1b2c3", "field") == "a1b2c3"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1_starts_with_digit",
        "has-dash",
        "has space",
        "drop; table",
        "col'b",
        "UPPER_ok_but_has_!",
    ],
)
def test_safe_query_identifier_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        wind._safe_query_identifier(bad, "field")


def test_rq_to_wind_maps_rq_suffixes_to_wind() -> None:
    assert wind.rq_to_wind("600519.XSHG") == "600519.SH"
    assert wind.rq_to_wind("000001.XSHE") == "000001.SZ"
    assert wind.rq_to_wind("430047.XBSE") == "430047.BJ"
    # Already-wind-form codes pass through unchanged.
    assert wind.rq_to_wind("600519.SH") == "600519.SH"
    assert wind.rq_to_wind(None) is None


def test_wind_to_rq_is_identity() -> None:
    assert wind.wind_to_rq("600519.SH") == "600519.SH"
    assert wind.wind_to_rq(None) is None


def test_factor_expression_and_log_transform() -> None:
    factor = wind.Factor("market_cap_3")
    assert factor.name == "market_cap_3"
    assert factor.transforms == []
    logged = wind.LOG(factor)
    assert "log" in logged.transforms
    assert logged.name == "market_cap_3"
    # LOG must not mutate the original factor.
    assert factor.transforms == []


def test_rqdatac_like_namespace_exposes_core_functions() -> None:
    namespace = wind.rqdatac_like
    assert namespace.init is wind.init
    assert namespace.get_price is wind.get_price
    assert namespace.instruments is wind.instruments
    assert namespace.index_components is wind.index_components
    assert namespace.is_st_stock is wind.is_st_stock
    assert namespace.is_suspended is wind.is_suspended


def test_init_signature_backward_compatible() -> None:
    signature = inspect.signature(wind.init)
    params = list(signature.parameters)
    assert params[:3] == ["username", "password", "addr"]
    assert signature.parameters["addr"].default == ("rqdatad-pro.ricequant.com", 16011)
    assert signature.parameters["username"].default is None
    assert signature.parameters["password"].default is None


def test_get_price_signature_unchanged() -> None:
    signature = inspect.signature(wind.get_price)
    expected = [
        "order_book_ids",
        "start_date",
        "end_date",
        "frequency",
        "fields",
        "adjust_type",
        "skip_suspended",
        "expect_df",
        "time_slice",
        "market",
    ]
    assert list(signature.parameters)[:10] == expected
    assert signature.parameters["frequency"].default == "1d"
    assert signature.parameters["adjust_type"].default == "pre"


def test_execute_generic_query_plan_signature_unchanged() -> None:
    signature = inspect.signature(wind.execute_generic_query_plan)
    assert list(signature.parameters) == ["plan"]


def test_index_components_signature_unchanged() -> None:
    signature = inspect.signature(wind.index_components)
    expected = [
        "order_book_id",
        "date",
        "start_date",
        "end_date",
        "return_create_tm",
        "market",
    ]
    assert list(signature.parameters) == expected
