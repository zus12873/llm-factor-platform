"""LLM provider protocol, shared types, and structured-output parsing.

The platform never executes model-produced source; the model only returns JSON that
is validated into a Pydantic model. ``parse_structured_content`` is the single place
that turns a content string into a typed object, so the "invalid output ->
``LLMResponseError``" guarantee is identical for every provider.

``FakeLLMProvider`` is a deterministic, offline implementation used by tests and by
the golden CLI when no real Kimi key is configured.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from factor_platform.domain.errors import LLMResponseError

if TYPE_CHECKING:
    from factor_platform.llm.usage import LLMUsageSink

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: str
    content: str


class ProviderHealth(BaseModel):
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None


class UsageRecord(BaseModel):
    schema_version: int = 1
    provider: str
    model: str | None = None
    request_id: str | None = None
    success: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    cost: float | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def structured_chat(
        self, messages: list[ChatMessage], response_model: type[T]
    ) -> T: ...

    def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]: ...

    async def health_check(self) -> ProviderHealth: ...


_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9]*\s*\n?")
_FENCE_CLOSE = re.compile(r"\n?```$")


def parse_structured_content(
    content: str,
    response_model: type[T],
    *,
    provider: str,
    request_id: str | None = None,
) -> T:
    """Validate ``content`` as JSON into ``response_model``.

    Tolerates a single ``` fenced wrapper. Any validation failure raises
    :class:`LLMResponseError`, never returning a partial object.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = _FENCE_OPEN.sub("", text, count=1)
        text = _FENCE_CLOSE.sub("", text, count=1).strip()
    try:
        return response_model.model_validate_json(text)
    except ValidationError as exc:
        raise LLMResponseError(
            "structured output failed validation", provider=provider, request_id=request_id
        ) from exc


class FakeLLMProvider:
    """Deterministic provider for tests and offline runs.

    Callers prime responses with :meth:`enqueue_content`; each ``structured_chat``
    consumes one. Health is a simple settable flag.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        healthy: bool = True,
        model: str = "fake-model",
        usage_sink: LLMUsageSink | None = None,
    ) -> None:
        self.name = name
        self.healthy = healthy
        self.model = model
        self._usage_sink = usage_sink
        self._queue: list[tuple[str, dict[str, int | None]]] = []
        self._call_count = 0

    def enqueue_content(
        self, content: str, *, usage: dict[str, int | None] | None = None
    ) -> None:
        self._queue.append((content, dict(usage or {})))

    async def structured_chat(
        self, messages: list[ChatMessage], response_model: type[T]
    ) -> T:
        del messages  # not used by the fake
        self._call_count += 1
        request_id = f"{self.name}-{self._call_count}"
        content, usage = self._queue.pop(0) if self._queue else ("", {})
        start = time.monotonic()
        success = True
        try:
            return parse_structured_content(
                content, response_model, provider=self.name, request_id=request_id
            )
        except LLMResponseError:
            success = False
            raise
        finally:
            self._record(request_id, success, usage, start)

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        del messages
        content = self._queue.pop(0)[0] if self._queue else ""
        for chunk in content:
            yield chunk

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(healthy=self.healthy)

    def _record(
        self,
        request_id: str,
        success: bool,
        usage: dict[str, int | None],
        start: float,
    ) -> None:
        if self._usage_sink is None:
            return
        self._usage_sink.record(
            UsageRecord(
                provider=self.name,
                model=self.model,
                request_id=request_id,
                success=success,
                latency_ms=(time.monotonic() - start) * 1000,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        )


__all__ = [
    "ChatMessage",
    "FakeLLMProvider",
    "LLMProvider",
    "ProviderHealth",
    "UsageRecord",
    "parse_structured_content",
]
