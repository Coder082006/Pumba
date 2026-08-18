"""StoragePort — S3-compatible object storage. SRS §34.9, §35.7.

Used for catalogue media and provider verification documents. Two access
patterns, and the distinction matters for privacy:

* Public media is served through the CDN with long cache lifetimes and
  content-hashed filenames.
* Provider documents are private and are reached only through short-lived
  signed URLs (SRS §35.7), because they contain identity documents.

Uploads are presigned so that large files travel client-to-storage directly
and never occupy an API worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["StoredObject", "PresignedUpload", "StoragePort"]


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]
    key: str
    expires_in_seconds: int


@runtime_checkable
class StoragePort(Protocol):
    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        max_bytes: int,
        expires_in_seconds: int = 900,
    ) -> PresignedUpload: ...

    def presign_download(self, key: str, *, expires_in_seconds: int = 300) -> str: ...

    def put(self, *, key: str, data: bytes, content_type: str) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...
