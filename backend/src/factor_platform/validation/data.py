"""Data-layer validation: is the input worth computing on.

Runs before the formula, on the fetched frames. The distinction it enforces is the
same one Task 10 drew for field verification: a structural defect blocks, and a
thin or empty sample is reported for the user to judge.

The blocking case is duplicate keys. A ``(date, security)`` pair appearing twice
means the query joined something it should not have, and every cross-sectional
operation downstream — rank, z-score, industry mean — silently double-counts that
security. The factor still computes and still looks like a factor.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from factor_platform.domain.models import (
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)

#: Below this fraction of non-null values the input is too thin to rank on.
SPARSE_THRESHOLD = 0.5

#: A cross-section this small cannot support quantile analysis.
MIN_SECURITIES = 5


def _finding(
    severity: ValidationSeverity, code: str, message: str, **evidence: object
) -> ValidationFinding:
    return ValidationFinding(
        severity=severity, code=code, message=message, evidence=evidence
    )


class DataValidator:
    """Audits the fetched input frames before anything is computed."""

    def validate(
        self,
        variables: Mapping[str, pd.DataFrame],
        *,
        expected_start: str | None = None,
        expected_end: str | None = None,
    ) -> ValidationReport:
        findings: list[ValidationFinding] = []

        if not variables:
            return ValidationReport(
                findings=[
                    _finding(
                        ValidationSeverity.ERROR,
                        "no_input_data",
                        "没有任何输入变量可供计算",
                    )
                ]
            )

        for name, frame in variables.items():
            findings.extend(self._one(name, frame, expected_start, expected_end))
        return ValidationReport(findings=findings)

    def _one(
        self,
        name: str,
        frame: pd.DataFrame,
        expected_start: str | None,
        expected_end: str | None,
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        if frame.empty:
            return [
                _finding(
                    ValidationSeverity.WARNING,
                    "empty_sample",
                    f"{name} 在请求区间内没有数据；字段本身可能有效，"
                    "需用户判断是否更换区间",
                    variable=name,
                )
            ]

        # Duplicate keys are the blocking case: every cross-sectional operation
        # downstream would double-count the repeated security.
        duplicate_dates = int(frame.index.duplicated().sum())
        duplicate_codes = len(frame.columns) - len(set(frame.columns))
        if duplicate_dates or duplicate_codes:
            findings.append(
                _finding(
                    ValidationSeverity.ERROR,
                    "duplicate_key",
                    f"{name} 存在重复键（{duplicate_dates} 个重复日期、"
                    f"{duplicate_codes} 个重复证券）；横截面算子会重复计入",
                    variable=name,
                    duplicate_dates=duplicate_dates,
                    duplicate_codes=duplicate_codes,
                )
            )

        non_null = float(frame.notna().to_numpy().mean())
        if non_null < SPARSE_THRESHOLD:
            findings.append(
                _finding(
                    ValidationSeverity.WARNING,
                    "sparse_data",
                    f"{name} 仅 {non_null:.1%} 的取值非空，横截面排名会退化",
                    variable=name,
                    non_null_rate=non_null,
                )
            )

        if len(frame.columns) < MIN_SECURITIES:
            findings.append(
                _finding(
                    ValidationSeverity.WARNING,
                    "narrow_cross_section",
                    f"{name} 只有 {len(frame.columns)} 只证券，不足以做分位分析",
                    variable=name,
                    securities=len(frame.columns),
                )
            )

        if frame.notna().to_numpy().any() and frame.nunique().max() <= 1:
            findings.append(
                _finding(
                    ValidationSeverity.ERROR,
                    "constant_input",
                    f"{name} 在整个区间内为常数，排名后全部相同",
                    variable=name,
                )
            )

        findings.extend(self._coverage(name, frame, expected_start, expected_end))
        return findings

    @staticmethod
    def _coverage(
        name: str,
        frame: pd.DataFrame,
        expected_start: str | None,
        expected_end: str | None,
    ) -> list[ValidationFinding]:
        """Report a window narrower than requested rather than silently shrinking it."""
        if expected_start is None or expected_end is None or frame.empty:
            return []
        actual_start = pd.Timestamp(frame.index.min())
        actual_end = pd.Timestamp(frame.index.max())
        wanted_start = pd.Timestamp(expected_start)
        wanted_end = pd.Timestamp(expected_end)
        if actual_start > wanted_start or actual_end < wanted_end:
            return [
                _finding(
                    ValidationSeverity.WARNING,
                    "partial_coverage",
                    f"{name} 实际覆盖 {actual_start.date()}~{actual_end.date()}，"
                    f"窄于请求的 {wanted_start.date()}~{wanted_end.date()}",
                    variable=name,
                    actual_start=str(actual_start.date()),
                    actual_end=str(actual_end.date()),
                )
            ]
        return []


__all__ = ["MIN_SECURITIES", "SPARSE_THRESHOLD", "DataValidator"]
