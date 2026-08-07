"""Tests for the semantic reducer and its cascade-invalidation table.

The naive fold this replaces took the last non-null value per key. That is wrong
the moment a user revises anything: revising the formula left the *old* field
selections, plan, code hash and execution result attached to the snapshot,
because no later event supplied a null for them. The session then described a
factor that had never been computed together.

Every test here is about one question: after a revision, is the snapshot still
internally consistent?
"""

from __future__ import annotations

from typing import Any

from factor_platform.domain.models import (
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    FactorSpec,
    FieldSelection,
    ResearchRequest,
    SessionSnapshot,
)
from factor_platform.orchestration.reducer import (
    FOLDED_KEYS,
    PERMANENT_KEYS,
    WRITES,
    FoldedEvent,
    fold_events,
)
from factor_platform.orchestration.states import EventType

# --------------------------------------------------------------------------- fixtures


def _request(start: str = "2024-01-01", end: str = "2024-06-30") -> dict[str, Any]:
    return ResearchRequest.model_validate(
        {
            "asset_type": "stock",
            "universe": "000300.SH",
            "start_date": start,
            "end_date": end,
            "research_idea": "higher ROE predicts returns",
        }
    ).model_dump(mode="json")


def _spec(name: str = "quality") -> dict[str, Any]:
    return FactorSpec.model_validate(
        {
            "factor_name": name,
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": "roe_ttm"}],
            },
            "canonical_formula": "rank(roe_ttm)",
            "variables": [{"logical_name": "roe_ttm", "meaning": "ROE TTM"}],
        }
    ).model_dump(mode="json")


def _selections(field: str = "s_fa_roe_ttm") -> list[dict[str, Any]]:
    return [
        FieldSelection(
            logical_name="roe_ttm", table="asharettmhis", field=field
        ).model_dump(mode="json")
    ]


def _plan() -> dict[str, Any]:
    return ExecutionPlan(metadata={"built_for": "roe_ttm"}).model_dump(mode="json")


def _result() -> dict[str, Any]:
    return ExecutionResult(
        status=ExecutionStatus.COMPLETED, artifact_uri="file:///artifacts/run-1.parquet"
    ).model_dump(mode="json")


def _event(sequence: int, event_type: EventType, **payload: Any) -> FoldedEvent:
    return FoldedEvent(sequence=sequence, event_type=event_type, payload=payload)


def _completed_session() -> list[FoldedEvent]:
    """A session that ran all the way through to a validated result."""
    return [
        _event(1, EventType.PARSE_STARTED, request=_request()),
        _event(2, EventType.FORMULA_PROPOSED, factor_spec=_spec()),
        _event(3, EventType.FORMULA_CONFIRMED),
        _event(4, EventType.FIELD_CANDIDATES_FOUND),
        _event(5, EventType.FIELDS_CONFIRMED, field_selections=_selections()),
        _event(
            6,
            EventType.CODE_GENERATED,
            plan=_plan(),
            generated_code="def factor(df): ...",
            code_sha256="a" * 64,
        ),
        _event(7, EventType.EXECUTION_STARTED),
        _event(
            8,
            EventType.EXECUTION_SUCCEEDED,
            execution_result=_result(),
            artifact_uri="file:///artifacts/run-1.parquet",
        ),
        _event(9, EventType.VALIDATION_PASSED),
    ]


def _revised(event_type: EventType, **payload: Any) -> SessionSnapshot:
    """Fold a completed session plus one revision event."""
    return fold_events("s1", [*_completed_session(), _event(10, event_type, **payload)])


# --------------------------------------------------------------------------- baseline fold


def test_fold_keeps_the_latest_value_for_a_key_across_events() -> None:
    snapshot = fold_events(
        "s1",
        [
            _event(1, EventType.PARSE_STARTED, request=_request()),
            _event(2, EventType.FORMULA_PROPOSED, factor_spec=_spec("first")),
            _event(3, EventType.FORMULA_CONFIRMED, factor_spec=_spec("second")),
        ],
    )
    assert snapshot.factor_spec is not None
    assert snapshot.factor_spec.factor_name == "second"


