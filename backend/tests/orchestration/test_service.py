"""End-to-end tests for the P0 workflow, against fakes.

Two properties are load-bearing here and neither is visible from a single method:

* **Refusals happen before external effects.** A disputed metric must stop the
  workflow while stopping is still free — after a Wind query it has already cost
  money and, more importantly, has already put data where it did not belong.
* **A revision cascades.** Changing the formula must clear the fields, plan and
  build hash. Task 3.5 built that machinery; this checks the workflow actually
  routes through it rather than appending a plain update event.
"""

from __future__ import annotations

import json

import pytest

from factor_platform.db.repository import SessionRepository
from factor_platform.domain.errors import DisputedMetricError
from factor_platform.domain.models import (
    FactorSpec,
    FieldSelection,
    FieldTimeRole,
    ResearchRequest,
)
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.orchestration.service import WorkflowService
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.planner import WindPlanner

DRAFT = {
    "factor_name": "momentum_20d",
    "hypothesis": "过去20日涨幅高的股票延续",
    "direction": "higher_is_better",
    "rebalance_frequency": "monthly",
    "formula_explanation": "rank(rolling_return(close,20))",
    "formula_ast": {
        "type": "call",
        "op": "rank",
        "args": [
            {
                "type": "call",
                "op": "rolling_return",
                "args": [{"type": "variable", "name": "close"}],
                "params": {"window": 20},
            }
        ],
    },
    "variables": [{"logical_name": "close", "meaning": "后复权收盘价"}],
}


def request() -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "asset_type": "stock",
            "universe": "000300.SH",
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "research_idea": "构建过去20个交易日的动量因子",
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


@pytest.fixture
def provider() -> FakeLLMProvider:
    fake = FakeLLMProvider()
    fake.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    return fake


@pytest.fixture
def workflow(engine, provider: FakeLLMProvider) -> WorkflowService:
    registry = MetricRegistry.load()
    planner = WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry)
    return WorkflowService(SessionRepository(engine), provider, planner, registry=registry)


async def drive_to_manifest(workflow: WorkflowService, session_id: str = "s1"):
    snapshot = await workflow.create_session(session_id)
    snapshot = await workflow.submit_message(session_id, request(), snapshot.version)
    spec = snapshot.factor_spec
    assert spec is not None
    snapshot = await workflow.confirm_formula(session_id, spec, snapshot.version)
    snapshot = await workflow.search_fields(session_id, [], snapshot.version)
    snapshot = await workflow.confirm_fields(session_id, confirmed_close(), snapshot.version)
    return await workflow.build_manifest(session_id, request(), snapshot.version)


# --------------------------------------------------------------------------- happy path


async def test_a_clear_idea_runs_through_to_a_plan(workflow: WorkflowService) -> None:
    snapshot = await drive_to_manifest(workflow)
    assert snapshot.state == "code_ready"
    assert snapshot.plan is not None
    assert snapshot.factor_spec is not None
    assert snapshot.factor_spec.canonical_formula


async def test_registered_aliases_are_discovered_and_persisted(
    workflow: WorkflowService,
) -> None:
    snapshot = await workflow.create_session("s-discovery")
    snapshot = await workflow.submit_message("s-discovery", request(), snapshot.version)
    assert snapshot.factor_spec is not None
    snapshot = await workflow.confirm_formula("s-discovery", snapshot.factor_spec, snapshot.version)
    snapshot = await workflow.discover_fields("s-discovery", snapshot.version)

    assert snapshot.state == "waiting_field_confirmation"
    close = next(
        candidate
        for candidate in snapshot.field_candidates
        if candidate.logical_name == "close"
        and candidate.table == "ashareeodprices"
        and candidate.field == "s_dq_adjclose"
    )
    assert close.source_tier == "alias"
    assert close.schema_status == "not_verified"


async def test_the_plan_resolves_index_membership_before_prices(
    workflow: WorkflowService,
) -> None:
    snapshot = await drive_to_manifest(workflow)
    assert snapshot.plan is not None
    tools = [step.tool for step in snapshot.plan.steps]
    assert tools.index("wind.index_components") < tools.index("wind.get_price")


async def test_price_alias_logical_name_still_reuses_registered_get_price(
    workflow: WorkflowService,
) -> None:
    snapshot = await workflow.create_session("s-price-alias")
    snapshot = await workflow.submit_message("s-price-alias", request(), snapshot.version)
    assert snapshot.factor_spec is not None
    spec = snapshot.factor_spec.model_copy(deep=True)
    spec.variables[0].logical_name = "adj_close"
    spec.formula_ast.args[0].args[0].name = "adj_close"
    snapshot = await workflow.confirm_formula("s-price-alias", spec, snapshot.version)
    snapshot = await workflow.search_fields("s-price-alias", [], snapshot.version)
    snapshot = await workflow.confirm_fields(
        "s-price-alias",
        [
            FieldSelection(
                logical_name="adj_close",
                table="ashareeodprices",
                field="s_dq_adjclose",
            )
        ],
        snapshot.version,
    )
    snapshot = await workflow.build_manifest("s-price-alias", request(), snapshot.version)

    price_step = next(step for step in snapshot.plan.steps if step.tool == "wind.get_price")
    assert price_step.inputs == ["adj_close"]
    assert price_step.arguments["fields"] == ["close"]


async def test_the_plan_carries_the_time_convention(workflow: WorkflowService) -> None:
    snapshot = await drive_to_manifest(workflow)
    assert snapshot.plan is not None
    assert snapshot.plan.time_convention.trade_date == "T+1"


# --------------------------------------------------------------------------- clarification


