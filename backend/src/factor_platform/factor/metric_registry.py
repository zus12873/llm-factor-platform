"""The single source of truth for what a business term means in Wind columns.

Metric definitions were previously spread across hard-coded tuples in the
clarification rules, the Wind alias YAML, and the golden-case fixtures. Three
copies drift, and none of them can be read by someone who does not write Python.
That last part is the binding constraint on this project: there is no full-time
researcher, so the metric definitions are precisely what the supervising advisor
has to sample-check, and they cannot check a tuple buried in a rules module.

Hence a flat YAML file and a three-value review state:

* ``unreviewed`` — usable for prototyping and internal trial runs, but every
  surface that shows a result computed from it must say so.
* ``reviewed`` — cleared for demos, the factor library and publication. Carries
  the reviewer, the date and their comment.
* ``disputed`` — refused outright. Not a warning.

That last distinction is the whole point. The two known-bad mappings registered
here (``float_a_shr`` is a share *count*, and the cash-flow statement's
``net_profit`` is the indirect-method opening line) both produce factors that look
entirely reasonable. A warning against a plausible-looking number is a warning
someone dismisses.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from factor_platform.domain.errors import DisputedMetricError


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    DISPUTED = "disputed"


class MetricDefinition(BaseModel):
    """One business term, its Wind mapping, and its review state."""

    schema_version: int = 1
    key: str
    display_zh: str
    definition: str
    category: str = ""

    wind_table: str
    wind_field: str
    time_role: str | None = None
    announcement_field: str | None = None
    unit: str | None = None

    plausible_range: tuple[float, float] | None = None
    reference_check: str = ""

    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_comment: str = ""
    evidence_version: int = 1
    note: str = ""


class GateVerdict(BaseModel):
    """Whether a metric may be used, and whether its use must be labelled."""

    key: str
    allowed: bool
    requires_warning: bool
    status: ReviewStatus | None = None
    reason: str = ""


class MetricRegistry:
    """Loads and queries the metric definition registry."""

    def __init__(self, definitions: Mapping[str, MetricDefinition]) -> None:
        self._definitions = dict(definitions)

    # ------------------------------------------------------------------ loading

    @staticmethod
    def default_path() -> Path:
        backend_root = Path(__file__).resolve().parents[3]
        return backend_root / "data" / "metric_definitions.yaml"

    @classmethod
    def load(cls, path: Path | str | None = None) -> MetricRegistry:
        raw = yaml.safe_load(
            Path(path or cls.default_path()).read_text(encoding="utf-8")
        ) or {}
        return cls.from_mapping(raw.get("metrics", {}))

    @classmethod
    def from_mapping(cls, block: Mapping[str, Any]) -> MetricRegistry:
        return cls(
            {
                str(key).upper(): MetricDefinition.model_validate(
                    {"key": str(key).upper(), **dict(value)}
                )
                for key, value in block.items()
                if isinstance(value, Mapping)
            }
        )

    # ------------------------------------------------------------------ queries

    def get(self, key: str) -> MetricDefinition | None:
        return self._definitions.get(key.strip().upper())

    def all(self) -> Iterator[MetricDefinition]:
        return iter(self._definitions.values())

    def options_for(self, category: str) -> list[str]:
        """Clarification options for a category, in registry order.

        Disputed metrics are never offered as a choice — presenting a known-wrong
        mapping in a picker invites exactly the mistake it is registered to stop.
        """
        return [
            definition.key
            for definition in self._definitions.values()
            if definition.category == category
            and definition.review_status is not ReviewStatus.DISPUTED
        ]

    def plausible_range(self, key: str) -> tuple[float, float] | None:
        definition = self.get(key)
        return definition.plausible_range if definition else None

    # ------------------------------------------------------------------ the gate

    def gate(self, key: str) -> GateVerdict:
        """Decide whether ``key`` may be used, and whether it must be labelled."""
        definition = self.get(key)
        if definition is None:
            return GateVerdict(
                key=key,
                allowed=False,
                requires_warning=True,
                reason=(
                    f"口径 {key} 未登记。未登记的口径不得使用——"
                    "先在 metric_definitions.yaml 中登记并说明映射"
                ),
            )

        if definition.review_status is ReviewStatus.DISPUTED:
            return GateVerdict(
                key=key,
                allowed=False,
                requires_warning=True,
                status=definition.review_status,
                reason=(
                    f"口径 {definition.display_zh} 已标记为有争议，禁止执行："
                    f"{definition.review_comment.strip()}"
                ),
            )

        if definition.review_status is ReviewStatus.UNREVIEWED:
            return GateVerdict(
                key=key,
                allowed=True,
                requires_warning=True,
                status=definition.review_status,
                reason=(
                    f"口径 {definition.display_zh} 未复核，可用于试算，"
                    "结果不得作为正式发布"
                ),
            )

        review_reason = (
            f"已由 {definition.reviewer} 复核"
            if definition.reviewer
            else "已完成人工复核"
        )
        if definition.reviewed_at:
            review_reason += f"（{definition.reviewed_at}）"

        return GateVerdict(
            key=key,
            allowed=True,
            requires_warning=False,
            status=definition.review_status,
            reason=review_reason,
        )

    def enforce(self, key: str) -> GateVerdict:
        """Raise if ``key`` may not be used; otherwise return its verdict.

        Used at the points where a wrong number would escape: planning, publishing
        to the factor library, and composing an index.
        """
        verdict = self.gate(key)
        if not verdict.allowed:
            raise DisputedMetricError(verdict.reason)
        return verdict


__all__ = [
    "GateVerdict",
    "MetricDefinition",
    "MetricRegistry",
    "ReviewStatus",
]
