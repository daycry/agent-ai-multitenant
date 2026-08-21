"""TOTP-secret encryption at rest (Plan 08 task_08_09; ADR 0143).

CLAUDE.md principle: no plaintext secrets in the DB. The base32 TOTP seed is
stored ONLY as Fernet ciphertext (``user_mfa_totp.secret_encrypted``).

WHY THIS IS NO LONGER A THIN WRAPPER OVER THE OIDC HELPERS (prod-05
task_prod05_03 / ADR 0143). It used to call
:func:`api_server.auth.sso.secrets.encrypt_client_secret` directly, "one rotation
story, one place to wire Vault later". The audit priced that decision (gap2-4):
rotating ``API_SERVER_SSO_ENCRYPTION_KEY`` — a key whose blast radius the operator
believes to be "the stored OIDC client secrets" — also invalidates every TOTP
seed on the platform. With ``API_SERVER_ADMIN_REQUIRE_MFA=true`` (the default
outside dev) that locks EVERY System Admin out of ``/admin``, i.e. out of the
surface they would use to fix it. Two secrets with different blast radii and
different rotation cadences must be two keys.

So this module now resolves its OWN key ring, ``settings.mfa_encryption_key_ring``
(:mod:`api_server.auth.crypto_keys`), and the coupling is preserved only as the
FALLBACK: with ``API_SERVER_MFA_ENCRYPTION_KEY(S)`` unset, the ring IS the SSO
ring, so every existing deployment and every stored seed keeps working untouched.

Adopting a dedicated key on a live deployment is the ordinary three-phase
rotation, and skipping the middle step is what locks people out:

  1. ``API_SERVER_MFA_ENCRYPTION_KEYS=<new-mfa-key>,<current-sso-key>`` → deploy.
     New enrolments use the new key; existing seeds still decrypt with the old.
  2. ``python -m api_server.cli reencrypt-secrets --tables user_mfa_totp`` →
     every seed is re-encrypted onto the new key.
  3. Drop ``<current-sso-key>`` from the list → deploy. The SSO key can now be
     rotated on its own without touching MFA.

The break-glass for the lockout it protects against is in
``docs/06-runbooks/05-key-rotation.md``.
"""

from __future__ import annotations

from cryptography.fernet import InvalidToken, MultiFernet

from api_server.auth.crypto_keys import build_multifernet
from api_server.config import get_settings


class MfaSecretError(Exception):
    """Raised when the stored TOTP secret cannot be decrypted.

    Surfaced as a server error: a TOTP secret that fails to decrypt is an
    operator problem (e.g. the encryption key was rotated without
    re-encrypting), not a user one. We never echo the cause to the client.
    """


def _fernet() -> MultiFernet:
    """Build the cipher over the MFA key ring (head encrypts, all decrypt)."""
    return build_multifernet(get_settings().mfa_encryption_key_ring)


def encrypt_totp_secret(plaintext_secret: str) -> str:
    """Return Fernet ciphertext for the base32 TOTP seed.

    Used by the enrollment path so the DB only ever holds the encrypted
    seed — never the clear value. Encrypts with the HEAD key of the ring.
    """
    token: bytes = _fernet().encrypt(plaintext_secret.encode("utf-8"))
    return token.decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_totp_secret` — tries EVERY key in the ring.

    Raises:
        MfaSecretError: the ciphertext is corrupt, or no key in the ring can
            decrypt it (the key that produced it has been retired without a
            re-encryption run — see the module docstring).
    """
    try:
        plaintext: bytes = _fernet().decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise MfaSecretError("failed to decrypt the stored TOTP secret") from exc
    return plaintext.decode("utf-8")


__all__ = [
    "MfaSecretError",
    "decrypt_totp_secret",
    "encrypt_totp_secret",
]
