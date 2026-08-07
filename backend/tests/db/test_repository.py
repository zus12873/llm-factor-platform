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


# --------------------------------------------------------------------------- revisions


async def _run_to_code_ready(repo: SessionRepository, session_id: str) -> int:
    """Drive a session to CODE_READY with a spec, fields and a build recorded."""
    await repo.create_session(session_id)
    await repo.append_event(session_id, EventType.PARSE_STARTED, {}, expected_version=0)
    await repo.append_event(
        session_id,
        EventType.FORMULA_PROPOSED,
        {
            "factor_spec": {
                "factor_name": "quality",
                "asset_type": "stock",
                "universe": "000300.SH",
                "frequency": "daily",
                "formula_ast": {"type": "variable", "name": "roe_ttm"},
                "variables": [{"logical_name": "roe_ttm", "meaning": "ROE TTM"}],
            }
        },
        expected_version=1,
    )
    await repo.append_event(session_id, EventType.FORMULA_CONFIRMED, {}, expected_version=2)
    await repo.append_event(
        session_id, EventType.FIELD_CANDIDATES_FOUND, {}, expected_version=3
    )
    await repo.append_event(
        session_id,
        EventType.FIELDS_CONFIRMED,
        {
            "field_selections": [
                {"logical_name": "roe_ttm", "table": "asharettmhis", "field": "s_fa_roe_ttm"}
            ]
        },
        expected_version=4,
    )
    await repo.append_event(
        session_id,
        EventType.CODE_GENERATED,
        {"plan": {"steps": []}, "code_sha256": "a" * 64},
        expected_version=5,
    )
    return 6


async def test_snapshot_after_revision_drops_stale_downstream_artifacts(engine) -> None:
    """The repository must fold semantically, not by last-non-null-wins."""
    repo = SessionRepository(engine)
    version = await _run_to_code_ready(repo, "s1")

    await repo.append_event(
        "s1",
        EventType.FORMULA_REVISED,
        {
            "factor_spec": {
                "factor_name": "quality_v2",
                "asset_type": "stock",
                "universe": "000300.SH",
                "frequency": "daily",
                "formula_ast": {"type": "variable", "name": "roa_ttm"},
                "variables": [{"logical_name": "roa_ttm", "meaning": "ROA TTM"}],
            }
        },
        expected_version=version,
    )

    snap = await repo.get_snapshot("s1")
    assert snap.factor_spec.factor_name == "quality_v2"
    assert snap.field_selections == []
    assert snap.plan is None
    assert snap.code_sha256 is None
    assert snap.state == SessionState.WAITING_FORMULA_CONFIRMATION.value


async def test_get_snapshot_at_returns_the_historical_version(engine) -> None:
    """Rollback is a prefix fold; no separate version table can drift from it."""
    repo = SessionRepository(engine)
    version = await _run_to_code_ready(repo, "s1")
    await repo.append_event(
        "s1",
        EventType.FORMULA_REVISED,
        {
            "factor_spec": {
                "factor_name": "quality_v2",
                "asset_type": "stock",
                "universe": "000300.SH",
                "frequency": "daily",
                "formula_ast": {"type": "variable", "name": "roa_ttm"},
                "variables": [{"logical_name": "roa_ttm", "meaning": "ROA TTM"}],
            }
        },
        expected_version=version,
    )

    historical = await repo.get_snapshot_at("s1", version)
    assert historical.version == version
    assert historical.factor_spec.factor_name == "quality"
    assert historical.field_selections != []
    assert historical.code_sha256 is not None


async def test_get_snapshot_at_beyond_the_stream_returns_the_latest(engine) -> None:
    repo = SessionRepository(engine)
    version = await _run_to_code_ready(repo, "s1")
    snap = await repo.get_snapshot_at("s1", version + 100)
    assert snap.version == version


async def test_revising_a_running_execution_is_rejected(engine) -> None:
    """A worker is in flight; revising under it would race the artifact write."""
    repo = SessionRepository(engine)
    version = await _run_to_code_ready(repo, "s1")
    await repo.append_event(
        "s1", EventType.EXECUTION_STARTED, {}, expected_version=version
    )
    with pytest.raises(IllegalTransitionError):
        await repo.append_event(
            "s1", EventType.FORMULA_REVISED, {}, expected_version=version + 1
        )
