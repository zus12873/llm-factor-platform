"""Tests for the ordered preprocessing pipeline.

These three operations used to be AST operators. They were moved out because a
tree cannot express their ordering, and their ordering changes the answer:
neutralising then standardising is not the same factor as standardising then
neutralising. Under the old boolean-switch design that order was fixed in code
and invisible to the person confirming the factor.

So the pipeline runs strictly by declared ``order``, and it records what it
actually ran. The record is what lets the formula validator notice that a factor
was standardised twice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_platform.domain.preprocessing import (
    PreprocessingOperation,
    PreprocessingPipeline,
    PreprocessingStep,
    PreprocessingTarget,
)
from factor_platform.factor.pipeline_executor import PipelineContext, PipelineExecutor

DATES = pd.date_range("2024-01-01", periods=3, freq="D")
CODES = ["600519.SH", "000001.SZ", "000002.SZ", "600000.SH"]


def frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=DATES, columns=CODES)


RAW = frame(
    [
        [1.0, 2.0, 3.0, 100.0],
        [2.0, 3.0, 4.0, 200.0],
        [3.0, 4.0, 5.0, 300.0],
    ]
)

INDUSTRIES = {"600519.SH": "food", "000001.SZ": "bank", "000002.SZ": "bank", "600000.SH": "bank"}


@pytest.fixture
def context() -> PipelineContext:
    return PipelineContext(industries=INDUSTRIES)


@pytest.fixture
def executor() -> PipelineExecutor:
    return PipelineExecutor()


def step(order: int, operation: str, target: str, **params: float) -> PreprocessingStep:
    return PreprocessingStep(
        order=order,
        operation=PreprocessingOperation(operation),
        target=PreprocessingTarget(target),
        parameters=params,
    )


# --------------------------------------------------------------------------- order


def test_steps_run_in_declared_order_not_list_order(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    pipeline = PreprocessingPipeline(
        steps=[
            step(2, "zscore", "factor"),
            step(1, "winsorize", "factor", lower=0.25, upper=0.75),
        ]
    )
    _, _, trace = executor.apply(pipeline, {}, RAW, context)
    assert [entry.operation for entry in trace] == ["winsorize", "zscore"]


def test_order_changes_the_result(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    """The reason these are not AST operators."""
    neutralize_first = executor.apply(
        PreprocessingPipeline(
            steps=[
                step(1, "industry_neutralize", "factor"),
                step(2, "zscore", "factor"),
            ]
        ),
        {},
        RAW,
        context,
    )[1]
    standardize_first = executor.apply(
        PreprocessingPipeline(
            steps=[
                step(1, "zscore", "factor"),
                step(2, "industry_neutralize", "factor"),
            ]
        ),
        {},
        RAW,
        context,
    )[1]
    assert not np.allclose(
        neutralize_first.to_numpy(), standardize_first.to_numpy(), equal_nan=True
    )


def test_the_executed_sequence_is_recorded(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    """Without a record, a double standardisation leaves no evidence."""
    pipeline = PreprocessingPipeline(
        steps=[step(1, "winsorize", "variables"), step(2, "zscore", "factor")]
    )
    _, _, trace = executor.apply(pipeline, {"close": RAW}, RAW, context)
    assert [(e.operation, e.target) for e in trace] == [
        ("winsorize", "variables"),
        ("zscore", "factor"),
    ]


# --------------------------------------------------------------------------- targets


def test_variable_steps_apply_before_the_formula_and_factor_steps_after(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    pipeline = PreprocessingPipeline(
        steps=[step(1, "winsorize", "variables", lower=0.25, upper=0.75)]
    )
    processed, factor, _ = executor.apply(pipeline, {"close": RAW}, RAW, context)
    assert processed["close"].max().max() < RAW.max().max()
    # The factor was not touched by a variables-targeted step.
    assert factor.equals(RAW)


def test_a_factor_step_does_not_touch_the_variables(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    pipeline = PreprocessingPipeline(steps=[step(1, "zscore", "factor")])
    processed, factor, _ = executor.apply(pipeline, {"close": RAW}, RAW, context)
    assert processed["close"].equals(RAW)
    assert not factor.equals(RAW)


def test_an_empty_pipeline_is_a_no_op(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    processed, factor, trace = executor.apply(
        PreprocessingPipeline(), {"close": RAW}, RAW, context
    )
    assert processed["close"].equals(RAW)
    assert factor.equals(RAW)
    assert trace == []


# --------------------------------------------------------------------------- operations


def test_winsorize_clips_the_cross_section_each_day(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    """600000.SH is an outlier every day; it must be pulled in, not dropped."""
    pipeline = PreprocessingPipeline(
        steps=[step(1, "winsorize", "factor", lower=0.25, upper=0.75)]
    )
    _, factor, _ = executor.apply(pipeline, {}, RAW, context)
    assert factor.loc[DATES[0], "600000.SH"] < 100.0
    assert factor.notna().all().all()


def test_zscore_centres_each_day_on_zero(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    pipeline = PreprocessingPipeline(steps=[step(1, "zscore", "factor")])
    _, factor, _ = executor.apply(pipeline, {}, RAW, context)
    assert factor.loc[DATES[0]].mean() == pytest.approx(0.0, abs=1e-12)


def test_industry_neutralize_removes_the_industry_mean(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    pipeline = PreprocessingPipeline(steps=[step(1, "industry_neutralize", "factor")])
    _, factor, _ = executor.apply(pipeline, {}, RAW, context)
    banks = ["000001.SZ", "000002.SZ", "600000.SH"]
    assert factor.loc[DATES[0], banks].mean() == pytest.approx(0.0, abs=1e-12)


def test_industry_neutralize_without_a_mapping_is_refused(
    executor: PipelineExecutor,
) -> None:
    """Silently skipping it would leave the factor loaded on industry."""
    pipeline = PreprocessingPipeline(steps=[step(1, "industry_neutralize", "factor")])
    with pytest.raises(ValueError, match="industry"):
        executor.apply(pipeline, {}, RAW, PipelineContext(industries={}))


def test_fillna_fills_only_missing_values(
    executor: PipelineExecutor, context: PipelineContext
) -> None:
    holed = RAW.copy()
    holed.iloc[0, 0] = np.nan
    pipeline = PreprocessingPipeline(steps=[step(1, "fillna", "factor", value=0.0)])
    _, factor, _ = executor.apply(pipeline, {}, holed, context)
    assert factor.iloc[0, 0] == 0.0
    assert factor.iloc[1, 0] == holed.iloc[1, 0]
