import pytest

from factor_platform.db.repository import SessionRepository
from factor_platform.domain.errors import ConcurrentUpdateError, IllegalTransitionError
from factor_platform.orchestration.states import EventType, SessionState


async def test_append_event_enforces_optimistic_lock(engine) -> None:
    repo = SessionRepository(engine)
    await repo.create_session("s1")
    await repo.append_event("s1", EventType.PARSE_STARTED, {}, expected_version=0)
    await repo.append_event("s1", EventType.FORMULA_PROPOSED, {}, expected_version=1)
    with pytest.raises(ConcurrentUpdateError):
        # Stale: the aggregate has already advanced past version 1.
        await repo.append_event("s1", EventType.CLARIFICATION_REQUESTED, {}, expected_version=1)


async def test_append_event_rejects_illegal_transition(engine) -> None:
    repo = SessionRepository(engine)
    await repo.create_session("s1")
    with pytest.raises(IllegalTransitionError):
        await repo.append_event("s1", EventType.FORMULA_CONFIRMED, {}, expected_version=0)
    # Nothing is persisted when the transition is rejected.
    snap = await repo.get_snapshot("s1")
    assert snap.version == 0


async def test_get_snapshot_folds_state_and_version(engine) -> None:
    repo = SessionRepository(engine)
    await repo.create_session("s1")
    await repo.append_event("s1", EventType.PARSE_STARTED, {}, expected_version=0)
    snap = await repo.get_snapshot("s1")
    assert snap.session_id == "s1"
    assert snap.state == SessionState.PARSING_INPUT.value
    assert snap.version == 1


async def test_get_snapshot_of_unknown_session_returns_none(engine) -> None:
    repo = SessionRepository(engine)
    assert await repo.get_snapshot("missing") is None
