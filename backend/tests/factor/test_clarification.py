from factor_platform.domain.models import FactorSpec
from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.metric_registry import MetricRegistry


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
            "canonical_formula": f"rank({variable.lower()})",
            "formula_explanation": f"对 {variable} 做横截面排名",
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


def test_clarification_options_come_from_the_registry() -> None:
    """The picker must not keep its own copy of the metric list.

    A second copy is how the UI ends up offering an option the registry has since
    marked disputed — the one place that decision was supposed to be enforceable.
    """
    registry = MetricRegistry.load()
    questions = ClarificationEngine(registry).questions(_make_spec(variable="盈利质量"))
    assert questions[0].options == registry.options_for("profitability")


def test_a_disputed_metric_is_never_offered_as_a_choice() -> None:
    registry = MetricRegistry.load()
    for category in ("profitability", "valuation", "growth"):
        for option in registry.options_for(category):
            assert registry.gate(option).allowed, (
                f"{option} is offered to the user but the registry refuses it"
            )


def test_missing_direction_is_blocking() -> None:
    spec = _make_spec(variable="close")
    spec.direction = None
    questions = ClarificationEngine().questions(spec)
    assert any(question.question_id == "direction" and question.blocking for question in questions)


def test_original_vague_idea_cannot_be_silently_resolved_by_model_draft() -> None:
    concrete_draft = _make_spec(variable="PE_TTM")
    questions = ClarificationEngine().questions(concrete_draft, "构建估值因子")
    assert any(
        question.question_id == "valuation_definition" and question.blocking
        for question in questions
    )


def test_original_concrete_idea_does_not_trigger_vague_term_rule() -> None:
    concrete_draft = _make_spec(variable="PE_TTM")
    questions = ClarificationEngine().questions(concrete_draft, "使用 PE_TTM 的倒数构建估值因子")
    assert all(question.question_id != "valuation_definition" for question in questions)


def test_human_metric_choice_replaces_a_multi_variable_model_guess() -> None:
    guessed = FactorSpec.model_validate(
        {
            **_make_spec().model_dump(mode="json"),
            "formula_ast": {
                "type": "call",
                "op": "divide",
                "args": [
                    {"type": "variable", "name": "operating_cashflow"},
                    {"type": "variable", "name": "net_profit"},
                ],
            },
            "variables": [
                {"logical_name": "operating_cashflow"},
                {"logical_name": "net_profit"},
            ],
        }
    )

    resolved = ClarificationEngine().apply_answers(guessed, {"profitability_definition": "ROE_TTM"})

    assert [variable.logical_name for variable in resolved.variables] == ["roe_ttm"]
    assert resolved.formula_ast.type == "variable"
    assert resolved.formula_ast.name == "roe_ttm"
    assert resolved.canonical_formula == "roe_ttm"
    assert "ROE_TTM" in resolved.formula_explanation
