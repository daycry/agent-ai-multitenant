"""Incoming-webhook signing-secret handling at rest (Plan 13 task_13_08).

CLAUDE.md principle: no plaintext secrets in the DB, never echoed/logged. A
per-project incoming-webhook config stores its HMAC signing secret as Fernet
CIPHERTEXT (``signing_secret_encrypted``), encrypted at rest with a key derived
from ``API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY``. This module turns the
plaintext into ciphertext on the config-write path and back to plaintext IN
MEMORY when a received event's signature is verified (task_13_08) — the clear
secret never reaches the DB and never leaves this process.

Mirrors :mod:`api_server.auth.sso.secrets` (OIDC client secret) and the
notification channel-secret pattern: the Fernet key is derived from the
configured string via SHA-256 → urlsafe-base64, so any non-empty configuration
string is a valid key (Fernet itself requires an exactly-32-byte
urlsafe-base64 value).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from api_server.config import get_settings


class IncomingWebhookSecretError(Exception):
    """Raised when an incoming-webhook signing secret cannot be decrypted.

    Surfaced as a server error (the stored ciphertext is corrupt or was
    produced with a different key) — never echoes the underlying cause to a
    caller, and never the secret.
    """


def _fernet() -> Fernet:
    """Build the Fernet cipher from the configured webhook encryption key."""
    raw = get_settings().incoming_webhook_encryption_key.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_signing_secret(plaintext: str) -> str:
    """Return Fernet ciphertext (str) for a webhook signing secret.

    Used by the config-write path so the DB only ever holds
    ``signing_secret_encrypted`` — never the clear value.
    """
    token: bytes = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_signing_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_signing_secret` (in memory, never logged).

    Raises:
        IncomingWebhookSecretError: the ciphertext is corrupt or was produced
            with a different key (e.g. the encryption key was rotated without
            re-encrypting the stored secrets).
    """
    try:
        plaintext: bytes = _fernet().decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise IncomingWebhookSecretError(
            "failed to decrypt the stored incoming-webhook signing secret"
        ) from exc
    return plaintext.decode("utf-8")


__all__ = [
    "IncomingWebhookSecretError",
    "decrypt_signing_secret",
    "encrypt_signing_secret",
]
