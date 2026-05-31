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

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7
from webauthn.helpers import bytes_to_base64url

from api_server.auth.deps import (
    AuthPrincipal,
    get_mfa_challenge_store,
    get_session_store,
    get_tenant_session,
    get_webauthn_challenge_store,
    require_tenant_member,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.mfa.challenge_store import MfaChallengeStore
from api_server.auth.mfa.secrets import MfaSecretError, decrypt_totp_secret, encrypt_totp_secret
from api_server.auth.mfa.store import (
    bump_webauthn_sign_count,
    load_enrollment,
    load_webauthn_credentials,
    load_webauthn_credentials_for_login,
)
from api_server.auth.mfa.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_code,
)
from api_server.auth.mfa.webauthn import (
    RpConfig,
    WebauthnCeremonyError,
    authentication_options_json,
    registration_options_json,
    verify_authentication,
    verify_registration,
)
from api_server.auth.mfa.webauthn_challenge_store import WebauthnChallengeStore
from api_server.auth.sessions import SessionStore
from api_server.config import Settings, get_settings
from api_server.db.models import User, UserMfaTotp, WebauthnCredential
from api_server.db.session import get_admin_sessionmaker
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.auth import LoginResponse
from api_server.schemas.mfa import (
    MfaConfirmRequest,
    MfaEnrollResponse,
    MfaStatusResponse,
    MfaTotpVerifyRequest,
    WebauthnCredentialsResponse,
    WebauthnCredentialSummary,
    WebauthnLoginBeginRequest,
    WebauthnLoginBeginResponse,
    WebauthnLoginFinishRequest,
    WebauthnRegisterBeginResponse,
    WebauthnRegisterFinishRequest,
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


# ===========================================================================
# WebAuthn (Plan 08 task_08_10) — a SECOND alternative second factor.
#
# Registration is interactive + tenant-scoped (require_tenant_member); the
# credential row lands under app.tenant_id (RLS). The login second factor
# (begin/finish) is authenticated only by the interim MFA challenge token —
# no JWT — exactly like the TOTP verify endpoint.
# ===========================================================================


# ---------------------------------------------------------------------------
# POST /auth/mfa/webauthn/register/begin — registration options
# ---------------------------------------------------------------------------
@router.post("/webauthn/register/begin", response_model=WebauthnRegisterBeginResponse)
async def webauthn_register_begin(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    challenges: WebauthnChallengeStore = Depends(get_webauthn_challenge_store),
) -> WebauthnRegisterBeginResponse:
    """Begin WebAuthn registration: mint creation options for the browser.

    Excludes the user's already-registered credentials so the same
    authenticator cannot be enrolled twice. The single-use challenge is
    stashed in Redis keyed by the user and checked by ``/register/finish``.
    """
    tenant_id = require_tenant_id(principal)
    settings = get_settings()

    existing = await load_webauthn_credentials(
        session, tenant_id=tenant_id, user_id=principal.user_id
    )
    account = await _account_label(session, principal.user_id)

    challenge = _new_webauthn_challenge()
    options_json = registration_options_json(
        _rp_config(settings),
        user_id=principal.user_id.bytes,
        user_name=account,
        user_display_name=account,
        challenge=challenge,
        exclude_credential_ids=[c.credential_id for c in existing],
    )
    await challenges.put_registration(
        str(principal.user_id),
        challenge,
        ttl_seconds=settings.webauthn_challenge_ttl_seconds,
    )
    return WebauthnRegisterBeginResponse(options=json.loads(options_json))


# ---------------------------------------------------------------------------
# POST /auth/mfa/webauthn/register/finish — verify + store the credential
# ---------------------------------------------------------------------------
@router.post("/webauthn/register/finish", response_model=WebauthnCredentialsResponse)
async def webauthn_register_finish(
    payload: WebauthnRegisterFinishRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    challenges: WebauthnChallengeStore = Depends(get_webauthn_challenge_store),
) -> WebauthnCredentialsResponse:
    """Verify the attestation and persist the credential (public material only).

    On success a new :class:`WebauthnCredential` row is stored under the
    active tenant (RLS): the credential id, the COSE PUBLIC key and the
    initial signature counter. No private key is ever stored.
    """
    tenant_id = require_tenant_id(principal)
    settings = get_settings()

    expected_challenge = await challenges.consume_registration(str(principal.user_id))
    if expected_challenge is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no WebAuthn registration in progress; begin first",
        )

    try:
        verified = verify_registration(
            _rp_config(settings),
            credential=payload.credential,
            expected_challenge=expected_challenge,
        )
    except WebauthnCeremonyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WebAuthn registration did not verify",
        ) from exc

    transports = _extract_transports(payload.credential)
    credential = WebauthnCredential(
        id=uuid7(),
        tenant_id=tenant_id,
        user_id=principal.user_id,
        credential_id=verified.credential_id,
        public_key=verified.public_key,
        sign_count=verified.sign_count,
        transports=transports,
        label=payload.label,
        confirmed_at=datetime.now(tz=UTC),
    )
    session.add(credential)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The credential id is globally unique: this authenticator is
        # already registered (here or in another tenant).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this authenticator is already registered",
        ) from exc

    creds = await load_webauthn_credentials(session, tenant_id=tenant_id, user_id=principal.user_id)
    return _credentials_response(creds)


