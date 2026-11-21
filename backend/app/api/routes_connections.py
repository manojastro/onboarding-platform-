from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import audit
from app.api.deps import SessionDep
from app.errors import ApiError
from app.models import SourceConnection
from app.schemas import ConnectionIn, ConnectionOut

router = APIRouter(tags=["connections"])


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(session: SessionDep) -> Sequence[SourceConnection]:
    return session.scalars(select(SourceConnection).order_by(SourceConnection.id)).all()


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionIn, session: SessionDep) -> SourceConnection:
    connection = SourceConnection(name=body.name, destination_type=body.destination_type)
    session.add(connection)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "connection_name_taken",
                       f"A source connection named {body.name!r} already exists.") from None
    audit.record_event(session, event_type="connection.created", subject_type="source_connection",
                       subject_id=connection.id,
                       detail={"name": connection.name, "destination_type": connection.destination_type})
    session.commit()
    return connection
