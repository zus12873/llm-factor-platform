"""Extract a factor definition from a research report, with citable evidence.

Three constraints shape this, and each one exists because of a specific way the
obvious implementation goes wrong.

**Only scored excerpts leave, never the report.** Boundary B4 forbids sending a
full report body to an external model, and this is the first place that rule
actually bites. Blocks are scored locally for formula/variable/sample language,
and only the top few — each tagged with its page — are sent.

**Every extracted variable must cite an evidence id that was in the input.** A
model asked to cite its source will happily invent a page number. Checking the
ids against what was actually sent turns that from an invisible failure into a
rejected extraction.

**Low confidence means manual confirmation, never execution.** A formula lifted
from a two-column page or an image caption parses fine and is wrong. When the
page layout flags say the text is unreliable, or the model's own confidence is
low, the status is forced to ``needs_manual_confirmation`` and the workflow stops
for a human — the platform does not execute a formula it is not sure it read.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field, field_validator

from factor_platform.domain.errors import DomainError
from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import DataRequirement, FactorDirection
from factor_platform.factor.ast_checks import check_ast
from factor_platform.llm.base import ChatMessage, LLMProvider
from factor_platform.llm.data_boundary import (
    GuardedProvider,
    LocalOnlyModeError,
    OutboundFilter,
)
from factor_platform.reports.pdf import ParsedReport

#: Terms that mark a block as likely to carry a factor definition. Scored locally
#: so the selection itself never requires sending anything out.
_SIGNALS: Final[dict[str, float]] = {
    "因子": 3.0, "定义": 2.5, "构建": 2.0, "公式": 3.0, "排名": 1.5,
    "横截面": 2.0, "标准化": 1.5, "中性化": 2.0, "调仓": 2.0, "回测": 1.0,
    "样本": 1.0, "股票池": 2.0, "收益率": 1.5, "市值": 1.5, "换手": 1.5,
    "factor": 3.0, "define": 2.0, "formula": 3.0, "rank": 1.5,
    "cross-sectional": 2.0, "universe": 2.0, "rebalance": 2.0, "backtest": 1.0,
}

#: How many excerpts may leave. A bound, not a tuning knob: it caps what crosses
#: B4 regardless of how long the report is.
MAX_EXCERPTS: Final = 8
MAX_EXCERPT_CHARS: Final = 600

#: Below this, the extraction must be confirmed by a human before it can run.
CONFIDENCE_THRESHOLD: Final = 0.7

#: Layout shapes that make extracted text unreliable regardless of confidence.
_UNRELIABLE_LAYOUT: Final[frozenset[str]] = frozenset({"multi_column", "has_image"})


class ExtractionError(DomainError):
    """Raised when an extraction cannot be trusted."""


class FormulaExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    NEEDS_MANUAL_CONFIRMATION = "needs_manual_confirmation"


class Excerpt(BaseModel):
    """One scored block, with the identifier the model must cite."""

    evidence_id: str
    page_number: int
    text: str
    score: float
    bbox: tuple[float, float, float, float]


class FormulaExtraction(BaseModel):
    """What was extracted, how sure we are, and what the user must do about it."""

    status: FormulaExtractionStatus
    confidence: float = 0.0
    source_pages: list[int] = Field(default_factory=list)
    extracted_text: str = ""
    warning: str = ""
    formula_ast: FormulaNode | None = None


class ExtractedFactor(BaseModel):
    factor_name: str = ""
    hypothesis: str = ""
    direction: FactorDirection | None = None
    variables: list[dict[str, str]] = Field(default_factory=list)
    evidence: list[Excerpt] = Field(default_factory=list)
    formula_extraction: FormulaExtraction


class _ExtractedVariableDraft(BaseModel):
    """One report variable with stable keys for downstream confirmation."""

    logical_name: str
    meaning: str = ""

    @field_validator("logical_name")
    @classmethod
    def normalize_logical_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("logical_name must not be empty")
        return normalized


class _ExtractionDraft(BaseModel):
    """The model-facing schema. Every claim must cite an evidence id."""

    factor_name: str = ""
    hypothesis: str = ""
    direction: FactorDirection | None = None
    confidence: float = 0.0
    extracted_formula_text: str = ""
    formula_ast: FormulaNode | None = None
    variables: list[_ExtractedVariableDraft] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)


def score_blocks(report: ParsedReport) -> list[Excerpt]:
    """Rank blocks by how likely they are to define a factor.

    Local and deterministic. The report never leaves to decide what to send.
    """
    excerpts: list[Excerpt] = []
    for page in report.pages:
        for index, block in enumerate(page.blocks):
            lowered = block.text.lower()
            score = sum(
                weight for term, weight in _SIGNALS.items() if term.lower() in lowered
            )
            if score <= 0:
                continue
            excerpts.append(
                Excerpt(
                    evidence_id=f"p{page.page_number}b{index}",
                    page_number=page.page_number,
                    text=block.text[:MAX_EXCERPT_CHARS],
                    score=score,
                    bbox=block.bbox,
                )
            )
    excerpts.sort(key=lambda e: (-e.score, e.page_number, e.evidence_id))
    return excerpts[:MAX_EXCERPTS]


class ReportExtractor:
    """Turns a parsed report into a factor draft with citable evidence."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        outbound_filter: OutboundFilter | None = None,
        local_only_mode: bool = False,
    ) -> None:
        self._provider = GuardedProvider(
            provider,
            outbound_filter or OutboundFilter(allow_report_excerpt=True),
            local_only_mode=local_only_mode,
        )
        self._local_only = local_only_mode

    async def extract(self, report: ParsedReport) -> ExtractedFactor:
        excerpts = score_blocks(report)
        if not excerpts:
            return _manual(
                "报告中未找到与因子定义相关的段落，请手动输入公式",
                excerpts=[],
            )

        if self._local_only:
            # Local-only mode still produces the evidence; it just refuses to ask
            # a model what it means.
            return _manual(
                "全本地模式：不调用外部模型，请依据下列证据手动确认公式",
                excerpts=excerpts,
            )

        try:
            draft = await self._provider.structured_chat(
                _messages(excerpts), _ExtractionDraft
            )
        except LocalOnlyModeError:
            return _manual("全本地模式禁止外部模型调用，请手动确认公式", excerpts)
        except Exception as exc:  # noqa: BLE001 - surfaced as manual confirmation
            return _manual(f"抽取失败，请手动确认公式：{exc}", excerpts)

        return self._audit(draft, excerpts, report)

    # ------------------------------------------------------------------ checks

    def _audit(
        self,
        draft: _ExtractionDraft,
        excerpts: Sequence[Excerpt],
        report: ParsedReport,
    ) -> ExtractedFactor:
        variables = [variable.model_dump() for variable in draft.variables]
        formula_ast = _normalize_formula_names(draft.formula_ast)
        known = {excerpt.evidence_id for excerpt in excerpts}
        invented = [eid for eid in draft.cited_evidence_ids if eid not in known]
        if invented:
            # A fabricated citation is the failure mode this check exists for:
            # the answer looks sourced and is not.
            return _manual(
                f"模型引用了输入中不存在的证据 {invented}，抽取结果不可信",
                excerpts,
            )

        cited = [e for e in excerpts if e.evidence_id in set(draft.cited_evidence_ids)]
        pages = sorted({e.page_number for e in cited}) or [
            e.page_number for e in excerpts[:1]
        ]

        unreliable = _unreliable_pages(report, pages)
        if unreliable:
            return _manual(
                f"第 {unreliable} 页为多栏或含图片版式，正文顺序可能错乱，"
                "公式需人工确认",
                cited or excerpts,
                factor_name=draft.factor_name,
                hypothesis=draft.hypothesis,
                direction=draft.direction,
                variables=variables,
                extracted_text=draft.extracted_formula_text,
                formula_ast=formula_ast,
                confidence=draft.confidence,
            )

        if draft.confidence < CONFIDENCE_THRESHOLD:
            return _manual(
                f"公式识别置信度 {draft.confidence:.2f} 低于阈值 "
                f"{CONFIDENCE_THRESHOLD}，需人工确认",
                cited or excerpts,
                factor_name=draft.factor_name,
                hypothesis=draft.hypothesis,
                direction=draft.direction,
                variables=variables,
                extracted_text=draft.extracted_formula_text,
                formula_ast=formula_ast,
                confidence=draft.confidence,
            )

        missing: list[str] = []
        if not variables:
            missing.append("variables")
        if formula_ast is None:
            missing.append("formula_ast")
        if draft.direction is None:
            missing.append("direction")
        if missing:
            return _manual(
                f"模型未提供可执行因子草稿所需字段 {missing}，需人工确认",
                cited or excerpts,
                factor_name=draft.factor_name,
                hypothesis=draft.hypothesis,
                direction=draft.direction,
                variables=variables,
                extracted_text=draft.extracted_formula_text,
                formula_ast=formula_ast,
                confidence=draft.confidence,
            )

        assert formula_ast is not None
        requirements = [DataRequirement.model_validate(variable) for variable in variables]
        ast_errors = [
            finding.code
            for finding in check_ast(formula_ast, requirements).findings
            if finding.severity == "error"
        ]
        if ast_errors:
            return _manual(
                f"模型公式 AST 未通过后端校验 {sorted(ast_errors)}，需人工确认",
                cited or excerpts,
                factor_name=draft.factor_name,
                hypothesis=draft.hypothesis,
                direction=draft.direction,
                variables=variables,
                extracted_text=draft.extracted_formula_text,
                formula_ast=formula_ast,
                confidence=draft.confidence,
            )

        return ExtractedFactor(
            factor_name=draft.factor_name,
            hypothesis=draft.hypothesis,
            direction=draft.direction,
            variables=variables,
            evidence=list(cited),
            formula_extraction=FormulaExtraction(
                status=FormulaExtractionStatus.EXTRACTED,
                confidence=draft.confidence,
                source_pages=pages,
                extracted_text=draft.extracted_formula_text,
                formula_ast=formula_ast,
            ),
        )


