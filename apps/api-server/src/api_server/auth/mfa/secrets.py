"""TOTP-secret encryption at rest (Plan 08 task_08_09).

CLAUDE.md principle: no plaintext secrets in the DB. The base32 TOTP seed
is stored ONLY as Fernet ciphertext (``user_mfa_totp.secret_encrypted``),
using the SAME mechanism the OIDC client secret already uses — the Fernet
key derived from ``API_SERVER_SSO_ENCRYPTION_KEY``
(:mod:`api_server.auth.sso.secrets`). We deliberately reuse that one key
rather than introduce a second secret-at-rest scheme: one rotation story,
one place to wire Vault later.

This is a thin, intention-revealing wrapper so the MFA code reads
``encrypt_totp_secret`` / ``decrypt_totp_secret`` instead of borrowing the
OIDC-named helpers directly.
"""

from __future__ import annotations

from api_server.auth.sso.secrets import (
    SSOSecretError,
    decrypt_client_secret,
    encrypt_client_secret,
)


class MfaSecretError(Exception):
    """Raised when the stored TOTP secret cannot be decrypted.

    Surfaced as a server error: a TOTP secret that fails to decrypt is an
    operator problem (e.g. the encryption key was rotated without
    re-encrypting), not a user one. We never echo the cause to the client.
    """


def encrypt_totp_secret(plaintext_secret: str) -> str:
    """Return Fernet ciphertext for the base32 TOTP seed.

    Used by the enrollment path so the DB only ever holds the encrypted
    seed — never the clear value.
    """
    return encrypt_client_secret(plaintext_secret)


def decrypt_totp_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_totp_secret`.

    Raises:
        MfaSecretError: the ciphertext is corrupt or was produced with a
            different key.
    """
    try:
        return decrypt_client_secret(ciphertext)
    except SSOSecretError as exc:
        raise MfaSecretError("failed to decrypt the stored TOTP secret") from exc


__all__ = [
    "MfaSecretError",
    "decrypt_totp_secret",
    "encrypt_totp_secret",
]
