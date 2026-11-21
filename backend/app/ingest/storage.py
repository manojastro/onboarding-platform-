"""Content-addressed storage for original uploaded files.

Files live at ``<upload_dir>/<sha[:2]>/<sha>/<safe name>``. The dataset row
stores that path relative to ``upload_dir`` plus the SHA-256, and every read
re-verifies the hash, so a modified file is detected instead of silently
re-parsed.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from app.config import settings


class StoredFileError(Exception):
    pass


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip(".")
    return (base or "upload")[:120]


def resolve(relative_path: str) -> Path:
    root = Path(settings.upload_dir).resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise StoredFileError(f"stored path {relative_path!r} escapes the upload directory")
    return path


def store_original(content: bytes, sha256: str, filename: str) -> tuple[str, bool]:
    """Write the file atomically. Returns (relative path, whether this call created it)."""
    relative = str(PurePosixPath(sha256[:2], sha256, safe_filename(filename)))
    target = resolve(relative)
    if target.exists():
        return relative, False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(content)
    os.replace(tmp, target)
    return relative, True


def remove(relative_path: str) -> None:
    resolve(relative_path).unlink(missing_ok=True)


def read_original(relative_path: str, expected_sha256: str) -> bytes:
    content = resolve(relative_path).read_bytes()
    if sha256_hex(content) != expected_sha256:
        raise StoredFileError(f"stored file {relative_path!r} does not match its recorded SHA-256")
    return content
