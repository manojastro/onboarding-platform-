"""Upload ingestion: validate the request, parse strictly, store, record.

A dataset row exists only for a file that parsed without a single error.
Rejections are still written to the audit log so the operator can see them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, policy
from app.errors import ApiError
from app.ingest import parser, storage
from app.ingest.fingerprint import schema_fingerprint
from app.models import Dataset, SourceConnection

PREVIEW_ROWS = 20
_MIB = 1024 * 1024


class UploadRejected(ApiError):
    pass


def ingest_upload(
    session: Session,
    *,
    source_connection_id: int,
    entity_type: str,
    filename: str,
    content: bytes,
    declared_size: int | None = None,
) -> Dataset:
    if session.get(SourceConnection, source_connection_id) is None:
        raise UploadRejected(404, "connection_not_found",
                             f"Source connection {source_connection_id} does not exist.")
    if entity_type not in policy.ENTITY_TYPES:
        allowed = ", ".join(policy.ENTITY_TYPES)
        raise UploadRejected(422, "invalid_entity_type",
                             f"entity_type must be one of: {allowed} (got {entity_type!r}).")

    size = max(len(content), declared_size or 0)
    context: dict[str, Any] = {
        "filename": filename,
        "entity_type": entity_type,
        "source_connection_id": source_connection_id,
        "size_bytes": size,
    }
    if size > policy.MAX_UPLOAD_BYTES:
        _reject(session, 413, "file_too_large",
                f"'{filename}' is {size / _MIB:.1f} MiB; the maximum upload size is "
                f"{policy.MAX_UPLOAD_BYTES // _MIB} MiB. Nothing was stored.", context)

    file_format = parser.detect_format(filename)
    if file_format is None:
        _reject(session, 415, "unsupported_format",
                f"'{filename}' is not a supported file type. Upload a .csv or .json file. Nothing was stored.",
                context)

    sha256 = storage.sha256_hex(content)
    context["sha256"] = sha256
    parsed = parser.parse_bytes(content, file_format)
    if not parsed.ok:
        noun = "problem" if parsed.error_count == 1 else "problems"
        _reject(session, 422, "parse_failed",
                f"'{filename}' was rejected: {parsed.error_count} {noun} found. Nothing was stored.",
                context, parsed)

    relative_path, created = storage.store_original(content, sha256, filename)
    try:
        dataset = Dataset(
            source_connection_id=source_connection_id,
            entity_type=entity_type,
            original_filename=filename,
            original_file_path=relative_path,
            file_format=file_format,
            file_size_bytes=len(content),
            file_sha256=sha256,
            schema_fingerprint=schema_fingerprint(parsed.columns),
            columns_json=parsed.columns,
            row_count=len(parsed.rows),
            parse_warnings_json=[w.as_dict() for w in parsed.warnings],
        )
        session.add(dataset)
        session.flush()
        audit.record_event(
            session, event_type="dataset.uploaded", subject_type="dataset", subject_id=dataset.id,
            detail={**context, "row_count": dataset.row_count, "columns": parsed.columns,
                    "schema_fingerprint": dataset.schema_fingerprint, "warning_count": len(parsed.warnings)},
        )
        suspicious = [w.as_dict() for w in parsed.warnings if w.code == "suspicious_instruction_text"]
        if suspicious:
            audit.record_event(
                session, event_type="dataset.suspicious_content_flagged", subject_type="dataset",
                subject_id=dataset.id,
                detail={"count": len(suspicious), "cells": suspicious[:20],
                        "handling": "Kept verbatim as data. No action was taken on this text."},
            )
        session.commit()
    except Exception:
        session.rollback()
        if created:
            storage.remove(relative_path)
        raise
    return dataset


def _reject(session: Session, status: int, code: str, message: str, context: dict[str, Any],
            parsed: parser.ParsedFile | None = None) -> NoReturn:
    errors = [e.as_dict() for e in parsed.errors] if parsed is not None else []
    error_count = parsed.error_count if parsed is not None else 0
    audit.record_event(
        session, event_type="dataset.rejected", subject_type="upload",
        detail={**context, "code": code, "message": message, "error_count": error_count,
                "first_errors": errors[:10]},
    )
    session.commit()
    extra: dict[str, Any] = {}
    if parsed is not None:
        extra = {"errors": errors, "error_count": error_count, "errors_truncated": error_count > len(errors)}
    raise UploadRejected(status, code, message, **extra)


def list_datasets(session: Session, limit: int = 100) -> Sequence[Dataset]:
    return session.scalars(select(Dataset).order_by(Dataset.id.desc()).limit(limit)).all()


def get_dataset(session: Session, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ApiError(404, "dataset_not_found", f"Dataset {dataset_id} does not exist.")
    return dataset


def load_rows(dataset: Dataset) -> parser.ParsedFile:
    """Re-parse the stored original. The stored file is the single source of truth for rows."""
    content = storage.read_original(dataset.original_file_path, dataset.file_sha256)
    parsed = parser.parse_bytes(content, dataset.file_format)
    if not parsed.ok:
        raise ApiError(500, "stored_file_unparseable",
                       f"Dataset {dataset.id}'s stored file no longer parses; the parser changed incompatibly.")
    return parsed
