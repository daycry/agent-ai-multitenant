"""Server-side sessions backed by Redis.

Why server-side and not stateless JWTs:

  - Revocation is immediate (delete the key, every subsequent request
    with the old token gets 401).
  - The JWT carries a session id (`sid`); the payload is opaque from
    the client's perspective.
  - Audit trail is centralized in one place.

Cookie payload stays in Redis; the `sessions` table only records
metadata for auditability and is written from richer flows in later
phases. Phase 0 keeps the table empty.
"""

from __future__ import annotations

import json
from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "session:"


def _key(sid: UUID) -> str:
    return f"{_KEY_PREFIX}{sid}"


class SessionStore:
    """Thin wrapper that hides the Redis layout from callers."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(
        self,
        sid: UUID,
        *,
        user_id: UUID,
        tenant_id: UUID | None,
        ttl_seconds: int,
    ) -> None:
        payload = {
            "user_id": str(user_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
        }
        await self._redis.set(_key(sid), json.dumps(payload), ex=ttl_seconds)

    async def get(self, sid: UUID) -> dict[str, str | None] | None:
        raw = await self._redis.get(_key(sid))
        if raw is None:
            return None
        parsed: dict[str, str | None] = json.loads(raw)
        return parsed

    async def revoke(self, sid: UUID) -> None:
        await self._redis.delete(_key(sid))
