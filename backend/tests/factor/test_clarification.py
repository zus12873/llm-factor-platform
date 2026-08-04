from factor_platform.domain.models import FactorSpec
from factor_platform.factor.clarification import ClarificationEngine


def _make_spec(variable: str = "close", period: str | None = None) -> FactorSpec:
    variable_fields: dict[str, object] = {"logical_name": variable.lower(), "meaning": variable}
    if period:
        variable_fields["financial_period"] = period
    return FactorSpec.model_validate(
        {
            "factor_name": "test",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "formula_text": f"rank({variable})",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": variable.lower()}],
            },
            "variables": [variable_fields],
        }
    )


def test_profitability_quality_is_blocking() -> None:
    questions = ClarificationEngine().questions(_make_spec(variable="盈利质量"))
    assert questions[0].blocking is True
    assert questions[0].options == ["ROE_TTM", "ROA_TTM", "CFO_TO_PROFIT"]
    assert questions[0].question_id == "profitability_definition"


def test_explicit_roe_ttm_needs_no_profitability_question() -> None:
    questions = ClarificationEngine().questions(_make_spec(variable="ROE_TTM", period="TTM"))
    assert all(question.question_id != "profitability_definition" for question in questions)


def test_valuation_is_blocking_until_concrete() -> None:
    questions = ClarificationEngine().questions(_make_spec(variable="估值"))
    assert questions[0].blocking is True
    assert questions[0].options == ["PE_TTM", "PB", "PS_TTM"]


def test_missing_direction_is_blocking() -> None:
    spec = _make_spec(variable="close")
    spec.direction = None
    questions = ClarificationEngine().questions(spec)
    assert any(question.question_id == "direction" and question.blocking for question in questions)
