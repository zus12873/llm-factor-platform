"""Preprocessing: an ordered pipeline, kept out of the formula AST.

Winsorising, standardising and industry-neutralising used to be expressible in
two places at once — as AST operators and as boolean switches — which made
"apply once or twice?" unanswerable from the contract alone. Worse, booleans
cannot express order, and order matters: neutralise-then-standardise is a
different factor from standardise-then-neutralise.

So transforms live here, in a pipeline with an explicit ``order`` and an
explicit ``target``, and the AST is left to express only the factor's
mathematics. :class:`DataRules` keeps the universe/data-selection switches that
were always orthogonal to the transforms.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class PreprocessingOperation(StrEnum):
    WINSORIZE = "winsorize"
    ZSCORE = "zscore"
    INDUSTRY_NEUTRALIZE = "industry_neutralize"
    FILLNA = "fillna"


class PreprocessingTarget(StrEnum):
    """What a step operates on.

    ``variables`` runs before the formula, once per raw input; ``factor`` runs
    after the formula, on its single output. This distinction is precisely what
    a boolean switch could not express.
    """

    VARIABLES = "variables"
    FACTOR = "factor"


class PreprocessingStep(BaseModel):
    schema_version: int = 1
    order: int
    operation: PreprocessingOperation
    target: PreprocessingTarget
    method: str | None = None
    parameters: dict[str, float | int | str] = Field(default_factory=dict)


class PreprocessingPipeline(BaseModel):
    """An ordered, duplicate-free sequence of transforms."""

    schema_version: int = 1
    version: int = 1
    steps: list[PreprocessingStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_unique_order_and_no_duplicates(self) -> PreprocessingPipeline:
        orders = [step.order for step in self.steps]
        repeated_order = [value for value, count in Counter(orders).items() if count > 1]
        if repeated_order:
            raise ValueError(f"duplicate step order: {sorted(repeated_order)}")

        pairs = [(step.operation, step.target) for step in self.steps]
        repeated_pair = [pair for pair, count in Counter(pairs).items() if count > 1]
        if repeated_pair:
            rendered = ", ".join(f"{op.value} on {target.value}" for op, target in repeated_pair)
            raise ValueError(f"duplicate operation: {rendered}")
        return self

    def ordered_steps(self) -> list[PreprocessingStep]:
        return sorted(self.steps, key=lambda step: step.order)

    def steps_for(self, target: PreprocessingTarget | str) -> list[PreprocessingStep]:
        wanted = PreprocessingTarget(target)
        return [step for step in self.ordered_steps() if step.target is wanted]


class DataRules(BaseModel):
    """Universe and data-selection rules.

    These were never transforms and are unaffected by ordering, so they stay as
    plain switches. The transform switches that used to live alongside them now
    belong to :class:`PreprocessingPipeline`.
    """

    schema_version: int = 1
    use_adjusted_price: bool = True
    exclude_st: bool = True
    exclude_suspended: bool = True
    min_listed_days: int = 0


__all__ = [
    "DataRules",
    "PreprocessingOperation",
    "PreprocessingPipeline",
    "PreprocessingStep",
    "PreprocessingTarget",
]
