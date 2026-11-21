from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app import policy
from app.api.deps import SessionDep
from app.schemas import DatasetDetailOut, DatasetOut
from app.services import datasets

router = APIRouter(tags=["datasets"])


@router.post("/datasets", response_model=DatasetOut, status_code=201)
def upload_dataset(
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    source_connection_id: Annotated[int, Form()],
    entity_type: Annotated[str, Form()],
) -> DatasetOut:
    # Read at most one byte past the limit: enough to know it is too big.
    content = file.file.read(policy.MAX_UPLOAD_BYTES + 1)
    dataset = datasets.ingest_upload(
        session,
        source_connection_id=source_connection_id,
        entity_type=entity_type,
        filename=file.filename or "upload",
        content=content,
        declared_size=file.size,
    )
    return DatasetOut.from_model(dataset)


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(session: SessionDep) -> list[DatasetOut]:
    return [DatasetOut.from_model(d) for d in datasets.list_datasets(session)]


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(dataset_id: int, session: SessionDep) -> DatasetDetailOut:
    dataset = datasets.get_dataset(session, dataset_id)
    parsed = datasets.load_rows(dataset)
    return DatasetDetailOut(**DatasetOut.from_model(dataset).model_dump(),
                            preview_rows=parsed.rows[: datasets.PREVIEW_ROWS])
