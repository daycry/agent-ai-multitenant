"""Pydantic schemas for the MFA TOTP endpoints (Plan 08 task_08_09)."""

from __future__ import annotations

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


__all__ = [
    "MfaConfirmRequest",
    "MfaEnrollResponse",
    "MfaRequiredResponse",
    "MfaStatusResponse",
    "MfaTotpVerifyRequest",
]
