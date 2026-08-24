"""OpenAI-compatible HTTP adapter (Kimi / Moonshot and similar).

Talks to ``/chat/completions`` for structured output and ``/models`` for health.
Structured output is requested as ``response_format=json_object`` and the model is
told the schema via the caller's system prompt (see :mod:`factor_platform.llm.prompts`);
the response is then validated into the target Pydantic model.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

from factor_platform.domain.errors import LLMResponseError
from factor_platform.llm.base import (
    ChatMessage,
    ProviderHealth,
    UsageRecord,
    parse_structured_content,
)
from factor_platform.llm.prompts import build_schema_instruction
from factor_platform.llm.usage import LLMUsageSink
from factor_platform.secrets import reveal_secret

if TYPE_CHECKING:
    from pydantic import BaseModel


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: SecretStr | str,
        model: str,
        reasoning_effort: str | None = None,
        temperature: float | None = 0,
        usage_sink: LLMUsageSink | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self._usage_sink = usage_sink
        self._client = client if client is not None else httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def structured_chat(
        self, messages: list[ChatMessage], response_model: type[BaseModel]
    ) -> Any:
        start = time.monotonic()
        request_id: str | None = None
        usage: dict[str, Any] = {}
        try:
            payload = self._payload(messages, response_model)
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            if response.status_code != 200:
                raise LLMResponseError(
                    f"LLM provider returned HTTP {response.status_code}",
                    provider=self.name,
                )
            data = response.json()
            request_id = data.get("id")
            usage = data.get("usage") or {}
            content = data["choices"][0]["message"]["content"]
            result = parse_structured_content(
                content, response_model, provider=self.name, request_id=request_id
            )
        except httpx.HTTPError as exc:
            self._record(request_id, False, usage, start)
            raise LLMResponseError(
                f"LLM provider transport error ({type(exc).__name__})",
                provider=self.name,
            ) from exc
        except Exception:
            self._record(request_id, False, usage, start)
            raise
        self._record(request_id, True, usage, start)
        return result

    def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        raise NotImplementedError("streaming is wired up in the API layer (Task 17)")

    async def health_check(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            response = await self._client.get(
                f"{self._base_url}/models", headers=self._headers()
            )
            return ProviderHealth(
                healthy=response.status_code == 200,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except httpx.HTTPError as exc:
            return ProviderHealth(healthy=False, error=type(exc).__name__)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {reveal_secret(self._api_key)}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[ChatMessage],
        response_model: type[BaseModel],
    ) -> dict[str, Any]:
        rendered = [{"role": message.role, "content": message.content} for message in messages]
        schema_instruction = build_schema_instruction(response_model)
        system_index = next(
            (index for index, part in enumerate(rendered) if part["role"] == "system"),
            None,
        )
        if system_index is None:
            rendered = [
                {"role": "system", "content": schema_instruction},
                *rendered,
            ]
        else:
            # response_format=json_object constrains syntax only. The exact target
            # schema must still be present even when a stage supplies its own
            # system instruction (FactorParser and ReportExtractor both do).
            rendered[system_index] = {
                **rendered[system_index],
                "content": f"{rendered[system_index]['content']}\n\n{schema_instruction}",
            }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": rendered,
            "response_format": {"type": "json_object"},
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _record(
        self,
        request_id: str | None,
        success: bool,
        usage: dict[str, Any],
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


__all__ = ["OpenAICompatibleProvider"]
