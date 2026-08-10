"""Tests for schema verification — does this column exist, and is it what we think.

Schema verification asks questions the database can answer without reading a
single row of market data: is the table there, is the column there, and is the
column being used in the time role the caller claims. It runs against
``information_schema`` with bound parameters only; a table or column name is
never interpolated into SQL, because those names arrive from a model and a PDF
extraction.

It is kept separate from sample verification on purpose. Merging them produces
the single worst failure mode in field discovery: a perfectly valid column that
happens to have no rows in the user's date range gets reported as an invalid
field, the user removes it from their factor, and the platform has just talked
them out of correct research.
"""

from __future__ import annotations

from typing import Any

import pytest

from factor_platform.domain.models import FieldCandidate, FieldTimeRole
from factor_platform.wind.schema_verify import (
    BLOCKING_STATUSES,
    SchemaVerifier,
    VerificationStatus,
)


class FakeQuery:
    """Records what was asked and returns canned information_schema rows."""

    def __init__(self) -> None:
        self.tables: set[str] = {"ashareeodprices"}
        self.columns: dict[str, str] = {
            "s_info_windcode": "varchar",
            "trade_dt": "varchar",
            "s_dq_close": "decimal",
            "report_period": "varchar",
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((sql, dict(params)))
        table = str(params.get("table", "")).lower()
        if "TABLES" in sql.upper() and "COLUMNS" not in sql.upper():
            return [{"TABLE_NAME": table}] if table in self.tables else []
        if table not in self.tables:
            return []
        return [
            {"COLUMN_NAME": name, "DATA_TYPE": dtype}
            for name, dtype in self.columns.items()
        ]


@pytest.fixture
def fake_query() -> FakeQuery:
    return FakeQuery()


@pytest.fixture
def verifier(fake_query: FakeQuery) -> SchemaVerifier:
    return SchemaVerifier(fake_query, database="wind")


def candidate(table: str = "ashareeodprices", field: str = "s_dq_close") -> FieldCandidate:
    return FieldCandidate(table=table, field=field)


# --------------------------------------------------------------------------- happy path


async def test_existing_column_passes_schema_verification(verifier: SchemaVerifier) -> None:
    verdict = await verifier.verify(candidate())
    assert verdict.status is VerificationStatus.SCHEMA_VALID_DATA_PRESENT
    assert verdict.is_blocking is False
    assert verdict.data_type == "decimal"


# --------------------------------------------------------------------------- rejections


async def test_unknown_column_is_field_invalid(
    verifier: SchemaVerifier, fake_query: FakeQuery
) -> None:
    fake_query.columns.pop("s_dq_close")
    verdict = await verifier.verify(candidate(field="s_dq_close"))
    assert verdict.status is VerificationStatus.FIELD_INVALID
    assert verdict.rejection_reason == "column_not_found"
    assert verdict.is_blocking is True


async def test_unknown_table_is_schema_invalid(verifier: SchemaVerifier) -> None:
    verdict = await verifier.verify(candidate(table="nosuchtable"))
    assert verdict.status is VerificationStatus.SCHEMA_INVALID
    assert verdict.rejection_reason == "table_not_found"
    assert verdict.is_blocking is True


async def test_time_role_mismatch_is_reported(verifier: SchemaVerifier) -> None:
    """Reading report_period as if it were the observation date is look-ahead.

    The column exists and the query would run; it would just silently align every
    financial value to the period it describes rather than the day it became
    knowable.
    """
    verdict = await verifier.verify(
        candidate(field="report_period"), expected_time_role=FieldTimeRole.OBSERVATION
    )
    assert verdict.status is VerificationStatus.TIME_ROLE_INVALID
    assert verdict.rejection_reason == "time_role_mismatch"
    assert verdict.is_blocking is True


async def test_matching_time_role_passes(verifier: SchemaVerifier) -> None:
    verdict = await verifier.verify(
        candidate(field="report_period"), expected_time_role=FieldTimeRole.REPORT_PERIOD
    )
    assert verdict.status is VerificationStatus.SCHEMA_VALID_DATA_PRESENT


# --------------------------------------------------------------------------- sql safety


async def test_identifiers_are_bound_never_interpolated(
    verifier: SchemaVerifier, fake_query: FakeQuery
) -> None:
    """Table and field names come from a model and a PDF extraction."""
    await verifier.verify(candidate(table="ashareeodprices", field="s_dq_close"))
    for sql, params in fake_query.calls:
        assert "ashareeodprices" not in sql
        assert "s_dq_close" not in sql
        assert "wind" not in sql
        assert params, "identifiers must travel as bound parameters"


async def test_a_hostile_table_name_never_reaches_the_sql_text(
    verifier: SchemaVerifier, fake_query: FakeQuery
) -> None:
    await verifier.verify(candidate(table="x; DROP TABLE ashareeodprices --"))
    for sql, _ in fake_query.calls:
        assert "DROP" not in sql.upper()


# --------------------------------------------------------------------------- contract


def test_only_the_three_structural_statuses_block() -> None:
    """A field with no rows in the sample window is not a broken field."""
    assert {
        VerificationStatus.SCHEMA_INVALID,
        VerificationStatus.FIELD_INVALID,
        VerificationStatus.TIME_ROLE_INVALID,
    } == BLOCKING_STATUSES
    assert VerificationStatus.SCHEMA_VALID_NO_DATA_IN_SAMPLE not in BLOCKING_STATUSES
    assert VerificationStatus.SCHEMA_VALID_DATA_SPARSE not in BLOCKING_STATUSES
