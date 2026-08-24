import httpx
import pytest
import respx
from pydantic import BaseModel

from factor_platform.domain.errors import LLMResponseError
from factor_platform.llm.base import ChatMessage, FakeLLMProvider
from factor_platform.llm.openai_compatible import OpenAICompatibleProvider


class Answer(BaseModel):
    value: int


async def test_structured_chat_validates_json(mock_provider: FakeLLMProvider) -> None:
    mock_provider.enqueue_content('{"value": 7}')
    answer = await mock_provider.structured_chat([], Answer)
    assert answer == Answer(value=7)


async def test_structured_chat_strips_fenced_json(mock_provider: FakeLLMProvider) -> None:
    mock_provider.enqueue_content('```json\n{"value": 9}\n```')
    answer = await mock_provider.structured_chat([], Answer)
    assert answer.value == 9


async def test_invalid_output_raises_llm_response_error(mock_provider: FakeLLMProvider) -> None:
    mock_provider.enqueue_content("not json at all")
    with pytest.raises(LLMResponseError):
        await mock_provider.structured_chat([], Answer)


@respx.mock
async def test_openai_provider_parses_chat_completion() -> None:
    base = "https://llm.example.com/v1"
    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "req-1",
                "choices": [{"message": {"content": '{"value": 42}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
    )
    provider = OpenAICompatibleProvider(name="kimi", base_url=base, api_key="x", model="m")
    answer = await provider.structured_chat([ChatMessage(role="user", content="hi")], Answer)
    assert answer.value == 42


@respx.mock
async def test_openai_provider_raises_on_http_error() -> None:
    base = "https://llm.example.com/v1"
    sensitive_body = "server detail that must not escape"
    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(500, text=sensitive_body)
    )
    provider = OpenAICompatibleProvider(name="kimi", base_url=base, api_key="x", model="m")
    with pytest.raises(LLMResponseError) as caught:
        await provider.structured_chat([ChatMessage(role="user", content="hi")], Answer)
    assert sensitive_body not in str(caught.value)


def test_openai_provider_appends_schema_to_existing_system_message() -> None:
    provider = OpenAICompatibleProvider(
        name="kimi", base_url="https://llm.example.com/v1", api_key="x", model="m"
    )
    payload = provider._payload(
        [
            ChatMessage(role="system", content="Stage-specific instruction."),
            ChatMessage(role="user", content="hi"),
        ],
        Answer,
    )
    system_messages = [
        message["content"] for message in payload["messages"] if message["role"] == "system"
    ]
    assert len(system_messages) == 1
    assert "Stage-specific instruction." in system_messages[0]
    assert '"value"' in system_messages[0]
    assert "Respond with JSON matching this schema" in system_messages[0]
