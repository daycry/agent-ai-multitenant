"""WebAuthn / FIDO2 server ceremony primitives (Plan 08 task_08_10).

Thin, intention-revealing wrappers around :mod:`webauthn` (py_webauthn).
They never touch the DB or Redis — they only build the options the browser
passes to ``navigator.credentials`` and verify the attestation / assertion
the authenticator returns — so they are fully testable offline with crafted
fixtures (no real authenticator / IdP needed).

WebAuthn is the SECOND alternative second factor, sitting next to TOTP in
the SAME opt-in MFA challenge flow:

  * **registration** (interactive, the user is logged in): the server emits
    ``generate_registration_options`` (carrying a random challenge), the
    browser signs it, and :func:`verify_registration` validates the
    attestation and returns the credential to persist — the PUBLIC key, the
    credential id, and the initial signature counter. The private key NEVER
    leaves the authenticator, so nothing secret is stored.
  * **authentication** (the login second factor): the server emits
    ``generate_authentication_options`` for the user's registered
    credentials, the authenticator signs the challenge, and
    :func:`verify_authentication` checks the signature against the stored
    public key AND that the new signature counter is strictly greater than
    the stored one. A stale counter (a cloned / replayed assertion) is
    rejected by py_webauthn — the core anti-replay control.

The Relying Party id / name / origin come from settings (see
:class:`api_server.config.Settings`): the RP id must be a registrable
suffix of the origin host and stay stable, or authenticators refuse to sign.
"""

from __future__ import annotations

from dataclasses import dataclass

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

# How long the browser ceremony may take before the options time out. The
# server-side single-use challenge in Redis has its own (tighter) TTL; this
# is the hint the authenticator UI uses.
CEREMONY_TIMEOUT_MS = 60_000

# A passkey is "preferred" not "required": we accept both roaming security
# keys (YubiKey, counter-backed) and platform authenticators. User
# verification is preferred so a PIN/biometric is used when available
# without hard-failing simpler keys.
_USER_VERIFICATION = UserVerificationRequirement.PREFERRED
_RESIDENT_KEY = ResidentKeyRequirement.PREFERRED


class WebauthnCeremonyError(Exception):
    """A WebAuthn attestation/assertion failed verification.

    Surfaced by the endpoints as a 400: the client sent something the RP
    cannot accept (wrong challenge, bad signature, stale counter, ...). The
    cause is never echoed to the client verbatim.
    """


@dataclass(frozen=True)
class RpConfig:
    """The Relying Party identity for a ceremony, sourced from settings."""

    rp_id: str
    rp_name: str
    origin: str


@dataclass(frozen=True)
class VerifiedRegistrationResult:
    """What :func:`verify_registration` hands back for persistence.

    Carries only public material — the credential id, the COSE public key
    and the initial signature counter. There is no private key here (it
    never leaves the authenticator).
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int


@dataclass(frozen=True)
class VerifiedAuthenticationResult:
    """What :func:`verify_authentication` hands back on a valid assertion."""

    credential_id: bytes
    new_sign_count: int


def registration_options_json(
    rp: RpConfig,
    *,
    user_id: bytes,
    user_name: str,
    user_display_name: str,
    challenge: bytes,
    exclude_credential_ids: list[bytes],
) -> str:
    """Build the registration options the browser passes to ``create()``.

    ``exclude_credential_ids`` are the user's already-registered credentials
    so the same authenticator cannot be enrolled twice. The returned JSON is
    sent verbatim to the frontend; the ``challenge`` is also stashed
    server-side (single-use) for :func:`verify_registration`.
    """
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=rp.rp_name,
        user_id=user_id,
        user_name=user_name,
        user_display_name=user_display_name,
        challenge=challenge,
        timeout=CEREMONY_TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=_RESIDENT_KEY,
            user_verification=_USER_VERIFICATION,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in exclude_credential_ids
        ],
    )
    return str(options_to_json(options))


def verify_registration(
    rp: RpConfig,
    *,
    credential: str | dict[str, object],
    expected_challenge: bytes,
) -> VerifiedRegistrationResult:
    """Validate an attestation, returning the credential to persist.

    Raises:
        WebauthnCeremonyError: the attestation does not verify (wrong
            challenge, wrong origin/RP, malformed credential, ...).
    """
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_presence=True,
            require_user_verification=False,
        )
    except (WebAuthnException, ValueError, KeyError) as exc:
        raise WebauthnCeremonyError("WebAuthn registration did not verify") from exc
    return VerifiedRegistrationResult(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
    )


def authentication_options_json(
    rp: RpConfig,
    *,
    challenge: bytes,
    allow_credential_ids: list[bytes],
) -> str:
    """Build the authentication options the browser passes to ``get()``.

    ``allow_credential_ids`` scopes the prompt to the user's registered
    authenticators. The ``challenge`` is stashed server-side (single-use)
    and checked by :func:`verify_authentication`.
    """
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        challenge=challenge,
        timeout=CEREMONY_TIMEOUT_MS,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in allow_credential_ids],
        user_verification=_USER_VERIFICATION,
    )
    return str(options_to_json(options))


def verify_authentication(
    rp: RpConfig,
    *,
    credential: str | dict[str, object],
    expected_challenge: bytes,
    public_key: bytes,
    current_sign_count: int,
) -> VerifiedAuthenticationResult:
    """Validate an assertion against a stored credential.

    py_webauthn checks the signature against ``public_key`` AND that the new
    signature counter is strictly greater than ``current_sign_count``
    (unless the authenticator does not implement counters, i.e. both stay
    0). A stale counter — a cloned / replayed assertion — therefore raises.

    Raises:
        WebauthnCeremonyError: the assertion does not verify (bad signature,
            wrong challenge/origin/RP, stale counter, ...).
    """
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            credential_public_key=public_key,
            credential_current_sign_count=current_sign_count,
            require_user_verification=False,
        )
    except (WebAuthnException, ValueError, KeyError) as exc:
        raise WebauthnCeremonyError("WebAuthn authentication did not verify") from exc
    return VerifiedAuthenticationResult(
        credential_id=verified.credential_id,
        new_sign_count=verified.new_sign_count,
    )


__all__ = [
    "CEREMONY_TIMEOUT_MS",
    "RpConfig",
    "VerifiedAuthenticationResult",
    "VerifiedRegistrationResult",
    "WebauthnCeremonyError",
    "authentication_options_json",
    "registration_options_json",
    "verify_authentication",
    "verify_registration",
]
