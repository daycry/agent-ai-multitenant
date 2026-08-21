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

ROTATION (prod-05 task_prod05_01). The cipher is a ``MultiFernet`` over the
``API_SERVER_SSO_ENCRYPTION_KEY(S)`` ring: the head key encrypts, every key
decrypts. Rotation is therefore three steps — add the new key at the head and
deploy, run ``python -m api_server.cli reencrypt-secrets``, drop the old key —
with no window where a stored secret is unreadable. See
:mod:`api_server.auth.crypto_keys` and
``docs/06-runbooks/05-key-rotation.md``.
"""

from __future__ import annotations

from cryptography.fernet import InvalidToken, MultiFernet

from api_server.auth.crypto_keys import build_multifernet
from api_server.config import get_settings

# The key inside a Vault KV entry that holds the OIDC client secret.
# Mirrors how the MCP layer keys its secrets (one well-known field).
VAULT_SECRET_FIELD = "client_secret"

# The key inside a Vault KV entry that holds the SAML SP private key
# (task_08_05). A distinct well-known field so an OIDC secret and a SAML
# SP key can live in the same Vault mount without colliding.
VAULT_SP_PRIVATE_KEY_FIELD = "sp_private_key"


class SSOSecretError(Exception):
    """Raised when the OIDC client secret cannot be resolved/decrypted.

    Surfaced to the caller as a 500-equivalent server error: a
    misconfigured secret is an operator problem, not a user one, and we
    never echo the underlying cause to the client.
    """


def _fernet() -> MultiFernet:
    """Build the cipher over the configured SSO key RING (prod-05 task_prod05_01).

    A :class:`MultiFernet`, not a single :class:`~cryptography.fernet.Fernet`:
    the ring's FIRST key encrypts and EVERY key decrypts, which is what makes
    rotating ``API_SERVER_SSO_ENCRYPTION_KEY`` an operation instead of a data
    loss. With one key configured (the default) the behaviour and the key
    material are byte-for-byte what they were before, so no stored ciphertext
    changes meaning.
    """
    return build_multifernet(get_settings().sso_encryption_key_ring)


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


def resolve_sp_private_key(
    *,
    sp_private_key_ref: str | None,
    sp_private_key_encrypted: str | None,
    vault_resolver: object | None,
) -> str | None:
    """Return the plaintext SAML SP private key (PEM), or ``None``.

    The SP private key is OPTIONAL: a tenant that does not sign its
    AuthnRequest and does not enable assertion/NameID encryption needs no
    key at all. So unlike :func:`resolve_client_secret`, "neither source
    set" is a valid state that returns ``None`` (the caller decides
    whether the absence is actually an error for the enabled features).

    Resolution order, identical otherwise to the OIDC secret:

      1. ``sp_private_key_ref`` set      → resolve via the VaultResolver.
      2. ``sp_private_key_encrypted`` set → Fernet-decrypt in place.
      3. neither set                     → ``None``.

    Raises:
        SSOSecretError: a Vault ref is set but Vault is unwired / failed /
            the entry lacks the well-known field, or the ciphertext is
            corrupt.
    """
    if sp_private_key_ref:
        if vault_resolver is None:
            raise SSOSecretError(
                "SAML config references a Vault SP private key but no "
                "VaultResolver is configured (set API_SERVER_VAULT_TOKEN)"
            )
        resolve = getattr(vault_resolver, "resolve", None)
        if resolve is None:  # pragma: no cover - defensive
            raise SSOSecretError("supplied vault_resolver has no resolve() method")
        try:
            entry = resolve(sp_private_key_ref)
        except Exception as exc:  # Vault libraries raise a zoo of types.
            raise SSOSecretError(f"Vault resolution failed for {sp_private_key_ref!r}") from exc
        key = entry.get(VAULT_SP_PRIVATE_KEY_FIELD) if isinstance(entry, dict) else None
        if not key:
            raise SSOSecretError(
                f"Vault entry {sp_private_key_ref!r} has no {VAULT_SP_PRIVATE_KEY_FIELD!r} field"
            )
        return str(key)

    if sp_private_key_encrypted:
        return decrypt_client_secret(sp_private_key_encrypted)

    return None


__all__ = [
    "VAULT_SECRET_FIELD",
    "VAULT_SP_PRIVATE_KEY_FIELD",
    "SSOSecretError",
    "decrypt_client_secret",
    "encrypt_client_secret",
    "resolve_client_secret",
    "resolve_sp_private_key",
]
