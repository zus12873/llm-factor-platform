import pytest

from factor_platform.orchestration.states import EventType, SessionState, apply_event


def test_formula_cannot_be_confirmed_before_proposal() -> None:
    with pytest.raises(ValueError, match="illegal transition"):
        apply_event(SessionState.CREATED, EventType.FORMULA_CONFIRMED)


def test_parsing_can_request_clarification() -> None:
    state = apply_event(SessionState.PARSING_INPUT, EventType.CLARIFICATION_REQUESTED)
    assert state is SessionState.NEEDS_CLARIFICATION


def test_happy_path_reaches_completed() -> None:
    state = SessionState.CREATED
    for event in [
        EventType.PARSE_STARTED,
        EventType.FORMULA_PROPOSED,
        EventType.FORMULA_CONFIRMED,
        EventType.FIELD_CANDIDATES_FOUND,
        EventType.FIELDS_CONFIRMED,
        EventType.CODE_GENERATED,
        EventType.EXECUTION_STARTED,
        EventType.EXECUTION_SUCCEEDED,
        EventType.VALIDATION_PASSED,
    ]:
        state = apply_event(state, event)
    assert state is SessionState.COMPLETED


def test_failed_can_repair_to_formula_confirmation() -> None:
    state = apply_event(SessionState.FAILED, EventType.REPAIR_PROPOSED)
    assert state is SessionState.WAITING_FORMULA_CONFIRMATION


def test_completed_is_terminal() -> None:
    with pytest.raises(ValueError, match="illegal transition"):
        apply_event(SessionState.COMPLETED, EventType.PARSE_STARTED)


# --------------------------------------------------------------------------- revisions


def test_cancelled_execution_returns_to_code_ready() -> None:
    state = apply_event(SessionState.EXECUTING, EventType.EXECUTION_CANCELLED)
    assert state is SessionState.CODE_READY


def test_formula_revision_requires_reconfirmation() -> None:
    state = apply_event(SessionState.COMPLETED, EventType.FORMULA_REVISED)
    assert state is SessionState.WAITING_FORMULA_CONFIRMATION


def test_fields_revision_requires_reconfirmation() -> None:
    state = apply_event(SessionState.CODE_READY, EventType.FIELDS_REVISED)
    assert state is SessionState.WAITING_FIELD_CONFIRMATION


def test_date_range_revision_returns_to_planning() -> None:
    """Date range does not touch the formula, so only the plan must be rebuilt."""
    state = apply_event(SessionState.COMPLETED, EventType.DATE_RANGE_REVISED)
    assert state is SessionState.PLANNING_FUNCTIONS


def test_request_revision_returns_to_parsing() -> None:
    state = apply_event(SessionState.COMPLETED, EventType.REQUEST_REVISED)
    assert state is SessionState.PARSING_INPUT


def test_execution_in_flight_cannot_be_revised() -> None:
    """A running job must be cancelled first; revising under it would race."""
    with pytest.raises(ValueError, match="illegal transition"):
        apply_event(SessionState.EXECUTING, EventType.FORMULA_REVISED)


def test_rerun_from_completed_returns_to_code_ready() -> None:
    state = apply_event(SessionState.COMPLETED, EventType.RERUN_REQUESTED)
    assert state is SessionState.CODE_READY


def test_clone_starts_a_new_session_awaiting_formula_confirmation() -> None:
    """A clone carries the definition but must be reconfirmed before it runs."""
    state = apply_event(SessionState.CREATED, EventType.SESSION_CLONED)
    assert state is SessionState.WAITING_FORMULA_CONFIRMATION
