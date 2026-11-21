from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select, text

from app import policy
from app.api.deps import SessionDep
from app.models import AuditEvent
from app.schemas import AuditEventOut

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/policy")
def get_policy() -> dict[str, Any]:
    return policy.describe()


@router.get("/audit-events", response_model=list[AuditEventOut])
def list_audit_events(session: SessionDep, limit: int = Query(default=50, ge=1, le=500)) -> list[AuditEventOut]:
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).all()
    return [
        AuditEventOut(id=e.id, actor=e.actor, event_type=e.event_type, subject_type=e.subject_type,
                      subject_id=e.subject_id, detail=e.detail_json, created_at=e.created_at)
        for e in events
    ]
