"""Schema verification: does the column exist, and is it what the caller thinks.

This answers the questions ``information_schema`` can settle without reading a
row of market data — table exists, column exists, column type, and whether the
column is being used in the time role the caller claims it plays.

It is deliberately **not** the same step as checking for data. Merging the two
produces the worst false negative in field discovery: a perfectly valid column
that happens to have no rows in the user's date range gets reported as an invalid
field, the user drops it from their factor, and the platform has talked them out
of correct research. So this module decides *validity* and
:mod:`factor_platform.wind.sample_verify` decides *availability*, and only the
structural verdicts here can block.

Table and column names arrive from a language model and from a PDF extraction of
Wind's documentation. They are never interpolated into SQL — every identifier
travels as a bound parameter against ``information_schema``, which is a data
catalog and therefore queryable by value.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from factor_platform.domain.models import FieldCandidate, FieldTimeRole


class VerificationStatus(StrEnum):
    """The six outcomes of field verification.

    Three describe a field that cannot be used at all; three describe a usable
    field with varying amounts of data behind it. Collapsing them to pass/fail is
    what made "no rows in your window" indistinguishable from "no such column".
    """

    SCHEMA_VALID_DATA_PRESENT = "schema_valid_data_present"
    SCHEMA_VALID_NO_DATA_IN_SAMPLE = "schema_valid_no_data_in_sample"
    SCHEMA_VALID_DATA_SPARSE = "schema_valid_data_sparse"
    SCHEMA_INVALID = "schema_invalid"
    FIELD_INVALID = "field_invalid"
    TIME_ROLE_INVALID = "time_role_invalid"


#: Only structural problems stop the workflow. Data thinness is information.
BLOCKING_STATUSES: frozenset[VerificationStatus] = frozenset(
    {
        VerificationStatus.SCHEMA_INVALID,
        VerificationStatus.FIELD_INVALID,
        VerificationStatus.TIME_ROLE_INVALID,
    }
)


class QueryExecutor(Protocol):
    """Executes a parameterized read against the trusted connection layer."""

    async def fetch(
        self, sql: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...


class SchemaVerdict(BaseModel):
    table: str
    field: str
    status: VerificationStatus
    rejection_reason: str | None = None
    data_type: str | None = None
    detail: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES


_TABLE_SQL = """
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %(schema)s AND LOWER(TABLE_NAME) = %(table)s
"""

_COLUMN_SQL = """
SELECT COLUMN_NAME, DATA_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %(schema)s AND LOWER(TABLE_NAME) = %(table)s
"""

# Columns whose name fixes their time role. Using one in a different role is a
# look-ahead bug that would otherwise run cleanly and produce plausible numbers.
_ROLE_BY_COLUMN: dict[str, FieldTimeRole] = {
    "trade_dt": FieldTimeRole.OBSERVATION,
    "price_date": FieldTimeRole.OBSERVATION,
    "ann_dt": FieldTimeRole.ANNOUNCEMENT,
    "ann_date": FieldTimeRole.ANNOUNCEMENT,
    "report_period": FieldTimeRole.REPORT_PERIOD,
    "opdate": FieldTimeRole.AS_OF,
}


class SchemaVerifier:
    """Checks a candidate against ``information_schema`` only."""

    def __init__(self, executor: QueryExecutor, *, database: str) -> None:
        self._executor = executor
        self._database = database

    async def verify(
        self,
        candidate: FieldCandidate,
        *,
        expected_time_role: FieldTimeRole | None = None,
    ) -> SchemaVerdict:
        table = candidate.table.strip().lower()
        field = candidate.field.strip().lower()
        params = {"schema": self._database, "table": table}

        tables = await self._executor.fetch(_TABLE_SQL, params)
        if not tables:
            return SchemaVerdict(
                table=table,
                field=field,
                status=VerificationStatus.SCHEMA_INVALID,
                rejection_reason="table_not_found",
                detail=f"table {table!r} is not present in schema {self._database!r}",
            )

        columns = {
            str(row["COLUMN_NAME"]).lower(): str(row.get("DATA_TYPE") or "")
            for row in await self._executor.fetch(_COLUMN_SQL, params)
        }
        if field not in columns:
            return SchemaVerdict(
                table=table,
                field=field,
                status=VerificationStatus.FIELD_INVALID,
                rejection_reason="column_not_found",
                detail=f"column {field!r} is not present in {table!r}",
            )

        declared_role = _ROLE_BY_COLUMN.get(field)
        if (
            expected_time_role is not None
            and declared_role is not None
            and declared_role is not expected_time_role
        ):
            return SchemaVerdict(
                table=table,
                field=field,
                status=VerificationStatus.TIME_ROLE_INVALID,
                rejection_reason="time_role_mismatch",
                data_type=columns[field],
                detail=(
                    f"{field!r} carries the {declared_role.value} date but is being "
                    f"used as {expected_time_role.value}; that alignment is look-ahead"
                ),
            )

        return SchemaVerdict(
            table=table,
            field=field,
            status=VerificationStatus.SCHEMA_VALID_DATA_PRESENT,
            data_type=columns[field],
            detail="column exists with a compatible time role",
        )


__all__ = [
    "BLOCKING_STATUSES",
    "QueryExecutor",
    "SchemaVerdict",
    "SchemaVerifier",
    "VerificationStatus",
]
