import json

import pytest

from factor_platform.domain.errors import LLMResponseError
from factor_platform.domain.models import ResearchRequest
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider


def _request() -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "asset_type": "stock",
            "universe": "000300.SH",
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "research_idea": "higher ROE predicts returns",
        }
    )


def _valid_draft() -> dict:
    return {
        "factor_name": "quality",
        "hypothesis": "ROE predicts returns",
        "direction": "higher_is_better",
        "formula_text": "rank(roe_ttm)",
        "formula_ast": {
            "type": "call",
            "op": "rank",
            "args": [{"type": "variable", "name": "roe_ttm"}],
        },
        "variables": [
            {"logical_name": "roe_ttm", "meaning": "ROE TTM", "point_in_time_required": True}
        ],
    }


async def test_invalid_provider_output_raises_llm_error() -> None:
    provider = FakeLLMProvider()
    provider.enqueue_content("not json at all")
    with pytest.raises(LLMResponseError):
        await FactorParser(provider).parse(_request())


async def test_valid_draft_returns_factor_spec() -> None:
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(_valid_draft()))
    spec = await FactorParser(provider).parse(_request())
    assert spec.factor_name == "quality"
    # Request envelope is merged in, never invented by the model.
    assert spec.asset_type.value == "stock"
    assert spec.universe == "000300.SH"
    assert spec.formula_ast.op == "rank"
