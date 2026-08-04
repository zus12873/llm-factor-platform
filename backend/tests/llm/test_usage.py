import contextlib

from pydantic import BaseModel

from factor_platform.domain.errors import LLMResponseError
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.llm.usage import LLMUsageSink


class Answer(BaseModel):
    value: int


async def test_provider_records_token_usage(
    mock_provider: FakeLLMProvider, usage_sink: LLMUsageSink
) -> None:
    mock_provider.enqueue_content(
        '{"value": 7}',
        usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    )
    await mock_provider.structured_chat([], Answer)
    assert usage_sink.records[0].total_tokens == 15
    assert usage_sink.records[0].success is True
    assert usage_sink.records[0].provider == "mock"


async def test_usage_records_failure(
    mock_provider: FakeLLMProvider, usage_sink: LLMUsageSink
) -> None:
    mock_provider.enqueue_content("not json")
    with contextlib.suppress(LLMResponseError):
        await mock_provider.structured_chat([], Answer)
    assert usage_sink.records[0].success is False


def test_usage_summary_aggregates(usage_sink: LLMUsageSink) -> None:
    usage_sink.records.extend(
        [
            _record(success=True, total_tokens=10),
            _record(success=True, total_tokens=20),
            _record(success=False, total_tokens=None),
        ]
    )
    summary = usage_sink.summary()
    assert summary["calls"] == 3
    assert summary["failures"] == 1
    assert summary["total_tokens"] == 30


def _record(*, success: bool, total_tokens: int | None):
    from factor_platform.llm.base import UsageRecord

    return UsageRecord(provider="mock", success=success, total_tokens=total_tokens)
