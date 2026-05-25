"""MinIO `ObjectStorage` implementation (Plan 04 task_04_09).

Wraps the sync `minio.Minio` client with `asyncio.to_thread` so it
plays nicely with FastAPI's async handlers. One bucket per
deployment; tenant isolation lives in the key prefix
(`kb/{tenant_id}/{kb_id}/{document_id}/{filename}`).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any
from urllib.parse import urlparse

from api_server.config import Settings, get_settings
from api_server.storage.base import ObjectMetadata, ObjectStorageError


class MinIOObjectStorage:
    """Real MinIO-backed implementation. Lazily creates the bucket
    on first use; subsequent calls are cheap (a `bucket_exists`
    check). Credentials come from `Settings`."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        bucket: str = "agentic-kb",
    ) -> None:
        self._settings = settings or get_settings()
        self._bucket = bucket
        self._client_lock = asyncio.Lock()
        # Typed as Any: minio.Minio has no public type stubs, and
        # leaving the attribute typed as `Minio | None` would force
        # every call site to widen back to Any anyway.
        self._client: Any = None  # lazily built — keeps cold-start fast

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                from minio import Minio  # — optional dep

                parsed = urlparse(self._settings.minio_url)
                host = (parsed.netloc or parsed.path).rstrip("/")
                secure = parsed.scheme == "https"
                client = Minio(
                    host,
                    access_key=self._settings.minio_access_key,
                    secret_key=self._settings.minio_secret_key.get_secret_value(),
                    secure=secure,
                )
                # Lazy bucket creation. `make_bucket` raises if it
                # already exists; the `bucket_exists` check makes the
                # whole block idempotent.
                exists = await asyncio.to_thread(client.bucket_exists, self._bucket)
                if not exists:
                    await asyncio.to_thread(client.make_bucket, self._bucket)
                self._client = client
        return self._client

    async def put_object(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata:
        client = await self._get_client()
        try:
            await asyncio.to_thread(
                client.put_object,
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except Exception as exc:  # — wrap any backend error
            raise ObjectStorageError(f"put_object {key!r} failed: {exc}") from exc
        return ObjectMetadata(key=key, size_bytes=len(data), content_type=content_type)

    async def get_object(self, *, key: str) -> bytes:
        client = await self._get_client()
        from minio.error import S3Error  # — optional dep

        try:
            response = await asyncio.to_thread(client.get_object, self._bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise KeyError(key) from exc
            raise ObjectStorageError(f"get_object {key!r} failed: {exc}") from exc
        try:
            return await asyncio.to_thread(response.read)
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def delete_object(self, *, key: str) -> None:
        client = await self._get_client()
        try:
            await asyncio.to_thread(client.remove_object, self._bucket, key)
        except Exception as exc:
            raise ObjectStorageError(f"delete_object {key!r} failed: {exc}") from exc

    async def object_exists(self, *, key: str) -> bool:
        client = await self._get_client()
        from minio.error import S3Error

        try:
            await asyncio.to_thread(client.stat_object, self._bucket, key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise ObjectStorageError(f"stat_object {key!r} failed: {exc}") from exc


__all__ = ["MinIOObjectStorage"]
