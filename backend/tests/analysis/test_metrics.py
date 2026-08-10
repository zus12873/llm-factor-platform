"""Tests for factor evaluation metrics.

The first test is the important one. A factor whose ranking exactly matches the
*next* period's returns must score IC 1.0 — and if the alignment is off by one,
it scores near zero instead. That single test catches the mistake that would
otherwise make every subsequent number quietly wrong.

The reverse mistake is worse and is tested too: a factor aligned against the
return it already knew about scores beautifully and means nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_platform.analysis.metrics import (
    MIN_CROSS_SECTION,
    AnalysisError,
    analyze_factor,
)

DATES = pd.date_range("2024-01-01", periods=12, freq="B")
CODES = [f"{i:06d}.SZ" for i in range(10)]


def frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=DATES[: len(rows)], columns=CODES)


def perfect_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A factor that ranks exactly like the return starting the next day."""
    rng = np.random.default_rng(5)
    factor_rows: list[list[float]] = []
    return_rows: list[list[float]] = []
    for _ in DATES:
        scores = rng.permutation(len(CODES)).astype(float)
        factor_rows.append(scores.tolist())
        return_rows.append((scores * 0.001).tolist())

    factor = frame(factor_rows)
    # forward_returns is indexed by the day the return starts, so the row that
    # corresponds to factor[T] sits at T+1.
    forward = frame([[0.0] * len(CODES), *return_rows[:-1]])
    return factor, forward


# --------------------------------------------------------------------------- alignment


def test_a_perfect_factor_scores_unit_ic() -> None:
    factor, forward = perfect_pair()
    result = analyze_factor(factor, forward, groups=2)
    assert result.ic.mean == pytest.approx(1.0)
    assert result.rank_ic.mean == pytest.approx(1.0)


def test_a_factor_aligned_against_its_own_period_does_not_score() -> None:
    """The look-ahead version: scoring against the return it already knew.

    If this passed, the alignment would be backwards and every metric in the
    system would be flattering nonsense.
    """
    factor, forward = perfect_pair()
    misaligned = forward.shift(1).fillna(0.0)
    result = analyze_factor(factor, misaligned, groups=2)
    assert abs(result.ic.mean) < 0.9


def test_an_inverted_factor_scores_negative_ic() -> None:
    factor, forward = perfect_pair()
    result = analyze_factor(-factor, forward, groups=2)
    assert result.ic.mean == pytest.approx(-1.0)


# --------------------------------------------------------------------------- summaries


def test_the_summary_reports_dispersion_not_only_the_mean() -> None:
    """An IC of 0.05 with a standard deviation of 0.30 is not a signal."""
    factor, forward = perfect_pair()
    result = analyze_factor(factor, forward, groups=2)
    assert result.ic.count > 0
    assert result.ic.positive_rate == pytest.approx(1.0)


def test_quantile_returns_are_ordered_lowest_factor_first() -> None:
    factor, forward = perfect_pair()
    result = analyze_factor(factor, forward, groups=5)
    assert len(result.quantiles.means) == 5
    assert result.quantiles.means[0] < result.quantiles.means[-1]
    assert result.quantiles.long_short_spread > 0
    assert result.quantiles.monotonic is True


def test_coverage_is_reported_alongside_the_numbers() -> None:
    """A factor computed on 30% of the universe is a different object."""
    factor, forward = perfect_pair()
    holed = factor.copy()
    holed.iloc[:, :3] = np.nan
    result = analyze_factor(holed, forward, groups=2)
    assert result.coverage_mean == pytest.approx(0.7)


# --------------------------------------------------------------------------- turnover


def test_a_stable_factor_has_no_turnover() -> None:
    stable = frame([[float(i) for i in range(len(CODES))] for _ in DATES])
    rng = np.random.default_rng(2)
    forward = frame(rng.normal(0, 0.01, size=(len(DATES), len(CODES))).tolist())
    result = analyze_factor(stable, forward, groups=2)
    assert result.turnover_mean == pytest.approx(0.0)


def test_a_fully_rotating_factor_has_complete_turnover() -> None:
    """Membership changes are what cost money in an equal-weighted book."""
    ascending = [float(i) for i in range(len(CODES))]
    rows = [ascending if index % 2 == 0 else ascending[::-1] for index in range(len(DATES))]
    rng = np.random.default_rng(3)
    forward = frame(rng.normal(0, 0.01, size=(len(DATES), len(CODES))).tolist())
    result = analyze_factor(frame(rows), forward, groups=2)
    assert result.turnover_mean == pytest.approx(1.0)


# --------------------------------------------------------------------------- refusals


def test_thin_cross_sections_are_dropped_and_reported() -> None:
    """An IC on three securities is noise with a decimal point."""
    factor, forward = perfect_pair()
    narrow = factor.iloc[:, :3]
    narrow_forward = forward.iloc[:, :3]
    with pytest.raises(AnalysisError, match=str(MIN_CROSS_SECTION)):
        analyze_factor(narrow, narrow_forward, groups=2)


def test_the_last_date_is_skipped_because_it_has_no_forward_return() -> None:
    """Not a defect: there is no next period to predict, so it cannot be scored."""
    factor, forward = perfect_pair()
    result = analyze_factor(factor, forward, groups=2)
    assert result.skipped_dates == 1
    assert result.evaluated_dates == len(DATES) - 1


def test_partially_thin_dates_are_skipped_with_a_reason() -> None:
    factor, forward = perfect_pair()
    baseline = analyze_factor(factor, forward, groups=2).skipped_dates

    holed = factor.copy()
    holed.iloc[0, 2:] = np.nan  # first date keeps only two usable names
    result = analyze_factor(holed, forward, groups=2)

    assert result.skipped_dates == baseline + 1
    assert str(MIN_CROSS_SECTION) in result.skipped_reason


def test_an_empty_factor_is_refused() -> None:
    with pytest.raises(AnalysisError, match="empty"):
        analyze_factor(pd.DataFrame(), pd.DataFrame(), groups=2)


def test_no_overlapping_dates_is_refused_with_a_useful_message() -> None:
    factor, _ = perfect_pair()
    elsewhere = pd.DataFrame(
        [[0.01] * len(CODES)],
        index=pd.date_range("2030-01-01", periods=1, freq="B"),
        columns=CODES,
    )
    with pytest.raises(AnalysisError, match="no dates"):
        analyze_factor(factor, elsewhere, groups=2)


def test_a_constant_factor_scores_zero_rather_than_nan() -> None:
    """NaN would propagate into the average and poison the whole series."""
    constant = frame([[1.0] * len(CODES) for _ in DATES])
    rng = np.random.default_rng(4)
    forward = frame(rng.normal(0, 0.01, size=(len(DATES), len(CODES))).tolist())
    result = analyze_factor(constant, forward, groups=2)
    assert result.ic.mean == pytest.approx(0.0)
