"""File storage abstraction: local directory in dev, S3-compatible bucket in prod."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Protocol

from .config import settings
from .db import session_scope
from .models import StoredFile


class Backend(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalBackend:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # keys are generated, but never trust paths
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Backend:  # pragma: no cover - needs a bucket
    def __init__(self, bucket: str, endpoint: str = ""):
        import boto3  # optional dependency

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint or None)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_backend: Backend | None = None


def backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = S3Backend(settings.s3_bucket, settings.s3_endpoint) if settings.storage_backend == "s3" else LocalBackend(settings.storage_dir)
    return _backend


def save_file(data: bytes, filename: str, project_id: int | None, uploaded_by: int | None = None, content_type: str | None = None) -> StoredFile:
    ctype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_name = "".join(c for c in Path(filename).name if c.isalnum() or c in "._-")[:120] or "file"
    key = f"p{project_id or 0}/{uuid.uuid4().hex}/{safe_name}"
    backend().put(key, data, ctype)
    with session_scope() as s:
        f = StoredFile(project_id=project_id, key=key, filename=safe_name, content_type=ctype, size=len(data), uploaded_by=uploaded_by)
        s.add(f)
        s.flush()
        return f


def read_file(f: StoredFile) -> bytes:
    return backend().get(f.key)
