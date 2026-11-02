"""Original-file storage: local directory (development / docker compose) or S3 (AWS).

Keys are server-generated (``<document>/<version>.<ext>``); user filenames never touch
the filesystem. S3 objects are encrypted at rest (SSE-S3); credentials come from the
IAM role, never from configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class LocalFileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("storage key escapes the storage root")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3FileStore:
    """Not exercised by automated tests (no AWS in CI) — see docs/known-limitations.md."""

    def __init__(self, bucket: str, client: Any | None = None):
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="AES256")

    def get(self, key: str) -> bytes:
        return bytes(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


@lru_cache
def get_store() -> LocalFileStore | S3FileStore:
    s = get_settings()
    return S3FileStore(s.s3_bucket) if s.s3_bucket else LocalFileStore(s.file_dir)
