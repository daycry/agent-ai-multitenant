"""Object storage abstraction (Plan 04 task_04_09).

A thin Protocol over MinIO / S3-compatible blob storage. The router
takes a :class:`ObjectStorage` via FastAPI dependency injection so
tests can pass an in-memory fake and production uses the real
MinIO client.

Why a separate abstraction (vs. calling minio.Minio directly):

  - tests don't need MinIO up to exercise the upload endpoint;
  - the small surface (`put_object` / `get_object` / `delete_object`)
    keeps the boto3 / minio differences out of the router;
  - switching to S3 / R2 later is a one-file change.
"""

from __future__ import annotations

from functools import lru_cache

from api_server.storage.base import ObjectMetadata, ObjectStorage, ObjectStorageError
from api_server.storage.memory import (
    InMemoryObjectStorage,
    get_default_in_memory_storage,
    reset_in_memory_storage,
)


@lru_cache(maxsize=1)
def _build_minio_storage() -> ObjectStorage:
    """Lazy MinIO client — only built once per process, on first
    request. Tests never reach this because they override
    `get_object_storage` with the in-memory implementation."""
    from api_server.storage.minio import MinIOObjectStorage

    return MinIOObjectStorage()


def get_object_storage() -> ObjectStorage:
    """FastAPI dependency. Production builds a `MinIOObjectStorage`;
    tests override this with `app.dependency_overrides`."""
    return _build_minio_storage()


__all__ = [
    "InMemoryObjectStorage",
    "ObjectMetadata",
    "ObjectStorage",
    "ObjectStorageError",
    "get_default_in_memory_storage",
    "get_object_storage",
    "reset_in_memory_storage",
]
