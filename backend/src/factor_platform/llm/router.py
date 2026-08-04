"""Provider router: prefer the (cheaper) Coding Plan, fall back to metered API.

Health is cached briefly so a single business request does not pay a health-check
round trip per call. The "never fall back after partial output" rule is enforced by
the orchestrator (it pins the chosen provider for the duration of one structured
exchange); the router itself only answers "which provider is healthy right now?".
"""

from __future__ import annotations

import time

from factor_platform.domain.errors import DomainError
from factor_platform.llm.base import LLMProvider, ProviderHealth


class NoHealthyProviderError(DomainError):
    """Raised when no configured provider passes its health check."""


class ProviderRouter:
    def __init__(self, providers: list[LLMProvider], *, health_ttl_seconds: float = 60.0) -> None:
        if not providers:
            raise ValueError("ProviderRouter requires at least one provider")
        self._providers = list(providers)
        self._health_ttl = health_ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}

    async def active_provider(self) -> LLMProvider:
        """Return the first healthy provider, in configured (preferred) order."""
        for provider in self._providers:
            health = await self._health_of(provider)
            if health.healthy:
                return provider
        raise NoHealthyProviderError("no healthy LLM provider available")

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
