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

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import UserMfaTotp, WebauthnCredential
from api_server.db.session import get_admin_sessionmaker

# The second-factor method names surfaced to the client in
# ``MfaRequiredResponse.mfa_methods`` and used by the verify endpoints.
MFA_METHOD_TOTP = "totp"
MFA_METHOD_WEBAUTHN = "webauthn"


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


async def load_webauthn_credentials(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID
) -> list[WebauthnCredential]:
    """Return the user's WebAuthn credentials in the bound tenant.

    Runs under the caller's tenant-scoped session (``app.tenant_id`` bound),
    so RLS guarantees it only ever returns THIS tenant's rows.
    """
    result = await session.execute(
        select(WebauthnCredential)
        .where(
            WebauthnCredential.tenant_id == tenant_id,
            WebauthnCredential.user_id == user_id,
        )
        .order_by(WebauthnCredential.created_at)
    )
    return list(result.scalars().all())


async def user_mfa_methods(user_id: UUID) -> list[str]:
    """Second-factor methods ``user_id`` has confirmed in ANY tenant.

    Used by the local-login password step, which has no tenant context yet
    (the session is minted with ``tenant_id=None``). Runs on the BYPASSRLS
    admin role for that reason — narrow existence probes that return only
    booleans, never a secret or another tenant's data. Ordered TOTP-first so
    the response is deterministic.
    """
    sessionmaker = get_admin_sessionmaker()
    methods: list[str] = []
    async with sessionmaker() as session, session.begin():
        has_totp = await session.execute(
            select(
                exists().where(
                    UserMfaTotp.user_id == user_id,
                    UserMfaTotp.confirmed_at.is_not(None),
                )
            )
        )
        if bool(has_totp.scalar()):
            methods.append(MFA_METHOD_TOTP)
        has_webauthn = await session.execute(
            select(
                exists().where(
                    WebauthnCredential.user_id == user_id,
                    WebauthnCredential.confirmed_at.is_not(None),
                )
            )
        )
        if bool(has_webauthn.scalar()):
            methods.append(MFA_METHOD_WEBAUTHN)
    return methods


async def load_webauthn_credentials_for_login(
    *, user_id: UUID, tenant_id: UUID | None
) -> list[WebauthnCredential]:
    """Load a user's confirmed WebAuthn credentials at login (no JWT yet).

    Runs on the BYPASSRLS admin role, scoped explicitly to
    ``(user_id, tenant_id)``: the authentication ceremony is authenticated
    only by the interim MFA challenge token, so there is no ``app.tenant_id``
    to bind. When the challenge has no tenant (local login picks one later),
    we match every confirmed credential for the user.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        stmt = select(WebauthnCredential).where(
            WebauthnCredential.user_id == user_id,
            WebauthnCredential.confirmed_at.is_not(None),
        )
        if tenant_id is not None:
            stmt = stmt.where(WebauthnCredential.tenant_id == tenant_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def bump_webauthn_sign_count(*, credential_pk: UUID, new_sign_count: int) -> None:
    """Persist the new signature counter after a verified assertion.

    Runs on the BYPASSRLS admin role (the verify call has no
    ``app.tenant_id``), scoped to the credential's own primary key. Also
    stamps ``last_used_at`` for observability.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        cred = await session.get(WebauthnCredential, credential_pk)
        if cred is None:  # pragma: no cover - the caller just loaded it
            return
        cred.sign_count = new_sign_count
        cred.last_used_at = datetime.now(tz=UTC)


__all__ = [
    "MFA_METHOD_TOTP",
    "MFA_METHOD_WEBAUTHN",
    "bump_webauthn_sign_count",
    "load_enrollment",
    "load_webauthn_credentials",
    "load_webauthn_credentials_for_login",
    "user_has_confirmed_totp",
    "user_mfa_methods",
]