# ---------------------------------------------------------------------------
# GET /auth/mfa/webauthn — list the caller's credentials in the active tenant
# ---------------------------------------------------------------------------
@router.get("/webauthn", response_model=WebauthnCredentialsResponse)
async def webauthn_list(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> WebauthnCredentialsResponse:
    """List the caller's registered WebAuthn credentials (RLS-scoped)."""
    tenant_id = require_tenant_id(principal)
    creds = await load_webauthn_credentials(session, tenant_id=tenant_id, user_id=principal.user_id)
    return _credentials_response(creds)


# ---------------------------------------------------------------------------
# DELETE /auth/mfa/webauthn/{credential_id} — remove one credential
# ---------------------------------------------------------------------------
@router.delete("/webauthn/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def webauthn_delete(
    credential_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Remove one of the caller's WebAuthn credentials in the active tenant."""
    tenant_id = require_tenant_id(principal)
    result = await session.execute(
        select(WebauthnCredential).where(
            WebauthnCredential.id == credential_id,
            WebauthnCredential.tenant_id == tenant_id,
            WebauthnCredential.user_id == principal.user_id,
        )
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such WebAuthn credential",
        )
    await session.delete(credential)
    await session.flush()


# ---------------------------------------------------------------------------
# POST /auth/mfa/webauthn/login/begin — authentication options for a login
# ---------------------------------------------------------------------------
@router.post("/webauthn/login/begin", response_model=WebauthnLoginBeginResponse)
async def webauthn_login_begin(
    payload: WebauthnLoginBeginRequest,
    mfa_challenges: MfaChallengeStore = Depends(get_mfa_challenge_store),
    challenges: WebauthnChallengeStore = Depends(get_webauthn_challenge_store),
) -> WebauthnLoginBeginResponse:
    """Begin the WebAuthn login second factor for a pending login.

    Authenticated only by the interim MFA challenge token (proving the first
    factor). The token is PEEKED (not consumed) so ``/login/finish`` can
    still complete it; the single-use WebAuthn challenge is stashed keyed by
    that token.
    """
    settings = get_settings()
    challenge_ctx = await mfa_challenges.peek(payload.mfa_token)
    if challenge_ctx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired MFA challenge",
        )

    creds = await load_webauthn_credentials_for_login(
        user_id=challenge_ctx.user_id, tenant_id=challenge_ctx.tenant_id
    )
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no WebAuthn credential registered",
        )

    challenge = _new_webauthn_challenge()
    options_json = authentication_options_json(
        _rp_config(settings),
        challenge=challenge,
        allow_credential_ids=[c.credential_id for c in creds],
    )
    await challenges.put_authentication(
        payload.mfa_token,
        challenge,
        ttl_seconds=settings.webauthn_challenge_ttl_seconds,
    )
    return WebauthnLoginBeginResponse(options=json.loads(options_json))


