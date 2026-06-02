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

Per-user session index (Plan 08 task_08_08 — SCIM deprovisioning).
Besides the per-session key, the store keeps a Redis SET per
``(user_id, tenant_id)`` holding that user's live session ids in the
tenant. This is what lets SCIM ``active=false`` / ``DELETE`` revoke a
user's access immediately: there is otherwise no reverse lookup from a
user to their sessions. The index is best-effort metadata — the
per-session key remains the source of truth for a session being live —
so a stale sid lingering in the set is harmless (revoking it is a no-op).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable
from typing import cast
from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "session:"
# Per-user, per-tenant index of live session ids (a Redis SET).
_USER_INDEX_PREFIX = "user-sessions:"


def _key(sid: UUID) -> str:
    return f"{_KEY_PREFIX}{sid}"


def _user_index_key(user_id: UUID, tenant_id: UUID) -> str:
    return f"{_USER_INDEX_PREFIX}{tenant_id}:{user_id}"


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
        # ``created_at`` (epoch seconds) lets the admin-hardening gate
        # (Plan 15 task_15_18) enforce a SHORT max session age for the
        # System-Admin surface independently of the JWT/session TTL — the
        # session can outlive 15 minutes for a regular user but an admin
        # request on a session older than the admin TTL is rejected.
        payload = {
            "user_id": str(user_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "created_at": int(time.time()),
        }
        await self._redis.set(_key(sid), json.dumps(payload), ex=ttl_seconds)
        # Index the session under (user, tenant) so SCIM deprovisioning can
        # find and revoke it. Only tenant-scoped sessions are indexed: a
        # pre-tenant session (local login before picking a tenant) is not
        # subject to per-tenant deprovisioning.
        if tenant_id is not None:
            index_key = _user_index_key(user_id, tenant_id)
            # redis-py types `sadd` as a sync/async union (ResponseT); on the
            # async client it is an awaitable — cast so mypy strict agrees.
            await cast("Awaitable[int]", self._redis.sadd(index_key, str(sid)))
            # Keep the index from outliving the sessions it points at: it
            # expires no sooner than the longest session it tracks.
            await self._redis.expire(index_key, ttl_seconds)

    async def get(self, sid: UUID) -> dict[str, str | int | None] | None:
        raw = await self._redis.get(_key(sid))
        if raw is None:
            return None
        parsed: dict[str, str | int | None] = json.loads(raw)
        return parsed

    async def revoke(self, sid: UUID) -> None:
        # Best-effort cleanup of the per-user index so a revoked sid does
        # not linger there. Read the payload first to recover (user,
        # tenant); the per-session delete is what actually ends the session.
        payload = await self.get(sid)
        if payload is not None:
            user_raw = payload.get("user_id")
            tenant_raw = payload.get("tenant_id")
            if user_raw and tenant_raw:
                await cast(
                    "Awaitable[int]",
                    self._redis.srem(
                        _user_index_key(UUID(str(user_raw)), UUID(str(tenant_raw))), str(sid)
                    ),
                )
        await self._redis.delete(_key(sid))

    async def revoke_user_sessions(self, user_id: UUID, tenant_id: UUID) -> int:
        """Revoke every live session of ``user_id`` in ``tenant_id``.

        Used by SCIM deprovisioning (``active=false`` / ``DELETE``) so a
        suspended user loses access immediately, not when their token
        happens to expire. Returns the number of sessions revoked.

        Iterates the per-user index set, deletes each session key, then
        clears the index. Stale sids (already expired) delete to 0 and are
        simply dropped — the count reflects sessions that were actually
        live.
        """
        index_key = _user_index_key(user_id, tenant_id)
        sids = await cast("Awaitable[set[str]]", self._redis.smembers(index_key))
        revoked = 0
        for sid in sids:
            deleted = await self._redis.delete(_key(UUID(sid)))
            revoked += int(deleted)
        await self._redis.delete(index_key)
        return revoked
