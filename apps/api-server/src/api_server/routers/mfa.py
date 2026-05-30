"""`/auth/mfa/*` endpoints — TOTP second factor (Plan 08 task_08_09).

MFA is ADDED ALONGSIDE the existing auth (local login + OIDC + SAML).
A user WITHOUT a confirmed TOTP factor logs in EXACTLY as before. Only a
user with ``confirmed_at IS NOT NULL`` is challenged after the
password/SSO step:

  1. the password/SSO step verifies the FIRST factor, sees a confirmed
     TOTP enrollment, and returns ``status: mfa_required`` + a single-use,
     short-lived challenge token (NOT a session) — see ``routers/auth.py``;
  2. ``POST /auth/mfa/totp/verify`` takes that token + a TOTP (or recovery)
     code and, only on success, mints the real Redis session + JWT.

Enrollment management (``POST /auth/mfa/totp/enroll`` →
``POST /auth/mfa/totp/confirm``, ``GET /auth/mfa/totp`` status, and
``DELETE /auth/mfa/totp`` disable) is interactive and tenant-scoped: it
needs a logged-in principal (``require_tenant_member``) and an active
tenant, so the enrollment row lands under ``app.tenant_id`` (RLS).

Secrets at rest (CLAUDE.md): the TOTP seed is Fernet-encrypted
(``user_mfa_totp.secret_encrypted``) and recovery codes are stored only as
SHA-256 digests — never in clear.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_mfa_challenge_store,
    get_session_store,
    get_tenant_session,
    require_tenant_member,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.mfa.challenge_store import MfaChallengeStore
from api_server.auth.mfa.secrets import MfaSecretError, decrypt_totp_secret, encrypt_totp_secret
from api_server.auth.mfa.store import load_enrollment
from api_server.auth.mfa.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_code,
)
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.models import User, UserMfaTotp
from api_server.db.session import get_admin_sessionmaker
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.auth import LoginResponse
from api_server.schemas.mfa import (
    MfaConfirmRequest,
    MfaEnrollResponse,
    MfaStatusResponse,
    MfaTotpVerifyRequest,
)

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])


# ---------------------------------------------------------------------------
# GET /auth/mfa/totp — status for the active tenant
# ---------------------------------------------------------------------------
@router.get("/totp", response_model=MfaStatusResponse)
async def totp_status(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MfaStatusResponse:
    """Whether the caller has a TOTP factor in the active tenant."""
    tenant_id = require_tenant_id(principal)
    enrollment = await load_enrollment(session, tenant_id=tenant_id, user_id=principal.user_id)
    if enrollment is None:
        return MfaStatusResponse(enrolled=False, confirmed=False, recovery_codes_remaining=0)
    return MfaStatusResponse(
        enrolled=True,
        confirmed=enrollment.confirmed_at is not None,
        recovery_codes_remaining=len(enrollment.recovery_codes),
    )


# ---------------------------------------------------------------------------
# POST /auth/mfa/totp/enroll — generate a secret + provisioning URI
# ---------------------------------------------------------------------------
@router.post("/totp/enroll", response_model=MfaEnrollResponse)
async def totp_enroll(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MfaEnrollResponse:
    """Begin TOTP enrollment: mint a secret + recovery codes (unconfirmed).

    The secret is shown once (as the otpauth:// URI / QR) and persisted
    only Fernet-encrypted; the recovery codes are shown once and persisted
    only as hashes. Enrollment does NOT gate login until confirmed with a
    valid code, so a half-finished enrollment never locks a user out.

    Re-enrolling overwrites an UNCONFIRMED secret (idempotent restart); a
    CONFIRMED factor must be disabled first (409) so a user cannot silently
    replace an active second factor.
    """
    tenant_id = require_tenant_id(principal)

    secret = generate_secret()
    recovery_codes = generate_recovery_codes()
    recovery_hashes = [hash_recovery_code(c) for c in recovery_codes]

    enrollment = await load_enrollment(session, tenant_id=tenant_id, user_id=principal.user_id)
    if enrollment is not None and enrollment.confirmed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TOTP is already enabled; disable it before re-enrolling",
        )
    if enrollment is None:
        enrollment = UserMfaTotp(
            id=uuid7(),
            tenant_id=tenant_id,
            user_id=principal.user_id,
            secret_encrypted=encrypt_totp_secret(secret),
            confirmed_at=None,
            recovery_codes=recovery_hashes,
        )
        session.add(enrollment)
        try:
            await session.flush()
        except IntegrityError as exc:
            # Race: a concurrent enroll created the row. Treat as a restart.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a TOTP enrollment is already in progress; retry",
            ) from exc
    else:
        # Unconfirmed row: overwrite the secret + recovery codes.
        enrollment.secret_encrypted = encrypt_totp_secret(secret)
        enrollment.recovery_codes = recovery_hashes
        await session.flush()

    account = await _account_label(session, principal.user_id)
    return MfaEnrollResponse(
        secret=secret,
        provisioning_uri=provisioning_uri(secret, account_name=account),
        recovery_codes=recovery_codes,
    )


# ---------------------------------------------------------------------------
# POST /auth/mfa/totp/confirm — prove possession, activate the factor
# ---------------------------------------------------------------------------
@router.post("/totp/confirm", response_model=MfaStatusResponse)
async def totp_confirm(
    payload: MfaConfirmRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MfaStatusResponse:
    """Confirm enrollment with a current TOTP code; from now on it gates login."""
    tenant_id = require_tenant_id(principal)
    enrollment = await load_enrollment(session, tenant_id=tenant_id, user_id=principal.user_id)
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no TOTP enrollment to confirm; enroll first",
        )
    if enrollment.confirmed_at is not None:
        # Already confirmed — idempotent success.
        return MfaStatusResponse(
            enrolled=True,
            confirmed=True,
            recovery_codes_remaining=len(enrollment.recovery_codes),
        )

    secret = _decrypt_or_500(enrollment.secret_encrypted)
    if not verify_code(secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid TOTP code",
        )
    enrollment.confirmed_at = datetime.now(tz=UTC)
    await session.flush()
    return MfaStatusResponse(
        enrolled=True,
        confirmed=True,
        recovery_codes_remaining=len(enrollment.recovery_codes),
    )


# ---------------------------------------------------------------------------
# DELETE /auth/mfa/totp — disable the factor
# ---------------------------------------------------------------------------
@router.delete("/totp", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Remove the caller's TOTP factor in the active tenant (back to 1FA)."""
    tenant_id = require_tenant_id(principal)
    enrollment = await load_enrollment(session, tenant_id=tenant_id, user_id=principal.user_id)
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no TOTP enrollment to disable",
        )
    await session.delete(enrollment)
    await session.flush()