def test_fold_reports_state_and_version_from_the_event_stream() -> None:
    snapshot = fold_events("s1", _completed_session())
    assert snapshot.state == "completed"
    assert snapshot.version == 9


def test_fold_of_an_empty_stream_is_a_created_session() -> None:
    snapshot = fold_events("s1", [])
    assert snapshot.state == "created"
    assert snapshot.version == 0
    assert snapshot.factor_spec is None


# --------------------------------------------------------------------------- cascade


def test_formula_revision_clears_all_downstream_artifacts() -> None:
    snapshot = _revised(EventType.FORMULA_REVISED, factor_spec=_spec("revised"))

    assert snapshot.factor_spec is not None
    assert snapshot.factor_spec.factor_name == "revised"
    # Everything derived from the old formula is gone, not merely superseded.
    assert snapshot.field_selections == []
    assert snapshot.plan is None
    assert snapshot.generated_code is None
    assert snapshot.code_sha256 is None
    assert snapshot.execution_result is None
    assert snapshot.artifact_uri is None


def test_fields_revision_keeps_the_formula_but_clears_plan_and_results() -> None:
    snapshot = _revised(EventType.FIELDS_REVISED, field_selections=_selections("s_fa_roe_diluted"))

    assert snapshot.factor_spec is not None
    assert snapshot.field_selections[0].field == "s_fa_roe_diluted"
    assert snapshot.plan is None
    assert snapshot.code_sha256 is None
    assert snapshot.execution_result is None


def test_date_range_revision_keeps_formula_and_fields_but_clears_plan() -> None:
    snapshot = _revised(EventType.DATE_RANGE_REVISED, request=_request(end="2024-12-31"))

    assert snapshot.factor_spec is not None
    assert snapshot.field_selections != []
    assert snapshot.plan is None
    assert snapshot.execution_result is None


def test_universe_revision_clears_plan_and_results() -> None:
    snapshot = _revised(EventType.UNIVERSE_REVISED, request=_request())

    assert snapshot.factor_spec is not None
    assert snapshot.plan is None
    assert snapshot.execution_result is None
    assert snapshot.artifact_uri is None


def test_preprocessing_revision_keeps_the_plan_but_clears_the_built_artifact() -> None:
    """Preprocessing changes what is computed, not which data is fetched."""
    snapshot = _revised(EventType.PREPROCESSING_REVISED, factor_spec=_spec())

    assert snapshot.plan is not None
    assert snapshot.code_sha256 is None
    assert snapshot.generated_code is None
    assert snapshot.execution_result is None


def test_time_convention_revision_clears_plan_and_the_built_artifact() -> None:
    """Signal/trade timing changes which rows are fetched *and* how they align.

    The convention lives inside FactorSpec, so a build made under the old one is
    stale even though the formula text did not change.
    """
    snapshot = _revised(EventType.TIME_CONVENTION_REVISED, factor_spec=_spec())

    assert snapshot.factor_spec is not None
    assert snapshot.plan is None
    assert snapshot.code_sha256 is None
    assert snapshot.execution_result is None


def test_request_revision_clears_the_factor_definition_itself() -> None:
    """A new research idea invalidates the parse, not just the downstream work."""
    snapshot = _revised(EventType.REQUEST_REVISED, request=_request())

    assert snapshot.factor_spec is None
    assert snapshot.field_selections == []
    assert snapshot.plan is None
    assert snapshot.execution_result is None


def test_revision_clears_a_stale_error_from_the_previous_attempt() -> None:
    failed = [
        *_completed_session()[:7],
        _event(
            8,
            EventType.EXECUTION_FAILED,
            last_error={
                "category": "empty_data",
                "code": "empty_result",
                "message": "no rows",
            },
        ),
    ]
    snapshot = fold_events(
        "s1", [*failed, _event(9, EventType.FORMULA_REVISED, factor_spec=_spec("revised"))]
    )
    assert snapshot.last_error is None


