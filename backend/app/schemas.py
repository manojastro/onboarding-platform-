"""API request/response models. Every endpoint that returns data declares one,
so values are serialised by Pydantic (Decimal -> string), never by FastAPI's
fallback encoder (Decimal -> float)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import policy
from app.models import Dataset


class IssueOut(BaseModel):
    code: str
    message: str
    line_number: int | None = None
    row_number: int | None = None
    column: str | None = None


class ConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    destination_type: str

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("destination_type")
    @classmethod
    def _known_destination(cls, value: str) -> str:
        if value not in policy.DESTINATION_TYPES:
            raise ValueError(f"must be one of {', '.join(policy.DESTINATION_TYPES)}")
        return value


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    destination_type: str
    created_at: datetime


class DatasetOut(BaseModel):
    id: int
    source_connection_id: int
    entity_type: str
    original_filename: str
    file_format: str
    file_size_bytes: int
    file_sha256: str
    schema_fingerprint: str
    columns: list[str]
    row_count: int
    warnings: list[IssueOut]
    uploaded_at: datetime

    @classmethod
    def from_model(cls, dataset: Dataset) -> DatasetOut:
        return cls(
            id=dataset.id,
            source_connection_id=dataset.source_connection_id,
            entity_type=dataset.entity_type,
            original_filename=dataset.original_filename,
            file_format=dataset.file_format,
            file_size_bytes=dataset.file_size_bytes,
            file_sha256=dataset.file_sha256,
            schema_fingerprint=dataset.schema_fingerprint,
            columns=list(dataset.columns_json),
            row_count=dataset.row_count,
            warnings=[IssueOut(**w) for w in dataset.parse_warnings_json],
            uploaded_at=dataset.uploaded_at,
        )


class DatasetDetailOut(DatasetOut):
    preview_rows: list[dict[str, str | None]]


class AuditEventOut(BaseModel):
    id: int
    actor: str
    event_type: str
    subject_type: str
    subject_id: str | None
    detail: dict[str, Any]
    created_at: datetime
