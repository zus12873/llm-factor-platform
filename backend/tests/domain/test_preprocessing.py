"""Ordered preprocessing pipeline.

Boolean switches could not express order, but order changes results: neutralise
then standardise is not the same factor as standardise then neutralise. The
pipeline makes order explicit and rejects the duplicate-transform cases that the
old dual representation (AST operator *and* boolean switch) allowed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factor_platform.domain.preprocessing import (
    DataRules,
    PreprocessingPipeline,
    PreprocessingStep,
)


def step(order: int, operation: str, target: str, **params: object) -> PreprocessingStep:
    return PreprocessingStep(order=order, operation=operation, target=target, parameters=params)


def test_pipeline_preserves_declared_order() -> None:
    pipeline = PreprocessingPipeline(
        steps=[
            step(2, "zscore", "factor"),
            step(1, "industry_neutralize", "factor"),
        ]
    )
    assert [s.operation for s in pipeline.ordered_steps()] == [
        "industry_neutralize",
        "zscore",
    ]


def test_duplicate_order_is_rejected() -> None:
    with pytest.raises(ValidationError, match="order"):
        PreprocessingPipeline(
            steps=[step(1, "zscore", "factor"), step(1, "industry_neutralize", "factor")]
        )


def test_same_operation_on_same_target_twice_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        PreprocessingPipeline(
            steps=[step(1, "zscore", "factor"), step(2, "zscore", "factor")]
        )


def test_same_operation_on_different_targets_is_allowed() -> None:
    pipeline = PreprocessingPipeline(
        steps=[step(1, "winsorize", "variables"), step(2, "winsorize", "factor")]
    )
    assert len(pipeline.steps) == 2


def test_variable_and_factor_steps_are_separable() -> None:
    pipeline = PreprocessingPipeline(
        steps=[
            step(1, "winsorize", "variables"),
            step(2, "industry_neutralize", "factor"),
            step(3, "zscore", "factor"),
        ]
    )
    assert [s.operation for s in pipeline.steps_for("variables")] == ["winsorize"]
    assert [s.operation for s in pipeline.steps_for("factor")] == [
        "industry_neutralize",
        "zscore",
    ]


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PreprocessingStep(order=1, operation="normalise_somehow", target="factor")


def test_unknown_target_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PreprocessingStep(order=1, operation="zscore", target="portfolio")


def test_empty_pipeline_is_valid() -> None:
    assert PreprocessingPipeline().ordered_steps() == []


def test_data_rules_no_longer_carry_transforms() -> None:
    rules = DataRules()
    assert rules.use_adjusted_price is True
    assert rules.exclude_st is True
    assert rules.exclude_suspended is True
    for removed in ("winsorize", "standardize", "neutralize_industry", "fillna_method"):
        assert not hasattr(rules, removed)
