"""Event-sourced session state machine.

The current state of a session is always the reduction of its immutable event log.
``apply_event`` is a pure function over ``(SessionState, EventType)``; every legal
transition is enumerated so that anything not allowed is rejected. Confirmation
requests must carry the version they were made against; an out-of-order or stale
event therefore can never silently corrupt the aggregate.
"""

from __future__ import annotations

from enum import StrEnum

from factor_platform.domain.errors import IllegalTransitionError


class SessionState(StrEnum):
    CREATED = "created"
    PARSING_INPUT = "parsing_input"
    NEEDS_CLARIFICATION = "needs_clarification"
    WAITING_FORMULA_CONFIRMATION = "waiting_formula_confirmation"
    SEARCHING_FIELDS = "searching_fields"
    WAITING_FIELD_CONFIRMATION = "waiting_field_confirmation"
    PLANNING_FUNCTIONS = "planning_functions"
    CODE_READY = "code_ready"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(StrEnum):
    PARSE_STARTED = "parse_started"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RESOLVED = "clarification_resolved"
    FORMULA_PROPOSED = "formula_proposed"
    FORMULA_CONFIRMED = "formula_confirmed"
    FIELD_CANDIDATES_FOUND = "field_candidates_found"
    FIELDS_CONFIRMED = "fields_confirmed"
    CODE_GENERATED = "code_generated"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    VALIDATION_PASSED = "validation_passed"
    EXECUTION_FAILED = "execution_failed"
    VALIDATION_FAILED = "validation_failed"
    REPAIR_PROPOSED = "repair_proposed"


# Short aliases keep the exhaustive legal-transition table readable on one line.
# Absent (state, event) pairs are rejected.
_S = SessionState
_E = EventType

_TRANSITIONS: dict[tuple[SessionState, EventType], SessionState] = {
    (_S.CREATED, _E.PARSE_STARTED): _S.PARSING_INPUT,
    (_S.PARSING_INPUT, _E.CLARIFICATION_REQUESTED): _S.NEEDS_CLARIFICATION,
    (_S.PARSING_INPUT, _E.FORMULA_PROPOSED): _S.WAITING_FORMULA_CONFIRMATION,
    (_S.NEEDS_CLARIFICATION, _E.CLARIFICATION_RESOLVED): _S.PARSING_INPUT,
    (_S.NEEDS_CLARIFICATION, _E.FORMULA_PROPOSED): _S.WAITING_FORMULA_CONFIRMATION,
    (_S.WAITING_FORMULA_CONFIRMATION, _E.FORMULA_CONFIRMED): _S.SEARCHING_FIELDS,
    (_S.SEARCHING_FIELDS, _E.FIELD_CANDIDATES_FOUND): _S.WAITING_FIELD_CONFIRMATION,
    (_S.WAITING_FIELD_CONFIRMATION, _E.FIELDS_CONFIRMED): _S.PLANNING_FUNCTIONS,
    (_S.PLANNING_FUNCTIONS, _E.CODE_GENERATED): _S.CODE_READY,
    (_S.CODE_READY, _E.EXECUTION_STARTED): _S.EXECUTING,
    (_S.EXECUTING, _E.EXECUTION_SUCCEEDED): _S.VALIDATING,
    (_S.VALIDATING, _E.VALIDATION_PASSED): _S.COMPLETED,
    (_S.EXECUTING, _E.EXECUTION_FAILED): _S.FAILED,
    (_S.VALIDATING, _E.VALIDATION_FAILED): _S.FAILED,
    # Repair: a classified failure can propose a new formula version.
    (_S.FAILED, _E.REPAIR_PROPOSED): _S.WAITING_FORMULA_CONFIRMATION,
    (_S.FAILED, _E.CODE_GENERATED): _S.CODE_READY,
}


def apply_event(state: SessionState, event: EventType) -> SessionState:
    """Return the state produced by applying ``event`` in ``state``.

    Raises :class:`IllegalTransitionError` (a ``ValueError``) if the pair is not a
    legal transition, so callers can match on ``"illegal transition"``.
    """
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as exc:
        raise IllegalTransitionError(
            f"illegal transition: {state.value} cannot accept {event.value}"
        ) from exc


__all__ = ["EventType", "SessionState", "apply_event"]
