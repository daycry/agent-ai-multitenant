"""Storage Protocol + shared error type (Plan 04 task_04_09)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class ObjectStorageError(RuntimeError):
    """Raised by an `ObjectStorage` implementation on any failure
    that the router should translate into a 5xx (storage backend
    down, signature error, etc.). The router catches it and returns
    503; client-facing 4xx (e.g. content-type mismatch) is the
    caller's responsibility."""


@dataclass(frozen=True)
class ObjectMetadata:
    """Subset of object metadata the router cares about."""

    key: str
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    """Async-friendly facade over MinIO / S3.

    The default tenant bucket is shared (MinIO is single-bucket per
    deployment by convention); keys carry the tenant id as a prefix
    so RLS-equivalent isolation lives in the key naming convention.

    All methods raise :class:`ObjectStorageError` on infrastructure
    failure; they may raise :class:`KeyError` on a missing key on
    `get_object` / `delete_object`.
    """

    async def put_object(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata: ...

    async def get_object(self, *, key: str) -> bytes: ...

    async def delete_object(self, *, key: str) -> None: ...

    async def object_exists(self, *, key: str) -> bool: ...

    async def list_objects(self, *, prefix: str) -> Sequence[str]:
        """All object keys under ``prefix``. Used by the knowledge GC
        (G-03) to find blobs with no live ``documents`` row. Raises
        :class:`ObjectStorageError` on infrastructure failure."""
        ...
