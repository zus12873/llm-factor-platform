"""Acceptance metrics over a case suite.

A single pass/fail count cannot tell you what to fix. A factor that comes out
wrong because the model mis-read the research idea is a different defect from one
that comes out wrong because the planner bound the wrong Wind column, which is
different again from one that fails because the date range has no data. This
report keeps those apart, names the failing cases, and groups the metrics the way
the work is divided.

It also refuses to report a score for anything this suite cannot measure.
Printing 0.0 would read as "measured and failing" and 1.0 as "verified"; both
misrepresent coverage in the one artifact final acceptance compares against, so
those groups sit in ``not_measured`` and carry no number.

The reasons in that list must be kept current. A stale reason is worse than none:
it describes a limitation that has since been fixed and sends the reader looking
for work already done. Field planning was in that list until the planner existed;
leaving it there afterwards understated what had been verified.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from factor_platform.domain.errors import LLMResponseError
from factor_platform.domain.models import DataRequirement, FactorSpec, FieldSelection
from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import GoldenCase
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.parser import FactorParser
from factor_platform.factor.renderer import render_canonical_formula
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.planner import WindPlanner


class MetricGroup(StrEnum):
    STRUCTURED_PARSING = "structured_parsing"
    CLARIFICATION = "clarification"
    FIELD_AND_PLANNING = "field_and_planning"
    EXECUTION_AND_SAFETY = "execution_and_safety"
    REPORT_PARSING = "report_parsing"
    EFFICIENCY = "efficiency"


class FailureKind(StrEnum):
    """Where a case broke, which is what decides who fixes it."""

    MODEL = "model"
    FIELD = "field"
    DATA = "data"
    EXECUTION = "execution"


class CaseFailure(BaseModel):
    case_id: str
    kind: str
    detail: str


class SuiteReport(BaseModel):
    schema_version: int = 1
    suite: str = "golden"
    total_cases: int
    passed: int
    failed: int

    # group -> metric name -> value. Groups in ``not_measured`` are absent.
    metrics: dict[str, dict[str, float]] = {}
    not_measured: list[MetricGroup] = []
    not_measured_reason: dict[str, str] = {}

    failure_breakdown: dict[str, int] = {}
    failures: list[CaseFailure] = []

    blocking_recall: float = 0.0
    blocking_precision: float = 0.0
    unnecessary_question_rate: float = 0.0
    missed_blocking: list[str] = []
    spurious_blocking: list[str] = []

    elapsed_seconds: float = 0.0


#: Groups this suite cannot score, with the reason *as it stands now*.
#:
#: These strings are part of the acceptance artifact, and a stale one is worse
#: than no reason at all: it describes a limitation that has since been fixed and
#: sends the reader looking for work that is already done. A guard test below
#: keeps them honest by asserting the named components are genuinely absent.
_UNMEASURED: dict[MetricGroup, str] = {
    MetricGroup.EXECUTION_AND_SAFETY: (
        "执行链路已实现（Task 13–15），但本套件的案例不携带输入 Parquet，"
        "因此没有任何案例真正执行——执行成功率、未来函数拦截率与错误分类"
        "准确率需要带真实数据的端到端运行才能测量"
    ),
    MetricGroup.REPORT_PARSING: (
        "研报链路已实现（Task 21–23），但本案例集不含研报案例；"
        "研报指标需要单独的研报验收集"
    ),
}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _planning_accuracy(cases: Sequence[GoldenCase]) -> dict[str, float]:
    """Score the planner against the tools each case pins.

    Measurable since Task 11. Leaving it in ``not_measured`` after the planner
    existed would have understated coverage in the one artifact final acceptance
    compares against.
    """
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    planner = WindPlanner(catalog, MetricRegistry.load())

    scored = 0
    matched = 0
    for case in cases:
        bindings = [
            FieldSelection.model_validate(field)
            for field in case.expected_fields
            if field.get("table") and field.get("field")
        ]
        if not bindings or not case.expected_tool_names:
            continue
        scored += 1
        spec = FactorSpec(
            factor_name=case.case_id,
            asset_type=case.request.asset_type,
            universe=case.request.universe,
            frequency=case.request.frequency,
            formula_ast=case.expected_formula_ast,
            variables=[
                DataRequirement(logical_name=b.logical_name, meaning="") for b in bindings
            ],
        )
        try:
            plan = planner.plan(spec, bindings, case.request)
        except Exception:  # noqa: BLE001 - a refusal counts as a miss, not a crash
            continue
        if sorted({step.tool for step in plan.steps}) == sorted(case.expected_tool_names):
            matched += 1

    return {
        "tool_selection_accuracy": _ratio(matched, scored),
        "planned_cases": float(scored),
    }


async def _run_one(case: GoldenCase) -> tuple[list[str], list[CaseFailure]]:
    """Parse one case offline; return raised blocking ids and any failures."""
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(case.provider_draft, ensure_ascii=False))

    try:
        spec = await FactorParser(provider).parse(case.request)
    except LLMResponseError as exc:
        return [], [
            CaseFailure(
                case_id=case.case_id, kind=FailureKind.MODEL, detail=f"parse failed: {exc}"
            )
        ]

    failures: list[CaseFailure] = []
    # An ambiguous case's ``expected_formula_ast`` is the *post-clarification*
    # factor: `rank(growth)` becomes `rank(revenue_yoy)` only once the user picks
    # a definition. Nothing here resolves clarifications — that is the
    # orchestrator — so scoring the draft against it would fail every ambiguous
    # case for behaving correctly.
    if case.category != "ambiguous" and spec.formula_ast.model_dump(
        exclude_none=True
    ) != case.expected_formula_ast.model_dump(exclude_none=True):
        failures.append(
            CaseFailure(
                case_id=case.case_id,
                kind=FailureKind.MODEL,
                detail="parsed AST differs from the expected AST",
            )
        )
    if spec.canonical_formula != render_canonical_formula(spec.formula_ast):
        failures.append(
            CaseFailure(
                case_id=case.case_id,
                kind=FailureKind.MODEL,
                detail="canonical formula is not a faithful render of the AST",
            )
        )
    if not spec.variables:
        failures.append(
            CaseFailure(
                case_id=case.case_id,
                kind=FailureKind.FIELD,
                detail="spec bound no variables",
            )
        )

    raised = [
        q.question_id
        for q in ClarificationEngine().questions(spec, case.request.research_idea)
        if q.blocking
    ]
    expected = set(case.expected_blocking_question_ids)
    if expected - set(raised):
        failures.append(
            CaseFailure(
                case_id=case.case_id,
                kind=FailureKind.MODEL,
                detail=f"missed blocking questions: {sorted(expected - set(raised))}",
            )
        )
    if set(raised) - expected:
        failures.append(
            CaseFailure(
                case_id=case.case_id,
                kind=FailureKind.MODEL,
                detail=f"asked unnecessary blocking questions: {sorted(set(raised) - expected)}",
            )
        )
    return raised, failures


_CaseResult = tuple[GoldenCase, list[str], list[CaseFailure]]


async def _run_all(cases: Sequence[GoldenCase]) -> list[_CaseResult]:
    results: list[_CaseResult] = []
    for case in cases:
        raised, failures = await _run_one(case)
        results.append((case, raised, failures))
    return results


def run_case_suite(cases: Sequence[GoldenCase], *, suite: str = "golden") -> SuiteReport:
    """Run every case offline and return a structured acceptance report."""
    started = time.monotonic()
    results = asyncio.run(_run_all(cases))
    elapsed = time.monotonic() - started

    failures = [failure for _, _, case_failures in results for failure in case_failures]
    failed_ids = {failure.case_id for failure in failures}

    expected_total = sum(len(c.expected_blocking_question_ids) for c in cases)
    caught = 0
    raised_total = 0
    correct_raised = 0
    missed: list[str] = []
    spurious: list[str] = []
    concrete = [c for c in cases if c.category != "ambiguous"]
    concrete_with_questions = 0

    for case, raised, _ in results:
        expected = set(case.expected_blocking_question_ids)
        caught += len(expected & set(raised))
        raised_total += len(raised)
        correct_raised += len(set(raised) & expected)
        if expected - set(raised):
            missed.append(case.case_id)
        if set(raised) - expected:
            spurious.append(case.case_id)
        if case.category != "ambiguous" and raised:
            concrete_with_questions += 1

    # Only concrete cases have a reachable target AST, so the rate is over them.
    ast_scored = [r for r in results if r[0].category != "ambiguous"]
    planning = _planning_accuracy(cases)
    ast_matches = sum(
        1
        for _, _, case_failures in ast_scored
        if not any("AST" in f.detail for f in case_failures)
    )
    parsed_ok = sum(
        1
        for case, _, case_failures in results
        if not any(f.detail.startswith("parse failed") for f in case_failures)
    )

    breakdown = {kind.value: 0 for kind in FailureKind}
    for failure in failures:
        breakdown[failure.kind] += 1

    return SuiteReport(
        suite=suite,
        total_cases=len(cases),
        passed=len(cases) - len(failed_ids),
        failed=len(failed_ids),
        metrics={
            MetricGroup.STRUCTURED_PARSING.value: {
                "parse_success_rate": _ratio(parsed_ok, len(cases)),
                # Denominator excludes ambiguous cases: see _run_one.
                "ast_match_rate": _ratio(ast_matches, len(ast_scored)),
                "ast_scored_cases": float(len(ast_scored)),
            },
            MetricGroup.CLARIFICATION.value: {
                "blocking_recall": _ratio(caught, expected_total),
                "blocking_precision": _ratio(correct_raised, raised_total),
                "unnecessary_question_rate": _ratio(
                    concrete_with_questions, len(concrete)
                )
                if concrete
                else 0.0,
            },
            MetricGroup.FIELD_AND_PLANNING.value: planning,
            MetricGroup.EFFICIENCY.value: {
                "elapsed_seconds": elapsed,
                "llm_calls": float(len(cases)),
                "cost": 0.0,
            },
        },
        not_measured=list(_UNMEASURED),
        not_measured_reason={group.value: reason for group, reason in _UNMEASURED.items()},
        failure_breakdown=breakdown,
        failures=failures,
        blocking_recall=_ratio(caught, expected_total),
        blocking_precision=_ratio(correct_raised, raised_total),
        unnecessary_question_rate=(
            concrete_with_questions / len(concrete) if concrete else 0.0
        ),
        missed_blocking=missed,
        spurious_blocking=spurious,
        elapsed_seconds=elapsed,
    )


__all__ = ["CaseFailure", "FailureKind", "MetricGroup", "SuiteReport", "run_case_suite"]
