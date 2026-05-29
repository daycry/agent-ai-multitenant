"""OIDC client-secret resolution — Vault first, Fernet-at-rest fallback.

CLAUDE.md principle: no plaintext secrets in the DB. The
``sso_configurations`` row stores the OIDC client secret in exactly one
of two forms:

  * ``client_secret_ref``       — a Vault pointer (``vault:<mount>/data/...``)
                                  resolved through the same VaultResolver
                                  the MCP layer already uses.
  * ``client_secret_encrypted`` — Fernet ciphertext, encrypted at rest
                                  with a key derived from the
                                  ``API_SERVER_SSO_ENCRYPTION_KEY`` setting.

This module turns either form into the plaintext secret in memory at
token-exchange time, and provides the encrypt helper the config-write
path (task_08_03) uses so a secret never reaches the DB in clear text.

The Fernet key is derived from the configured secret via SHA-256 →
urlsafe-base64, so any non-empty configuration string is a valid key
(Fernet itself requires an exactly-32-byte urlsafe-base64 value).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from api_server.config import get_settings

# The key inside a Vault KV entry that holds the OIDC client secret.
# Mirrors how the MCP layer keys its secrets (one well-known field).
VAULT_SECRET_FIELD = "client_secret"


class SSOSecretError(Exception):
    """Raised when the OIDC client secret cannot be resolved/decrypted.

    Surfaced to the caller as a 500-equivalent server error: a
    misconfigured secret is an operator problem, not a user one, and we
    never echo the underlying cause to the client.
    """


def _fernet() -> Fernet:
    """Build the Fernet cipher from the configured SSO encryption key."""
    raw = get_settings().sso_encryption_key.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_client_secret(plaintext: str) -> str:
    """Return Fernet ciphertext (str) for `plaintext`.

    Used by the config-write path so the DB only ever holds an
    encrypted ``client_secret_encrypted`` — never the clear value.
    """
    token: bytes = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_client_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_client_secret`.

    Raises:
        SSOSecretError: the ciphertext is corrupt or was produced with a
            different key (e.g. the encryption key was rotated without
            re-encrypting the stored secrets).
    """
    try:
        plaintext: bytes = _fernet().decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise SSOSecretError("failed to decrypt the stored OIDC client secret") from exc
    return plaintext.decode("utf-8")


def resolve_client_secret(
    *,
    client_secret_ref: str | None,
    client_secret_encrypted: str | None,
    vault_resolver: object | None,
) -> str:
    """Return the plaintext OIDC client secret.

    Resolution order:

      1. ``client_secret_ref`` set  → resolve via the VaultResolver.
      2. ``client_secret_encrypted`` set → Fernet-decrypt in place.
      3. neither set → :class:`SSOSecretError` (a confidential OIDC
         client with no secret cannot do the code exchange).

    The ``vault_resolver`` is typed ``object`` to avoid importing the
    shared-mcp protocol here; it just needs a ``resolve(ref) -> dict``
    method (duck-typed, matches ``shared_mcp.VaultResolver``).

    Raises:
        SSOSecretError: no secret configured, Vault unwired/failed, or
            the Vault entry lacks the well-known field.
    """
    if client_secret_ref:
        if vault_resolver is None:
            raise SSOSecretError(
                "OIDC config references a Vault secret but no VaultResolver "
                "is configured (set API_SERVER_VAULT_TOKEN)"
            )
        resolve = getattr(vault_resolver, "resolve", None)
        if resolve is None:  # pragma: no cover - defensive
            raise SSOSecretError("supplied vault_resolver has no resolve() method")
        try:
            entry = resolve(client_secret_ref)
        except Exception as exc:  # Vault libraries raise a zoo of types.
            raise SSOSecretError(f"Vault resolution failed for {client_secret_ref!r}") from exc
        secret = entry.get(VAULT_SECRET_FIELD) if isinstance(entry, dict) else None
        if not secret:
            raise SSOSecretError(
                f"Vault entry {client_secret_ref!r} has no {VAULT_SECRET_FIELD!r} field"
            )
        return str(secret)

    if client_secret_encrypted:
        return decrypt_client_secret(client_secret_encrypted)

    raise SSOSecretError("OIDC configuration has no client secret configured")


__all__ = [
    "SSOSecretError",
    "VAULT_SECRET_FIELD",
    "decrypt_client_secret",
    "encrypt_client_secret",
    "resolve_client_secret",
]
