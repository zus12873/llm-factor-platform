"""Semantic reducer: fold an event stream into a *self-consistent* snapshot.

The obvious fold — walk the events, keep the last non-null value for each key —
is wrong as soon as a user revises a settled decision. Revising the formula
emits an event carrying the new spec; it carries no null for the field
selections, plan, build hash or execution result, so the naive fold leaves all
of them attached. The session then advertises a factor whose parts were never
computed together, and nothing in the data says so.

This reducer fixes that by making each event declare two things explicitly:

* ``WRITES`` — the snapshot keys the event is allowed to set. Payloads are built
  by callers and round-trip through JSON; the reducer, not the caller, decides
  what an event may move.
* ``INVALIDATES`` — the downstream keys the event destroys. Invalidation runs
  *before* the write, so an event that revises an upstream value clears what
  depended on it and then records the replacement.

Historical versions need no separate table: the snapshot at version *n* is the
fold of the first *n* events. Storing folded versions alongside the log would be
a second source of truth that can drift from it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from factor_platform.domain.models import SessionSnapshot
from factor_platform.orchestration.states import EventType, SessionState, apply_event

# --------------------------------------------------------------------------- key registry

# Snapshot identity: computed from the stream itself, never folded, never cleared.
PERMANENT_KEYS: Final[frozenset[str]] = frozenset(
    {"schema_version", "session_id", "state", "version"}
)

# Named groups make the cascade table below auditable at a glance. They are the
# stages of the pipeline: what was asked, what it means, where the data lives,
# how it is fetched, what was built, what came out.
_REQUEST: Final[frozenset[str]] = frozenset({"request"})
_PARSE: Final[frozenset[str]] = frozenset({"ambiguities", "clarifications"})
_DEFINITION: Final[frozenset[str]] = frozenset({"factor_spec"})
_FIELD_CANDIDATES: Final[frozenset[str]] = frozenset({"field_candidates"})
_FIELDS: Final[frozenset[str]] = frozenset({"field_selections"})
_PLAN: Final[frozenset[str]] = frozenset({"plan"})
_BUILD: Final[frozenset[str]] = frozenset({"generated_code", "code_sha256"})
_RESULTS: Final[frozenset[str]] = frozenset({"execution_result", "artifact_uri"})
_DIAGNOSTICS: Final[frozenset[str]] = frozenset({"last_error"})

# Everything foldable from a payload.
FOLDED_KEYS: Final[frozenset[str]] = (
    _REQUEST
    | _PARSE
    | _DEFINITION
    | _FIELD_CANDIDATES
    | _FIELDS
    | _PLAN
    | _BUILD
    | _RESULTS
    | _DIAGNOSTICS
)

# --------------------------------------------------------------------------- per-event policy

WRITES: Final[Mapping[EventType, frozenset[str]]] = {
    EventType.PARSE_STARTED: _REQUEST,
    EventType.CLARIFICATION_REQUESTED: _PARSE | _DEFINITION,
    EventType.CLARIFICATION_RESOLVED: _PARSE | _DEFINITION,
    EventType.FORMULA_PROPOSED: _DEFINITION,
    EventType.FORMULA_CONFIRMED: _DEFINITION,
    EventType.FIELD_CANDIDATES_FOUND: _FIELD_CANDIDATES,
    EventType.FIELDS_CONFIRMED: _FIELDS,
    EventType.CODE_GENERATED: _PLAN | _BUILD,
    EventType.EXECUTION_STARTED: frozenset(),
    EventType.EXECUTION_SUCCEEDED: _RESULTS,
    EventType.VALIDATION_PASSED: _RESULTS,
    EventType.EXECUTION_FAILED: _RESULTS | _DIAGNOSTICS,
    EventType.VALIDATION_FAILED: _RESULTS | _DIAGNOSTICS,
    EventType.REPAIR_PROPOSED: _DEFINITION,
    EventType.REQUEST_REVISED: _REQUEST,
    EventType.FORMULA_REVISED: _DEFINITION,
    EventType.FIELDS_REVISED: _FIELDS,
    EventType.UNIVERSE_REVISED: _REQUEST,
    EventType.DATE_RANGE_REVISED: _REQUEST,
    EventType.PREPROCESSING_REVISED: _DEFINITION,
    EventType.TIME_CONVENTION_REVISED: _DEFINITION,
    EventType.EXECUTION_CANCELLED: frozenset(),
    EventType.RERUN_REQUESTED: frozenset(),
    EventType.SESSION_CLONED: _REQUEST | _DEFINITION,
}

# What each event destroys. Absent events destroy nothing.
#
# The rule when a boundary is unclear is to invalidate more, not less:
# over-invalidating costs a rebuild, under-invalidating ships a stale artifact
# as if it were current.
INVALIDATES: Final[Mapping[EventType, frozenset[str]]] = {
    # A new research idea invalidates the parse itself, not just its consequences.
    EventType.REQUEST_REVISED: (
        _PARSE
        | _DEFINITION
        | _FIELD_CANDIDATES
        | _FIELDS
        | _PLAN
        | _BUILD
        | _RESULTS
        | _DIAGNOSTICS
    ),
    # A different formula needs different inputs, hence a different everything.
    EventType.FORMULA_REVISED: (
        _FIELD_CANDIDATES | _FIELDS | _PLAN | _BUILD | _RESULTS | _DIAGNOSTICS
    ),
    # Different columns: the retrieval plan and the build both change.
    EventType.FIELDS_REVISED: _PLAN | _BUILD | _RESULTS | _DIAGNOSTICS,
    # Universe and date range change *which rows* are fetched, not what is
    # computed from them, so the build survives and the plan does not.
    EventType.UNIVERSE_REVISED: _PLAN | _RESULTS | _DIAGNOSTICS,
    EventType.DATE_RANGE_REVISED: _PLAN | _RESULTS | _DIAGNOSTICS,
    # Preprocessing changes the computation, not the retrieval.
    EventType.PREPROCESSING_REVISED: _BUILD | _RESULTS | _DIAGNOSTICS,
    # Timing changes both: which rows are in scope, and how the signal aligns to
    # the trade date inside the build.
    EventType.TIME_CONVENTION_REVISED: _PLAN | _BUILD | _RESULTS | _DIAGNOSTICS,
    # A cancelled run may have written a partial artifact; it is not a result.
    EventType.EXECUTION_CANCELLED: _RESULTS,
    # Rerun keeps the definition, fields and build; only the output is redone.
    EventType.RERUN_REQUESTED: _RESULTS | _DIAGNOSTICS,
    # A clone inherits the definition and must recompute everything after it.
    EventType.SESSION_CLONED: (
        _PARSE | _FIELD_CANDIDATES | _FIELDS | _PLAN | _BUILD | _RESULTS | _DIAGNOSTICS
    ),
}

_NOTHING: Final[frozenset[str]] = frozenset()


# --------------------------------------------------------------------------- fold


@dataclass(frozen=True)
class FoldedEvent:
    """One persisted event, as the reducer sees it."""

    sequence: int
    event_type: EventType
    payload: Mapping[str, Any] = field(default_factory=dict)


def fold_events(session_id: str, events: Sequence[FoldedEvent]) -> SessionSnapshot:
    """Fold ``events`` in sequence order into the session's current snapshot.

    Passing a prefix of the stream yields that historical version, which is how
    a session is rolled back: re-fold up to the chosen version, then redo the
    invalidated work. Rolling back never resurrects an artifact, because the
    events that produced it are simply not in the prefix.

    Raises :class:`~factor_platform.domain.errors.IllegalTransitionError` if the
    stream contains a transition the state machine forbids.
    """
    state = SessionState.CREATED
    version = 0
    folded: dict[str, Any] = {}

    for event in events:
        state = apply_event(state, event.event_type)
        version = event.sequence

        for key in INVALIDATES.get(event.event_type, _NOTHING):
            folded.pop(key, None)

        for key in WRITES.get(event.event_type, _NOTHING):
            value = event.payload.get(key)
            if value is not None:
                folded[key] = value

    return SessionSnapshot(session_id=session_id, state=state.value, version=version, **folded)


__all__ = [
    "FOLDED_KEYS",
    "INVALIDATES",
    "PERMANENT_KEYS",
    "WRITES",
    "FoldedEvent",
    "fold_events",
]
