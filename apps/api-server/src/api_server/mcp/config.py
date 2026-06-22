"""Pydantic schema for `Project.mcp_servers` entries (Plan 05 task_05_04).

`Project.mcp_servers` is a JSONB column carrying a list of MCP server
declarations. The shape mirrors :class:`shared_mcp.types.MCPServerConfig`
(the runtime dataclass the async client consumes), but lives here as a
Pydantic model because:

* HTTP requests arrive as JSON dicts, not dataclasses — Pydantic gives
  us field-level error messages instead of opaque TypeErrors.
* We can re-serialise to canonical dicts before persisting, which keeps
  the JSONB blob diff-stable across edits.
* The validator owns *cross-server* invariants (no duplicate names
  within one project) that the dataclass cannot enforce on its own.

The runtime dataclass and this model must stay in lock-step. If a field
changes shape there, update it here and bump the test in
``tests/unit/test_mcp_config_schema.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# Mirror of `shared_mcp.types.Transport`. We duplicate the Literal here
# rather than importing because api-server's mypy hook doesn't always
# see shared_mcp (the editable install lives in the runtime venv only,
# same situation as shared_llm — see pyproject.toml mypy overrides).
Transport = Literal["stdio", "sse", "streamable_http"]


_NAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_\-.]{0,63}$"


class MCPServerConfigModel(BaseModel):
    """One MCP server entry inside `Project.mcp_servers`.

    Fields match :class:`shared_mcp.types.MCPServerConfig` 1:1. The
    transport-specific invariants are enforced by :meth:`_transport_invariants`
    so callers get a single ValidationError listing every problem
    instead of being drip-fed one ``__post_init__`` raise at a time.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=_NAME_PATTERN,
        description=(
            "Human-facing name for this server inside the project. Must "
            "match `^[a-zA-Z][a-zA-Z0-9_\\-.]{0,63}$` — used as a path "
            "fragment when namespacing tools as `<server>.<tool>`."
        ),
    )
    transport: Transport

    command: str | None = Field(default=None, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)

    url: str | None = Field(default=None, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=64)

    # Pointer into Vault — resolved at connect time by task_05_05.
    # None means "this server needs no auth" (typical for local stdio
    # servers like docling-mcp).
    auth_ref: str | None = Field(default=None, max_length=512)

    timeout_s: float = Field(default=30.0, gt=0, le=300)

    # Per-server cap on tool output bytes (defence against chatty/malicious
    # servers that would otherwise exhaust the model's context window). Matches
    # the dataclass default; bounded to a sane range (1 KiB .. 1 MiB).
    max_output_bytes: int = Field(default=65536, ge=1024, le=1_048_576)

    @field_validator("auth_ref")
    @classmethod
    def _auth_ref_must_be_vault(cls, value: str | None) -> str | None:
        """Per CLAUDE.md `Vault is the only credentials path` — anything
        we accept as `auth_ref` must be a Vault pointer. Cleartext
        tokens in JSONB are a deliberate non-goal."""
        if value is None:
            return None
        if not value.startswith("vault:"):
            raise ValueError(
                "auth_ref must be a Vault pointer (start with 'vault:'); "
                "raw secrets in config are not allowed"
            )
        return value

    @model_validator(mode="after")
    def _transport_invariants(self) -> MCPServerConfigModel:
        """Make sure `transport` and the transport-specific fields
        line up. Mirrors `shared_mcp.types.MCPServerConfig.__post_init__`
        but raises one ValidationError listing every conflict at once."""
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("transport='stdio' requires `command`")
            if self.url is not None:
                raise ValueError("transport='stdio' must not set `url`")
            if self.headers:
                raise ValueError("transport='stdio' must not set `headers`")
        else:  # sse | streamable_http
            if not self.url:
                raise ValueError(f"transport={self.transport!r} requires `url`")
            if self.command is not None:
                raise ValueError(f"transport={self.transport!r} must not set `command`")
            if self.args:
                raise ValueError(f"transport={self.transport!r} must not set `args`")
            if self.env:
                raise ValueError(f"transport={self.transport!r} must not set `env`")
        return self


def validate_mcp_servers_payload(
    servers: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate the full `Project.mcp_servers` payload.

    Args:
        servers: The raw list-of-dicts arriving in a Project create/update.
            `None` and empty list both mean "no MCP servers declared".

    Returns:
        Canonical list of dicts ready to persist in JSONB. Each dict is
        the model's ``.model_dump()`` (so missing optional fields appear
        as their defaults, which keeps DB diffs stable).

    Raises:
        ValueError: on any per-entry validation problem, on duplicate
            names within the list, or on entries that are not dicts.
            The message is prefixed with the offending index so the
            caller (FastAPI's exception handler) can point the user at
            the right entry.
    """
    if not servers:
        return []
    seen_names: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(servers):
        if not isinstance(raw, dict):
            raise ValueError(f"mcp_servers[{i}]: expected an object, got {type(raw).__name__}")
        try:
            cfg = MCPServerConfigModel.model_validate(raw)
        except ValidationError as exc:
            # Re-raise as ValueError so FastAPI's `model_validator` on
            # the outer Project schema can re-wrap it without losing
            # the index context.
            raise ValueError(f"mcp_servers[{i}]: {exc}") from exc
        if cfg.name in seen_names:
            raise ValueError(
                f"mcp_servers[{i}]: duplicate server name {cfg.name!r} — "
                "names must be unique within a project"
            )
        seen_names.add(cfg.name)
        out.append(cfg.model_dump(mode="json"))
    return out


__all__ = [
    "MCPServerConfigModel",
    "Transport",
    "validate_mcp_servers_payload",
]
