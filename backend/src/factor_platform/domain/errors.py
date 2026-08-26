"""Domain-level exceptions shared across modules.

Module-specific errors (e.g. ``UnsafeProgramError``, ``ReportLimitError``) live with
their own modules; this module holds only the cross-cutting domain errors.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all factor-platform domain errors."""


class IllegalTransitionError(DomainError, ValueError):
    """Raised when a session state transition is not permitted."""


class ConcurrentUpdateError(DomainError):
    """Raised when a stale ``expected_version`` conflicts with the stored aggregate."""


class DisputedMetricError(DomainError):
    """Raised when a metric mapping known to be wrong is about to be used.

    Blocking rather than warning is deliberate: the disputed mappings all produce
    factors that look entirely reasonable, and a warning against a plausible
    number is a warning someone dismisses.
    """


class LLMResponseError(DomainError):
    """Raised when an LLM provider returns output that cannot be validated."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.request_id = request_id
        detail = message
        if provider:
            detail = f"[{provider}] {detail}"
        if request_id:
            detail = f"{detail} (request_id={request_id})"
        super().__init__(detail)


class RealExecutionUnavailableError(DomainError):
    """Raised when a session requests live execution without a live runner."""


class ReportFormulaUnconfirmedError(DomainError):
    """Low-confidence extraction cannot enter the workflow without a typed formula."""


class ReportArtifactNotFoundError(DomainError):
    """Upload id is unknown or its extraction record is missing."""
