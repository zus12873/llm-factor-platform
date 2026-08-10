"""Ordered preprocessing: the transforms a tree could not express.

Winsorising, standardising and industry-neutralising were AST operators until
Task 2.5 moved them here, for one reason: their order changes the answer.
Neutralising then standardising is a different factor from standardising then
neutralising, and a formula tree has no way to say which the user meant. The
design before that used boolean switches, which fixed the order in code where the
person confirming the factor could not see it.

So the pipeline is an explicit ordered list, and the executor does two things the
old design could not:

* runs strictly by declared ``order``, independent of list position;
* records the sequence it actually ran, which is what lets the formula validator
  notice that a factor was standardised twice.

Targets separate the two halves of the run. ``variables`` steps apply to each raw
input *before* the formula is evaluated; ``factor`` steps apply to the result
*after*. Winsorising an input and winsorising the output are different
operations, and conflating them is how a pipeline ends up clipping twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import pandas as pd

from factor_platform.domain.preprocessing import (
    PreprocessingOperation,
    PreprocessingPipeline,
    PreprocessingStep,
    PreprocessingTarget,
)

#: Default winsorisation quantiles when the step declares none.
_DEFAULT_LOWER: Final = 0.01
_DEFAULT_UPPER: Final = 0.99


@dataclass(frozen=True)
class PipelineContext:
    """Cross-sectional context the transforms need beyond the values themselves."""

    industries: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceEntry:
    """One executed step, as it will appear in the result metadata."""

    order: int
    operation: str
    target: str
    parameters: Mapping[str, Any] = field(default_factory=dict)


class PipelineExecutor:
    """Applies an ordered preprocessing pipeline to variables and factor."""

    def apply(
        self,
        pipeline: PreprocessingPipeline,
        variables: Mapping[str, pd.DataFrame],
        factor: pd.DataFrame,
        context: PipelineContext,
    ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, list[TraceEntry]]:
        """Return ``(processed_variables, processed_factor, trace)``.

        The trace is not diagnostic decoration: it is the only record of what was
        actually applied, and the validator compares it against the declared
        pipeline to catch a double standardisation.
        """
        processed = {name: frame.copy() for name, frame in variables.items()}
        result = factor.copy()
        trace: list[TraceEntry] = []

        for step in pipeline.ordered_steps():
            if step.target is PreprocessingTarget.VARIABLES:
                processed = {
                    name: self._run(step, frame, context)
                    for name, frame in processed.items()
                }
            else:
                result = self._run(step, result, context)
            trace.append(
                TraceEntry(
                    order=step.order,
                    operation=step.operation.value,
                    target=step.target.value,
                    parameters=dict(step.parameters),
                )
            )

        return processed, result, trace

    # ------------------------------------------------------------------ transforms

    def _run(
        self, step: PreprocessingStep, frame: pd.DataFrame, context: PipelineContext
    ) -> pd.DataFrame:
        if step.operation is PreprocessingOperation.WINSORIZE:
            return _winsorize(frame, step.parameters)
        if step.operation is PreprocessingOperation.ZSCORE:
            return _zscore(frame)
        if step.operation is PreprocessingOperation.INDUSTRY_NEUTRALIZE:
            return _industry_neutralize(frame, context)
        return _fillna(frame, step.parameters)


def _winsorize(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    """Clip each day's cross-section to its own quantiles.

    Clipping rather than dropping: an outlier is usually a real security with a
    real weight, and removing it changes the universe rather than the scale.
    """
    lower = float(parameters.get("lower", _DEFAULT_LOWER))
    upper = float(parameters.get("upper", _DEFAULT_UPPER))
    low = frame.quantile(lower, axis=1)
    high = frame.quantile(upper, axis=1)
    return frame.clip(lower=low, upper=high, axis=0)


def _zscore(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardise within each day, against that day's own cross-section."""
    mean = frame.mean(axis=1)
    std = frame.std(axis=1)
    # A zero-variance day would otherwise produce infinities that survive ranking.
    return frame.sub(mean, axis=0).div(std.replace(0.0, pd.NA), axis=0)


def _industry_neutralize(frame: pd.DataFrame, context: PipelineContext) -> pd.DataFrame:
    """Subtract each day's industry mean from every member of that industry.

    Refuses when the industry map is missing. Skipping silently would leave the
    factor loaded on industry while the spec says it was neutralised — a
    discrepancy nothing downstream re-derives.
    """
    if not context.industries:
        raise ValueError(
            "industry_neutralize requires an industry mapping; refusing to skip it "
            "silently, which would leave the factor loaded on industry"
        )
    missing = [code for code in frame.columns if code not in context.industries]
    if missing:
        raise ValueError(
            f"industry unknown for {len(missing)} securities "
            f"(e.g. {missing[:3]}); cannot neutralize a partial cross-section"
        )

    industries = pd.Series(
        [context.industries[code] for code in frame.columns], index=frame.columns
    )
    group_means = frame.T.groupby(industries).transform("mean").T
    return frame - group_means


def _fillna(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    return frame.fillna(float(parameters.get("value", 0.0)))


__all__ = ["PipelineContext", "PipelineExecutor", "TraceEntry"]
