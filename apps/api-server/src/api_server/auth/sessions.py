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

GLOBAL per-user index (prod-09 / authz-4, 2026-07-30). The per-tenant index
alone left a hole big enough to drive the whole login flow through: **every
session is born tenant-less**. ``POST /auth/login`` and the SSO callback mint
what their own docstrings call a "tenant-less IDENTITY session"
(``tenant_id=None``), and only afterwards ``/auth/session/resolve`` /
``/auth/session/select-tenant`` mint a SECOND, tenant-scoped session **without
revoking the first**. Those identity sessions were indexed nowhere, so
:meth:`revoke_user_sessions` could not reach them at all.

For a tenant user that is an orphaned identity token for the session TTL. For a
**System Admin it is the most privileged credential in the deployment**: their
session is tenant-less BY DESIGN (``resolve_session`` returns ``state="admin"``
and they enter the portfolio view with the identity token), and the
``X-Tenant-Id`` header then grants cross-tenant access on top of it. Exactly the
session an off-boarding has to be able to cut, and exactly the one it could not.

So ``create`` also indexes EVERY session — tenant-scoped or not — under a
per-``user_id`` set, and :meth:`revoke_all_user_sessions` walks it. The
per-tenant index stays, and stays the one SCIM uses: deprovisioning a user in
tenant A must not end their session in tenant B (see
:meth:`revoke_user_sessions`). The two scopes are two explicit methods rather
than one method with an implicit ``tenant_id=None``, so the blast radius is
visible at the call site.
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
# Per-user index of EVERY live session id, tenant-scoped or not (a Redis SET).
# Deliberately a DIFFERENT prefix, not `user-sessions:<something>:<user>`: a
# sentinel inside the tenant slot would make any future `SCAN user-sessions:*`
# silently mix the two scopes, which is the class of mistake this index exists
# to fix.
_USER_ALL_INDEX_PREFIX = "user-sessions-all:"


def _key(sid: UUID) -> str:
    return f"{_KEY_PREFIX}{sid}"


def _user_index_key(user_id: UUID, tenant_id: UUID) -> str:
    return f"{_USER_INDEX_PREFIX}{tenant_id}:{user_id}"


def _user_all_index_key(user_id: UUID) -> str:
    return f"{_USER_ALL_INDEX_PREFIX}{user_id}"


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
        # ALWAYS index under the user, tenant or no tenant: this is the only
        # index a tenant-less session (every login's identity session, and a
        # System Admin's ONLY session) ever appears in. See the module docstring.
        await self._index(_user_all_index_key(user_id), sid, ttl_seconds)
        # Additionally index under (user, tenant) so SCIM deprovisioning can
        # find and revoke a user's sessions IN ONE TENANT. A pre-tenant session
        # is not subject to per-tenant deprovisioning, so it stays out of here.
        if tenant_id is not None:
            await self._index(_user_index_key(user_id, tenant_id), sid, ttl_seconds)

    async def _index(self, index_key: str, sid: UUID, ttl_seconds: int) -> None:
        """Add ``sid`` to an index SET and bound that SET's lifetime."""
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
        # Best-effort cleanup of BOTH per-user indexes so a revoked sid does
        # not linger in either. Read the payload first to recover (user,
        # tenant); the per-session delete is what actually ends the session.
        # Leaving the sid in the global index would inflate the count
        # `revoke_all_user_sessions` reports — the number an auditor reads as
        # "sessions that were live".
        payload = await self.get(sid)
        if payload is not None:
            user_raw = payload.get("user_id")
            tenant_raw = payload.get("tenant_id")
            if user_raw:
                user_id = UUID(str(user_raw))
                await self._unindex(_user_all_index_key(user_id), sid)
                if tenant_raw:
                    await self._unindex(_user_index_key(user_id, UUID(str(tenant_raw))), sid)
        await self._redis.delete(_key(sid))

    async def _unindex(self, index_key: str, sid: UUID) -> None:
        await cast("Awaitable[int]", self._redis.srem(index_key, str(sid)))

    async def revoke_user_sessions(self, user_id: UUID, tenant_id: UUID) -> int:
        """Revoke every live session of ``user_id`` **in ``tenant_id``**.

        Used by SCIM deprovisioning (``active=false`` / ``DELETE``) so a
        suspended user loses access immediately, not when their token
        happens to expire. Returns the number of sessions revoked.

        Deliberately scoped to ONE tenant: a user deprovisioned in tenant A may
        still be a legitimate member of tenant B, and their tenant-less identity
        session proves nothing but identity (``/auth/session/resolve`` answers
        ``no_access`` once the membership is inactive). To end EVERY session of a
        user — an off-boarding, or retiring ``is_system_admin`` — use
        :meth:`revoke_all_user_sessions`.

        Iterates the per-tenant index set, deletes each session key, then
        clears the index. Stale sids (already expired) delete to 0 and are
        simply dropped — the count reflects sessions that were actually
        live.
        """
        index_key = _user_index_key(user_id, tenant_id)
        sids = await cast("Awaitable[set[str]]", self._redis.smembers(index_key))
        all_index_key = _user_all_index_key(user_id)
        revoked = 0
        for sid in sids:
            deleted = await self._redis.delete(_key(UUID(sid)))
            revoked += int(deleted)
            # The session is gone; drop it from the global index too so that
            # index keeps meaning "live sessions of this user".
            await self._unindex(all_index_key, UUID(sid))
        await self._redis.delete(index_key)
        return revoked

    async def revoke_all_user_sessions(self, user_id: UUID) -> int:
        """Revoke EVERY live session of ``user_id``, tenant-scoped or not.

        The wider hammer, for when the problem is the IDENTITY rather than one
        membership: an off-boarding, a credential believed leaked, or retiring
        ``users.is_system_admin`` — whose session is tenant-less and therefore
        invisible to :meth:`revoke_user_sessions` (prod-09 / authz-4). Returns
        the number of sessions that were actually live.

        Walks the global per-user index and, for each session it kills, also
        removes the sid from that session's per-tenant index so the two indexes
        do not disagree.
        """
        index_key = _user_all_index_key(user_id)
        sids = await cast("Awaitable[set[str]]", self._redis.smembers(index_key))
        revoked = 0
        for sid in sids:
            session_id = UUID(sid)
            # Read the payload BEFORE the delete: it is the only place the
            # session's tenant is recorded, and we need it to clean that index.
            payload = await self.get(session_id)
            deleted = await self._redis.delete(_key(session_id))
            revoked += int(deleted)
            if payload is not None:
                tenant_raw = payload.get("tenant_id")
                if tenant_raw:
                    await self._unindex(_user_index_key(user_id, UUID(str(tenant_raw))), session_id)
        await self._redis.delete(index_key)
        return revoked
