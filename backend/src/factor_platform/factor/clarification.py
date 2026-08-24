"""Deterministic clarification engine.

The LLM proposes a ``FactorSpec``; this engine independently audits it for
*blocking* ambiguities (terms so vague that guessing would corrupt the factor) and
*non-blocking* defaults (reasonable choices surfaced for explicit confirmation).
The rules are plain Python so blocking behavior is stable and testable without any
LLM call.

The *trigger* words stay here — recognising that "盈利质量" is vague is a language
judgement. The *options* come from the metric registry, because those are metric
definitions and must be reviewable by someone who does not read Python. Keeping a
second copy here is how the picker ends up offering an option the registry has
since marked disputed.
"""

from __future__ import annotations

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import (
    ClarificationQuestion,
    DataRequirement,
    FactorDirection,
    FactorSpec,
    Frequency,
)
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.renderer import render_canonical_formula

# Vague business terms, and the concrete forms that mean the user already chose.
_PROFITABILITY_HINTS = ("盈利质量", "盈利能力", "profitability", "earnings quality")
_PROFITABILITY_CONCRETE = ("roe_ttm", "roa_ttm", "cfo_to_profit", "净资产收益率", "总资产收益率")

_VALUATION_HINTS = ("估值", "估值因子", "valuation", "价值因子", "value factor")
_VALUATION_CONCRETE = ("pe_ttm", "pb", "ps_ttm", "市盈率", "市净率")

_GROWTH_HINTS = (
    "营收增长",
    "收入增长",
    "利润增长",
    "成长性",
    "growth factor",
    "增长因子",
)
_GROWTH_CONCRETE = (
    "revenue_yoy",
    "net_profit_yoy",
    "operating_profit_yoy",
    "营收同比",
    "净利润同比",
)

_COMPOSITE_HINTS = ("复合因子", "composite factor")

_QUESTION_CATEGORY = {
    "profitability_definition": "profitability",
    "valuation_definition": "valuation",
    "growth_definition": "growth",
}


