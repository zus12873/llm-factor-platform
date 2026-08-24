from __future__ import annotations

import pandas as pd

from factor_platform.domain.models import FieldSelection, FieldTimeRole
from factor_platform.execution.real_wind import (
    _wide_report_period,
    apply_confirmed_announcement_requirements,
    complete_registered_selections,
)
from factor_platform.factor.metric_registry import MetricRegistry


def test_registered_financial_selection_gets_point_in_time_roles() -> None:
    selection = FieldSelection(
        logical_name="roe_ttm",
        table="asharettmhis",
        field="s_fa_roe_ttm",
    )
    completed = complete_registered_selections([selection], MetricRegistry.load())[0]
    assert completed.time_role is FieldTimeRole.REPORT_PERIOD
    assert completed.point_in_time is True
    assert completed.report_period_field == "report_period"
    assert completed.announcement_date_field == "ann_dt"


def test_confirmed_announcement_field_satisfies_formula_requirement() -> None:
    from tests.execution.test_manifest import spec

    factor = spec()
    factor.variables[0].logical_name = "roe_ttm"
    factor.variables[0].point_in_time_required = True
    selection = FieldSelection(
        logical_name="roe_ttm",
        table="asharettmhis",
        field="s_fa_roe_ttm",
        time_role=FieldTimeRole.REPORT_PERIOD,
        point_in_time=True,
        announcement_date_field="ann_dt",
    )
    updated = apply_confirmed_announcement_requirements(factor, [selection])
    assert updated.variables[0].announcement_date_required is True
    assert factor.variables[0].announcement_date_required is False


def test_missing_announcement_field_does_not_weaken_formula_gate() -> None:
    from tests.execution.test_manifest import spec

    factor = spec()
    factor.variables[0].point_in_time_required = True
    selection = FieldSelection(
        logical_name="close",
        table="ashareeodprices",
        field="s_dq_close",
        point_in_time=True,
    )
    updated = apply_confirmed_announcement_requirements(factor, [selection])
    assert updated.variables[0].announcement_date_required is False


def test_report_value_is_not_visible_until_after_announcement() -> None:
    raw = pd.DataFrame(
        {
            "order_book_id": ["000001.SZ"],
            "report_period": [pd.Timestamp("2022-12-31")],
            "announcement_date": [pd.Timestamp("2023-03-31")],
            "s_fa_roe_ttm": [10.0],
        }
    )
    dates = pd.date_range("2023-03-31", "2023-04-03", freq="D")
    result = _wide_report_period(raw, "s_fa_roe_ttm", dates, offset_days=1)
    assert pd.isna(result.loc["2023-03-31", "000001.SZ"])
    assert result.loc["2023-04-01", "000001.SZ"] == 10.0


def test_old_period_revision_does_not_replace_a_newer_public_period() -> None:
    raw = pd.DataFrame(
        {
            "order_book_id": ["000001.SZ"] * 3,
            "report_period": pd.to_datetime(
                ["2022-12-31", "2023-03-31", "2022-12-31"]
            ),
            "announcement_date": pd.to_datetime(
                ["2023-03-31", "2023-04-30", "2023-05-15"]
            ),
            "s_fa_roe_ttm": [10.0, 11.0, 12.0],
        }
    )
    dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-05-16"])
    result = _wide_report_period(raw, "s_fa_roe_ttm", dates, offset_days=1)
    assert result["000001.SZ"].tolist() == [10.0, 11.0, 11.0]
