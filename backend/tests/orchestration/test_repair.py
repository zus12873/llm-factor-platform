"""Tests for classified repair.

Most of these assert a *refusal*, which is the substance of this module. Repairing
a formula in response to a missing field produces a different factor that runs
cleanly — and the user receives it as a fix for the one they asked for. That is
worse than the original failure, because the original failure was visible.
"""

from __future__ import annotations

import json

import pytest

from factor_platform.domain.models import ErrorCategory, FactorSpec, StructuredError
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.orchestration.repair import (
    MAX_REPAIR_ATTEMPTS,
    ErrorClassifier,
    RepairLimitError,
    RepairService,
)


def spec() -> FactorSpec:
    return FactorSpec.model_validate(
        {
            "factor_name": "momentum",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "canonical_formula": "rolling_mean(close, window=0)",
            "formula_ast": {
                "type": "call",
                "op": "rolling_mean",
                "args": [{"type": "variable", "name": "close"}],
                "params": {"window": 0},
            },
            "variables": [{"logical_name": "close", "meaning": "收盘价"}],
        }
    )


def error(
    code: str = "invalid_window", category: ErrorCategory = ErrorCategory.FORMULA
) -> StructuredError:
    return StructuredError(category=category, code=code, message=f"{code} 触发")


def repair_service(window: int = 20) -> RepairService:
    provider = FakeLLMProvider()
    provider.enqueue_content(
        json.dumps(
            {
                "explanation": f"窗口 0 无意义，改为 {window}",
                "formula_ast": {
                    "type": "call",
                    "op": "rolling_mean",
                    "args": [{"type": "variable", "name": "close"}],
                    "params": {"window": window},
                },
            },
            ensure_ascii=False,
        )
    )
    return RepairService(provider)


# --------------------------------------------------------------------------- refusals


def test_a_missing_field_is_not_repaired_as_a_formula() -> None:
    """Repairing the formula would produce a different factor that runs."""
    decision = ErrorClassifier().classify(
        error(code="unknown_wind_field", category=ErrorCategory.FIELD)
    )
    assert decision.repairable is False
    assert "字段问题不是公式问题" in decision.reason


def test_empty_data_is_not_repaired_as_a_formula() -> None:
    """Widening the window is the user's decision, not the model's."""
    decision = ErrorClassifier().classify(
        error(code="empty_result", category=ErrorCategory.EMPTY_DATA)
    )
    assert decision.repairable is False
    assert "只有用户能做的决定" in decision.reason


def test_a_time_basis_error_is_never_auto_repaired() -> None:
    """An automatic fix here could manufacture look-ahead."""
    decision = ErrorClassifier().classify(
        error(code="signal_traded_before_available", category=ErrorCategory.TIME_BASIS)
    )
    assert decision.repairable is False


def test_an_unregistered_error_code_is_not_repaired() -> None:
    """Repairing a failure mode nobody thought through yields another factor."""
    decision = ErrorClassifier().classify(error(code="something_new"))
    assert decision.repairable is False
    assert "未登记即不修复" in decision.reason


# --------------------------------------------------------------------------- the cap


def test_the_cap_is_exactly_two() -> None:
    assert MAX_REPAIR_ATTEMPTS == 2


def test_a_third_attempt_is_refused() -> None:
    decision = ErrorClassifier().classify(error(), attempts_used=2)
    assert decision.repairable is False
    assert decision.attempts_remaining == 0


async def test_proposing_a_third_repair_raises() -> None:
    with pytest.raises(RepairLimitError, match="用尽"):
        await repair_service().propose(spec(), error(), attempts_used=2)


def test_remaining_attempts_are_reported() -> None:
    assert ErrorClassifier().classify(error(), attempts_used=1).attempts_remaining == 1


# --------------------------------------------------------------------------- repairs


def test_a_parameter_error_is_repairable() -> None:
    decision = ErrorClassifier().classify(error(code="invalid_window"))
    assert decision.repairable is True
    assert "因子意图未变" in decision.reason


async def test_a_repair_produces_a_new_version_not_a_mutation() -> None:
    """The failed spec stays in history so the user can see what changed."""
    original = spec()
    proposal = await repair_service().propose(original, error())

    assert proposal.spec.version == original.version + 1
    assert original.formula_ast.params == {"window": 0}, "the original must not change"


async def test_a_repair_must_be_confirmed_before_it_runs() -> None:
    """A silently repaired factor is one nobody agreed to."""
    proposal = await repair_service().propose(spec(), error())
    assert proposal.requires_confirmation is True


async def test_the_repair_explains_what_it_changed() -> None:
    proposal = await repair_service().propose(spec(), error())
    assert "窗口" in proposal.explanation


async def test_the_repaired_formula_is_actually_corrected() -> None:
    proposal = await repair_service().propose(spec(), error())
    assert proposal.spec.formula_ast.params == {"window": 20}


async def test_the_attempt_number_is_recorded() -> None:
    proposal = await repair_service().propose(spec(), error(), attempts_used=1)
    assert proposal.attempt == 2
