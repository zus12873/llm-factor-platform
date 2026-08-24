"""Provider router: prefer the (cheaper) Coding Plan, fall back to metered API.

Health is cached briefly so a single business request does not pay a health-check
round trip per call. The "never fall back after partial output" rule is enforced by
the orchestrator (it pins the chosen provider for the duration of one structured
exchange); the router itself only answers "which provider is healthy right now?".
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from factor_platform.domain.errors import DomainError
from factor_platform.llm.base import ChatMessage, LLMProvider, ProviderHealth
from factor_platform.llm.data_boundary import LocalOnlyModeError

if TYPE_CHECKING:
    from pydantic import BaseModel


class NoHealthyProviderError(DomainError):
    """Raised when no configured provider passes its health check."""


class ProviderRouter:
    name = "configured-provider-router"

    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        health_ttl_seconds: float = 60.0,
        local_only_mode: bool = False,
    ) -> None:
        self._providers = list(providers)
        self._health_ttl = health_ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}
        self.local_only_mode = local_only_mode

    async def active_provider(self) -> LLMProvider:
        """Return the first healthy provider, in configured (preferred) order.

        Refuses outright in local-only mode. The check belongs here rather than
        at the call sites because there is no way to reach a provider without
        first asking the router for one.
        """
        if self.local_only_mode:
            raise LocalOnlyModeError("local-only mode forbids external model calls")
        for provider in self._providers:
            health = await self._health_of(provider)
            if health.healthy:
                return provider
        raise NoHealthyProviderError("no healthy LLM provider available")

    async def structured_chat(
        self, messages: list[ChatMessage], response_model: type[BaseModel]
    ) -> Any:
        """Pin one healthy provider for this complete structured exchange."""
        provider = await self.active_provider()
        return await provider.structured_chat(messages, response_model)

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """Select before yielding; never switch after output has begun."""
        provider = await self.active_provider()
        async for chunk in provider.stream_chat(messages):
            yield chunk

    async def health_check(self) -> ProviderHealth:
        try:
            await self.active_provider()
        except (LocalOnlyModeError, NoHealthyProviderError) as exc:
            return ProviderHealth(healthy=False, error=type(exc).__name__)
        return ProviderHealth(healthy=True)

    def invalidate(self, name: str | None = None) -> None:
        """Drop cached health for ``name`` (or all providers when ``None``)."""
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

    async def _health_of(self, provider: LLMProvider) -> ProviderHealth:
        now = time.monotonic()
        cached = self._cache.get(provider.name)
        if cached is not None and (now - cached[1]) < self._health_ttl:
            return ProviderHealth(healthy=cached[0])
        health = await provider.health_check()
        self._cache[provider.name] = (health.healthy, now)
        return health


__all__ = ["NoHealthyProviderError", "ProviderRouter"]
