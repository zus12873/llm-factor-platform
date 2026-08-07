"""Event-sourced session repository with optimistic concurrency control.

``append_event`` validates the requested transition against the folded state, then
checks ``expected_version`` against the highest sequence number. Both checks happen
inside one transaction, so a rejected or stale append leaves no row behind.

Snapshots are derived: ``get_snapshot`` folds the whole event stream each call. This
is cheap for a single-host prototype and guarantees the snapshot can never drift from
the events. The fold itself lives in
:mod:`factor_platform.orchestration.reducer`, which knows which keys each event
may write and which downstream keys it invalidates; the repository only supplies
the ordered stream.

``get_snapshot_at`` folds a prefix, which is how a session is rolled back to an
earlier version. There is deliberately no table of stored snapshots: it would be
a second source of truth that can disagree with the log.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.db.models import SessionEventRecord, SessionRecord
from factor_platform.domain.errors import ConcurrentUpdateError
from factor_platform.domain.models import SessionSnapshot
from factor_platform.orchestration.reducer import FoldedEvent, fold_events
from factor_platform.orchestration.states import EventType, SessionState, apply_event


class SessionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_session(self, session_id: str) -> None:
        """Create the session anchor. Idempotent for an existing id."""
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(select(SessionRecord).where(SessionRecord.id == session_id))
            ).first()
            if existing is None:
                await conn.execute(insert(SessionRecord).values(id=session_id))

    async def append_event(
        self,
        session_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        expected_version: int,
    ) -> int:
        """Append one event, validating transition and version atomically.

        Returns the new sequence number. Raises :class:`IllegalTransitionError` if
        the event is not legal in the current state, or :class:`ConcurrentUpdateError`
        if ``expected_version`` is stale.
        """
        async with self._engine.begin() as conn:
            session = (
                await conn.execute(select(SessionRecord).where(SessionRecord.id == session_id))
            ).first()
            if session is None:
                raise ConcurrentUpdateError(f"unknown session: {session_id}")

            rows = (
                await conn.execute(
                    select(SessionEventRecord.sequence, SessionEventRecord.event_type)
                    .where(SessionEventRecord.session_id == session_id)
                    .order_by(SessionEventRecord.sequence)
                )
            ).all()

            state = SessionState.CREATED
            for row in rows:
                state = apply_event(state, EventType(row.event_type))
            current_max = rows[-1].sequence if rows else 0

            if current_max != expected_version:
                raise ConcurrentUpdateError(
                    f"stale version: expected {expected_version}, actual {current_max}"
                )

            # Validate the transition last so the error is the most specific.
            apply_event(state, event_type)  # raises IllegalTransitionError if illegal

            new_sequence = expected_version + 1
            await conn.execute(
                insert(SessionEventRecord).values(
                    session_id=session_id,
                    sequence=new_sequence,
                    event_type=event_type.value,
                    payload_json=dict(payload),
                )
            )
            return new_sequence

    async def _read_events(self, session_id: str) -> list[FoldedEvent] | None:
        """Return the ordered event stream, or ``None`` for an unknown session."""
        async with self._engine.connect() as conn:
            session = (
                await conn.execute(select(SessionRecord).where(SessionRecord.id == session_id))
            ).first()
            if session is None:
                return None

            rows = (
                await conn.execute(
                    select(
                        SessionEventRecord.sequence,
                        SessionEventRecord.event_type,
                        SessionEventRecord.payload_json,
                    )
                    .where(SessionEventRecord.session_id == session_id)
                    .order_by(SessionEventRecord.sequence)
                )
            ).all()

        return [
            FoldedEvent(
                sequence=row.sequence,
                event_type=EventType(row.event_type),
                payload=row.payload_json or {},
            )
            for row in rows
        ]

    async def get_snapshot(self, session_id: str) -> SessionSnapshot | None:
        events = await self._read_events(session_id)
        if events is None:
            return None
        return fold_events(session_id, events)

    async def get_snapshot_at(self, session_id: str, version: int) -> SessionSnapshot | None:
        """Return the snapshot as of ``version``, folding only events up to it.

        A version past the end of the stream yields the latest snapshot, so
        callers need not special-case an aggregate that has not advanced.
        """
        events = await self._read_events(session_id)
        if events is None:
            return None
        return fold_events(session_id, [e for e in events if e.sequence <= version])


__all__ = ["SessionRepository"]
