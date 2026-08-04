"""ORM models: append-only session event log.

Two tables back the event-sourced aggregate:

* ``sessions``       -- one row per session (identity + creation time);
* ``session_events`` -- an append-only, monotonically numbered event stream.

The current aggregate state is always the reduction of ``session_events`` in
``sequence`` order; ``sessions`` exists only as an anchor and for fast existence
checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from factor_platform.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SessionEventRecord(Base):
    __tablename__ = "session_events"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
