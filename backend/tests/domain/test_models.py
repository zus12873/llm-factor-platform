import pytest
from pydantic import ValidationError

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import FactorSpec, ResearchRequest


def test_formula_call_requires_registered_operator_shape() -> None:
    node = FormulaNode(
        type="call", op="rank", args=[FormulaNode(type="variable", name="roe_ttm")]
    )
    assert node.args[0].name == "roe_ttm"


def test_variable_rejects_call_fields() -> None:
    with pytest.raises(ValidationError):
        FormulaNode(type="variable", name="close", op="rank")


def test_call_rejects_unregistered_operator() -> None:
    with pytest.raises(ValidationError):
        FormulaNode(type="call", op="execute_sql", args=[])


def test_factor_spec_has_one_executable_formula_and_no_rival_display_formula() -> None:
    spec = FactorSpec.model_validate(
        {
            "factor_name": "quality",
            "hypothesis": "higher ROE may predict returns",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "rebalance_frequency": "monthly",
            "direction": "higher_is_better",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": "roe_ttm"}],
            },
            "canonical_formula": "rank(roe_ttm)",
            "formula_explanation": "对 ROE_TTM 做横截面排名",
            "variables": [
                {"logical_name": "roe_ttm", "meaning": "ROE TTM", "point_in_time_required": True}
            ],
        }
    )
    assert spec.formula_ast.op == "rank"
    assert spec.canonical_formula == "rank(roe_ttm)"
    # The model's prose is display-only and must not masquerade as a second
    # authoritative formula.
    assert not hasattr(spec, "formula_text")


def test_factor_spec_carries_a_default_time_convention() -> None:
    spec = FactorSpec.model_validate(
        {
            "factor_name": "momentum",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "formula_ast": {"type": "variable", "name": "close"},
            "variables": [{"logical_name": "close", "meaning": "close"}],
        }
    )
    assert spec.time_convention.signal_date == "T"
    assert spec.time_convention.trade_date == "T+1"
    assert spec.preprocessing.ordered_steps() == []


def test_factor_spec_defaults_version_and_evidence() -> None:
    spec = FactorSpec.model_validate(
        {
            "factor_name": "momentum",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "canonical_formula": "rank(close)",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": "close"}],
            },
            "variables": [{"logical_name": "close", "meaning": "close price"}],
        }
    )
    assert spec.schema_version == 1
    assert spec.version == 1
    assert spec.source_evidence == []
    # rebalance frequency is optional and unknown until confirmed.
    assert spec.rebalance_frequency is None


def test_research_request_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"asset_type": "stock"})
