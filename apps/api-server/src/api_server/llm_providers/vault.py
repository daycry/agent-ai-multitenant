"""Vault store for an LLM provider's credential (Plan 11.2 task_11_2_02).

CLAUDE.md / ADR 0028 hard rule: **credentials go ONLY to Vault.** A
provider's credential — the Claude/Copilot OAuth token, the Azure APIM
API key, the optional Ollama bearer — is written to Vault at
``platform/llm/<provider_id>`` (under the platform KV v2 mount,
``secret``) and the DB persists ONLY the pointer ``secret_vault_path``.
The credential VALUE never lands in a DB column, is never logged, and is
never returned in any API response (write-only).

The MCP layer already ships a *read-only* resolver
(:class:`shared_mcp.HvacVaultResolver`); the provider admin surface also
needs to WRITE (and DELETE on provider removal) credentials. This module
adds that write/read/delete store as a thin, injectable seam:

  * :class:`LLMProviderVaultStore` — the Protocol the router depends on.
  * :class:`HvacLLMProviderVaultStore` — the production binding wrapping
    an ``hvac.Client`` (KV v2). ``hvac`` is lazy-imported (same optional
    -dep pattern as ``HvacVaultResolver`` / the Claude SDK), so the
    package keeps zero hvac cost when Vault is not wired.
  * :class:`InMemoryLLMProviderVaultStore` — the in-memory test double:
    models the KV write/read/delete with no real Vault, so the CRUD tests
    can assert "the secret lands in Vault and is ABSENT from the response
    and the DB row" without standing up hvac.

The store's keys are stable, well-known field names per provider kind
(``oauth_token`` / ``api_key`` / ``bearer_token``) so the runtime factory
(task_11_2_04) reads them back deterministically.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# The platform KV v2 mount (matches the installer's PLATFORM_KV_MOUNT and
# the per-service Vault policies in vault_bootstrap.py). All provider
# secrets live under this mount at ``platform/llm/<provider_id>``.
PLATFORM_KV_MOUNT = "secret"

# The logical KV path prefix for provider credentials. ``secret_vault_path``
# persisted in the DB is exactly ``platform/llm/<provider_id>`` — the path
# under the mount, NOT including the mount or the KV-v2 ``data`` segment.
PROVIDER_SECRET_PREFIX = "platform/llm"

# Well-known field names inside a provider's KV entry, keyed by kind. The
# router writes exactly the field its kind needs; the runtime factory reads
# the same field back. (claude_sdk / copilot carry an OAuth token; the
# minted Copilot JWT is task_11_2_03's concern.)
SECRET_FIELD_OAUTH_TOKEN = "oauth_token"
SECRET_FIELD_API_KEY = "api_key"
SECRET_FIELD_BEARER_TOKEN = "bearer_token"


def provider_secret_path(provider_id: object) -> str:
    """The logical Vault path for a provider's credential.

    Returns ``platform/llm/<provider_id>`` — the value persisted in the
    ``llm_providers.secret_vault_path`` column. It is a *pointer*, never a
    secret.
    """
    return f"{PROVIDER_SECRET_PREFIX}/{provider_id}"


class LLMProviderVaultError(Exception):
    """Raised when a provider credential cannot be written/read/deleted.

    The message is surfaced to the operator (mapped to a 5xx/typed error by
    the router) and MUST NOT carry the secret value — only the path + a
    generic cause. Never log the secret.
    """


@runtime_checkable
class LLMProviderVaultStore(Protocol):
    """Write/read/delete a provider's credential in Vault (KV v2).

    One small surface the router depends on. Methods are sync (``hvac`` is
    sync; a single short HTTP call is fine inside the async endpoint, same
    as the MCP resolver). ``secret`` is always a ``{field: value}`` dict so
    a kind can carry more than one credential field if needed.
    """

    def write_secret(self, path: str, secret: dict[str, str]) -> None:
        """Write (create/overwrite) the credential at the logical *path*.

        Raises :class:`LLMProviderVaultError` if Vault refuses.
        """
        ...

    def read_secret(self, path: str) -> dict[str, str]:
        """Read the credential fields back. Empty dict if the path is absent.

        Raises :class:`LLMProviderVaultError` on a Vault transport error
        (an *absent* path is NOT an error — it returns ``{}``).
        """
        ...

    def delete_secret(self, path: str) -> None:
        """Delete the credential (and its KV metadata). No-op if absent.

        Raises :class:`LLMProviderVaultError` on a Vault transport error.
        """
        ...


class HvacLLMProviderVaultStore:
    """Production store wrapping an ``hvac.Client`` (KV v2 only).

    ``hvac`` is imported lazily (constructor stores the client unwrapped,
    typed ``Any``) so the package keeps the zero-cost optional-Vault story.
    Paths are the logical ``platform/llm/<id>`` form; the hvac KV-v2 API
    takes ``mount_point`` + ``path`` separately, so the mount is held here
    and never embedded in the path.
    """

    def __init__(self, client: Any, *, mount_point: str = PLATFORM_KV_MOUNT) -> None:
        self._client = client
        self._mount = mount_point

    def write_secret(self, path: str, secret: dict[str, str]) -> None:
        try:
            self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=self._mount,
                path=path,
                secret=secret,
            )
        except Exception as exc:  # hvac raises a zoo of exception types
            raise LLMProviderVaultError(f"Vault write failed for {path!r}") from exc

    def read_secret(self, path: str) -> dict[str, str]:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                mount_point=self._mount, path=path
            )
        except Exception as exc:  # — translate hvac's exception zoo
            # An absent path raises InvalidPath in hvac; treat "no secret"
            # as an empty dict rather than an error so callers can detect a
            # missing credential without catching hvac types here.
            if _is_invalid_path(exc):
                return {}
            raise LLMProviderVaultError(f"Vault read failed for {path!r}") from exc
        try:
            data = resp["data"]["data"]
        except (KeyError, TypeError) as exc:
            raise LLMProviderVaultError(
                f"Vault response for {path!r} has no `data.data` field"
            ) from exc
        if not isinstance(data, dict):
            raise LLMProviderVaultError(f"Vault secret at {path!r} is not a key/value object")
        return {str(k): str(v) for k, v in data.items()}

    def delete_secret(self, path: str) -> None:
        try:
            # Delete every version + the metadata so a rotated/removed
            # provider leaves nothing behind.
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                mount_point=self._mount, path=path
            )
        except Exception as exc:  # — translate hvac's exception zoo
            if _is_invalid_path(exc):
                return  # already gone — idempotent delete
            raise LLMProviderVaultError(f"Vault delete failed for {path!r}") from exc


def _is_invalid_path(exc: Exception) -> bool:
    """Best-effort detection of hvac's "path not found" without importing hvac.

    hvac raises ``hvac.exceptions.InvalidPath`` (a subclass) for an absent
    KV path. We match by class name so this module never imports hvac at
    runtime (keeping the optional-dep story) while still distinguishing
    "missing" from a real transport failure.
    """
    return type(exc).__name__ == "InvalidPath" or "InvalidPath" in {
        c.__name__ for c in type(exc).__mro__
    }


class InMemoryLLMProviderVaultStore:
    """In-memory KV store — the test/dev double (no real Vault).

    Maps the logical ``platform/llm/<id>`` path to its ``{field: value}``
    dict, modelling create/overwrite, read (empty dict when absent) and an
    idempotent delete. The CRUD tests inject this via a dependency override
    and assert the secret landed here AND is absent from the API response
    and the DB row.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def write_secret(self, path: str, secret: dict[str, str]) -> None:
        self._store[path] = dict(secret)

    def read_secret(self, path: str) -> dict[str, str]:
        return dict(self._store.get(path, {}))

    def delete_secret(self, path: str) -> None:
        self._store.pop(path, None)

    # -- test helpers -------------------------------------------------------
    def has_secret(self, path: str) -> bool:
        """True iff a credential is stored at *path* (used by tests)."""
        return bool(self._store.get(path))


__all__ = [
    "HvacLLMProviderVaultStore",
    "InMemoryLLMProviderVaultStore",
    "LLMProviderVaultError",
    "LLMProviderVaultStore",
    "PLATFORM_KV_MOUNT",
    "PROVIDER_SECRET_PREFIX",
    "SECRET_FIELD_API_KEY",
    "SECRET_FIELD_BEARER_TOKEN",
    "SECRET_FIELD_OAUTH_TOKEN",
    "provider_secret_path",
]
