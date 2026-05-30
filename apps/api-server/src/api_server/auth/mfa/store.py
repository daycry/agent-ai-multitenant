"""DB lookups for TOTP enrollments (Plan 08 task_08_09).

Two access patterns, both RLS-aware:

  * **Per-tenant, under an open session** (enrollment / confirm / verify):
    the caller already holds a tenant-scoped session with ``app.tenant_id``
    bound, so a plain ``SELECT`` is RLS-scoped to the active tenant.
  * **At local login, where there is no tenant yet** — the password step
    must decide "does this user have ANY confirmed TOTP factor?" before a
    tenant is picked. That probe runs on the BYPASSRLS admin role (a
    deliberate, narrow read) because there is no ``app.tenant_id`` to scope
    by; it returns only a boolean, never another tenant's secret.

Keeping these in one module means the login flow and the MFA endpoints
share exactly one definition of "confirmed enrollment".
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import UserMfaTotp
from api_server.db.session import get_admin_sessionmaker


async def load_enrollment(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID
) -> UserMfaTotp | None:
    """Return the user's TOTP enrollment in the bound tenant, or ``None``.

    Runs under the caller's tenant-scoped session (``app.tenant_id`` bound),
    so RLS guarantees it only ever returns THIS tenant's row.
    """
    result = await session.execute(
        select(UserMfaTotp).where(
            UserMfaTotp.tenant_id == tenant_id,
            UserMfaTotp.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def user_has_confirmed_totp(user_id: UUID) -> bool:
    """True iff ``user_id`` has a confirmed TOTP factor in ANY tenant.

    Used by the local-login password step, which has no tenant context yet
    (the session is minted with ``tenant_id=None``). Runs on the BYPASSRLS
    admin role for that reason — it is a narrow existence probe that
    returns only a boolean and never exposes a secret or another tenant's
    data.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        found = await session.execute(
            select(
                exists().where(
                    UserMfaTotp.user_id == user_id,
                    UserMfaTotp.confirmed_at.is_not(None),
                )
            )
        )
        return bool(found.scalar())


__all__ = ["load_enrollment", "user_has_confirmed_totp"]
