"""Resumable server-sent events over the persisted session log.

The event stream is not a live broadcast that a client must catch — it is a
projection of a durable log, and that is what makes it resumable. A browser that
sleeps, loses wifi, or reloads sends ``Last-Event-ID`` and gets exactly what it
missed. A broadcast-only design would leave that browser permanently behind with
no way to tell.

The SSE ``id`` is the event's sequence number, which is already monotonic per
session. Inventing a separate stream id would create a second ordering to keep
consistent with the first.

Heartbeats are comments (``: keepalive``). Proxies drop idle connections, and a
comment refreshes the timer without appearing as an event to the client.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.db.models import SessionEventRecord

router = APIRouter(prefix="/api/sessions", tags=["events"])

#: How often to look for new events. Polling a local SQLite file is cheap, and it
#: avoids a notification channel that would have to be kept correct separately.
POLL_INTERVAL_SECONDS = 0.5
HEARTBEAT_INTERVAL_SECONDS = 15.0


def get_engine() -> AsyncEngine:  # pragma: no cover - overridden by the app
    raise NotImplementedError("engine dependency is wired in main.create_app")


async def read_events_after(
    engine: AsyncEngine, session_id: str, after: int
) -> list[tuple[int, str, dict]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    SessionEventRecord.sequence,
                    SessionEventRecord.event_type,
                    SessionEventRecord.payload_json,
                )
                .where(SessionEventRecord.session_id == session_id)
                .where(SessionEventRecord.sequence > after)
                .order_by(SessionEventRecord.sequence)
            )
        ).all()
    return [(row.sequence, row.event_type, row.payload_json or {}) for row in rows]


def format_event(sequence: int, event_type: str, payload: dict) -> str:
    body = json.dumps(
        {"sequence": sequence, "event_type": event_type, "payload": payload},
        ensure_ascii=False,
    )
    return f"id: {sequence}\nevent: {event_type}\ndata: {body}\n\n"


async def event_stream(
    engine: AsyncEngine,
    session_id: str,
    last_event_id: int,
    *,
    max_idle_polls: int | None = None,
) -> AsyncIterator[str]:
    """Yield everything after ``last_event_id``, then follow the log.

    ``max_idle_polls`` bounds the follow phase so tests terminate; in production
    it is ``None`` and the stream ends when the client disconnects.
    """
    cursor = last_event_id
    idle = 0

    while True:
        events = await read_events_after(engine, session_id, cursor)
        for sequence, event_type, payload in events:
            cursor = sequence
            yield format_event(sequence, event_type, payload)

        if events:
            idle = 0
        else:
            idle += 1
            if max_idle_polls is not None and idle >= max_idle_polls:
                return
            if idle * POLL_INTERVAL_SECONDS >= HEARTBEAT_INTERVAL_SECONDS:
                idle = 0
                # A comment keeps proxies from dropping the connection without
                # the client seeing a phantom event.
                yield ": keepalive\n\n"
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@router.get("/{session_id}/events")
async def stream_session_events(
    session_id: str,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    replay_only: bool = False,
) -> StreamingResponse:
    """Stream session events, resuming after ``Last-Event-ID`` when supplied.

    ``replay_only`` returns the backlog and closes, which is what a client that
    just wants to catch up needs — and what makes this testable without a
    connection that never ends.
    """
    try:
        after = int(last_event_id) if last_event_id else 0
    except ValueError:
        after = 0

    return StreamingResponse(
        event_stream(engine, session_id, after, max_idle_polls=1 if replay_only else None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "event_stream",
    "format_event",
    "get_engine",
    "read_events_after",
    "router",
]
