import json

import pytest

from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import load_golden_cases
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider

EXPECTED_CASE_IDS = {
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
    assert len(cases) == 10
    assert {case.case_id for case in cases} == EXPECTED_CASE_IDS
    assert all(case.expected_formula_ast and case.expected_tool_names for case in cases)


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda case: case.case_id)
async def test_case_blocking_matches_expected(case) -> None:
    """Each golden case must reproduce its expected blocking clarifications."""
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(case.provider_draft))
    spec = await FactorParser(provider).parse(case.request)
    questions = ClarificationEngine().questions(spec)
    actual_blocking = sorted(question.question_id for question in questions if question.blocking)
    expected = sorted(case.expected_blocking_question_ids)
    assert actual_blocking == expected, (
        f"{case.case_id}: blocking {actual_blocking} != expected {expected}"
    )
