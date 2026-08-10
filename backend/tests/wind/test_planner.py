"""Tests for the deterministic Wind retrieval planner.

The planner turns a confirmed factor spec plus confirmed field bindings into an
``ExecutionPlan``. Everything it does is decided by rules, not by a model: the
model's job ended when the user confirmed the formula and the fields.

Three properties carry most of the weight:

* **It refuses rather than guesses.** A variable with no confirmed field, or a
  financial field with no announcement date, fails at planning time. Both would
  otherwise produce a plan that runs cleanly and returns wrong numbers.
* **It adds what the user did not think to ask for.** Historical index members
  before prices, a warm-up window long enough for the longest rolling operator,
  ST/suspension filters from the data rules.
* **It carries the time convention into the plan.** Retrieval and execution both
  need to know when the signal is knowable; leaving it implicit is how a pipeline
  that reads no future field still trades on information it could not have had.
"""

from __future__ import annotations

import pytest

from factor_platform.domain.errors import DisputedMetricError
from factor_platform.domain.models import (
    DataRules,
    FactorSpec,
    FieldSelection,
    FieldTimeRole,
    ResearchRequest,
)
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.planner import PlanningError, WindPlanner


@pytest.fixture(scope="module")
def catalog() -> CapabilityCatalog:
    return CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return MetricRegistry.load()


@pytest.fixture
def planner(catalog: CapabilityCatalog, registry: MetricRegistry) -> WindPlanner:
    return WindPlanner(catalog, registry)


# --------------------------------------------------------------------------- fixtures


def momentum_spec(window: int = 20, universe: str = "000300.SH") -> FactorSpec:
    return FactorSpec.model_validate(
        {
            "factor_name": "momentum",
            "asset_type": "stock",
            "universe": universe,
            "frequency": "daily",
            "direction": "higher_is_better",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [
                    {
                        "type": "call",
                        "op": "rolling_return",
                        "args": [{"type": "variable", "name": "close"}],
                        "params": {"window": window},
                    }
                ],
            },
            "variables": [{"logical_name": "close", "meaning": "后复权收盘价"}],
        }
    )


def roe_spec() -> FactorSpec:
    return FactorSpec.model_validate(
        {
            "factor_name": "quality",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": "roe_ttm"}],
            },
            "variables": [
                {
                    "logical_name": "roe_ttm",
                    "meaning": "净资产收益率 ROE_TTM",
                    "point_in_time_required": True,
                }
            ],
        }
    )


def request(universe: str = "000300.SH") -> ResearchRequest:
    """The trusted envelope: universe and date range never come from the model."""
    return ResearchRequest.model_validate(
        {
            "asset_type": "stock",
            "universe": universe,
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "research_idea": "planner fixture",
        }
    )