class ClarificationEngine:
    """Audits a FactorSpec and returns the questions a user must resolve."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self._registry = registry or MetricRegistry.load()

    def questions(
        self, spec: FactorSpec, research_idea: str | None = None
    ) -> list[ClarificationQuestion]:
        """Audit the model draft together with the user's original wording.

        The original idea is authoritative for ambiguity detection. A model may
        turn a vague term such as ``估值`` into a concrete PE formula while
        drafting the spec; that must not silently resolve a choice the user never
        made.
        """
        return [
            *self._profitability(spec, research_idea),
            *self._valuation(spec, research_idea),
            *self._growth(spec, research_idea),
            *self._direction(spec),
            *self._rebalance(spec),
        ]

    def apply_answers(self, spec: FactorSpec, answers: dict[str, str]) -> FactorSpec:
        """Apply explicit human choices to a draft without another model call."""
        updated = spec.model_copy(deep=True)
        for question_id, answer in answers.items():
            if question_id == "direction":
                updated.direction = FactorDirection(answer)
                continue
            if question_id == "rebalance_frequency":
                updated.rebalance_frequency = Frequency(answer)
                continue
            category = _QUESTION_CATEGORY.get(question_id)
            if category is None:
                continue
            definition = self._registry.get(answer)
            if definition is None or definition.category != category:
                raise ValueError(f"invalid clarification answer for {question_id}")
            option_names = {option.lower() for option in self._registry.options_for(category)}
            targets = {
                variable.logical_name
                for variable in updated.variables
                if variable.logical_name.lower() in option_names
            }
            if not targets and len(updated.variables) == 1:
                targets = {updated.variables[0].logical_name}
            updated.formula_explanation = (
                f"人工确认采用 {definition.display_zh}（{definition.key}）作为该口径。"
            )
            if not targets:
                # The model may have silently expanded a vague idea into a
                # multi-variable formula (for example cash flow / profit).
                # The human's explicit choice supersedes that guess: replace
                # the whole guessed expression with the selected registered
                # metric instead of trying to splice it into unrelated leaves.
                updated.variables = [
                    DataRequirement(
                        logical_name=answer.lower(),
                        meaning=definition.display_zh,
                        asset_type=updated.asset_type,
                        frequency=updated.frequency,
                        unit=definition.unit,
                        point_in_time_required=definition.time_role == "report_period",
                    )
                ]
                updated.formula_ast = FormulaNode(type="variable", name=answer.lower())
                continue
            replacement = answer.lower()
            updated.variables = [
                DataRequirement(
                    **{
                        **variable.model_dump(mode="python"),
                        "logical_name": replacement,
                        "meaning": definition.display_zh,
                        "unit": definition.unit,
                        "point_in_time_required": definition.time_role == "report_period",
                        "announcement_date_required": False,
                    }
                )
                if variable.logical_name in targets
                else variable
                for variable in updated.variables
            ]
            updated.formula_ast = _rename_variables(updated.formula_ast, targets, replacement)
        updated.canonical_formula = render_canonical_formula(updated.formula_ast)
        return updated

    # -- rules ---------------------------------------------------------------

    def _profitability(
        self, spec: FactorSpec, research_idea: str | None
    ) -> list[ClarificationQuestion]:
        return self._vague_term(
            spec,
            hints=_PROFITABILITY_HINTS,
            concrete=_PROFITABILITY_CONCRETE,
            question_id="profitability_definition",
            question="请明确「盈利质量」的具体口径。",
            category="profitability",
            research_idea=research_idea,
        )

    def _valuation(
        self, spec: FactorSpec, research_idea: str | None
    ) -> list[ClarificationQuestion]:
        return self._vague_term(
            spec,
            hints=_VALUATION_HINTS,
            concrete=_VALUATION_CONCRETE,
            question_id="valuation_definition",
            question="请明确「估值」的具体指标。",
            category="valuation",
            research_idea=research_idea,
        )

    def _growth(self, spec: FactorSpec, research_idea: str | None) -> list[ClarificationQuestion]:
        return self._vague_term(
            spec,
            hints=_GROWTH_HINTS,
            concrete=_GROWTH_CONCRETE,
            question_id="growth_definition",
            question="请明确「增长」的具体口径。",
            category="growth",
            research_idea=research_idea,
        )

    def _vague_term(
        self,
        spec: FactorSpec,
        *,
        hints: tuple[str, ...],
        concrete: tuple[str, ...],
        question_id: str,
        question: str,
        category: str,
        research_idea: str | None,
    ) -> list[ClarificationQuestion]:
        spec_blob = self._blob(spec)
        idea_blob = (research_idea or "").lower()
        # Registered composite templates may intentionally supply a default for
        # one leg (the golden quality-value case is the canonical example). The
        # standalone vague-factor cases must still stop before such a guess.
        vague_in_idea = (
            not _hits(idea_blob, _COMPOSITE_HINTS)
            and _hits(idea_blob, hints)
            and not _hits(idea_blob, concrete)
        )
        vague_in_spec = _hits(spec_blob, hints) and not _hits(spec_blob, concrete)
        if not (vague_in_idea or vague_in_spec):
            return []
        return [
            self._blocking(
                question_id,
                question,
                self._registry.options_for(category),
                field="variables",
            )
        ]

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
        parts = [
            spec.factor_name,
            spec.hypothesis,
            spec.canonical_formula,
            spec.formula_explanation,
        ]
        for variable in spec.variables:
            parts.append(variable.logical_name)
            parts.append(variable.meaning or "")
        return " ".join(parts).lower()


def _hits(blob: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in blob for needle in needles)


def _rename_variables(node: FormulaNode, targets: set[str], replacement: str) -> FormulaNode:
    updated = node.model_copy(deep=True)
    if updated.type == "variable" and updated.name in targets:
        updated.name = replacement
    updated.args = [_rename_variables(child, targets, replacement) for child in updated.args]
    return updated


__all__ = ["ClarificationEngine"]
