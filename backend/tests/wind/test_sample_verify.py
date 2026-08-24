"""Tests for data-existence verification — does this column have rows we can use.

The original design sampled "3 securities × 5 trading days" for everything. That
is right for daily quotes and wrong for everything else: a quarterly financial
column has no rows on four out of five arbitrary trading days, an index
membership table changes only on rebalance dates, and a static description table
has no date column at all. Under a uniform sample, all three look empty, and an
empty sample was being read as a broken field.

So the sample plan is chosen by data shape, and — critically — an empty result is
its own status. "The column exists but your date range has no rows" is a fact for
the user to act on, not a verdict against the field.
"""

from __future__ import annotations

from typing import Any

import pytest

from factor_platform.domain.models import FieldCandidate, QueryShape
from factor_platform.wind.sample_verify import (
    SamplePlan,
    SampleVerifier,
    plan_for_shape,
)
from factor_platform.wind.schema_verify import VerificationStatus


class FakeQuery:
    """Returns canned sample rows and records the plan it was asked to execute."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [
            {"s_info_windcode": "600519.SH", "trade_dt": "20240102", "value": 1688.0},
            {"s_info_windcode": "600519.SH", "trade_dt": "20240103", "value": 1690.0},
            {"s_info_windcode": "000001.SZ", "trade_dt": "20240102", "value": 9.87},
        ]
        self.last_plan: SamplePlan | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((sql, dict(params)))
        if "information_schema.COLUMNS" in sql:
            return [
                {"column_name": "s_info_windcode"},
                {"column_name": "trade_dt"},
                {"column_name": "report_period"},
                {"column_name": "ann_dt"},
                {"column_name": "s_con_indate"},
                {"column_name": "s_con_outdate"},
                {"column_name": "s_dq_close"},
                {"column_name": "oper_rev"},
            ]
        return list(self.rows)


@pytest.fixture
def fake_query() -> FakeQuery:
    return FakeQuery()


@pytest.fixture
def verifier(fake_query: FakeQuery) -> SampleVerifier:
    return SampleVerifier(fake_query)


def candidate(table: str = "ashareeodprices", field: str = "s_dq_close") -> FieldCandidate:
    return FieldCandidate(table=table, field=field)


# --------------------------------------------------------------------------- statuses


async def test_rows_present_is_data_present(verifier: SampleVerifier) -> None:
    verdict = await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    assert verdict.status is VerificationStatus.SCHEMA_VALID_DATA_PRESENT
    assert verdict.is_blocking is False
    assert verdict.row_count == 3


async def test_valid_field_with_empty_sample_is_not_an_error(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    """The single most damaging false negative in field discovery."""
    fake_query.rows = []
    verdict = await verifier.verify(
        candidate(table="ashareincome", field="oper_rev"),
        plan_for_shape(QueryShape.REPORT_PERIOD),
    )
    assert verdict.status is VerificationStatus.SCHEMA_VALID_NO_DATA_IN_SAMPLE
    assert verdict.is_blocking is False
    assert "no rows" in verdict.detail.lower()


async def test_mostly_null_values_are_sparse_not_absent(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    fake_query.rows = [
        {"s_info_windcode": "600519.SH", "trade_dt": "20240102", "value": 1688.0},
        {"s_info_windcode": "600519.SH", "trade_dt": "20240103", "value": None},
        {"s_info_windcode": "600519.SH", "trade_dt": "20240104", "value": None},
        {"s_info_windcode": "600519.SH", "trade_dt": "20240105", "value": None},
    ]
    verdict = await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    assert verdict.status is VerificationStatus.SCHEMA_VALID_DATA_SPARSE
    assert verdict.is_blocking is False
    assert verdict.non_null_rate == pytest.approx(0.25)


async def test_a_sparse_field_still_reports_its_sample_values(
    verifier: SampleVerifier,
) -> None:
    verdict = await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    assert verdict.sample_values, "the user needs to see what came back"


# --------------------------------------------------------------------------- shapes


def test_daily_quotes_sample_trading_days() -> None:
    plan = plan_for_shape(QueryShape.POINT_RANGE)
    assert plan.security_count == 3
    assert 5 <= plan.period_count <= 20
    assert plan.period_kind == "trading_day"


def test_quarterly_financials_sample_report_periods_not_trading_days() -> None:
    """Five arbitrary trading days hit no quarterly rows at all."""
    plan = plan_for_shape(QueryShape.REPORT_PERIOD)
    assert plan.security_count == 3
    assert plan.period_count == 8
    assert plan.period_kind == "report_period"


def test_announcement_events_sample_years() -> None:
    plan = plan_for_shape(QueryShape.ANNOUNCEMENT_RANGE)
    assert plan.period_kind == "year"
    assert plan.period_count >= 2


def test_static_lookup_samples_a_wide_cross_section_and_no_periods() -> None:
    plan = plan_for_shape(QueryShape.STATIC_LOOKUP)
    assert plan.security_count == 10
    assert plan.period_count == 0
    assert plan.period_kind is None


def test_interval_data_must_cover_an_entry_and_an_exit() -> None:
    """Sampling one instant cannot tell an open interval from a closed one."""
    plan = plan_for_shape(QueryShape.INTERVAL_OVERLAP)
    assert plan.requires_entry_and_exit is True


def test_index_membership_covers_at_least_two_rebalances() -> None:
    plan = plan_for_shape(QueryShape.CROSS_SECTION_ASOF)
    assert plan.min_rebalance_dates >= 2


def test_every_query_shape_has_a_sample_plan() -> None:
    """A shape without a plan would silently fall back to the daily-quote one."""
    for shape in QueryShape:
        assert plan_for_shape(shape) is not None


# --------------------------------------------------------------------------- bounds


async def test_the_sample_query_is_bounded(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    """Verification must never be able to pull a full table."""
    await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    sql, params = fake_query.calls[-1]
    assert "LIMIT" in sql.upper()
    assert params["row_limit"] <= 200


async def test_business_table_is_sampled_after_schema_validation(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    sql, params = fake_query.calls[-1]
    assert "FROM ashareeodprices" in sql
    assert "s_dq_close AS value" in sql
    assert "information_schema" not in sql
    assert params == {"row_limit": 60}


async def test_identifiers_are_resolved_through_information_schema_first(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    await verifier.verify(candidate(), plan_for_shape(QueryShape.POINT_RANGE))
    schema_sql, schema_params = fake_query.calls[0]
    assert "information_schema.COLUMNS" in schema_sql
    assert schema_params == {"table": "ashareeodprices"}


async def test_malicious_identifier_is_rejected_before_business_query(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    with pytest.raises(ValueError, match="invalid Wind table"):
        await verifier.verify(
            candidate(table="ashareeodprices; drop table x"),
            plan_for_shape(QueryShape.POINT_RANGE),
        )
    assert fake_query.calls == []


async def test_missing_shape_role_is_structural_failure(
    verifier: SampleVerifier, fake_query: FakeQuery
) -> None:
    original_fetch = fake_query.fetch

    async def without_interval_roles(
        sql: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if "information_schema.COLUMNS" in sql:
            fake_query.calls.append((sql, dict(params)))
            return [
                {"column_name": "s_info_windcode"},
                {"column_name": "s_dq_close"},
            ]
        return await original_fetch(sql, params)

    fake_query.fetch = without_interval_roles  # type: ignore[method-assign]
    verdict = await verifier.verify(
        candidate(), plan_for_shape(QueryShape.INTERVAL_OVERLAP)
    )
    assert verdict.status is VerificationStatus.TIME_ROLE_INVALID
    assert verdict.is_blocking is True
    assert len(fake_query.calls) == 1
