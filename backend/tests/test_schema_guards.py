"""Integrity rules that live in Postgres, proven against Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import engine
from app.models import AuditEvent, Base, Dataset, ImportRecord, ImportRun, MappingVersion, SourceConnection


def make_connection(session) -> SourceConnection:
    connection = SourceConnection(name="guard", destination_type="mock")
    session.add(connection)
    session.flush()
    return connection


def make_dataset(session, connection: SourceConnection, **overrides) -> Dataset:
    values = dict(
        source_connection_id=connection.id, entity_type="customer", original_filename="c.csv",
        original_file_path="aa/aa/c.csv", file_format="csv", file_size_bytes=10, file_sha256="0" * 64,
        schema_fingerprint="sfp1:x", columns_json=["id"], row_count=1,
    )
    dataset = Dataset(**{**values, **overrides})
    session.add(dataset)
    session.flush()
    return dataset


def make_import_run(session, connection: SourceConnection) -> ImportRun:
    dataset = make_dataset(session, connection)
    version = MappingVersion(source_connection_id=connection.id, entity_type="customer", version_no=1,
                             mapping_json={})
    session.add(version)
    session.flush()
    run = ImportRun(source_connection_id=connection.id, dataset_id=dataset.id, mapping_version_id=version.id,
                    mode="import")
    session.add(run)
    session.flush()
    return run


def record(run: ImportRun, entity_type: str = "customer", external_id: str = "C-1") -> ImportRecord:
    return ImportRecord(import_run_id=run.id, source_connection_id=run.source_connection_id,
                        entity_type=entity_type, external_record_id=external_id, status="pending")


# --- Schema shape ----------------------------------------------------------------------------


def test_migrations_match_the_models(migrated_db):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_no_floating_point_columns_exist(migrated_db):
    in_models = [f"{t.name}.{c.name}" for t in Base.metadata.tables.values() for c in t.columns
                 if isinstance(c.type, sa.Float)]
    assert in_models == []
    with engine.connect() as conn:
        in_database = conn.execute(sa.text(
            "SELECT table_name || '.' || column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type IN ('real', 'double precision')"
        )).scalars().all()
    assert in_database == []


def test_decimal_is_written_to_json_as_exact_text(session):
    event = AuditEvent(actor="t", event_type="probe", subject_type="t", detail_json={"amount": Decimal("19.990")})
    session.add(event)
    session.commit()
    stored = session.execute(sa.text("SELECT detail_json ->> 'amount' FROM audit_event")).scalar_one()
    assert stored == "19.990"


def test_json_numbers_come_back_from_the_database_as_decimal(session):
    session.execute(sa.text(
        "INSERT INTO audit_event (actor, event_type, subject_type, detail_json) "
        """VALUES ('t', 'probe', 't', '{"amount": 19.990}'::jsonb)"""
    ))
    session.commit()
    detail = session.scalars(sa.select(AuditEvent.detail_json)).one()
    assert detail["amount"] == Decimal("19.990")
    assert isinstance(detail["amount"], Decimal)


# --- The idempotency anchor ------------------------------------------------------------------


def test_idempotency_anchor_rejects_a_second_row_for_the_same_record(session):
    run = make_import_run(session, make_connection(session))
    session.add(record(run))
    session.flush()
    session.add(record(run))
    with pytest.raises(IntegrityError, match="uq_import_record_idempotency_anchor"):
        session.flush()


def test_same_external_id_is_allowed_for_the_other_entity_type(session):
    run = make_import_run(session, make_connection(session))
    session.add_all([record(run, "customer", "X-1"), record(run, "order", "X-1")])
    session.flush()


# --- Mapping version immutability ------------------------------------------------------------


def test_draft_is_editable_but_an_approved_version_is_frozen(session):
    connection = make_connection(session)
    version = MappingVersion(source_connection_id=connection.id, entity_type="customer", version_no=1,
                             mapping_json={"fields": []})
    session.add(version)
    session.commit()

    version.mapping_json = {"fields": ["edited while draft"]}
    session.commit()

    version.is_immutable = True
    version.approved_by = "operator"
    version.approved_at = datetime.now(UTC)
    session.commit()

    version.mapping_json = {"fields": ["edited after approval"]}
    with pytest.raises(DBAPIError, match="immutable"):
        session.commit()
    session.rollback()

    session.delete(version)
    with pytest.raises(DBAPIError, match="immutable"):
        session.commit()
    session.rollback()

    assert session.get(MappingVersion, version.id).mapping_json == {"fields": ["edited while draft"]}


def test_approval_requires_an_approver_and_a_timestamp(session):
    connection = make_connection(session)
    session.add(MappingVersion(source_connection_id=connection.id, entity_type="customer", version_no=1,
                               mapping_json={}, is_immutable=True))
    with pytest.raises(IntegrityError, match="approval_consistent"):
        session.commit()


# --- Audit log -------------------------------------------------------------------------------


def test_audit_log_is_append_only(session):
    event = AuditEvent(actor="t", event_type="probe", subject_type="t")
    session.add(event)
    session.commit()

    event.event_type = "tampered"
    with pytest.raises(DBAPIError, match="append-only"):
        session.commit()
    session.rollback()

    session.delete(event)
    with pytest.raises(DBAPIError, match="append-only"):
        session.commit()


# --- Dataset constraints ---------------------------------------------------------------------


def test_a_dataset_must_have_rows(session):
    with pytest.raises(IntegrityError, match="row_count_positive"):
        make_dataset(session, make_connection(session), row_count=0)


def test_entity_type_is_constrained_in_the_database(session):
    with pytest.raises(IntegrityError, match="entity_type_allowed"):
        make_dataset(session, make_connection(session), entity_type="invoice")