def _unreliable_pages(report: ParsedReport, pages: Sequence[int]) -> list[int]:
    by_number = {page.page_number: page for page in report.pages}
    return [
        number
        for number in pages
        if by_number.get(number)
        and _UNRELIABLE_LAYOUT & set(by_number[number].layout_flags)
    ]


def _normalize_formula_names(node: FormulaNode | None) -> FormulaNode | None:
    if node is None:
        return None
    if node.type == "variable":
        assert node.name is not None
        return node.model_copy(update={"name": node.name.strip().lower()})
    if node.type == "call":
        return node.model_copy(
            update={"args": [_normalize_formula_names(arg) for arg in node.args]}
        )
    return node


def _manual(
    warning: str,
    excerpts: Sequence[Excerpt],
    *,
    factor_name: str = "",
    hypothesis: str = "",
    direction: FactorDirection | None = None,
    variables: list[dict[str, str]] | None = None,
    extracted_text: str = "",
    formula_ast: FormulaNode | None = None,
    confidence: float = 0.0,
) -> ExtractedFactor:
    return ExtractedFactor(
        factor_name=factor_name,
        hypothesis=hypothesis,
        direction=direction,
        variables=list(variables or []),
        evidence=list(excerpts),
        formula_extraction=FormulaExtraction(
            status=FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION,
            confidence=confidence,
            source_pages=sorted({e.page_number for e in excerpts}),
            extracted_text=extracted_text,
            formula_ast=formula_ast,
            warning=warning,
        ),
    )


_SYSTEM_PROMPT = """你从卖方研报的片段中抽取因子定义。

规则：
1. 只依据给出的片段作答，不得引入片段之外的信息。
2. 每条结论必须在 cited_evidence_ids 中给出对应的证据 ID。
3. 证据 ID 必须来自输入；不得编造。
4. 无法确定的字段留空，不要猜测；confidence 如实反映把握程度。
5. 调仓频率、股票池、时间窗口等未明确写出的内容一律留空。
"""


def _messages(excerpts: Sequence[Excerpt]) -> list[ChatMessage]:
    body = "\n\n".join(
        f"[{excerpt.evidence_id}] （第 {excerpt.page_number} 页）\n{excerpt.text}"
        for excerpt in excerpts
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=body),
    ]


_WHITESPACE = re.compile(r"\s+")

__all__ = [
    "CONFIDENCE_THRESHOLD",
    "MAX_EXCERPTS",
    "Excerpt",
    "ExtractedFactor",
    "ExtractionError",
    "FormulaExtraction",
    "FormulaExtractionStatus",
    "ReportExtractor",
    "score_blocks",
]
