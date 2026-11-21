from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditEvent

SYSTEM_ACTOR = "system"


def record_event(
    session: Session,
    *,
    event_type: str,
    subject_type: str,
    subject_id: object | None = None,
    detail: dict[str, Any] | None = None,
    actor: str | None = None,
) -> AuditEvent:
    """Stage an audit event in the caller's transaction (the caller commits)."""
    event = AuditEvent(
        actor=actor or settings.operator_name,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=None if subject_id is None else str(subject_id),
        detail_json=detail or {},
    )
    session.add(event)
    return event
