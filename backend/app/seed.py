"""Idempotent seed: makes sure the demo connection exists. Safe to run on every start."""

from __future__ import annotations

from sqlalchemy import select

from app import audit
from app.db import SessionLocal
from app.models import SourceConnection

DEMO_CONNECTION_NAME = "Demo (mock destination)"


def seed() -> None:
    with SessionLocal() as session:
        existing = session.scalar(select(SourceConnection.id).where(SourceConnection.name == DEMO_CONNECTION_NAME))
        if existing is not None:
            return
        connection = SourceConnection(name=DEMO_CONNECTION_NAME, destination_type="mock")
        session.add(connection)
        session.flush()
        audit.record_event(session, actor=audit.SYSTEM_ACTOR, event_type="connection.created",
                           subject_type="source_connection", subject_id=connection.id,
                           detail={"name": connection.name, "destination_type": "mock", "seeded": True})
        session.commit()
        print(f"seeded source_connection {connection.id}: {DEMO_CONNECTION_NAME}")


if __name__ == "__main__":
    seed()
