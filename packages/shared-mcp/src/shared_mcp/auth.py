"""Vault-backed auth injection for MCP servers (Plan 05 task_05_05).

`MCPServerConfig.auth_ref` is a *pointer* of the form ``vault:<path>``
(see :mod:`shared_mcp.types`). It is never the secret itself; the
secret lives inside Vault. This module resolves the pointer at
:meth:`shared_mcp.MCPClient.connect` time and folds the secret into
the runtime config — env vars for stdio servers, request headers for
HTTP transports (sse / streamable_http).

Design constraints:

* The resolver is an injection point so tests don't need a real Vault.
  :class:`StaticVaultResolver` is the in-memory test double;
  :class:`HvacVaultResolver` is the production wrapper around
  `hvac.Client` (lazy-imported so the package keeps zero hvac runtime
  cost when nobody needs Vault — see the ADR 0021 pattern for
  optional deps).
* :func:`apply_vault_auth` is a pure function: same input → same
  output, no mutation of the original frozen config. The MCP client
  only ever sees the *resolved* config; the pointer never leaks past
  this module.
* On any failure (unknown ``auth_ref``, missing entry, malformed
  pointer) we raise :class:`MCPAuthError`. The client / agent loop
  already maps that onto ``ToolResult.ok=False`` so the agent gets a
  useful error instead of a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from shared_mcp.exceptions import MCPAuthError
from shared_mcp.types import MCPServerConfig

VAULT_PREFIX = "vault:"


@runtime_checkable
class VaultResolver(Protocol):
    """Pluggable secret resolver. One method, sync — the call happens
    inside the async client right before opening the transport but the
    Vault round-trip itself is sync (hvac is sync; that's fine since
    it's a single short HTTP request and we're already paying the
    transport setup cost)."""

    def resolve(self, auth_ref: str) -> dict[str, str]:
        """Return the secret keyed by `auth_ref`.

        The returned dict's keys are merged into either ``env``
        (stdio transport) or ``headers`` (sse / streamable_http) of
        the :class:`MCPServerConfig`. Layout choices:

        * stdio: ``{"GITHUB_TOKEN": "ghp_..."}`` becomes
          ``env={"GITHUB_TOKEN": "ghp_..."}``.
        * http: ``{"Authorization": "Bearer ..."}`` becomes
          ``headers={"Authorization": "Bearer ..."}``.

        Raises:
            MCPAuthError: pointer is unknown, malformed, or Vault refused.
        """
        ...


@dataclass(frozen=True)
class StaticVaultResolver:
    """In-memory resolver — used by tests and local dev without Vault.

    `values` maps the *full* ``vault:...`` pointer to the secret's
    key/value pairs. Mirrors how hvac would return one secret:
    each KV-v2 ``data`` field becomes one entry in the dict.
    """

    values: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve(self, auth_ref: str) -> dict[str, str]:
        try:
            entry = self.values[auth_ref]
        except KeyError as exc:
            raise MCPAuthError(f"no secret registered for auth_ref={auth_ref!r}") from exc
        # Hand back a copy so callers can mutate freely without
        # poisoning the resolver's state.
        return dict(entry)


@dataclass(frozen=True)
class HvacVaultResolver:
    """Production resolver wrapping an `hvac.Client` (KV v2 only).

    Pointers must look like ``vault:<mount>/data/<path>`` — the
    ``/data/`` segment is the canonical KV-v2 prefix the hvac client
    expects. Example::

        cfg.auth_ref = "vault:secret/data/mcp/github/proj-42"
        # → mount="secret", path="mcp/github/proj-42"
        # → client.secrets.kv.v2.read_secret_version(
        #       mount_point="secret", path="mcp/github/proj-42")

    The constructor stores the client *unwrapped* — we deliberately do
    not import ``hvac`` at module import time so the package keeps
    its zero-cost optional-Vault story (same pattern as the Claude
    SDK in shared-llm).
    """

    client: Any  # hvac.Client — left untyped to avoid importing hvac

    def resolve(self, auth_ref: str) -> dict[str, str]:
        if not auth_ref.startswith(VAULT_PREFIX):
            raise MCPAuthError(f"auth_ref must start with {VAULT_PREFIX!r}, got {auth_ref!r}")
        path = auth_ref[len(VAULT_PREFIX) :]
        # KV v2 paths embed `/data/`; we split there to get (mount, sub-path).
        marker = "/data/"
        if marker not in path:
            raise MCPAuthError(
                f"auth_ref {auth_ref!r} is not a KV v2 path " f"(expected '<mount>/data/<path>')"
            )
        mount, _, sub_path = path.partition(marker)
        try:
            resp = self.client.secrets.kv.v2.read_secret_version(mount_point=mount, path=sub_path)
        except Exception as exc:  # hvac raises a zoo of exception types
            raise MCPAuthError(f"Vault read failed for {auth_ref!r}: {exc}") from exc
        try:
            data = resp["data"]["data"]
        except (KeyError, TypeError) as exc:
            raise MCPAuthError(f"Vault response for {auth_ref!r} has no `data.data` field") from exc
        if not isinstance(data, dict):
            raise MCPAuthError(f"Vault secret at {auth_ref!r} is not a key/value object")
        # Coerce to plain {str: str} so the downstream merge into env /
        # headers can't trip on non-string values.
        return {str(k): str(v) for k, v in data.items()}


def apply_vault_auth(
    config: MCPServerConfig,
    resolver: VaultResolver | None,
) -> MCPServerConfig:
    """Return a new :class:`MCPServerConfig` with the Vault secret
    folded into ``env`` (stdio) or ``headers`` (http transports).

    Behaviour matrix:

    +-------------------+-----------+----------------------------------+
    | config.auth_ref   | resolver  | result                           |
    +===================+===========+==================================+
    | None              | any       | config unchanged                 |
    +-------------------+-----------+----------------------------------+
    | "vault:..."       | None      | raises MCPAuthError              |
    +-------------------+-----------+----------------------------------+
    | "vault:..."       | configured| new config with secret merged    |
    +-------------------+-----------+----------------------------------+

    The resolver always wins on key collisions — declaring a static
    ``env["GITHUB_TOKEN"]`` and ALSO an ``auth_ref`` that yields the
    same key is a config mistake; the Vault value is authoritative.
    """
    if config.auth_ref is None:
        return config
    if resolver is None:
        raise MCPAuthError(
            f"server {config.name!r} declares auth_ref={config.auth_ref!r} "
            "but no VaultResolver was supplied to MCPClient.connect()"
        )
    secret = resolver.resolve(config.auth_ref)
    if not secret:
        # An empty dict from a resolver is almost certainly a bug —
        # surface it loudly rather than silently producing a config
        # with no auth.
        raise MCPAuthError(f"resolver returned no key/value pairs for {config.auth_ref!r}")

    if config.transport == "stdio":
        merged_env = {**config.env, **secret}
        return replace(config, env=merged_env)
    # sse | streamable_http
    merged_headers = {**config.headers, **secret}
    return replace(config, headers=merged_headers)


__all__ = [
    "HvacVaultResolver",
    "StaticVaultResolver",
    "VAULT_PREFIX",
    "VaultResolver",
    "apply_vault_auth",
]
