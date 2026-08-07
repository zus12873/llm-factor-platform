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
    # Revisions: the user changes a settled decision and the session rewinds to
    # the earliest step that decision invalidates.
    REQUEST_REVISED = "request_revised"
    FORMULA_REVISED = "formula_revised"
    FIELDS_REVISED = "fields_revised"
    UNIVERSE_REVISED = "universe_revised"
    DATE_RANGE_REVISED = "date_range_revised"
    PREPROCESSING_REVISED = "preprocessing_revised"
    TIME_CONVENTION_REVISED = "time_convention_revised"
    EXECUTION_CANCELLED = "execution_cancelled"
    RERUN_REQUESTED = "rerun_requested"
    SESSION_CLONED = "session_cloned"


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

# Revisions rewind the session to the earliest step the change invalidates, and
# every rewind lands on a state that demands the work be redone rather than
# reusing it. ``EXECUTING`` and ``VALIDATING`` are deliberately absent from every
# source set below: a job is in flight, so a revision would race the worker.
# Cancel first, then revise.
_SETTLED: frozenset[SessionState] = frozenset(
    {
        _S.WAITING_FIELD_CONFIRMATION,
        _S.PLANNING_FUNCTIONS,
        _S.CODE_READY,
        _S.COMPLETED,
        _S.FAILED,
    }
)

_REVISIONS: tuple[tuple[EventType, frozenset[SessionState], SessionState], ...] = (
    (
        _E.REQUEST_REVISED,
        _SETTLED | {_S.NEEDS_CLARIFICATION, _S.WAITING_FORMULA_CONFIRMATION},
        _S.PARSING_INPUT,
    ),
    (
        _E.FORMULA_REVISED,
        _SETTLED | {_S.WAITING_FORMULA_CONFIRMATION, _S.SEARCHING_FIELDS},
        _S.WAITING_FORMULA_CONFIRMATION,
    ),
    (_E.FIELDS_REVISED, _SETTLED, _S.WAITING_FIELD_CONFIRMATION),
    (_E.UNIVERSE_REVISED, _SETTLED, _S.PLANNING_FUNCTIONS),
    (_E.DATE_RANGE_REVISED, _SETTLED, _S.PLANNING_FUNCTIONS),
    (_E.PREPROCESSING_REVISED, _SETTLED, _S.PLANNING_FUNCTIONS),
    (_E.TIME_CONVENTION_REVISED, _SETTLED, _S.PLANNING_FUNCTIONS),
    # Lifecycle: cancel a running job, rerun a settled one, clone into a new session.
    (_E.EXECUTION_CANCELLED, frozenset({_S.EXECUTING}), _S.CODE_READY),
    (_E.RERUN_REQUESTED, frozenset({_S.COMPLETED, _S.FAILED}), _S.CODE_READY),
    (_E.SESSION_CLONED, frozenset({_S.CREATED}), _S.WAITING_FORMULA_CONFIRMATION),
)

_TRANSITIONS.update(
    {
        (source, event): target
        for event, sources, target in _REVISIONS
        for source in sources
    }
)


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
