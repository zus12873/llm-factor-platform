import json

import pytest

from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import load_golden_cases
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider

# The Week-1 acceptance cases. The suite has grown past these (see
# test_case_coverage.py for composition rules); these must never disappear,
# because they are the ones the Week-1 gate was signed off against.
FOUNDING_CASE_IDS = {
    "momentum_20d",
    "low_volatility_20d",
    "price_volume",
    "profitability_ambiguous",
    "valuation_ambiguous",
    "quality_value",
    "growth_ambiguous",
    "historical_hs300",
    "point_in_time_financial",
    "complex_vague",
}


def test_all_golden_cases_have_complete_expected_contracts() -> None:
    cases = load_golden_cases()
    assert {case.case_id for case in cases} >= FOUNDING_CASE_IDS
    assert all(case.expected_formula_ast for case in cases)


def test_concrete_cases_pin_the_tools_the_planner_must_choose() -> None:
    """Only resolved cases can name tools.

    An ambiguous case has no confirmed field bindings until the user answers, so
    it has no determined retrieval plan either. Demanding a tool list for one
    would mean inventing the answer the case exists to ask about.
    """
    for case in load_golden_cases():
        if case.category != "ambiguous":
            assert case.expected_tool_names, f"{case.case_id} pins no tools"


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda case: case.case_id)
async def test_case_blocking_matches_expected(case) -> None:
    """Each golden case must reproduce its expected blocking clarifications."""
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(case.provider_draft))
    spec = await FactorParser(provider).parse(case.request)
    questions = ClarificationEngine().questions(spec, case.request.research_idea)
    actual_blocking = sorted(question.question_id for question in questions if question.blocking)
    expected = sorted(case.expected_blocking_question_ids)
    assert actual_blocking == expected, (
        f"{case.case_id}: blocking {actual_blocking} != expected {expected}"
    )
