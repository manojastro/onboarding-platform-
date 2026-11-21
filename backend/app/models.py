"""Database schema. Table names are fixed by the build spec so the phases line up.

The integrity rules that matter live in Postgres, not only in Python:

* ``uq_import_record_idempotency_anchor`` — one row per
  (source_connection_id, entity_type, external_record_id).
* Approved mapping versions are immutable (trigger in migration 0001).
* ``audit_event`` is append-only (trigger in migration 0001).
* No floating-point column exists anywhere; money is ``NUMERIC``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import policy

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

FILE_FORMATS = ("csv", "json")
IMPORT_RUN_MODES = ("dry_run", "import")
IMPORT_RUN_STATES = ("pending", "running", "completed", "completed_with_errors", "failed", "blocked", "cancelled")
IMPORT_RECORD_STATUSES = (
    "pending", "in_flight", "created", "updated", "unchanged", "uncertain", "failed", "rejected",
)


def sql_in(column: str, values: Iterable[str]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        int: sa.BigInteger(),
        str: sa.Text(),
        datetime: sa.DateTime(timezone=True),
        Decimal: sa.Numeric(18, 6),
        dict[str, Any]: JSONB(),
        list[Any]: JSONB(),
    }


class SourceConnection(Base):
    __tablename__ = "source_connection"
    __table_args__ = (
        sa.CheckConstraint(sql_in("destination_type", policy.DESTINATION_TYPES), name="destination_type_allowed"),
    )

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    destination_type: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Dataset(Base):
    __tablename__ = "dataset"
    __table_args__ = (
        sa.CheckConstraint(sql_in("entity_type", policy.ENTITY_TYPES), name="entity_type_allowed"),
        sa.CheckConstraint(sql_in("file_format", FILE_FORMATS), name="file_format_allowed"),
        sa.CheckConstraint("row_count > 0", name="row_count_positive"),
        sa.CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
    )

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    source_connection_id: Mapped[int] = mapped_column(sa.ForeignKey("source_connection.id"), index=True)
    entity_type: Mapped[str]
    original_filename: Mapped[str]
    original_file_path: Mapped[str]  # relative to settings.upload_dir
    file_format: Mapped[str]
    file_size_bytes: Mapped[int]
    file_sha256: Mapped[str] = mapped_column(sa.String(64))
    schema_fingerprint: Mapped[str] = mapped_column(index=True)
    columns_json: Mapped[list[Any]]
    row_count: Mapped[int]
    parse_warnings_json: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    uploaded_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class MappingVersion(Base):
    __tablename__ = "mapping_version"
    __table_args__ = (
        sa.UniqueConstraint("source_connection_id", "entity_type", "version_no", name="uq_mapping_version_number"),
        sa.CheckConstraint(sql_in("entity_type", policy.ENTITY_TYPES), name="entity_type_allowed"),
        sa.CheckConstraint("version_no > 0", name="version_no_positive"),
        sa.CheckConstraint(
            "(is_immutable AND approved_by IS NOT NULL AND approved_at IS NOT NULL)"
            " OR (NOT is_immutable AND approved_by IS NULL AND approved_at IS NULL)",
            name="approval_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    source_connection_id: Mapped[int] = mapped_column(sa.ForeignKey("source_connection.id"))
    entity_type: Mapped[str]
    version_no: Mapped[int]
    mapping_json: Mapped[dict[str, Any]]
    based_on_dataset_id: Mapped[int | None] = mapped_column(sa.ForeignKey("dataset.id"))
    approved_by: Mapped[str | None]
    approved_at: Mapped[datetime | None]
    is_immutable: Mapped[bool] = mapped_column(server_default=sa.false())
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ImportRun(Base):
    __tablename__ = "import_run"
    __table_args__ = (
        sa.CheckConstraint(sql_in("mode", IMPORT_RUN_MODES), name="mode_allowed"),
        sa.CheckConstraint(sql_in("state", IMPORT_RUN_STATES), name="state_allowed"),
    )

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    source_connection_id: Mapped[int] = mapped_column(sa.ForeignKey("source_connection.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(sa.ForeignKey("dataset.id"))
    mapping_version_id: Mapped[int] = mapped_column(sa.ForeignKey("mapping_version.id"))
    mode: Mapped[str]
    state: Mapped[str] = mapped_column(server_default=sa.text("'pending'"))
    counts_json: Mapped[dict[str, Any]] = mapped_column(server_default=sa.text("'{}'::jsonb"))
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(server_default=sa.text("'{}'::jsonb"))
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    started_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ImportRecord(Base):
    __tablename__ = "import_record"
    __table_args__ = (
        # The idempotency anchor. Every destination write is keyed off this row.
        sa.UniqueConstraint(
            "source_connection_id", "entity_type", "external_record_id",
            name="uq_import_record_idempotency_anchor",
        ),
        sa.CheckConstraint(sql_in("entity_type", policy.ENTITY_TYPES), name="entity_type_allowed"),
        sa.CheckConstraint(sql_in("status", IMPORT_RECORD_STATUSES), name="status_allowed"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
    )

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    import_run_id: Mapped[int] = mapped_column(sa.ForeignKey("import_run.id"), index=True)
    source_connection_id: Mapped[int] = mapped_column(sa.ForeignKey("source_connection.id"))
    entity_type: Mapped[str]
    external_record_id: Mapped[str]
    payload_hash: Mapped[str | None]
    destination_id: Mapped[str | None]
    status: Mapped[str]
    attempt_count: Mapped[int] = mapped_column(server_default=sa.text("0"))
    last_error: Mapped[str | None]
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), onupdate=sa.func.now())


class RecordValidationError(Base):
    __tablename__ = "validation_error"

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    import_run_id: Mapped[int] = mapped_column(sa.ForeignKey("import_run.id"), index=True)
    row_number: Mapped[int]
    field: Mapped[str | None]
    error_code: Mapped[str]
    message: Mapped[str]
    external_record_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (sa.Index("ix_audit_event_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    actor: Mapped[str]
    event_type: Mapped[str] = mapped_column(index=True)
    subject_type: Mapped[str]
    subject_id: Mapped[str | None]
    detail_json: Mapped[dict[str, Any]] = mapped_column(server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), index=True)


class LlmTrace(Base):
    __tablename__ = "llm_trace"

    id: Mapped[int] = mapped_column(sa.Identity(), primary_key=True)
    purpose: Mapped[str]
    model: Mapped[str]
    prompt_hash: Mapped[str] = mapped_column(index=True)
    prompt_json: Mapped[dict[str, Any]]
    response_json: Mapped[dict[str, Any] | None]
    outcome: Mapped[str]
    error: Mapped[str | None]
    cache_hit: Mapped[bool] = mapped_column(server_default=sa.false())
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    latency_ms: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
