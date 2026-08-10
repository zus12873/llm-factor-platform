"""Classified failure repair, capped at two attempts.

Only a narrow class of failures may be repaired automatically: the ones where the
factor's *intent* is intact and only a parameter or a policy is wrong. A window of
zero, a type mismatch, an alignment error — the user asked for something coherent
and the spec expressed it badly.

Everything else is refused, and the refusals matter more than the repairs:

* **A missing Wind field is not a formula problem.** Asking a model to repair the
  formula would produce a *different factor* that happens to run, and the user
  would receive it as a fix for the one they asked for.
* **Empty data is not a formula problem either.** The right response is to widen
  the window or change the universe — decisions only the user can make.

The cap is two. A third attempt means the classifier was wrong about being able
to fix this, and continuing would burn model calls while walking the factor
further from what was asked for. And every repair produces a *proposal*: the user
confirms the new formula before it can run, because a silently repaired factor is
one nobody agreed to.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from factor_platform.domain.errors import DomainError
from factor_platform.domain.formula import FORMULA_OPERATORS, FormulaNode
from factor_platform.domain.models import ErrorCategory, FactorSpec, StructuredError
from factor_platform.llm.base import ChatMessage, LLMProvider
from factor_platform.llm.data_boundary import GuardedProvider, OutboundFilter

#: Two, and no configuration knob. A third attempt means the classification was
#: wrong, and retrying a misclassification just spends money.
MAX_REPAIR_ATTEMPTS: Final = 2

#: Error codes where the intent survives and only the expression is wrong.
REPAIRABLE_CODES: Final[frozenset[str]] = frozenset(
    {
        "invalid_window",
        "window_out_of_range",
        "invalid_parameter",
        "type_mismatch",
        "divide_by_zero_policy",
        "alignment_error",
        "missing_window",
        "param_count_exceeded",
    }
)

#: Categories that are never a formula problem, whatever the model might propose.
_NEVER_REPAIRABLE: Final[frozenset[ErrorCategory]] = frozenset(
    {
        ErrorCategory.FIELD,
        ErrorCategory.EMPTY_DATA,
        ErrorCategory.TIME_BASIS,
        ErrorCategory.RESOURCE,
        ErrorCategory.INFRASTRUCTURE,
    }
)

_REFUSAL_REASON: Final[dict[ErrorCategory, str]] = {
    ErrorCategory.FIELD: (
        "字段问题不是公式问题。改公式会得到一个能跑但不同的因子，"
        "而用户会把它当成对原问题的修复。请重新确认字段绑定。"
    ),
    ErrorCategory.EMPTY_DATA: (
        "样本区间无数据不是公式问题。应放宽区间或更换股票池——"
        "这是只有用户能做的决定。"
    ),
    ErrorCategory.TIME_BASIS: (
        "时间口径问题涉及信号可得性，自动修复可能引入未来函数。请人工确认。"
    ),
    ErrorCategory.RESOURCE: "资源限制问题，重试同一公式不会改变结果。",
    ErrorCategory.INFRASTRUCTURE: "基础设施故障，与公式无关。",
}


class RepairLimitError(DomainError):
    """Raised when a session has already used its repair attempts."""


class RepairDecision(BaseModel):
    """Whether this failure may be repaired, and why not when it may not."""

    repairable: bool
    reason: str
    attempts_used: int = 0
    attempts_remaining: int = MAX_REPAIR_ATTEMPTS


class RepairProposal(BaseModel):
    """A proposed new spec version. Requires user confirmation before it runs."""

    spec: FactorSpec
    explanation: str
    attempt: int
    requires_confirmation: bool = True


class _RepairDraft(BaseModel):
    """Model-facing schema: a corrected formula plus what was changed."""

    explanation: str = ""
    formula_ast: dict = {}


class ErrorClassifier:
    """Decides whether a structured error is worth attempting to repair."""

    def classify(self, error: StructuredError, *, attempts_used: int = 0) -> RepairDecision:
        remaining = max(0, MAX_REPAIR_ATTEMPTS - attempts_used)

        if attempts_used >= MAX_REPAIR_ATTEMPTS:
            return RepairDecision(
                repairable=False,
                reason=(
                    f"已用尽 {MAX_REPAIR_ATTEMPTS} 次修复机会。再试意味着分类判断有误，"
                    "继续只会让因子离用户所要的越来越远。"
                ),
                attempts_used=attempts_used,
                attempts_remaining=0,
            )

        if error.category in _NEVER_REPAIRABLE:
            return RepairDecision(
                repairable=False,
                reason=_REFUSAL_REASON[error.category],
                attempts_used=attempts_used,
                attempts_remaining=remaining,
            )

        if error.code not in REPAIRABLE_CODES:
            return RepairDecision(
                repairable=False,
                reason=(
                    f"错误码 {error.code} 未登记为可修复。未登记即不修复——"
                    "自动修复一个没想清楚的失败类型，产出的是另一个因子。"
                ),
                attempts_used=attempts_used,
                attempts_remaining=remaining,
            )

        return RepairDecision(
            repairable=True,
            reason=f"{error.code} 属于参数/表达问题，因子意图未变，可尝试修复",
            attempts_used=attempts_used,
            attempts_remaining=remaining,
        )


class RepairService:
    """Proposes a corrected spec for a repairable failure."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        classifier: ErrorClassifier | None = None,
        outbound_filter: OutboundFilter | None = None,
    ) -> None:
        self._provider = GuardedProvider(provider, outbound_filter or OutboundFilter())
        self._classifier = classifier or ErrorClassifier()

    async def propose(
        self, spec: FactorSpec, error: StructuredError, *, attempts_used: int = 0
    ) -> RepairProposal:
        decision = self._classifier.classify(error, attempts_used=attempts_used)
        if not decision.repairable:
            raise RepairLimitError(decision.reason)

        draft = await self._provider.structured_chat(
            _messages(spec, error), _RepairDraft
        )

        # Validate the model's AST before it goes anywhere near a spec. An
        # unvalidated dict would pass model_copy and fail later, at a point where
        # the error no longer says "the repair was malformed".
        repaired_ast = (
            FormulaNode.model_validate(draft.formula_ast)
            if draft.formula_ast
            else spec.formula_ast
        )

        # A new version, never a mutation: the failed spec stays in the history so
        # the user can see what changed and why.
        return RepairProposal(
            spec=spec.model_copy(
                update={"version": spec.version + 1, "formula_ast": repaired_ast}
            ),
            explanation=draft.explanation,
            attempt=attempts_used + 1,
        )


_SYSTEM_PROMPT: Final = f"""你修复一个因子公式中的参数或表达错误。

约束：
1. 只修正错误所指出的问题，**不得改变因子的研究意图**。
2. 只能使用以下算子：{", ".join(sorted(FORMULA_OPERATORS))}
3. 不得新增或删除变量。
4. 不得改变时间口径或股票池。
5. explanation 用一句话说明改了什么、为什么。

输出修正后的 formula_ast 与 explanation。
"""


def _messages(spec: FactorSpec, error: StructuredError) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"当前公式：{spec.canonical_formula}\n"
                f"AST：{spec.formula_ast.model_dump_json(exclude_none=True)}\n"
                f"错误：[{error.category.value}/{error.code}] {error.message}"
            ),
        ),
    ]


__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "REPAIRABLE_CODES",
    "ErrorClassifier",
    "RepairDecision",
    "RepairLimitError",
    "RepairProposal",
    "RepairService",
]
