"""Data-existence verification: does this column actually have rows we can use.

Schema verification says the column is real. This says whether anything is in it,
using a small bounded read — and it picks the sample by **data shape**, which the
original uniform "3 securities × 5 trading days" could not.

That uniform sample was wrong for everything except daily quotes. A quarterly
financial column has no rows on four out of five arbitrary trading days. An index
membership table changes only on rebalance dates. A static description table has
no date column at all. Under one sample plan they all came back empty, and empty
was being read as "invalid field" — so the platform would reject a correct column
and tell the user their research idea was unsupported.

Hence the second rule here: an empty sample is its own status, and it does not
block. "The column exists, your window has no rows" is a fact the user acts on.
"""

from __future__ import annotations

import re
from typing import Any, Final

from pydantic import BaseModel, Field

from factor_platform.domain.models import FieldCandidate, QueryShape
from factor_platform.wind.schema_verify import (
    BLOCKING_STATUSES,
    QueryExecutor,
    VerificationStatus,
)

#: Below this share of non-null values the sample is reported as sparse rather
#: than present, so a column that exists but is barely populated is visible
#: before it silently produces a factor of mostly NaN.
SPARSE_THRESHOLD: Final = 0.5

#: Verification must never be able to pull a whole table.
MAX_SAMPLE_ROWS: Final = 200


class SamplePlan(BaseModel):
    """How many securities and periods to sample, and of what kind."""

    shape: QueryShape
    security_count: int
    period_count: int
    period_kind: str | None = None
    requires_entry_and_exit: bool = False
    min_rebalance_dates: int = 0
    rationale: str = ""

    @property
    def row_limit(self) -> int:
        return min(MAX_SAMPLE_ROWS, max(self.security_count * max(self.period_count, 1), 10))


_PLANS: Final[dict[QueryShape, SamplePlan]] = {
    QueryShape.POINT_RANGE: SamplePlan(
        shape=QueryShape.POINT_RANGE,
        security_count=3,
        period_count=20,
        period_kind="trading_day",
        rationale="daily quotes have a row per security per trading day",
    ),
    QueryShape.REPORT_PERIOD: SamplePlan(
        shape=QueryShape.REPORT_PERIOD,
        security_count=3,
        period_count=8,
        period_kind="report_period",
        rationale="two years of quarters; trading days would sample mostly gaps",
    ),
    QueryShape.ANNOUNCEMENT_RANGE: SamplePlan(
        shape=QueryShape.ANNOUNCEMENT_RANGE,
        security_count=3,
        period_count=3,
        period_kind="year",
        rationale="events are sparse and clustered; a few days prove nothing",
    ),
    QueryShape.STATIC_LOOKUP: SamplePlan(
        shape=QueryShape.STATIC_LOOKUP,
        security_count=10,
        period_count=0,
        period_kind=None,
        rationale="no time dimension; breadth across boards is the only coverage",
    ),
    QueryShape.INTERVAL_OVERLAP: SamplePlan(
        shape=QueryShape.INTERVAL_OVERLAP,
        security_count=3,
        period_count=3,
        period_kind="year",
        requires_entry_and_exit=True,
        rationale="one instant cannot distinguish an open interval from a closed one",
    ),
    QueryShape.CROSS_SECTION_ASOF: SamplePlan(
        shape=QueryShape.CROSS_SECTION_ASOF,
        security_count=3,
        period_count=2,
        period_kind="rebalance_date",
        min_rebalance_dates=2,
        rationale="membership only changes on rebalance dates",
    ),
}


def plan_for_shape(shape: QueryShape) -> SamplePlan:
    """Return the sample plan for ``shape``.

    Every shape has an explicit entry; there is no default, because falling back
    to the daily-quote plan is exactly the bug this module exists to fix.
    """
    return _PLANS[shape]


class SampleVerdict(BaseModel):
    table: str
    field: str
    status: VerificationStatus
    row_count: int = 0
    non_null_rate: float = 0.0
    sample_values: list[Any] = Field(default_factory=list)
    plan: SamplePlan | None = None
    detail: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES


_COLUMN_SQL = """
SELECT LOWER(COLUMN_NAME) AS column_name
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND LOWER(TABLE_NAME) = %(table)s
"""

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")
_CODE_FIELDS: Final[tuple[str, ...]] = (
    "s_info_windcode",
    "s_con_windcode",
    "windcode",
    "s_info_code",
)
_ROLE_FIELDS: Final[dict[QueryShape, dict[str, tuple[str, ...]]]] = {
    QueryShape.POINT_RANGE: {
        "sample_time": ("trade_dt", "price_date", "opdate"),
    },
    QueryShape.REPORT_PERIOD: {
        "report_period": ("report_period",),
    },
    QueryShape.ANNOUNCEMENT_RANGE: {
        "announcement_date": ("ann_dt", "ann_date"),
    },
    QueryShape.STATIC_LOOKUP: {},
    QueryShape.INTERVAL_OVERLAP: {
        "interval_start": ("s_con_indate", "entry_dt"),
        "interval_end": ("s_con_outdate", "remove_dt"),
    },
    QueryShape.CROSS_SECTION_ASOF: {
        "sample_time": ("trade_dt", "s_con_indate", "opdate"),
    },
}


