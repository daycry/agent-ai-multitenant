"""Notification channel-secret encryption (write path) — Plan 10 task_10_15.

CLAUDE.md principle: NO plaintext secrets in the DB. When a Tenant Admin
configures a notification channel through the config UI / API, the channel's
secret (bot token, SMTP password, webhook signing key, Twilio auth token, …)
must reach the ``notification_channels`` row in exactly one never-plaintext
form (a CHECK constraint in migration 0045 enforces "at most one"):

  * ``secret_ref``       — a Vault pointer (``vault:<mount>/data/...``).
  * ``secret_encrypted`` — Fernet ciphertext, encrypted at rest with a key
                           derived from ``API_SERVER_NOTIFICATION_ENCRYPTION_KEY``.

This module is the WRITE side: it turns a plaintext secret into Fernet
ciphertext for storage. The dispatcher's ``notification_dispatcher.secrets``
is the READ side that decrypts it at send time. Both derive the SAME Fernet
key from the SAME raw configuration string (SHA-256 → urlsafe-base64), so
``API_SERVER_NOTIFICATION_ENCRYPTION_KEY`` MUST equal the dispatcher's
``NOTIFY_NOTIFICATION_ENCRYPTION_KEY``. Mirrors ``auth.sso.secrets``.

The plaintext only ever lives in memory during the encrypt call; it is never
logged and never echoed back to the client (the API returns only
``has_secret`` + ``secret_source``).
"""

from __future__ import annotations

from cryptography.fernet import MultiFernet

from api_server.auth.crypto_keys import build_multifernet
from api_server.config import get_settings


def _fernet() -> MultiFernet:
    """Build the cipher over the notification key RING (prod-05 task_prod05_01).

    The head key encrypts, every key decrypts. The PAIR CONTRACT with the
    dispatcher is unchanged and now applies to the whole ring:
    ``API_SERVER_NOTIFICATION_ENCRYPTION_KEYS`` must equal
    ``NOTIFY_NOTIFICATION_ENCRYPTION_KEYS``, because the dispatcher is the READ
    side of the ciphertext this module writes. Deploying only one of the two
    services during a rotation is what breaks the pair — the runbook orders both
    in the same window, and ``tests/unit/test_multifernet_builders.py`` pins the
    two parsers against each other so they cannot drift silently.
    """
    return build_multifernet(get_settings().notification_encryption_key_ring)


def encrypt_channel_secret(plaintext: str) -> str:
    """Return Fernet ciphertext (str) for ``plaintext``.

    The channel-config write path uses this so the DB only ever holds an
    encrypted ``secret_encrypted`` — never the clear value. The ciphertext
    is decryptable by the dispatcher's ``decrypt_secret`` because both
    services derive the same key.
    """
    token: bytes = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


__all__ = ["encrypt_channel_secret"]
