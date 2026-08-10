"""Tests for the three validation layers.

Every check here targets something that runs cleanly and produces a plausible
number — that is the selection criterion. Anything that raises on its own needs no
validator.

The severity split is the substance. Blocking the wrong things trains people to
override the gate; warning about the right things lets a unit error reach a
published factor. So each test pins not just detection but level.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_platform.domain.models import FactorSpec
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.validation.data import DataValidator
from factor_platform.validation.formula import FormulaValidator
from factor_platform.validation.result import ResultValidator

DATES = pd.date_range("2024-01-01", periods=6, freq="B")
CODES = ["600519.SH", "000001.SZ", "000002.SZ", "600000.SH", "601318.SH"]


def frame(values: list[list[float]] | None = None) -> pd.DataFrame:
    if values is None:
        rng = np.random.default_rng(11)
        values = (rng.normal(size=(len(DATES), len(CODES))) * 10 + 100).tolist()
    return pd.DataFrame(values, index=DATES, columns=CODES)


def spec(**overrides: object) -> FactorSpec:
    payload: dict = {
        "factor_name": "quality",
        "asset_type": "stock",
        "universe": "000300.SH",
        "frequency": "daily",
        "direction": "higher_is_better",
        "formula_ast": {
            "type": "call",
            "op": "rank",
            "args": [{"type": "variable", "name": "roe_ttm"}],
        },
        "variables": [{"logical_name": "roe_ttm", "meaning": "ROE"}],
    }
    payload.update(overrides)
    return FactorSpec.model_validate(payload)


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return MetricRegistry.load()


# --------------------------------------------------------------------------- data layer


def test_duplicate_keys_are_blocking() -> None:
    """Every cross-sectional operator would double-count the repeated security."""
    duplicated = frame()
    duplicated.columns = ["600519.SH", "600519.SH", *CODES[2:]]
    report = DataValidator().validate({"close": duplicated})
    assert report.has_error("duplicate_key")


def test_an_empty_sample_warns_rather_than_blocks() -> None:
    """The field may be perfectly valid; the window is the user's decision."""
    report = DataValidator().validate({"close": pd.DataFrame()})
    assert report.has_warning("empty_sample")
    assert not report.has_error("empty_sample")


def test_a_constant_input_is_blocking() -> None:
    report = DataValidator().validate({"close": frame([[1.0] * 5] * 6)})
    assert report.has_error("constant_input")


def test_a_sparse_input_warns() -> None:
    holed = frame()
    holed.iloc[:5] = np.nan
    assert DataValidator().validate({"close": holed}).has_warning("sparse_data")


def test_a_narrow_cross_section_warns() -> None:
    narrow = frame().iloc[:, :2]
    assert DataValidator().validate({"close": narrow}).has_warning("narrow_cross_section")


def test_partial_coverage_warns_rather_than_shrinking_silently() -> None:
    report = DataValidator().validate(
        {"close": frame()}, expected_start="2023-01-01", expected_end="2024-12-31"
    )
    assert report.has_warning("partial_coverage")


def test_no_input_at_all_is_blocking() -> None:
    assert DataValidator().validate({}).has_error("no_input_data")


# --------------------------------------------------------------------------- formula layer


def test_a_signal_traded_before_it_is_available_is_blocking() -> None:
    """The query layer can be perfectly point-in-time and this still be wrong."""
    subject = spec()
    subject.time_convention = subject.time_convention.model_construct(
        signal_date="T",
        trade_date="T",
        information_available_time=subject.time_convention.information_available_time,
        observation_time=subject.time_convention.observation_time,
        execution_price=subject.time_convention.execution_price,
        forward_return_start="T+1_OPEN",
        forward_return_end="T+N_OPEN",
        announcement_timing_policy=subject.time_convention.announcement_timing_policy,
    )
    report = FormulaValidator().validate(subject)
    assert report.has_error("signal_traded_before_available")


