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