async def test_an_ambiguous_idea_stops_for_clarification(engine) -> None:
    vague = dict(DRAFT)
    vague["variables"] = [{"logical_name": "profitability", "meaning": "盈利质量"}]
    vague["formula_ast"] = {
        "type": "call",
        "op": "rank",
        "args": [{"type": "variable", "name": "profitability"}],
    }
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(vague, ensure_ascii=False))
    registry = MetricRegistry.load()
    workflow = WorkflowService(
        SessionRepository(engine),
        provider,
        WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry),
        registry=registry,
    )

    snapshot = await workflow.create_session("s-vague")
    snapshot = await workflow.submit_message("s-vague", request(), snapshot.version)

    assert snapshot.state == "needs_clarification"
    assert any(q.blocking for q in snapshot.clarifications)
    assert snapshot.factor_spec is not None

    resolved = await workflow.resolve_clarification(
        "s-vague",
        {"profitability_definition": "ROE_TTM"},
        snapshot.version,
    )
    assert resolved.state == "waiting_formula_confirmation"
    assert resolved.clarifications == []
    assert resolved.factor_spec is not None
    assert resolved.factor_spec.variables[0].logical_name == "roe_ttm"
    assert "roe_ttm" in resolved.factor_spec.canonical_formula


# --------------------------------------------------------------------------- refusals


async def test_a_disputed_metric_is_refused_before_any_external_call(
    workflow: WorkflowService,
) -> None:
    """Stopping is free here; after a Wind query it is not."""
    snapshot = await workflow.create_session("s-disputed")
    snapshot = await workflow.submit_message("s-disputed", request(), snapshot.version)
    spec = snapshot.factor_spec
    assert spec is not None
    snapshot = await workflow.confirm_formula("s-disputed", spec, snapshot.version)
    snapshot = await workflow.search_fields("s-disputed", [], snapshot.version)

    disputed = [
        FieldSelection(
            logical_name="float_mv",
            table="ashareeodderivativeindicator",
            field="float_a_shr",
        )
    ]
    with pytest.raises(DisputedMetricError):
        await workflow.confirm_fields("s-disputed", disputed, snapshot.version)

    # And it left no trace in the session history.
    after = await workflow._snapshot("s-disputed")
    assert after.field_selections == []


async def test_building_a_manifest_without_a_formula_is_refused(
    workflow: WorkflowService,
) -> None:
    snapshot = await workflow.create_session("s-empty")
    with pytest.raises(ValueError, match="formula"):
        await workflow.build_manifest("s-empty", request(), snapshot.version)


# --------------------------------------------------------------------------- revisions


async def test_revising_the_formula_clears_fields_and_plan(
    workflow: WorkflowService,
) -> None:
    """Task 3.5 built the cascade; this checks the workflow routes through it."""
    snapshot = await drive_to_manifest(workflow)
    assert snapshot.field_selections and snapshot.plan is not None

    revised = FactorSpec.model_validate(
        {
            **snapshot.factor_spec.model_dump(mode="json"),
            "factor_name": "momentum_60d",
        }
    )
    after = await workflow.revise_formula("s1", revised, snapshot.version)

    assert after.state == "waiting_formula_confirmation"
    assert after.field_selections == []
    assert after.plan is None


async def test_revising_an_ast_re_renders_the_canonical_formula(
    workflow: WorkflowService,
) -> None:
    snapshot = await drive_to_manifest(workflow)
    assert snapshot.factor_spec is not None
    revised = snapshot.factor_spec.model_copy(deep=True)
    rolling = revised.formula_ast.args[0]
    rolling.params["window"] = 21
    revised.canonical_formula = "stale display text"

    after = await workflow.revise_formula("s1", revised, snapshot.version)

    assert "window=21" in after.factor_spec.canonical_formula
    assert "stale" not in after.factor_spec.canonical_formula


async def test_revising_the_date_range_keeps_the_formula(
    workflow: WorkflowService,
) -> None:
    snapshot = await drive_to_manifest(workflow)
    wider = request().model_copy(update={"end_date": "2024-12-31"})
    after = await workflow.revise_request("s1", wider, snapshot.version)
    assert after.factor_spec is None  # request revision invalidates the parse
    assert after.state == "parsing_input"


async def test_a_rerun_before_anything_ran_is_refused(
    workflow: WorkflowService,
) -> None:
    """There is nothing to redo from ``code_ready`` — the action there is to run.

    Allowing it would let the UI offer "rerun" on a factor that has never
    produced a result, and the user would reasonably read that as confirmation
    that one exists.
    """
    from factor_platform.domain.errors import IllegalTransitionError

    snapshot = await drive_to_manifest(workflow)
    with pytest.raises(IllegalTransitionError):
        await workflow.rerun("s1", snapshot.version)


async def test_a_clone_carries_the_definition_but_no_artifacts(
    workflow: WorkflowService,
) -> None:
    await drive_to_manifest(workflow)
    clone = await workflow.clone_session("s1", "s1-copy")
    assert clone.factor_spec is not None
    assert clone.field_selections == []
    assert clone.plan is None
    assert clone.state == "waiting_formula_confirmation"


# --------------------------------------------------------------------------- concurrency


async def test_a_stale_version_is_rejected(workflow: WorkflowService) -> None:
    """Two tabs on the same session must not silently overwrite each other."""
    from factor_platform.domain.errors import ConcurrentUpdateError

    snapshot = await workflow.create_session("s-stale")
    await workflow.submit_message("s-stale", request(), snapshot.version)
    with pytest.raises(ConcurrentUpdateError):
        await workflow.submit_message("s-stale", request(), snapshot.version)
