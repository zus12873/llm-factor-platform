"""Result-layer validation, including the three metric-governance rules.

This layer is the compensating control for having no full-time researcher. A
domain expert glancing at a factor notices immediately that an ROE of 3000% is
wrong; nothing in the pipeline does, because 3000 is a perfectly good float. So
the plausible ranges the expert carries in their head are written down in the
metric registry, and checked here.

The three governance rules and why each has the severity it does:

* ``implausible_magnitude`` — **error**. A value outside the registry's range is
  almost always a unit mistake (yuan for ten-thousand yuan, ratio for percent),
  and those are exactly the errors that survive every other check.
* ``reference_mismatch`` — **warning**. An independent recomputation disagreeing
  is evidence of a problem, but it can also be the reference that is stale.
* ``unreviewed_metric`` — **warning**. Usable for trial runs; the point is that
  the label travels with the result rather than being remembered.

``disputed`` metrics never reach here — they are refused at planning time.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from factor_platform.domain.models import (
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)
from factor_platform.factor.metric_registry import MetricRegistry, ReviewStatus

#: Relative difference above which a reference recomputation counts as disagreeing.
REFERENCE_TOLERANCE = 0.05


def _finding(
    severity: ValidationSeverity, code: str, message: str, **evidence: object
) -> ValidationFinding:
    return ValidationFinding(
        severity=severity, code=code, message=message, evidence=evidence
    )


class ResultValidator:
    """Audits the computed factor, with the metric registry as the yardstick."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self._registry = registry or MetricRegistry.load()

    def validate(
        self,
        factor: pd.DataFrame,
        *,
        metric_keys: Sequence[str] = (),
        reference: pd.DataFrame | None = None,
        apply_metric_bounds: bool = True,
    ) -> ValidationReport:
        findings: list[ValidationFinding] = []
        findings.extend(self._distribution(factor))
        if apply_metric_bounds:
            findings.extend(self._magnitude(factor, metric_keys))
        findings.extend(self._review_status(metric_keys))
        findings.extend(self._reference(factor, reference))
        return ValidationReport(findings=findings)

    # ------------------------------------------------------------------ shape

    @staticmethod
    def _distribution(factor: pd.DataFrame) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        if factor.empty:
            return [
                _finding(
                    ValidationSeverity.ERROR, "empty_result", "因子结果为空"
                )
            ]

        values = factor.to_numpy()
        non_null = float(pd.notna(values).mean())
        if non_null == 0.0:
            return [
                _finding(
                    ValidationSeverity.ERROR,
                    "all_null_result",
                    "因子结果全为空值",
                )
            ]

        if factor.stack().nunique() <= 1:
            findings.append(
                _finding(
                    ValidationSeverity.WARNING,
                    "constant_factor",
                    "因子在所有证券与日期上取值相同，不含任何截面信息",
                )
            )

        if non_null < 0.5:
            findings.append(
                _finding(
                    ValidationSeverity.WARNING,
                    "sparse_result",
                    f"仅 {non_null:.1%} 的因子值非空",
                    non_null_rate=non_null,
                )
            )
        return findings

    # ------------------------------------------------------------------ governance

    def _magnitude(
        self, factor: pd.DataFrame, metric_keys: Sequence[str]
    ) -> list[ValidationFinding]:
        """Check the result against the range a domain expert would expect."""
        findings: list[ValidationFinding] = []
        if factor.empty:
            return findings

        observed_low = float(factor.min(numeric_only=True).min())
        observed_high = float(factor.max(numeric_only=True).max())

        for key in metric_keys:
            bounds = self._registry.plausible_range(key)
            if bounds is None:
                continue
            low, high = bounds
            if observed_low < low or observed_high > high:
                findings.append(
                    _finding(
                        ValidationSeverity.ERROR,
                        "implausible_magnitude",
                        (
                            f"{key} 的结果落在 [{observed_low:.4g}, {observed_high:.4g}]，"
                            f"超出登记的合理区间 [{low:g}, {high:g}]；"
                            "这类偏差通常是单位口径错误（元/万元、比值/百分数）"
                        ),
                        metric=key,
                        observed=[observed_low, observed_high],
                        expected=[low, high],
                    )
                )
        return findings

    def _review_status(self, metric_keys: Sequence[str]) -> list[ValidationFinding]:
        """Attach the review label to the result, so it travels with the number."""
        unreviewed = [
            key
            for key in metric_keys
            if (definition := self._registry.get(key)) is not None
            and definition.review_status is ReviewStatus.UNREVIEWED
        ]
        if not unreviewed:
            return []
        return [
            _finding(
                ValidationSeverity.WARNING,
                "unreviewed_metric",
                (
                    f"因子引用了未复核口径：{', '.join(unreviewed)}；"
                    "结果可用于试算，不得作为正式发布"
                ),
                metrics=unreviewed,
            )
        ]

    @staticmethod
    def _reference(
        factor: pd.DataFrame, reference: pd.DataFrame | None
    ) -> list[ValidationFinding]:
        """Compare against an independent recomputation, when one is available."""
        if reference is None or factor.empty or reference.empty:
            return []
        aligned_factor, aligned_reference = factor.align(reference, join="inner")
        if aligned_factor.empty:
            return []
        scale = aligned_reference.abs().to_numpy().mean()
        if not scale:
            return []
        difference = (aligned_factor - aligned_reference).abs().to_numpy().mean() / scale
        if difference <= REFERENCE_TOLERANCE:
            return []
        return [
            _finding(
                ValidationSeverity.WARNING,
                "reference_mismatch",
                (
                    f"与独立算路的平均相对差异为 {difference:.1%}，"
                    f"超过 {REFERENCE_TOLERANCE:.0%} 阈值；"
                    "可能是本因子有误，也可能是参照口径已过时"
                ),
                relative_difference=difference,
            )
        ]


__all__ = ["REFERENCE_TOLERANCE", "ResultValidator"]
