"""Tests for composites and index weights.

The first test is the one that matters. PE is lower-is-better, ROE is
higher-is-better; averaging their raw values sums a signal with its own negation.
The composite still ranks, still charts, still looks like a factor — and carries
no information. Nothing downstream detects that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_platform.indices.service import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    CompositeError,
    IndexRule,
    Rebalance,
    Weighting,
    combine_factors,
    generate_index,
)

DATES = pd.date_range("2024-01-01", periods=60, freq="B")
CODES = ["A", "B", "C", "D", "E", "F"]


def frame(values: list[list[float]] | None = None, *, scale: float = 1.0) -> pd.DataFrame:
    if values is None:
        rng = np.random.default_rng(7)
        values = (rng.normal(0, 1, size=(len(DATES), len(CODES))) * scale).tolist()
    return pd.DataFrame(values, index=DATES[: len(values)], columns=CODES)


def ascending() -> pd.DataFrame:
    """A ranks worst, F ranks best."""
    return frame([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0] for _ in DATES])


def descending() -> pd.DataFrame:
    """A ranks best when the direction says lower is better."""
    return frame([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0][::-1] for _ in DATES])


# --------------------------------------------------------------------------- direction


def test_direction_is_normalized_before_weighting() -> None:
    """Otherwise the two factors cancel and the composite means nothing."""
    quality = ascending()  # higher is better
    valuation = ascending()  # lower is better — same raw values, opposite meaning

    combined = combine_factors(
        [quality, valuation], [0.5, 0.5], [HIGHER_IS_BETTER, LOWER_IS_BETTER]
    )
    # With directions applied the two cancel exactly, which is the correct answer
    # for identical-but-opposite inputs.
    assert combined.iloc[0].abs().max() == pytest.approx(0.0, abs=1e-9)


def test_two_agreeing_factors_reinforce() -> None:
    combined = combine_factors(
        [ascending(), descending()], [0.5, 0.5], [HIGHER_IS_BETTER, LOWER_IS_BETTER]
    )
    assert combined.loc[DATES[0], "F"] > combined.loc[DATES[0], "A"]


def test_an_invalid_direction_is_refused() -> None:
    with pytest.raises(CompositeError, match="direction"):
        combine_factors([ascending()], [1.0], [0])


# --------------------------------------------------------------------------- scaling


def test_a_large_scale_factor_does_not_dominate_its_weight() -> None:
    """Un-standardised, yuan would swamp a ratio no matter what weight was set."""
    huge = ascending() * 1_000_000
    small = descending()

    combined = combine_factors(
        [huge, small], [0.01, 0.99], [HIGHER_IS_BETTER, HIGHER_IS_BETTER]
    )
    # The 0.99-weighted factor decides the ordering, as the researcher intended.
    assert combined.loc[DATES[0], "A"] > combined.loc[DATES[0], "F"]


def test_a_zero_variance_day_does_not_produce_infinities() -> None:
    flat = frame([[5.0] * len(CODES) for _ in DATES])
    combined = combine_factors([flat], [1.0], [HIGHER_IS_BETTER])
    assert np.isfinite(combined.to_numpy()).all()


# --------------------------------------------------------------------------- refusals


def test_mismatched_lengths_are_refused() -> None:
    with pytest.raises(CompositeError, match="same length"):
        combine_factors([ascending(), descending()], [1.0], [HIGHER_IS_BETTER])


def test_non_finite_weights_are_refused() -> None:
    with pytest.raises(CompositeError, match="finite"):
        combine_factors([ascending()], [float("inf")], [HIGHER_IS_BETTER])


def test_weights_summing_to_zero_are_refused() -> None:
    with pytest.raises(CompositeError, match="sum above zero"):
        combine_factors(
            [ascending(), descending()], [1.0, -1.0], [HIGHER_IS_BETTER, HIGHER_IS_BETTER]
        )


def test_factors_with_no_common_dates_are_refused() -> None:
    elsewhere = pd.DataFrame(
        [[1.0] * len(CODES)],
        index=pd.date_range("2030-01-01", periods=1, freq="B"),
        columns=CODES,
    )
    with pytest.raises(CompositeError, match="share no dates"):
        combine_factors([ascending(), elsewhere], [0.5, 0.5], [1, 1])


# --------------------------------------------------------------------------- index


def composite() -> pd.DataFrame:
    return combine_factors([ascending()], [1.0], [HIGHER_IS_BETTER])


def test_weights_sum_to_one_per_rebalance_date() -> None:
    artifact = generate_index(
        composite(), IndexRule(top_n=3, weighting=Weighting.EQUAL)
    )
    sums = artifact.to_frame().groupby("date")["weight"].sum()
    assert np.allclose(sums.to_numpy(), 1.0)


def test_top_n_limits_the_selection() -> None:
    artifact = generate_index(composite(), IndexRule(top_n=2))
    counts = artifact.to_frame().groupby("date")["code"].count()
    assert (counts == 2).all()


def test_monthly_and_weekly_rebalances_differ_in_frequency() -> None:
    monthly = generate_index(composite(), IndexRule(top_n=2, rebalance=Rebalance.MONTHLY))
    weekly = generate_index(composite(), IndexRule(top_n=2, rebalance=Rebalance.WEEKLY))
    assert len(weekly.rebalance_dates) > len(monthly.rebalance_dates)


def test_factor_score_weighting_never_produces_a_negative_weight() -> None:
    """A z-scored composite is centred on zero; raw scores would short the bottom."""
    artifact = generate_index(
        composite(), IndexRule(top_n=4, weighting=Weighting.FACTOR_SCORE)
    )
    assert (artifact.to_frame()["weight"] >= 0).all()


def test_the_artifact_states_what_it_is_not() -> None:
    """A reader holding only the CSV must not mistake it for a tradeable book."""
    artifact = generate_index(composite(), IndexRule(top_n=2))
    assert "不构成可直接下单的组合" in artifact.limitation_note


def test_the_csv_export_carries_date_code_and_weight() -> None:
    csv = generate_index(composite(), IndexRule(top_n=2)).to_csv()
    assert csv.splitlines()[0] == "date,code,weight"


# --------------------------------------------------------------------------- review gate


def test_a_disputed_metric_cannot_reach_an_index() -> None:
    """An index is the furthest a number travels from whoever defined it."""
    with pytest.raises(CompositeError, match="FLOAT_MV"):
        generate_index(composite(), IndexRule(top_n=2), metric_keys=["FLOAT_MV"])


def test_an_unreviewed_metric_labels_the_index() -> None:
    artifact = generate_index(
        composite(), IndexRule(top_n=2), metric_keys=["ROE_TTM"]
    )
    assert artifact.review_status == "unreviewed"
    assert "ROE_TTM" in artifact.review_note


def test_declaring_no_metric_is_treated_as_unreviewed() -> None:
    artifact = generate_index(composite(), IndexRule(top_n=2))
    assert artifact.review_status == "unreviewed"
