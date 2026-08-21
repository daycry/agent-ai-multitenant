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

import secrets

from cryptography.fernet import InvalidToken, MultiFernet

from api_server.auth.crypto_keys import build_multifernet
from api_server.config import get_settings

# Bytes of CSPRNG entropy in a freshly minted incoming-webhook signing secret,
# urlsafe-base64 encoded (~43 chars). The operator copies this clear value into
# the external provider (GitHub/Jira/...) so it stamps the matching HMAC; we
# store only the Fernet ciphertext. 32 bytes is well beyond brute-force reach.
SIGNING_SECRET_BYTES = 32


class IncomingWebhookSecretError(Exception):
    """Raised when an incoming-webhook signing secret cannot be decrypted.

    Surfaced as a server error (the stored ciphertext is corrupt or was
    produced with a different key) — never echoes the underlying cause to a
    caller, and never the secret.
    """


def _fernet() -> MultiFernet:
    """Build the cipher over the webhook key RING (prod-05 task_prod05_01).

    Head key encrypts, every key decrypts — so rotating
    ``API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY`` no longer means every project's
    inbound integration starts rejecting signatures. Note the asymmetry with the
    other families: the signing secret itself is shown to the operator ONCE and
    pasted into GitHub/Jira, so a lost ciphertext cannot be recovered by asking
    the provider — the ring is the only safety net there is.
    """
    return build_multifernet(get_settings().incoming_webhook_encryption_key_ring)


def generate_signing_secret() -> str:
    """Mint a fresh, high-entropy incoming-webhook signing secret (clear).

    The clear value is shown to the operator EXACTLY ONCE (on create / rotate)
    so they can paste it into the external provider's webhook config; only its
    Fernet ciphertext (:func:`encrypt_signing_secret`) ever reaches the DB, so
    the secret can never be retrieved again. A rotate mints a new one and
    re-encrypts, invalidating the previous value.
    """
    return secrets.token_urlsafe(SIGNING_SECRET_BYTES)


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
    "SIGNING_SECRET_BYTES",
    "IncomingWebhookSecretError",
    "decrypt_signing_secret",
    "encrypt_signing_secret",
    "generate_signing_secret",
]
