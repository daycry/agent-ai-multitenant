"""Pydantic schemas for the MFA endpoints (Plan 08 task_08_09 + task_08_10)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# TOTP codes are 6 digits; recovery codes are 10 hex chars. Bound the
# inputs so an oversized body is rejected cheaply before any crypto.
_CODE_MIN_LEN = 1
_CODE_MAX_LEN = 32


class MfaEnrollResponse(BaseModel):
    """Result of starting TOTP enrollment.

    Carries the freshly generated secret + the ``otpauth://`` provisioning
    URI (the UI renders it as a QR code) and the one-time recovery codes.
    The recovery codes are shown EXACTLY ONCE here — only their hashes are
    persisted. Enrollment is not active until confirmed with a valid code.
    """

    secret: str = Field(description="Base32 TOTP seed (also encoded in the URI).")
    provisioning_uri: str = Field(description="otpauth:// URI for the authenticator app / QR.")
    recovery_codes: list[str] = Field(description="One-time recovery codes, shown only here.")


class MfaConfirmRequest(BaseModel):
    """Confirm enrollment by proving possession with a current TOTP code."""

    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(min_length=_CODE_MIN_LEN, max_length=_CODE_MAX_LEN)


class MfaStatusResponse(BaseModel):
    """Whether the caller has a confirmed TOTP factor in the active tenant."""

    enrolled: bool = Field(description="A TOTP enrollment row exists.")
    confirmed: bool = Field(description="The enrollment has been confirmed and gates login.")
    recovery_codes_remaining: int = Field(description="Unused one-time recovery codes left.")


class MfaTotpVerifyRequest(BaseModel):
    """Complete a login awaiting MFA: challenge token + a TOTP/recovery code.

    The ``code`` accepts BOTH a current 6-digit TOTP and a one-time
    recovery code (the verify endpoint tries TOTP first, then recovery).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    mfa_token: str = Field(min_length=1, description="The interim challenge token from login.")
    code: str = Field(min_length=_CODE_MIN_LEN, max_length=_CODE_MAX_LEN)


class MfaRequiredResponse(BaseModel):
    """Interim login result when the user has a confirmed TOTP factor.

    Returned by the password/SSO step INSTEAD of a session: it carries no
    access token, only the single-use challenge token to present to
    ``/auth/mfa/totp/verify`` together with a code.
    """

    status: str = Field(default="mfa_required", description="Always 'mfa_required'.")
    mfa_token: str = Field(description="Single-use, short-lived interim challenge token.")
    mfa_methods: list[str] = Field(
        default_factory=lambda: ["totp"],
        description="Second-factor methods the user can use to complete login.",
    )


# ---------------------------------------------------------------------------
# WebAuthn (Plan 08 task_08_10)
# ---------------------------------------------------------------------------
class WebauthnRegisterBeginResponse(BaseModel):
    """The registration options the browser passes to ``navigator.credentials.create``.

    ``options`` is the py_webauthn-generated PublicKeyCredentialCreationOptions
    serialized as JSON (challenge + RP + user + exclude list). The challenge
    inside is single-use and also stashed server-side for the verify call.
    """

    options: dict[str, Any] = Field(description="WebAuthn creation options for the browser.")


class WebauthnRegisterFinishRequest(BaseModel):
    """The attestation the browser returns from a registration ceremony."""

    model_config = ConfigDict(str_strip_whitespace=False)

    credential: dict[str, Any] = Field(
        description="The PublicKeyCredential JSON from navigator.credentials.create."
    )
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional human label for the authenticator ('YubiKey 5').",
    )


class WebauthnCredentialSummary(BaseModel):
    """A registered WebAuthn credential, for the management list."""

    id: str = Field(description="The stored row id (for delete).")
    label: str | None = Field(default=None)
    sign_count: int = Field(description="Last accepted signature counter.")


class WebauthnCredentialsResponse(BaseModel):
    """The caller's registered WebAuthn credentials in the active tenant."""

    credentials: list[WebauthnCredentialSummary] = Field(default_factory=list)


class WebauthnLoginBeginRequest(BaseModel):
    """Start the WebAuthn login second factor from an interim challenge token."""

    model_config = ConfigDict(str_strip_whitespace=True)

    mfa_token: str = Field(min_length=1, description="The interim challenge token from login.")


class WebauthnLoginBeginResponse(BaseModel):
    """The authentication options the browser passes to ``navigator.credentials.get``."""

    options: dict[str, Any] = Field(description="WebAuthn request options for the browser.")


class WebauthnLoginFinishRequest(BaseModel):
    """Complete the WebAuthn login second factor: challenge token + assertion."""

    model_config = ConfigDict(str_strip_whitespace=False)

    mfa_token: str = Field(description="The interim challenge token from login.")
    credential: dict[str, Any] = Field(
        description="The PublicKeyCredential JSON from navigator.credentials.get."
    )


__all__ = [
    "MfaConfirmRequest",
    "MfaEnrollResponse",
    "MfaRequiredResponse",
    "MfaStatusResponse",
    "MfaTotpVerifyRequest",
    "WebauthnCredentialSummary",
    "WebauthnCredentialsResponse",
    "WebauthnLoginBeginRequest",
    "WebauthnLoginBeginResponse",
    "WebauthnLoginFinishRequest",
    "WebauthnRegisterBeginResponse",
    "WebauthnRegisterFinishRequest",
]