class SampleVerifier:
    """Reads a bounded sample and classifies what came back."""

    def __init__(self, executor: QueryExecutor) -> None:
        self._executor = executor

    async def verify(self, candidate: FieldCandidate, plan: SamplePlan) -> SampleVerdict:
        table = candidate.table.strip().lower()
        field = candidate.field.strip().lower()
        _safe_identifier(table, "table")
        _safe_identifier(field, "field")

        # Identifiers cannot be bound by MySQL. Resolve them against the live
        # schema first, then interpolate only the exact lowercase names returned
        # by information_schema. This keeps the business-table read controlled
        # without pretending that querying COLUMNS is a data sample.
        schema_rows = await self._executor.fetch(_COLUMN_SQL, {"table": table})
        columns = {
            str(row.get("column_name") or row.get("COLUMN_NAME") or "").lower()
            for row in schema_rows
        }
        if field not in columns:
            return SampleVerdict(
                table=table,
                field=field,
                status=VerificationStatus.FIELD_INVALID,
                plan=plan,
                detail=f"column {field!r} disappeared before the sample read",
            )

        role_fields: dict[str, str] = {}
        for role, candidates in _ROLE_FIELDS[plan.shape].items():
            resolved = next((name for name in candidates if name in columns), None)
            if resolved is None:
                return SampleVerdict(
                    table=table,
                    field=field,
                    status=VerificationStatus.TIME_ROLE_INVALID,
                    plan=plan,
                    detail=(
                        f"{table!r} has no verified {role} column required by "
                        f"the {plan.shape.value} query shape"
                    ),
                )
            role_fields[role] = resolved

        code_field = next((name for name in _CODE_FIELDS if name in columns), None)
        select_parts = [f"{field} AS value"]
        if code_field is not None and code_field != field:
            select_parts.append(f"{code_field} AS sample_code")
        select_parts.extend(
            f"{name} AS {role}" for role, name in role_fields.items() if name != field
        )
        sample_sql = (
            f"SELECT {', '.join(select_parts)} FROM {table} "
            f"WHERE {field} IS NOT NULL LIMIT %(row_limit)s"
        )
        rows = await self._executor.fetch(sample_sql, {"row_limit": plan.row_limit})

        if not rows:
            return SampleVerdict(
                table=table,
                field=field,
                status=VerificationStatus.SCHEMA_VALID_NO_DATA_IN_SAMPLE,
                plan=plan,
                detail=(
                    f"column exists but the {plan.shape.value} sample returned no rows "
                    f"({plan.security_count} securities × {plan.period_count} "
                    f"{plan.period_kind or 'periods'}); the window may simply predate "
                    "coverage"
                ),
            )

        values = [_value_of(row, "value") for row in rows]
        non_null = [value for value in values if value is not None]
        rate = len(non_null) / len(values)
        sparse = rate < SPARSE_THRESHOLD

        return SampleVerdict(
            table=table,
            field=field,
            status=(
                VerificationStatus.SCHEMA_VALID_DATA_SPARSE
                if sparse
                else VerificationStatus.SCHEMA_VALID_DATA_PRESENT
            ),
            row_count=len(rows),
            non_null_rate=rate,
            sample_values=non_null[:5],
            plan=plan,
            detail=(
                f"{len(non_null)}/{len(values)} sampled rows are non-null"
                + (" — usable but thin" if sparse else "")
            ),
        )


def _value_of(row: dict[str, Any], field: str) -> Any:
    """Pull the sampled column out of a row, tolerating the driver's casing."""
    if field in row:
        return row[field]
    lowered = {key.lower(): value for key, value in row.items()}
    if field in lowered:
        return lowered[field]
    # Fall back to the one column that is neither the code nor the date.
    ignored = {"s_info_windcode", "trade_dt", "report_period", "ann_dt"}
    remaining = [value for key, value in lowered.items() if key not in ignored]
    return remaining[0] if remaining else None


def _safe_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid Wind {label}: {value!r}")
    return value


__all__ = [
    "MAX_SAMPLE_ROWS",
    "SPARSE_THRESHOLD",
    "SamplePlan",
    "SampleVerdict",
    "SampleVerifier",
    "plan_for_shape",
]