# ---------------------------------------------------------------------------
# POST /auth/mfa/totp/verify — complete a login awaiting MFA
# ---------------------------------------------------------------------------
@router.post("/totp/verify", response_model=LoginResponse)
async def totp_verify(
    payload: MfaTotpVerifyRequest,
    challenges: MfaChallengeStore = Depends(get_mfa_challenge_store),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Second factor: challenge token + TOTP/recovery code -> a real session.

    Unauthenticated by JWT — the single-use challenge token (minted by the
    password/SSO step, proving the first factor) IS the credential here.
    On success this mints the SAME Redis session + JWT the non-MFA path
    would have, bound to the tenant the challenge recorded.
    """
    challenge = await challenges.consume(payload.mfa_token)
    if challenge is None:
        # Unknown / expired / already-used challenge token.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired MFA challenge",
        )

    code_ok = await _verify_second_factor(
        user_id=challenge.user_id,
        tenant_id=challenge.tenant_id,
        code=payload.code,
    )
    if not code_ok:
        # The challenge was consumed above (single-use), so a wrong code
        # forces the user back through the first factor — no code-guessing
        # loop against one challenge token.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid MFA code",
        )

    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=challenge.user_id,
        tenant_id=challenge.tenant_id,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=challenge.user_id,
        session_id=session_id,
        tenant_id=challenge.tenant_id,
        is_system_admin=challenge.is_system_admin,
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _decrypt_or_500(ciphertext: str) -> str:
    try:
        return decrypt_totp_secret(ciphertext)
    except MfaSecretError as exc:
        # Operator misconfiguration (e.g. rotated encryption key) — never
        # echo the cause.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MFA is misconfigured for this account",
        ) from exc


async def _account_label(session: AsyncSession, user_id: UUID) -> str:
    """The account name shown in the authenticator (the user's email)."""
    result = await session.execute(select(User.email).where(User.id == user_id))
    email = result.scalar_one_or_none()
    return email or str(user_id)


async def _verify_second_factor(*, user_id: UUID, tenant_id: UUID | None, code: str) -> bool:
    """Verify a TOTP code OR consume a one-time recovery code.

    Runs on the BYPASSRLS admin role, scoped explicitly to
    ``(user_id, tenant_id)``: the verify call is authenticated only by the
    challenge token (no JWT/session yet), so there is no ``app.tenant_id``
    to bind. When the challenge has no tenant (local login picks one
    later), we match the single CONFIRMED enrollment for the user.

    A recovery code is consumed (its digest removed) on a match, so it
    works exactly once. Returns True on a valid TOTP or recovery code.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        stmt = select(UserMfaTotp).where(
            UserMfaTotp.user_id == user_id,
            UserMfaTotp.confirmed_at.is_not(None),
        )
        if tenant_id is not None:
            stmt = stmt.where(UserMfaTotp.tenant_id == tenant_id)
        result = await session.execute(stmt)
        enrollment = result.scalars().first()
        if enrollment is None:
            return False

        secret = decrypt_totp_secret(enrollment.secret_encrypted)
        if verify_code(secret, code):
            return True

        # Fall back to a one-time recovery code: match by digest, consume.
        digest = hash_recovery_code(code)
        if digest in enrollment.recovery_codes:
            # Reassign (don't mutate in place) so SQLAlchemy detects the
            # JSONB change and persists the consumption. The admin role
            # (BYPASSRLS) writes without an app.tenant_id binding.
            remaining = [d for d in enrollment.recovery_codes if d != digest]
            enrollment.recovery_codes = remaining
            await session.flush()
            return True
        return False
