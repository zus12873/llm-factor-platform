"""Formula-layer validation: is this computation self-consistent and honest about time.

The checks here catch things that run cleanly and produce plausible numbers. That
is the whole selection criterion — anything that raises on its own does not need a
validator.

Three of them matter most:

* **Signal/trade timing.** A factor computed from the T close is not knowable
  during T. The query layer can be perfectly point-in-time and this can still be
  wrong, because it is about *when the signal is acted on*, not which rows were
  read.
* **Financial availability.** A report-period value used before its announcement
  date is look-ahead with a plausible face: the numbers are real, they were just
  not public yet.
* **Duplicate standardisation.** Compared against the trace the pipeline actually
  produced, not the declared pipeline. Standardising twice compresses the spread
  and changes every downstream statistic, and nothing else re-derives it.
"""

from __future__ import annotations

from collections.abc import Sequence

from factor_platform.domain.formula import ROLLING_OPERATORS, FormulaNode
from factor_platform.domain.models import (
    FactorSpec,
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)
from factor_platform.domain.time_convention import offset_of

#: Operations that rescale a cross-section. Applying two of them to the same
#: target is almost always an accident.
_STANDARDIZING = frozenset({"zscore", "winsorize"})


def _error(code: str, message: str, **evidence: object) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationSeverity.ERROR, code=code, message=message, evidence=evidence
    )


def _warning(code: str, message: str, **evidence: object) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationSeverity.WARNING, code=code, message=message, evidence=evidence
    )


class FormulaValidator:
    """Audits a spec plus the pipeline trace produced by a run."""

    def validate(
        self,
        spec: FactorSpec,
        *,
        pipeline_trace: Sequence[dict[str, object]] = (),
    ) -> ValidationReport:
        findings: list[ValidationFinding] = []
        findings.extend(self._timing(spec))
        findings.extend(self._financial_availability(spec))
        findings.extend(self._duplicate_standardization(pipeline_trace))
        findings.extend(self._windows(spec.formula_ast))
        findings.extend(self._direction(spec))
        return ValidationReport(findings=findings)

    # ------------------------------------------------------------------ timing

    @staticmethod
    def _timing(spec: FactorSpec) -> list[ValidationFinding]:
        """Reject a signal traded before it could have been known.

        ``TimeConvention`` already refuses the impossible combinations at
        construction, but a spec can be assembled field by field, and this is the
        last point where the whole picture is visible.
        """
        convention = spec.time_convention
        try:
            signal = offset_of(convention.signal_date)
            trade = offset_of(convention.trade_date)
        except ValueError:
            return []

        after_session = convention.information_available_time.value in {
            "T_AFTER_CLOSE",
            "ANNOUNCEMENT_AFTER_CLOSE",
        }
        earliest = signal + 1 if after_session else signal
        if trade < earliest:
            return [
                _error(
                    "signal_traded_before_available",
                    (
                        f"信号在 {convention.signal_date} 形成、"
                        f"{convention.information_available_time.value} 才可得，"
                        f"却在 {convention.trade_date} 交易——最早只能到 T+{earliest}"
                    ),
                    signal_date=convention.signal_date,
                    trade_date=convention.trade_date,
                )
            ]
        return []

    @staticmethod
    def _financial_availability(spec: FactorSpec) -> list[ValidationFinding]:
        """A report-period value used without an announcement date is look-ahead."""
        offenders = [
            variable.logical_name
            for variable in spec.variables
            if variable.point_in_time_required and not variable.announcement_date_required
        ]
        if not offenders:
            return []
        return [
            _error(
                "future_financial_data",
                (
                    f"{', '.join(offenders)} 声明需要点时可得，却未要求公告日；"
                    "报告期数值在公告前不可知，直接使用即为未来函数"
                ),
                variables=offenders,
            )
        ]

    # ------------------------------------------------------------------ pipeline

    @staticmethod
    def _duplicate_standardization(
        trace: Sequence[dict[str, object]],
    ) -> list[ValidationFinding]:
        """Compare against what actually ran, not what was declared."""
        seen: set[tuple[str, str]] = set()
        duplicates: list[str] = []
        for entry in trace:
            operation = str(entry.get("operation", ""))
            target = str(entry.get("target", ""))
            if operation not in _STANDARDIZING:
                continue
            if (operation, target) in seen:
                duplicates.append(f"{operation} on {target}")
            seen.add((operation, target))
        if not duplicates:
            return []
        return [
            _error(
                "duplicate_standardization",
                (
                    f"实际执行序列中重复标准化：{', '.join(duplicates)}；"
                    "二次标准化会压缩离散度并改变所有下游统计量"
                ),
                duplicates=duplicates,
            )
        ]

    # ------------------------------------------------------------------ shape

    @staticmethod
    def _windows(node: FormulaNode) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.op in ROLLING_OPERATORS:
                window = (current.params or {}).get("window")
                if not isinstance(window, int | float) or window < 2:
                    findings.append(
                        _warning(
                            "degenerate_window",
                            f"{current.op} 的窗口为 {window!r}，小于 2 时该算子无意义",
                            operator=current.op,
                            window=window,
                        )
                    )
            stack.extend(current.args or [])
        return findings

    @staticmethod
    def _direction(spec: FactorSpec) -> list[ValidationFinding]:
        if spec.direction is None:
            return [
                _error(
                    "direction_unset",
                    "因子方向未确认，无法判断分位收益的符号",
                )
            ]
        return []


__all__ = ["FormulaValidator"]