# ---------------------------------------------------------------------------
# POST /auth/mfa/webauthn/login/finish — verify the assertion -> a session
# ---------------------------------------------------------------------------
@router.post("/webauthn/login/finish", response_model=LoginResponse)
async def webauthn_login_finish(
    payload: WebauthnLoginFinishRequest,
    mfa_challenges: MfaChallengeStore = Depends(get_mfa_challenge_store),
    challenges: WebauthnChallengeStore = Depends(get_webauthn_challenge_store),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Second factor: challenge token + WebAuthn assertion -> a real session.

    Consumes BOTH single-use tokens (the interim MFA challenge AND the
    WebAuthn challenge), verifies the assertion against the stored public
    key, bumps the signature counter (rejecting a stale / replayed counter),
    and mints the SAME Redis session + JWT the non-MFA path would have.
    """
    settings = get_settings()

    # Consume the interim MFA challenge first (single-use): a wrong assertion
    # then forces the user back through the first factor.
    challenge_ctx = await mfa_challenges.consume(payload.mfa_token)
    if challenge_ctx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired MFA challenge",
        )
    expected_challenge = await challenges.consume_authentication(payload.mfa_token)
    if expected_challenge is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no WebAuthn login in progress; begin first",
        )

    creds = await load_webauthn_credentials_for_login(
        user_id=challenge_ctx.user_id, tenant_id=challenge_ctx.tenant_id
    )
    matched = _match_credential(creds, payload.credential)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid MFA assertion",
        )

    try:
        verified = verify_authentication(
            _rp_config(settings),
            credential=payload.credential,
            expected_challenge=expected_challenge,
            public_key=matched.public_key,
            current_sign_count=matched.sign_count,
        )
    except WebauthnCeremonyError as exc:
        # Bad signature, wrong challenge/origin, OR a stale (replayed) counter.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid MFA assertion",
        ) from exc

    await bump_webauthn_sign_count(credential_pk=matched.id, new_sign_count=verified.new_sign_count)

    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=challenge_ctx.user_id,
        tenant_id=challenge_ctx.tenant_id,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=challenge_ctx.user_id,
        session_id=session_id,
        tenant_id=challenge_ctx.tenant_id,
        is_system_admin=challenge_ctx.is_system_admin,
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


# ----- WebAuthn helpers (task_08_10) -----
def _rp_config(settings: Settings) -> RpConfig:
    """Build the Relying Party identity for a ceremony from settings."""
    return RpConfig(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        origin=settings.webauthn_origin,
    )


def _new_webauthn_challenge() -> bytes:
    """A fresh, unpredictable WebAuthn challenge (the signed-over nonce)."""
    from webauthn.helpers import generate_challenge

    return bytes(generate_challenge())


def _credentials_response(creds: list[WebauthnCredential]) -> WebauthnCredentialsResponse:
    return WebauthnCredentialsResponse(
        credentials=[
            WebauthnCredentialSummary(id=str(c.id), label=c.label, sign_count=c.sign_count)
            for c in creds
        ]
    )


def _extract_transports(credential: dict[str, object]) -> list[str]:
    """Pull the authenticator's advertised transports out of the response.

    The browser puts them under ``response.transports``; absent for older
    authenticators, in which case we store an empty list.
    """
    response = credential.get("response")
    if not isinstance(response, dict):
        return []
    transports = response.get("transports")
    if isinstance(transports, list):
        return [str(t) for t in transports]
    return []


def _match_credential(
    creds: list[WebauthnCredential], credential: dict[str, object]
) -> WebauthnCredential | None:
    """Find the stored credential whose id matches the assertion's ``rawId``.

    The assertion carries the credential id as a base64url string in ``id``
    (and ``rawId``); we compare it byte-for-byte against the stored ids so
    only THIS user's registered authenticator can satisfy the challenge.
    """
    presented = credential.get("id") or credential.get("rawId")
    if not isinstance(presented, str):
        return None
    for c in creds:
        if bytes_to_base64url(c.credential_id) == presented:
            return c
    return None
