"""In-memory `ObjectStorage` implementation for tests (Plan 04 task_04_09)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from api_server.storage.base import ObjectMetadata


@dataclass
class InMemoryObjectStorage:
    """Dict-backed store. Thread-safe via a lock so concurrent
    pytest-asyncio tasks don't race on the same instance.

    Implements :class:`ObjectStorage` structurally — no inheritance
    needed because `ObjectStorage` is a `typing.Protocol`.
    """

    _store: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def put_object(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata:
        async with self._lock:
            self._store[key] = (data, content_type)
        return ObjectMetadata(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
        )

    async def get_object(self, *, key: str) -> bytes:
        async with self._lock:
            if key not in self._store:
                raise KeyError(key)
            return self._store[key][0]

    async def delete_object(self, *, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def object_exists(self, *, key: str) -> bool:
        async with self._lock:
            return key in self._store

    async def list_objects(self, *, prefix: str) -> list[str]:
        async with self._lock:
            return [k for k in self._store if k.startswith(prefix)]


# Module-level singleton makes it trivial for the test fixture to
# override `Depends(get_object_storage)` once and have all routes
# share the same store.
_default_in_memory = InMemoryObjectStorage()


def reset_in_memory_storage() -> None:
    """Wipe the shared in-memory store. Called by test fixtures
    between runs so a previous test's blobs never leak."""
    _default_in_memory._store.clear()


def get_default_in_memory_storage() -> InMemoryObjectStorage:
    return _default_in_memory


__all__ = [
    "InMemoryObjectStorage",
    "get_default_in_memory_storage",
    "reset_in_memory_storage",
]
