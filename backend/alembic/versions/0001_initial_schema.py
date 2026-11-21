"""Initial schema: the eight tables, the idempotency anchor, integrity triggers.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-14

Values are frozen here on purpose (migrations are snapshots; they must not
import application code that may change later).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITY_TYPES_SQL = "entity_type IN ('customer', 'order')"
JSONB = postgresql.JSONB()


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False)


def _timestamp(name: str, *, nullable: bool = False, default_now: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.text("now()") if default_now else None)


def _fk(column: str, table: str, source: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{table}.id"], name=op.f(f"fk_{source}_{column}_{table}"))


def upgrade() -> None:
    op.create_table(
        "source_connection",
        _id(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("destination_type", sa.Text(), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_connection")),
        sa.UniqueConstraint("name", name=op.f("uq_source_connection_name")),
        sa.CheckConstraint("destination_type IN ('mock', 'stripe')",
                           name=op.f("ck_source_connection_destination_type_allowed")),
    )

    op.create_table(
        "dataset",
        _id(),
        sa.Column("source_connection_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("original_file_path", sa.Text(), nullable=False),
        sa.Column("file_format", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("schema_fingerprint", sa.Text(), nullable=False),
        sa.Column("columns_json", JSONB, nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("parse_warnings_json", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        _timestamp("uploaded_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset")),
        _fk("source_connection_id", "source_connection", "dataset"),
        sa.CheckConstraint(ENTITY_TYPES_SQL, name=op.f("ck_dataset_entity_type_allowed")),
        sa.CheckConstraint("file_format IN ('csv', 'json')", name=op.f("ck_dataset_file_format_allowed")),
        sa.CheckConstraint("row_count > 0", name=op.f("ck_dataset_row_count_positive")),
        sa.CheckConstraint("file_size_bytes > 0", name=op.f("ck_dataset_file_size_positive")),
    )
    op.create_index(op.f("ix_dataset_source_connection_id"), "dataset", ["source_connection_id"])
    op.create_index(op.f("ix_dataset_schema_fingerprint"), "dataset", ["schema_fingerprint"])

    op.create_table(
        "mapping_version",
        _id(),
        sa.Column("source_connection_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("mapping_json", JSONB, nullable=False),
        sa.Column("based_on_dataset_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        _timestamp("approved_at", nullable=True, default_now=False),
        sa.Column("is_immutable", sa.Boolean(), server_default=sa.false(), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_version")),
        _fk("source_connection_id", "source_connection", "mapping_version"),
        _fk("based_on_dataset_id", "dataset", "mapping_version"),
        sa.UniqueConstraint("source_connection_id", "entity_type", "version_no",
                            name=op.f("uq_mapping_version_number")),
        sa.CheckConstraint(ENTITY_TYPES_SQL, name=op.f("ck_mapping_version_entity_type_allowed")),
        sa.CheckConstraint("version_no > 0", name=op.f("ck_mapping_version_version_no_positive")),
        sa.CheckConstraint(
            "(is_immutable AND approved_by IS NOT NULL AND approved_at IS NOT NULL)"
            " OR (NOT is_immutable AND approved_by IS NULL AND approved_at IS NULL)",
            name=op.f("ck_mapping_version_approval_consistent"),
        ),
    )

    op.create_table(
        "import_run",
        _id(),
        sa.Column("source_connection_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("mapping_version_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("counts_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("checkpoint_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("started_at", nullable=True, default_now=False),
        _timestamp("heartbeat_at", nullable=True, default_now=False),
        _timestamp("finished_at", nullable=True, default_now=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_run")),
        _fk("source_connection_id", "source_connection", "import_run"),
        _fk("dataset_id", "dataset", "import_run"),
        _fk("mapping_version_id", "mapping_version", "import_run"),
        sa.CheckConstraint("mode IN ('dry_run', 'import')", name=op.f("ck_import_run_mode_allowed")),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'completed_with_errors', 'failed', 'blocked', "
            "'cancelled')",
            name=op.f("ck_import_run_state_allowed"),
        ),
    )
    op.create_index(op.f("ix_import_run_source_connection_id"), "import_run", ["source_connection_id"])

    op.create_table(
        "import_record",
        _id(),
        sa.Column("import_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_connection_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("external_record_id", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=True),
        sa.Column("destination_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_record")),
        _fk("import_run_id", "import_run", "import_record"),
        _fk("source_connection_id", "source_connection", "import_record"),
        # The idempotency anchor.
        sa.UniqueConstraint("source_connection_id", "entity_type", "external_record_id",
                            name=op.f("uq_import_record_idempotency_anchor")),
        sa.CheckConstraint(ENTITY_TYPES_SQL, name=op.f("ck_import_record_entity_type_allowed")),
        sa.CheckConstraint(
            "status IN ('pending', 'in_flight', 'created', 'updated', 'unchanged', 'uncertain', 'failed', "
            "'rejected')",
            name=op.f("ck_import_record_status_allowed"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_import_record_attempt_count_non_negative")),
    )
    op.create_index(op.f("ix_import_record_import_run_id"), "import_record", ["import_run_id"])

    op.create_table(
        "validation_error",
        _id(),
        sa.Column("import_run_id", sa.BigInteger(), nullable=False),
        sa.Column("row_number", sa.BigInteger(), nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("external_record_id", sa.Text(), nullable=True),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_error")),
        _fk("import_run_id", "import_run", "validation_error"),
    )
    op.create_index(op.f("ix_validation_error_import_run_id"), "validation_error", ["import_run_id"])

    op.create_table(
        "audit_event",
        _id(),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=True),
        sa.Column("detail_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
    )
    op.create_index(op.f("ix_audit_event_event_type"), "audit_event", ["event_type"])
    op.create_index(op.f("ix_audit_event_created_at"), "audit_event", ["created_at"])
    op.create_index(op.f("ix_audit_event_subject"), "audit_event", ["subject_type", "subject_id"])

    op.create_table(
        "llm_trace",
        _id(),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("prompt_json", JSONB, nullable=False),
        sa.Column("response_json", JSONB, nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_trace")),
    )
    op.create_index(op.f("ix_llm_trace_prompt_hash"), "llm_trace", ["prompt_hash"])

    # Approved mapping versions are immutable. Edits must create version n+1.
    op.execute("""
        CREATE FUNCTION mapping_version_guard_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.is_immutable THEN
                RAISE EXCEPTION 'mapping_version % (v%) is approved and immutable; create a new version instead',
                    OLD.id, OLD.version_no;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_mapping_version_immutable
        BEFORE UPDATE OR DELETE ON mapping_version
        FOR EACH ROW EXECUTE FUNCTION mapping_version_guard_immutable();
    """)

    # The audit log is append-only for the application. (TRUNCATE by the DB owner is
    # still possible; this guards against application bugs, not a hostile DBA.)
    op.execute("""
        CREATE FUNCTION audit_event_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only (% blocked)', TG_OP;
        END
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_event_append_only
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_event_append_only ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS audit_event_append_only()")
    op.execute("DROP TRIGGER IF EXISTS trg_mapping_version_immutable ON mapping_version")
    op.execute("DROP FUNCTION IF EXISTS mapping_version_guard_immutable()")
    for table in ("llm_trace", "audit_event", "validation_error", "import_record", "import_run",
                  "mapping_version", "dataset", "source_connection"):
        op.drop_table(table)
