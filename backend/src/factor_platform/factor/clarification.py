"""Deterministic clarification engine.

The LLM proposes a ``FactorSpec``; this engine independently audits it for
*blocking* ambiguities (terms so vague that guessing would corrupt the factor) and
*non-blocking* defaults (reasonable choices surfaced for explicit confirmation).
The rules are plain Python so blocking behavior is stable and testable without any
LLM call.
"""

from __future__ import annotations

from factor_platform.domain.models import ClarificationQuestion, FactorSpec

# Vague business terms -> the concrete indicators the user must choose between.
_PROFITABILITY_HINTS = ("盈利质量", "盈利能力", "profitability", "earnings quality")
_PROFITABILITY_CONCRETE = ("roe_ttm", "roa_ttm", "cfo_to_profit", "净资产收益率", "总资产收益率")
_PROFITABILITY_OPTIONS = ["ROE_TTM", "ROA_TTM", "CFO_TO_PROFIT"]

_VALUATION_HINTS = ("估值", "估值因子", "valuation", "价值因子", "value factor")
_VALUATION_CONCRETE = ("pe_ttm", "pb", "ps_ttm", "市盈率", "市净率")
_VALUATION_OPTIONS = ["PE_TTM", "PB", "PS_TTM"]

_GROWTH_HINTS = (
    "营收增长", "收入增长", "利润增长", "成长性", "growth factor", "增长因子",
)
_GROWTH_CONCRETE = (
    "revenue_yoy", "net_profit_yoy", "operating_profit_yoy", "营收同比", "净利润同比",
)
_GROWTH_OPTIONS = [
    "REVENUE_YOY",
    "NET_PROFIT_YOY",
    "OPERATING_PROFIT_YOY",
]


class ClarificationEngine:
    """Audits a FactorSpec and returns the questions a user must resolve."""

    def questions(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        return [
            *self._profitability(spec),
            *self._valuation(spec),
            *self._growth(spec),
            *self._direction(spec),
            *self._rebalance(spec),
        ]

    # -- rules ---------------------------------------------------------------

    def _profitability(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        blob = self._blob(spec)
        if _hits(blob, _PROFITABILITY_HINTS) and not _hits(blob, _PROFITABILITY_CONCRETE):
            return [
                self._blocking(
                    "profitability_definition",
                    "请明确「盈利质量」的具体口径。",
                    _PROFITABILITY_OPTIONS,
                    field="variables",
                )
            ]
        return []

    def _valuation(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        blob = self._blob(spec)
        if _hits(blob, _VALUATION_HINTS) and not _hits(blob, _VALUATION_CONCRETE):
            return [
                self._blocking(
                    "valuation_definition",
                    "请明确「估值」的具体指标。",
                    _VALUATION_OPTIONS,
                    field="variables",
                )
            ]
        return []

    def _growth(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        blob = self._blob(spec)
        if _hits(blob, _GROWTH_HINTS) and not _hits(blob, _GROWTH_CONCRETE):
            return [
                self._blocking(
                    "growth_definition",
                    "请明确「增长」的具体口径。",
                    _GROWTH_OPTIONS,
                    field="variables",
                )
            ]
        return []

    def _direction(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        if spec.direction is None:
            return [
                self._blocking(
                    "direction",
                    "该因子的方向是什么（数值越大越好 / 越小越好）？",
                    ["higher_is_better", "lower_is_better"],
                    field="direction",
                )
            ]
        return []

    def _rebalance(self, spec: FactorSpec) -> list[ClarificationQuestion]:
        if spec.rebalance_frequency is None:
            return [
                ClarificationQuestion(
                    question_id="rebalance_frequency",
                    question="调仓频率采用哪个？",
                    options=["daily", "weekly", "monthly"],
                    field="rebalance_frequency",
                    recommended="monthly",
                    blocking=False,
                )
            ]
        return []

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _blocking(
        question_id: str, question: str, options: list[str], *, field: str
    ) -> ClarificationQuestion:
        return ClarificationQuestion(
            question_id=question_id,
            question=question,
            options=options,
            field=field,
            blocking=True,
        )

    @staticmethod
    def _blob(spec: FactorSpec) -> str:
        parts = [spec.factor_name, spec.hypothesis, spec.formula_text]
        for variable in spec.variables:
            parts.append(variable.logical_name)
            parts.append(variable.meaning or "")
        return " ".join(parts).lower()


def _hits(blob: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in blob for needle in needles)


__all__ = ["ClarificationEngine"]
