"""Trust boundary B4: internal data leaving for an external model service.

The other boundaries in this system protect the platform from what comes in. B4
protects the data from what goes out, and it is the only boundary whose failure
is silent — a prompt carrying a Wind price table to a third-party API succeeds,
returns a useful answer, and leaves no trace that licensed data was
redistributed. Nothing downstream will ever flag it.

So the filter is default-deny by category, and it runs *before* the provider is
handed anything. The discriminator that does most of the work: a Wind field
**name** is schema metadata and may leave; a Wind field **value** is data and
may not. ``{"field": "s_dq_close"}`` is a candidate to show the user;
``{"s_dq_close": 1688.0}`` is a row out of the database.

``LOCAL_ONLY_MODE`` turns the boundary into a wall: no external call at all.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, TypeVar

from pydantic import BaseModel

from factor_platform.domain.errors import DomainError, LLMResponseError
from factor_platform.llm.base import ChatMessage, ProviderHealth, UsageRecord

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from factor_platform.llm.base import LLMProvider
    from factor_platform.llm.usage import LLMUsageSink

T = TypeVar("T", bound=BaseModel)


class OutboundCategory(StrEnum):
    """Why a payload may not leave."""

    WIND_RAW_DATA = "wind_raw_data"
    CONNECTION_STRING = "connection_string"
    SECRET = "secret"
    INTERNAL_CODE = "internal_code"
    FULL_RESULT_DATA = "full_result_data"
    FULL_REPORT_BODY = "full_report_body"


class OutboundViolation(BaseModel):
    """A refusal, described without reproducing what triggered it.

    ``detail`` and ``location`` are written into audit logs, so neither may ever
    carry the offending value — an audit trail that quotes the leak is a leak.
    """

    category: OutboundCategory
    location: str
    detail: str


class OutboundBlockedError(DomainError):
    """Raised when a provider call carries data that may not leave."""

    def __init__(self, violation: OutboundViolation) -> None:
        self.violation = violation
        super().__init__(
            f"outbound blocked at {violation.location}: "
            f"{violation.category.value} ({violation.detail})"
        )


class LocalOnlyModeError(DomainError):
    """Raised when an external model call is attempted in local-only mode."""


# --------------------------------------------------------------------------- detectors

# Wind columns are uniformly prefixed and lowercased by the catalog layer.
_WIND_FIELD: Final = re.compile(
    r"^(s_|f_|b_|w_|ann_|trade_dt$|report_period$|opdate$|object_id$)", re.IGNORECASE
)
_DATE_LIKE: Final = re.compile(r"^\d{8}$|^\d{4}-\d{2}-\d{2}$")

# A field name immediately bound to a number, in JSON or CSV shape. This catches a
# table that was rendered to text before being put in a prompt.
_SERIALIZED_ROW: Final = re.compile(
    r"[\"']?\b((?:s|f|b|w)_[a-z0-9_]+|trade_dt)\b[\"']?\s*[:,=]\s*[\"']?-?\d",
    re.IGNORECASE,
)

_CONNECTION_STRING: Final = re.compile(
    r"\b(?:mysql|postgresql|oracle|mssql|sqlite)(?:\+\w+)?://|"
    r"\b(?:jdbc|dsn)\s*[:=]|"
    r"\bhost\s*=\s*\S+\s*;\s*(?:user|uid)\s*=",
    re.IGNORECASE,
)

_SECRET_VALUE: Final = re.compile(
    r"\bsk-[A-Za-z0-9_-]{20,}|"
    r"\bgh[pousr]_[A-Za-z0-9]{30,}|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

# Keys whose *name* means the value is a credential, whatever the value looks like.
_SECRET_KEY: Final = re.compile(
    r"password|passwd|secret|api[_-]?key|access[_-]?token|credential|private[_-]?key"
    r"|_pwd$|^pwd$|^token$|_user$|^user$|^uid$|^dsn$",
    re.IGNORECASE,
)

_CODE_KEY: Final = re.compile(r"generated_code|factor_py|source_code|^script$|^source$")
_CODE_SHAPE: Final = re.compile(r"^\s*(?:import\s+\w|from\s+\w+\s+import\s|def\s+\w+\s*\()", re.M)

_REPORT_BODY_KEY: Final = re.compile(r"report_text|report_body|pdf_text|full_text|raw_text")
_REPORT_EXCERPT_KEY: Final = re.compile(r"report_excerpt|excerpt")

_RESULT_KEY: Final = re.compile(r"factor_values|result_rows|execution_result|^values$")

# Above this, a list stops being an example and becomes the data itself.
_MAX_RESULT_ROWS: Final = 20


def _is_data_row(mapping: Mapping[str, Any]) -> bool:
    """True when a mapping looks like a row of Wind values rather than metadata.

    Metadata is names describing a schema; a row binds those names to observed
    values. The presence of a number or a date under a Wind-shaped key is what
    separates them.
    """
    has_wind_key = any(_WIND_FIELD.search(str(key)) for key in mapping)
    if not has_wind_key:
        return False
    return any(
        isinstance(value, bool | int | float)
        or (isinstance(value, str) and _DATE_LIKE.match(value))
        for value in mapping.values()
    )


class OutboundFilter:
    """Decides whether a payload may be sent to an external model service.

    ``allow_report_excerpt`` opts into sending a bounded, redacted slice of a
    research report; without it no report text leaves at all.
    """

    def __init__(
        self, *, allow_report_excerpt: bool = False, max_excerpt_chars: int = 2000
    ) -> None:
        self._allow_report_excerpt = allow_report_excerpt
        self._max_excerpt_chars = max_excerpt_chars

    def check(self, payload: Any) -> OutboundViolation | None:
        """Return the first violation in ``payload``, or ``None`` if it may leave."""
        for violation in self._walk(payload, path=()):
            return violation
        return None

    def _walk(self, node: Any, *, path: tuple[str, ...]) -> Iterator[OutboundViolation]:
        location = ".".join(path) or "<root>"

        if isinstance(node, Mapping):
            if _is_data_row(node):
                yield OutboundViolation(
                    category=OutboundCategory.WIND_RAW_DATA,
                    location=location,
                    detail="mapping binds Wind field names to observed values",
                )
                return
            for key, value in node.items():
                yield from self._check_key(str(key), value, path=(*path, str(key)))
                yield from self._walk(value, path=(*path, str(key)))
            return

        if isinstance(node, str):
            yield from self._check_text(node, location=location)
            return

        if isinstance(node, Sequence) and not isinstance(node, str | bytes):
            if len(node) > _MAX_RESULT_ROWS and all(
                isinstance(item, Mapping) for item in node
            ):
                yield OutboundViolation(
                    category=OutboundCategory.FULL_RESULT_DATA,
                    location=location,
                    detail=f"{len(node)} rows exceeds the {_MAX_RESULT_ROWS}-row sample limit",
                )
                return
            for index, item in enumerate(node):
                yield from self._walk(item, path=(*path, str(index)))

    def _check_key(
        self, key: str, value: Any, *, path: tuple[str, ...]
    ) -> Iterator[OutboundViolation]:
        location = ".".join(path)

        if _SECRET_KEY.search(key):
            yield OutboundViolation(
                category=OutboundCategory.SECRET,
                location=location,
                detail=f"key {key!r} names a credential",
            )
            return

        if _CODE_KEY.search(key):
            yield OutboundViolation(
                category=OutboundCategory.INTERNAL_CODE,
                location=location,
                detail=f"key {key!r} carries internal source",
            )
            return

        if _RESULT_KEY.search(key) and isinstance(value, Sequence | Mapping):
            yield OutboundViolation(
                category=OutboundCategory.FULL_RESULT_DATA,
                location=location,
                detail=f"key {key!r} carries computed result data",
            )
            return

        if _REPORT_BODY_KEY.search(key):
            yield OutboundViolation(
                category=OutboundCategory.FULL_REPORT_BODY,
                location=location,
                detail=f"key {key!r} carries full report text",
            )
            return

        if _REPORT_EXCERPT_KEY.search(key):
            yield from self._check_excerpt(value, location=location)

    def _check_excerpt(self, value: Any, *, location: str) -> Iterator[OutboundViolation]:
        if not self._allow_report_excerpt:
            yield OutboundViolation(
                category=OutboundCategory.FULL_REPORT_BODY,
                location=location,
                detail="report excerpts are disabled (OUTBOUND_ALLOW_REPORT_EXCERPT)",
            )
            return
        if isinstance(value, str) and len(value) > self._max_excerpt_chars:
            yield OutboundViolation(
                category=OutboundCategory.FULL_REPORT_BODY,
                location=location,
                detail=(
                    f"excerpt of {len(value)} chars exceeds the "
                    f"{self._max_excerpt_chars}-char limit"
                ),
            )

    def _check_text(self, text: str, *, location: str) -> Iterator[OutboundViolation]:
        if _SECRET_VALUE.search(text):
            yield OutboundViolation(
                category=OutboundCategory.SECRET,
                location=location,
                detail="text contains a credential-shaped token",
            )
            return
        if _CONNECTION_STRING.search(text):
            yield OutboundViolation(
                category=OutboundCategory.CONNECTION_STRING,
                location=location,
                detail="text contains database connection details",
            )
            return
        if _SERIALIZED_ROW.search(text):
            yield OutboundViolation(
                category=OutboundCategory.WIND_RAW_DATA,
                location=location,
                detail="text binds a Wind field name to a numeric value",
            )
            return
        if _CODE_SHAPE.search(text):
            yield OutboundViolation(
                category=OutboundCategory.INTERNAL_CODE,
                location=location,
                detail="text contains executable source",
            )


# --------------------------------------------------------------------------- enforcement


class GuardedProvider:
    """Wraps a provider so nothing reaches it without passing the filter.

    The filter runs before delegation, so a blocked call is not a call: the
    provider is never given the payload to transmit, and the refusal is recorded
    with its category but never its content.
    """

    def __init__(
        self,
        provider: LLMProvider,
        outbound_filter: OutboundFilter,
        *,
        usage_sink: LLMUsageSink | None = None,
        local_only_mode: bool = False,
    ) -> None:
        self._provider = provider
        self._filter = outbound_filter
        self._usage_sink = usage_sink or getattr(provider, "_usage_sink", None)
        self._local_only_mode = local_only_mode
        self.name = provider.name

    async def structured_chat(self, messages: list[ChatMessage], response_model: type[T]) -> T:
        if self._local_only_mode:
            raise LocalOnlyModeError("local-only mode forbids external model calls")

        text_length = sum(len(message.content) for message in messages)
        payload = {"messages": [message.model_dump() for message in messages]}

        violation = self._filter.check(payload)
        if violation is not None:
            self._audit(success=False, text_length=text_length, violation=violation)
            raise OutboundBlockedError(violation)

        start = time.monotonic()
        try:
            result = await self._provider.structured_chat(messages, response_model)
        except LLMResponseError:
            self._audit(
                success=False,
                text_length=text_length,
                failure_reason="invalid_structured_output",
                latency_ms=(time.monotonic() - start) * 1000,
            )
            raise
        self._audit(
            success=True, text_length=text_length, latency_ms=(time.monotonic() - start) * 1000
        )
        return result

    def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        if self._local_only_mode:
            raise LocalOnlyModeError("local-only mode forbids external model calls")
        violation = self._filter.check(
            {"messages": [message.model_dump() for message in messages]}
        )
        if violation is not None:
            raise OutboundBlockedError(violation)
        return self._provider.stream_chat(messages)

    async def health_check(self) -> ProviderHealth:
        if self._local_only_mode:
            return ProviderHealth(healthy=False, error="local_only_mode")
        return await self._provider.health_check()

    def _audit(
        self,
        *,
        success: bool,
        text_length: int,
        violation: OutboundViolation | None = None,
        failure_reason: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """Record what left, how much of it, and why it was refused — never the body."""
        if self._usage_sink is None:
            return
        self._usage_sink.record(
            UsageRecord(
                provider=self.name,
                model=getattr(self._provider, "model", None),
                success=success,
                blocked=violation is not None,
                text_length=text_length,
                failure_reason=(
                    violation.category.value if violation is not None else failure_reason
                ),
                latency_ms=latency_ms,
            )
        )


__all__ = [
    "GuardedProvider",
    "LocalOnlyModeError",
    "OutboundBlockedError",
    "OutboundCategory",
    "OutboundFilter",
    "OutboundViolation",
]