# --------------------------------------------------------------------------- rerun / cancel / clone


def test_rerun_keeps_definition_fields_and_plan_but_clears_results() -> None:
    snapshot = _revised(EventType.RERUN_REQUESTED)

    assert snapshot.factor_spec is not None
    assert snapshot.field_selections != []
    assert snapshot.plan is not None
    assert snapshot.code_sha256 is not None
    assert snapshot.execution_result is None
    assert snapshot.artifact_uri is None


def test_cancelled_execution_clears_partial_results() -> None:
    running = _completed_session()[:7]
    snapshot = fold_events("s1", [*running, _event(8, EventType.EXECUTION_CANCELLED)])

    assert snapshot.plan is not None
    assert snapshot.execution_result is None
    assert snapshot.artifact_uri is None


def test_clone_seeds_a_new_session_with_the_definition_only() -> None:
    snapshot = fold_events(
        "s2",
        [
            _event(
                1,
                EventType.SESSION_CLONED,
                request=_request(),
                factor_spec=_spec(),
                cloned_from={"session_id": "s1", "version": 9},
            )
        ],
    )
    assert snapshot.factor_spec is not None
    assert snapshot.request is not None
    # A clone must recompute; it never inherits the source session's artifacts.
    assert snapshot.field_selections == []
    assert snapshot.plan is None
    assert snapshot.execution_result is None


# --------------------------------------------------------------------------- regressions


def test_reconfirmation_after_revision_uses_only_the_new_selections() -> None:
    """The pre-revision selections must not reappear once fields are reconfirmed."""
    events = [
        *_completed_session(),
        _event(10, EventType.FORMULA_REVISED, factor_spec=_spec("revised")),
        _event(11, EventType.FORMULA_CONFIRMED),
        _event(12, EventType.FIELD_CANDIDATES_FOUND),
        _event(13, EventType.FIELDS_CONFIRMED, field_selections=_selections("s_fa_roa_ttm")),
    ]
    snapshot = fold_events("s1", events)

    assert [s.field for s in snapshot.field_selections] == ["s_fa_roa_ttm"]


def test_every_event_type_declares_the_keys_it_may_write() -> None:
    """A new event without a write declaration must not silently fold nothing."""
    assert set(WRITES) == set(EventType)


def test_an_event_cannot_write_a_key_it_did_not_declare() -> None:
    """EXECUTION_STARTED carries no snapshot state; a stray spec must not land.

    Payloads are built by callers and round-trip through JSON, so the reducer —
    not the caller — decides which keys an event is allowed to move.
    """
    snapshot = fold_events(
        "s1",
        [
            _event(1, EventType.PARSE_STARTED, request=_request()),
            _event(2, EventType.FORMULA_PROPOSED, factor_spec=_spec()),
            _event(3, EventType.FORMULA_CONFIRMED),
            _event(4, EventType.FIELD_CANDIDATES_FOUND),
            _event(5, EventType.FIELDS_CONFIRMED, field_selections=_selections()),
            _event(6, EventType.CODE_GENERATED, plan=_plan()),
            _event(7, EventType.EXECUTION_STARTED, factor_spec=_spec("smuggled")),
        ],
    )
    assert snapshot.factor_spec is not None
    assert snapshot.factor_spec.factor_name == "quality"


def test_every_snapshot_field_is_classified_by_the_reducer() -> None:
    """A new SessionSnapshot field must be registered before it can be used.

    Without this, a later task adds a downstream artifact to the snapshot, forgets
    the invalidation table, and revisions silently leave it stale — the exact bug
    this reducer exists to prevent. Registering is not optional: classify the key
    as folded (and add it to the relevant cascades) or as permanent.
    """
    assert set(SessionSnapshot.model_fields) == PERMANENT_KEYS | FOLDED_KEYS
