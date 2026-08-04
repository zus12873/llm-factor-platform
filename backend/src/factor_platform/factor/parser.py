"""Natural-language -> FactorSpec parser.

The model returns a validated ``_FactorSpecDraft`` (semantic content only); this
module merges the trusted request envelope (asset type, universe, frequency) into a
full :class:`FactorSpec`. Invalid model output propagates as ``LLMResponseError`` --
the parser never hands back a partial spec.
"""

from __future__ import annotations

from pydantic import BaseModel

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import (
    DataRequirement,
    FactorDirection,
    FactorSpec,
    Frequency,
    ResearchRequest,
)
from factor_platform.llm.base import ChatMessage, LLMProvider
from factor_platform.llm.prompts import build_system_prompt


class _FactorSpecDraft(BaseModel):
    """The LLM-facing schema: semantic fields only, no trusted envelope."""

    factor_name: str
    hypothesis: str = ""
    direction: FactorDirection | None = None
    rebalance_frequency: Frequency | None = None
    formula_text: str
    formula_ast: FormulaNode
    variables: list[DataRequirement]


class FactorParser:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def parse(self, request: ResearchRequest) -> FactorSpec:
        messages = [
            ChatMessage(role="system", content=build_system_prompt(_render_prompt(request))),
            ChatMessage(role="user", content=request.research_idea),
        ]
        draft = await self._provider.structured_chat(messages, _FactorSpecDraft)
        return FactorSpec(
            factor_name=draft.factor_name,
            hypothesis=draft.hypothesis,
            asset_type=request.asset_type,
            universe=request.universe,
            frequency=request.frequency,
            direction=draft.direction,
            rebalance_frequency=draft.rebalance_frequency,
            formula_text=draft.formula_text,
            formula_ast=draft.formula_ast,
            variables=draft.variables,
            preprocessing=request.preprocessing,
        )


def _render_prompt(request: ResearchRequest) -> str:
    return (
        "Convert the user's research idea into a single factor spec. "
        f"Asset type: {request.asset_type.value}; universe: {request.universe}; "
        f"date range: {request.start_date} to {request.end_date}; "
        f"frequency: {request.frequency.value}. "
        "Only emit fields you can justify from the idea; leave unknown fields null."
    )


__all__ = ["FactorParser"]
