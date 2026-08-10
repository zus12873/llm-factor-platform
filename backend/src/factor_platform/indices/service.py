"""Linear multifactor composites and index weight generation.

Three rules, each preventing a composite that looks reasonable and is not.

**Directions are normalised before weighting, not after.** PE is
lower-is-better and ROE is higher-is-better. Averaging their raw values sums a
signal with its own negation and produces a composite that cancels out — while
still ranking, still charting, still looking like a factor.

**Each factor is standardised before it is weighted.** Un-standardised, a factor
measured in tens of thousands of yuan dominates one measured as a ratio no matter
what weight the researcher assigned. The weights would be decorative and the
researcher would have no way to see it.

**Inputs must be published library versions.** A composite built from a session's
working state cannot be reproduced — the session moves on, its artifacts are
swept, and the composite becomes a number with no derivation.

This produces weights and a CSV. It places no orders and models no costs; calling
the output a portfolio would overstate what it is.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError
from factor_platform.factor.metric_registry import MetricRegistry, ReviewStatus

#: Direction multipliers. Applied before weighting; see the module docstring.
HIGHER_IS_BETTER: Final = 1
LOWER_IS_BETTER: Final = -1


class CompositeError(DomainError):
    """Raised when a composite or index cannot be built as specified."""


class Weighting(StrEnum):
    EQUAL = "equal"
    FACTOR_SCORE = "factor_score"


class Rebalance(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class IndexRule(BaseModel):
    top_n: int = Field(default=50, ge=1)
    weighting: Weighting = Weighting.EQUAL
    rebalance: Rebalance = Rebalance.MONTHLY


class IndexArtifact(BaseModel):
    """Rebalance-date weights, plus what the caller must not assume."""

    rule: IndexRule
    rebalance_dates: list[str] = Field(default_factory=list)
    #: ``date, code, weight`` rows.
    rows: list[dict[str, object]] = Field(default_factory=list)
    review_status: str = ReviewStatus.UNREVIEWED.value
    review_note: str = ""
    #: Stated in the artifact, not only the docs: a reader with the CSV alone
    #: must not mistake these weights for an executable portfolio.
    limitation_note: str = (
        "本产物仅为调仓日权重，未计交易成本、冲击成本、涨跌停与停牌限制，"
        "不构成可直接下单的组合。"
    )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)

    def to_csv(self) -> str:
        return self.to_frame().to_csv(index=False)


def combine_factors(
    factors: list[pd.DataFrame],
    weights: list[float],
    directions: list[int],
) -> pd.DataFrame:
    """Combine factors into one score.

    Order matters and is fixed here: align, apply direction, standardise, then
    weight. Standardising after weighting would make the weights meaningless, and
    applying direction after standardising would standardise a mixed-sign series.
    """
    if not factors:
        raise CompositeError("no factors supplied")
    if not (len(factors) == len(weights) == len(directions)):
        raise CompositeError(
            f"factors ({len(factors)}), weights ({len(weights)}) and directions "
            f"({len(directions)}) must have the same length"
        )
    if any(direction not in (HIGHER_IS_BETTER, LOWER_IS_BETTER) for direction in directions):
        raise CompositeError("direction must be +1 (higher is better) or -1 (lower is better)")

    total = sum(weights)
    if not np.isfinite(total) or total <= 0:
        raise CompositeError(f"weights must be finite and sum above zero, got {weights}")
    if any(not np.isfinite(weight) for weight in weights):
        raise CompositeError("weights must all be finite")

    dates = factors[0].index
    codes = factors[0].columns
    for frame in factors[1:]:
        dates = dates.intersection(frame.index)
        codes = codes.intersection(frame.columns)
    if len(dates) == 0 or len(codes) == 0:
        raise CompositeError("factors share no dates or no securities after alignment")

    combined = pd.DataFrame(0.0, index=dates, columns=codes)
    for frame, weight, direction in zip(factors, weights, directions, strict=True):
        aligned = frame.loc[dates, codes] * direction
        combined = combined + _zscore(aligned) * (weight / total)
    return combined


def generate_index(
    composite: pd.DataFrame,
    rule: IndexRule,
    *,
    metric_keys: list[str] | None = None,
    registry: MetricRegistry | None = None,
) -> IndexArtifact:
    """Select the top names per rebalance date and assign weights summing to one."""
    if composite.empty:
        raise CompositeError("composite is empty")

    resolved_registry = registry or MetricRegistry.load()
    review_status, review_note = _review_gate(metric_keys or [], resolved_registry)

    rows: list[dict[str, object]] = []
    dates: list[str] = []

    for date in _rebalance_dates(composite.index, rule.rebalance):
        # `.loc[date]` is typed as Series | DataFrame; a duplicated index would
        # give a frame, and silently taking the first row would pick an arbitrary
        # cross-section.
        row = composite.loc[date]
        if isinstance(row, pd.DataFrame):
            raise CompositeError(
                f"composite has duplicate rows for {date}; the cross-section is ambiguous"
            )
        scores: pd.Series = row.dropna()
        if scores.empty:
            continue
        selected = scores.nlargest(min(rule.top_n, len(scores)))
        weights = _weights_for(selected, rule.weighting)
        if weights is None:
            continue

        dates.append(str(pd.Timestamp(date).date()))
        for code, weight in weights.items():
            rows.append(
                {
                    "date": str(pd.Timestamp(date).date()),
                    "code": code,
                    "weight": float(weight),
                }
            )

    if not rows:
        raise CompositeError("no rebalance date produced a usable selection")

    return IndexArtifact(
        rule=rule,
        rebalance_dates=dates,
        rows=rows,
        review_status=review_status,
        review_note=review_note,
    )


def _review_gate(metric_keys: list[str], registry: MetricRegistry) -> tuple[str, str]:
    """A disputed metric cannot reach an index, and unreviewed ones are labelled.

    An index is the furthest a number travels from the person who defined it, so
    the gate is strictest here.
    """
    notes: list[str] = []
    status = ReviewStatus.REVIEWED.value

    for key in metric_keys:
        verdict = registry.gate(key)
        if not verdict.allowed:
            raise CompositeError(f"指数引用了不可用的口径 {key}：{verdict.reason}")
        definition = registry.get(key)
        if definition and definition.review_status is ReviewStatus.UNREVIEWED:
            status = ReviewStatus.UNREVIEWED.value
            notes.append(f"{key} 未复核")

    if not metric_keys:
        status = ReviewStatus.UNREVIEWED.value
        notes.append("未声明口径，视为未复核")

    return status, "；".join(notes)


def _zscore(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardise each date's cross-section.

    A zero-variance day becomes zeros rather than infinities: an infinite score
    survives ranking and takes the whole date with it.
    """
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mean, axis=0).div(std, axis=0).fillna(0.0)


def _rebalance_dates(index: pd.Index, rebalance: Rebalance) -> list[pd.Timestamp]:
    """Last available date in each period — the day the decision is actually made."""
    stamps = pd.DatetimeIndex(index)
    if len(stamps) == 0:
        return []
    rule = "W" if rebalance is Rebalance.WEEKLY else "ME"
    grouped = pd.Series(stamps, index=stamps).resample(rule).max().dropna()
    return list(grouped)


def _weights_for(selected: pd.Series, weighting: Weighting) -> pd.Series | None:
    if weighting is Weighting.EQUAL:
        return pd.Series(1.0 / len(selected), index=selected.index)

    # Factor-score weighting needs positive scores; a z-scored composite is
    # centred on zero, so it is shifted to be strictly positive first. Using raw
    # scores would give negative weights, which is a short position nobody asked
    # for.
    shifted = selected - selected.min() + 1e-9
    total = float(shifted.sum())
    if not np.isfinite(total) or total <= 0:
        return None
    return shifted / total


__all__ = [
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
    "CompositeError",
    "IndexArtifact",
    "IndexRule",
    "Rebalance",
    "Weighting",
    "combine_factors",
    "generate_index",
]
