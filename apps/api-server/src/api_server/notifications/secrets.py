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

import base64
import hashlib

from cryptography.fernet import Fernet

from api_server.config import get_settings


def _fernet() -> Fernet:
    """Build the Fernet cipher from the configured notification key."""
    raw = get_settings().notification_encryption_key.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


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