def test_a_conventional_signal_and_trade_pair_passes() -> None:
    assert not FormulaValidator().validate(spec()).has_error(
        "signal_traded_before_available"
    )


def test_point_in_time_data_without_an_announcement_date_is_blocking() -> None:
    subject = spec(
        variables=[
            {
                "logical_name": "roe_ttm",
                "meaning": "ROE",
                "point_in_time_required": True,
                "announcement_date_required": False,
            }
        ]
    )
    assert FormulaValidator().validate(subject).has_error("future_financial_data")


def test_duplicate_standardization_in_the_executed_trace_is_blocking() -> None:
    """Compared against what ran, not what was declared."""
    trace = [
        {"order": 1, "operation": "zscore", "target": "factor"},
        {"order": 2, "operation": "zscore", "target": "factor"},
    ]
    report = FormulaValidator().validate(spec(), pipeline_trace=trace)
    assert report.has_error("duplicate_standardization")


def test_standardizing_variables_and_factor_separately_is_not_duplicate() -> None:
    """Different targets are different operations, not a repeat."""
    trace = [
        {"order": 1, "operation": "zscore", "target": "variables"},
        {"order": 2, "operation": "zscore", "target": "factor"},
    ]
    assert not FormulaValidator().validate(spec(), pipeline_trace=trace).has_error(
        "duplicate_standardization"
    )


def test_a_missing_direction_is_blocking() -> None:
    assert FormulaValidator().validate(spec(direction=None)).has_error("direction_unset")


def test_a_degenerate_window_warns() -> None:
    subject = spec(
        formula_ast={
            "type": "call",
            "op": "rolling_mean",
            "args": [{"type": "variable", "name": "roe_ttm"}],
            "params": {"window": 1},
        }
    )
    assert FormulaValidator().validate(subject).has_warning("degenerate_window")


# --------------------------------------------------------------------------- result layer


def test_a_result_outside_the_registered_range_is_blocking(
    registry: MetricRegistry,
) -> None:
    """An ROE of 3000% is obviously wrong to a person and fine to a float."""
    absurd = frame([[3000.0] * 5] * 6)
    report = ResultValidator(registry).validate(absurd, metric_keys=["ROE_TTM"])
    assert report.has_error("implausible_magnitude")


def test_a_result_inside_the_registered_range_passes(registry: MetricRegistry) -> None:
    sane = frame([[12.0, 15.0, 8.0, 20.0, 11.0]] * 6)
    report = ResultValidator(registry).validate(sane, metric_keys=["ROE_TTM"])
    assert not report.has_error("implausible_magnitude")


def test_an_unreviewed_metric_warns_so_the_label_travels(
    registry: MetricRegistry,
) -> None:
    sane = frame([[12.0, 15.0, 8.0, 20.0, 11.0]] * 6)
    report = ResultValidator(registry).validate(sane, metric_keys=["ROE_TTM"])
    assert report.has_warning("unreviewed_metric")


def test_a_constant_factor_warns(registry: MetricRegistry) -> None:
    report = ResultValidator(registry).validate(frame([[0.5] * 5] * 6))
    assert report.has_warning("constant_factor")


def test_an_all_null_result_is_blocking(registry: MetricRegistry) -> None:
    empty = frame()
    empty.iloc[:, :] = np.nan
    assert ResultValidator(registry).validate(empty).has_error("all_null_result")


def test_a_reference_disagreement_warns_rather_than_blocks(
    registry: MetricRegistry,
) -> None:
    """It may be this factor that is wrong, or the reference that is stale."""
    computed = frame([[10.0] * 5] * 6)
    reference = frame([[20.0] * 5] * 6)
    report = ResultValidator(registry).validate(computed, reference=reference)
    assert report.has_warning("reference_mismatch")
    assert not report.has_error("reference_mismatch")


def test_a_matching_reference_is_silent(registry: MetricRegistry) -> None:
    values = frame([[10.0] * 5] * 6)
    report = ResultValidator(registry).validate(values, reference=values.copy())
    assert not report.has_warning("reference_mismatch")
