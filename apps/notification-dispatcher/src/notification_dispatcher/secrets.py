"""Channel-secret resolution — Vault first, Fernet-at-rest fallback.

CLAUDE.md principle: NO plaintext secrets in the DB. A
``notification_channels`` row stores its secret (bot token, SMTP
password, webhook signing key, Twilio auth token, …) in exactly one of
two never-plaintext forms (a CHECK constraint in the migration enforces
"at most one"):

  * ``secret_ref``       — a Vault pointer (``vault:<mount>/data/...``).
  * ``secret_encrypted`` — Fernet ciphertext, encrypted at rest with a key
                           derived from ``NOTIFY_NOTIFICATION_ENCRYPTION_KEY``.

This module turns either form into the plaintext secret IN MEMORY at send
time — the plaintext never touches the DB and is never logged. Mirrors the
SSO precedent (``api_server.auth.sso.secrets``): the Fernet key is derived
from the configured secret via SHA-256 → urlsafe-base64, so any non-empty
configuration string is a valid key.

Vault resolution is intentionally left as a hook here: the dispatcher
service does not yet bundle the shared-mcp VaultResolver, so a
``secret_ref`` raises a clear error pointing at the Fase B wiring. The
Fernet path is fully functional today so the encrypted-at-rest default
works without Vault.
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import InvalidToken, MultiFernet

from notification_dispatcher.config import Settings
from notification_dispatcher.crypto_keys import build_multifernet


class ChannelSecretError(Exception):
    """Raised when a channel secret cannot be resolved/decrypted.

    A misconfigured secret is an operator problem, not a user one, and we
    never echo the underlying cause back over the wire.
    """


def _fernet(settings: Settings) -> MultiFernet:
    """Build the cipher over the notification key RING (prod-05 task_prod05_01).

    Head key encrypts, EVERY key decrypts — and on this READ side the "every key
    decrypts" half is the whole point: during a rotation the api-server may still
    hold rows encrypted with the previous key, and a send that fails with
    ``InvalidToken`` is a notification silently lost.
    """
    return build_multifernet(settings.notification_encryption_key_ring)


def encrypt_secret(plaintext: str, settings: Settings) -> str:
    """Return Fernet ciphertext (str) for ``plaintext``.

    The channel-write path (Fase B/C UI / API) uses this so the DB only
    ever holds an encrypted ``secret_encrypted`` — never the clear value.
    """
    token: bytes = _fernet(settings).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(ciphertext: str, settings: Settings) -> str:
    """Reverse of :func:`encrypt_secret`.

    Raises:
        ChannelSecretError: the ciphertext is corrupt or was produced with
            a different key (e.g. the encryption key was rotated without
            re-encrypting the stored secrets).
    """
    try:
        plaintext: bytes = _fernet(settings).decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise ChannelSecretError("failed to decrypt the stored channel secret") from exc
    return plaintext.decode("utf-8")


def resolve_channel_secret(channel: Any, settings: Settings) -> str | None:
    """Return the plaintext channel secret, or ``None`` for a secretless
    channel (e.g. ``in_app``).

    Resolution order:

      1. ``secret_ref`` set       → Vault (not yet wired in the dispatcher
         service — raises a clear :class:`ChannelSecretError` pointing at
         the Fase B wiring rather than silently sending unauthenticated).
      2. ``secret_encrypted`` set → Fernet-decrypt in place.
      3. neither set              → ``None`` (secretless channel).

    The ``channel`` is duck-typed (the ORM ``NotificationChannel``); it
    just needs ``secret_ref`` / ``secret_encrypted`` attributes.
    """
    secret_ref = getattr(channel, "secret_ref", None)
    secret_encrypted = getattr(channel, "secret_encrypted", None)

    if secret_ref:
        raise ChannelSecretError(
            "channel references a Vault secret (secret_ref) but the dispatcher "
            "has no VaultResolver wired yet — that lands with the Fase B "
            "channel adapters. Use secret_encrypted (Fernet at rest) for now."
        )
    if secret_encrypted:
        return decrypt_secret(secret_encrypted, settings)
    return None


__all__ = [
    "ChannelSecretError",
    "decrypt_secret",
    "encrypt_secret",
    "resolve_channel_secret",
]
