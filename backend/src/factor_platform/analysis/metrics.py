"""Factor evaluation: IC, Rank IC, quantile returns and turnover.

The alignment rule is the whole thing. A factor observed on day T predicts the
return *from* day T+1 — using the return that ends on T would score the factor
against a period it already knew about, and the result is a spectacular IC that
means nothing. Every metric here is computed after that shift, and the shift is
applied once, here, rather than left to each caller.

Two smaller rules that follow from the same concern:

* **Thin cross-sections are dropped, not scored.** An IC computed on three
  securities is noise with a decimal point, and averaging it into the series
  makes the whole series unreliable in a way nothing later can detect.
* **Turnover counts membership changes, not weight changes.** For an
  equal-weighted quantile portfolio, what costs money is a name entering or
  leaving — and that is what a rebalancing researcher is trying to size.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError

#: Below this many securities, a cross-sectional statistic is noise.
MIN_CROSS_SECTION: Final = 5


class AnalysisError(DomainError):
    """Raised when a factor cannot be evaluated as given."""


class SeriesSummary(BaseModel):
    """A metric series reduced to what a researcher reads first."""

    mean: float
    std: float
    positive_rate: float
    count: int

    @property
    def information_ratio(self) -> float:
        """Mean over standard deviation — an IC of 0.05 with std 0.30 is not a signal."""
        return self.mean / self.std if self.std else 0.0


class QuantileReturns(BaseModel):
    """Mean forward return per quantile, lowest factor value first."""

    means: list[float] = Field(default_factory=list)
    long_short_spread: float = 0.0
    monotonic: bool = False


class AnalysisResult(BaseModel):
    ic: SeriesSummary
    rank_ic: SeriesSummary
    quantiles: QuantileReturns
    turnover_mean: float = 0.0
    coverage_mean: float = 0.0
    evaluated_dates: int = 0
    skipped_dates: int = 0
    #: Dates dropped for a thin cross-section, so the shortfall is visible rather
    #: than silently reducing the sample.
    skipped_reason: str = ""


def analyze_factor(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    *,
    groups: int = 5,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> AnalysisResult:
    """Evaluate ``factor`` against ``forward_returns``.

    ``forward_returns`` is indexed by the date the return *starts*, so it is
    shifted back by one period to line up with the factor observation that
    predicted it. Getting this backwards produces an excellent IC and a useless
    factor.
    """
    if factor.empty or forward_returns.empty:
        raise AnalysisError("factor or forward returns are empty")

    aligned_factor, aligned_returns = _align(factor, forward_returns)
    if aligned_factor.empty:
        raise AnalysisError(
            "factor and forward returns share no dates after alignment; "
            "check that the return series starts one period after the factor"
        )

    ic_values: list[float] = []
    rank_ic_values: list[float] = []
    coverage: list[float] = []
    quantile_rows: list[list[float]] = []
    memberships: list[set[str]] = []
    skipped = 0

    for date in aligned_factor.index:
        values = aligned_factor.loc[date]
        returns = aligned_returns.loc[date]
        usable = values.notna() & returns.notna()

        if int(usable.sum()) < min_cross_section:
            # Scoring this date would inject noise that later averaging cannot
            # distinguish from signal.
            skipped += 1
            continue

        day_factor = values[usable]
        day_returns = returns[usable]
        coverage.append(float(usable.sum()) / len(values))

        ic_values.append(_safe_corr(day_factor, day_returns))
        rank_ic_values.append(_safe_corr(day_factor.rank(), day_returns.rank()))

        buckets = _quantile_means(day_factor, day_returns, groups)
        if buckets is not None:
            quantile_rows.append(buckets)
            memberships.append(_top_bucket(day_factor, groups))

    if not ic_values:
        raise AnalysisError(
            f"no date had at least {min_cross_section} usable securities; "
            "the factor cannot be evaluated on this sample"
        )

    means = (
        [float(np.nanmean([row[i] for row in quantile_rows])) for i in range(groups)]
        if quantile_rows
        else []
    )

    return AnalysisResult(
        ic=_summarize(ic_values),
        rank_ic=_summarize(rank_ic_values),
        quantiles=QuantileReturns(
            means=means,
            long_short_spread=(means[-1] - means[0]) if means else 0.0,
            monotonic=_is_monotonic(means),
        ),
        turnover_mean=_turnover(memberships),
        coverage_mean=float(np.mean(coverage)) if coverage else 0.0,
        evaluated_dates=len(ic_values),
        skipped_dates=skipped,
        skipped_reason=(
            f"{skipped} 个交易日的有效证券数少于 {min_cross_section}，未计入"
            if skipped
            else ""
        ),
    )


def _align(
    factor: pd.DataFrame, forward_returns: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Line the return that *starts* at T+1 up with the factor observed at T.

    Done here, once. Leaving it to callers means every caller has a chance to get
    it backwards, and the wrong direction looks like an excellent factor.
    """
    shifted = forward_returns.shift(-1)
    dates = factor.index.intersection(shifted.index)
    codes = factor.columns.intersection(shifted.columns)
    return factor.loc[dates, codes], shifted.loc[dates, codes]


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    """Correlation, with a constant series treated as no information."""
    if left.nunique() <= 1 or right.nunique() <= 1:
        return 0.0
    value = float(left.corr(right))
    return 0.0 if np.isnan(value) else value


def _quantile_means(
    values: pd.Series, returns: pd.Series, groups: int
) -> list[float] | None:
    """Mean forward return per quantile bucket, or ``None`` if unsplittable."""
    if values.nunique() < groups:
        return None
    try:
        labels = pd.qcut(values.rank(method="first"), groups, labels=False)
    except ValueError:
        return None
    return [
        float(returns[labels == bucket].mean()) if (labels == bucket).any() else float("nan")
        for bucket in range(groups)
    ]


def _top_bucket(values: pd.Series, groups: int) -> set[str]:
    if values.nunique() < groups:
        return set()
    try:
        labels = pd.qcut(values.rank(method="first"), groups, labels=False)
    except ValueError:
        return set()
    return set(values.index[labels == groups - 1])


def _turnover(memberships: list[set[str]]) -> float:
    """One-way membership turnover of the top bucket.

    Counts names entering and leaving rather than weight drift: in an
    equal-weighted portfolio the trade happens when membership changes, and that
    is the cost a researcher is trying to size.
    """
    if len(memberships) < 2:
        return 0.0
    rates: list[float] = []
    for previous, current in zip(memberships, memberships[1:], strict=False):
        if not previous:
            continue
        rates.append(len(current - previous) / len(previous))
    return float(np.mean(rates)) if rates else 0.0


def _summarize(values: list[float]) -> SeriesSummary:
    array = np.asarray(values, dtype=float)
    return SeriesSummary(
        mean=float(np.mean(array)),
        std=float(np.std(array, ddof=1)) if len(array) > 1 else 0.0,
        positive_rate=float((array > 0).mean()),
        count=len(array),
    )


def _is_monotonic(means: list[float]) -> bool:
    """Whether quantile returns increase across buckets.

    Reported rather than judged: a non-monotonic factor may still be useful, and
    calling it a failure here would be a research opinion this layer has no
    standing to hold.
    """
    if len(means) < 2:
        return False
    clean = [value for value in means if not np.isnan(value)]
    return len(clean) == len(means) and all(
        later >= earlier for earlier, later in zip(clean, clean[1:], strict=False)
    )


__all__ = [
    "MIN_CROSS_SECTION",
    "AnalysisError",
    "AnalysisResult",
    "QuantileReturns",
    "SeriesSummary",
    "analyze_factor",
]