def confirmed_close() -> list[FieldSelection]:
    return [
        FieldSelection(
            logical_name="close",
            table="ashareeodprices",
            field="s_dq_adjclose",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]


def confirmed_roe(*, announcement: str | None = "ann_dt") -> list[FieldSelection]:
    return [
        FieldSelection(
            logical_name="roe_ttm",
            table="asharettmhis",
            field="s_fa_roe_ttm",
            time_role=FieldTimeRole.REPORT_PERIOD,
            point_in_time=True,
            announcement_date_field=announcement,
            report_period_field="report_period",
        )
    ]


# --------------------------------------------------------------------------- ordering


def test_index_membership_is_resolved_before_prices(planner: WindPlanner) -> None:
    """Prices for today's members over a historical window is survivorship bias.

    Asserted as an ordering, not adjacency: universe filters legitimately sit
    between the two, and pinning the exact index would make this test fail every
    time a filter is added.
    """
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    tools = [step.tool for step in plan.steps]
    assert tools.index("wind.index_components") < tools.index("wind.get_price")
    assert tools[0] == "wind.index_components"


def test_a_non_index_universe_skips_the_membership_step(planner: WindPlanner) -> None:
    plan = planner.plan(momentum_spec(universe="all"), confirmed_close(), request("all"))
    assert all(step.tool != "wind.index_components" for step in plan.steps)


def test_membership_step_gets_its_arguments_filled_from_the_request(
    planner: WindPlanner,
) -> None:
    """``find_exact`` returns the tool name only for membership/calendar tools."""
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    step = next(s for s in plan.steps if s.tool == "wind.index_components")
    assert step.arguments["order_book_id"] == "000300.SH"
    assert step.arguments["start_date"]
    assert step.arguments["end_date"]


# --------------------------------------------------------------------------- tool choice


def test_prices_use_the_registered_price_function(planner: WindPlanner) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    price_step = next(s for s in plan.steps if s.tool == "wind.get_price")
    assert price_step.arguments["fields"] == ["close"]


def test_a_valuation_field_does_not_get_routed_to_the_price_function(
    planner: WindPlanner, catalog: CapabilityCatalog
) -> None:
    """``get_price`` serves nine OHLCV outputs and raises on anything else.

    Routing by table would send every ``ashareeodderivativeindicator`` column —
    PE, PB, market cap — into it, and the plan would only fail once a worker ran
    it. The capability catalog already declares what each tool produces; the
    planner asks it instead of keeping a second, wrong copy.
    """
    spec = momentum_spec()
    spec.variables[0].logical_name = "pe_ttm"
    spec.formula_ast.args[0].args[0].name = "pe_ttm"
    selection = [
        FieldSelection(
            logical_name="pe_ttm",
            table="ashareeodderivativeindicator",
            field="s_val_pe_ttm",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]
    plan = planner.plan(spec, selection, request())

    price_outputs = {
        output.output for output in catalog.get_tool("get_price").exact_outputs
    }
    assert "pe_ttm" not in price_outputs
    assert all(step.tool != "wind.get_price" for step in plan.steps)
    generic = next(s for s in plan.steps if s.tool == "wind.execute_generic_query_plan")
    assert generic.arguments["field"] == "s_val_pe_ttm"


def test_close_is_routed_to_the_price_function_because_the_catalog_says_so(
    planner: WindPlanner, catalog: CapabilityCatalog
) -> None:
    price_outputs = {
        output.output for output in catalog.get_tool("get_price").exact_outputs
    }
    assert "close" in price_outputs
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    assert any(step.tool == "wind.get_price" for step in plan.steps)


def test_a_field_without_a_registered_function_uses_a_controlled_query_shape(
    planner: WindPlanner,
) -> None:
    """Only the six vetted shapes; never free-form SQL."""
    plan = planner.plan(roe_spec(), confirmed_roe(), request())
    step = next(s for s in plan.steps if s.tool == "wind.execute_generic_query_plan")
    assert step.arguments["shape"] == "report_period"
    assert step.arguments["table"] == "asharettmhis"
    assert step.arguments["field"] == "s_fa_roe_ttm"


# --------------------------------------------------------------------------- refusals


def test_an_unconfirmed_variable_is_refused(planner: WindPlanner) -> None:
    """Planning around a guess is how the wrong column reaches production."""
    with pytest.raises(PlanningError, match="close"):
        planner.plan(momentum_spec(), [], request())


def test_a_point_in_time_field_without_an_announcement_date_is_refused(
    planner: WindPlanner,
) -> None:
    with pytest.raises(PlanningError, match="announcement"):
        planner.plan(roe_spec(), confirmed_roe(announcement=None), request())


def test_a_disputed_metric_stops_the_plan(planner: WindPlanner) -> None:
    spec = roe_spec()
    spec.variables[0].logical_name = "float_mv"
    spec.formula_ast.args[0].name = "float_mv"
    selection = confirmed_roe()
    selection[0].logical_name = "float_mv"
    with pytest.raises(DisputedMetricError):
        planner.plan(spec, selection, request())


# --------------------------------------------------------------------------- time


def test_plan_carries_the_time_convention(planner: WindPlanner) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    assert plan.time_convention.signal_date == "T"
    assert plan.time_convention.trade_date == "T+1"


def test_warmup_start_precedes_the_requested_start(planner: WindPlanner) -> None:
    """A 20-day rolling return needs 20 days of history before the first value."""
    plan = planner.plan(momentum_spec(window=20), confirmed_close(), request())
    assert plan.warmup_start is not None
    assert plan.warmup_start < plan.metadata["start_date"]


def test_a_longer_window_reaches_further_back(planner: WindPlanner) -> None:
    short = planner.plan(momentum_spec(window=20), confirmed_close(), request())
    long = planner.plan(momentum_spec(window=240), confirmed_close(), request())
    assert long.warmup_start < short.warmup_start


def test_a_formula_without_rolling_operators_needs_no_warmup(
    planner: WindPlanner,
) -> None:
    plan = planner.plan(roe_spec(), confirmed_roe(), request())
    assert plan.warmup_start == plan.metadata["start_date"]


def test_an_after_close_announcement_is_not_usable_the_same_day(
    planner: WindPlanner,
) -> None:
    spec = roe_spec()
    spec.time_convention.announcement_timing_policy = "conservative"
    plan = planner.plan(spec, confirmed_roe(), request())
    step = next(s for s in plan.steps if s.tool == "wind.execute_generic_query_plan")
    assert step.arguments["as_of_offset_days"] >= 1


# --------------------------------------------------------------------------- data rules


def test_universe_filters_come_from_the_data_rules(planner: WindPlanner) -> None:
    spec = momentum_spec()
    spec.data_rules = DataRules(exclude_st=True, exclude_suspended=True)
    plan = planner.plan(spec, confirmed_close(), request())
    tools = [step.tool for step in plan.steps]
    assert "wind.is_st_stock" in tools
    assert "wind.is_suspended" in tools


def test_no_filters_are_added_when_the_rules_do_not_ask(planner: WindPlanner) -> None:
    spec = momentum_spec()
    spec.data_rules = DataRules(exclude_st=False, exclude_suspended=False)
    plan = planner.plan(spec, confirmed_close(), request())
    tools = [step.tool for step in plan.steps]
    assert "wind.is_st_stock" not in tools


# --------------------------------------------------------------------------- provenance


def test_every_step_names_a_tool_that_exists_in_the_catalog(
    planner: WindPlanner, catalog: CapabilityCatalog
) -> None:
    """A planned tool the executor does not have fails at run time, not plan time."""
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    for step in plan.steps:
        assert catalog.get_tool(step.tool.removeprefix("wind.")) is not None, (
            f"{step.tool} is not in the capability catalog"
        )


def test_plan_records_the_confirmed_bindings_it_was_built_from(
    planner: WindPlanner,
) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close(), request())
    assert plan.metadata["confirmed_fields"] == ["close -> ashareeodprices.s_dq_adjclose"]
