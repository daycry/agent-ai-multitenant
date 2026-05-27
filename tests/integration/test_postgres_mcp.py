"""Structural validation for the postgres-mcp catalog template
(Plan 05 task_05_10).

The roadmap test signal expects ``pytest tests/integration/test_postgres_mcp.py``.
The pattern mirrors ``test_github_mcp.py`` — we don't spawn the real
postgres-mcp server (that needs a connection-string secret in CI and
flakes on Postgres availability), we just pin that the catalog entry
renders to a valid runtime config and the Vault wiring round-trips.

What this file pins specifically (vs the SCM tests):

* postgres-mcp's secret is a single ``DATABASE_URI`` — proves the
  template doesn't try to split it into host/user/pass/db (which
  would defeat Vault's "the URI is the secret" model).
* The default timeout matches `MCPServerConfig`'s default (30s) — DB
  queries shouldn't hold the agent loop hostage; aggressive timeouts
  are part of the security envelope per the Plan 05 "decisiones
  clave".
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.mcp.config import MCPServerConfigModel
from shared_mcp import (
    CATALOG,
    MCPServerConfig,
    StaticVaultResolver,
    apply_vault_auth,
)
from shared_mcp.catalog import render_vault_path

pytestmark = pytest.mark.integration


TEMPLATE_ID = "postgres-mcp"


def _render(*, project_id: str, name: str) -> dict[str, Any]:
    template = CATALOG[TEMPLATE_ID]
    auth_ref = render_vault_path(template, project_id=project_id)
    return {
        "name": name,
        "transport": template.transport,
        "command": template.command,
        "args": list(template.args),
        "env": dict(template.static_env),
        "url": template.url,
        "headers": dict(template.static_headers),
        "auth_ref": auth_ref,
        "timeout_s": template.default_timeout_s,
    }


# ---------------------------------------------------------------------------
# Catalog presence
# ---------------------------------------------------------------------------
def test_postgres_template_exists_in_catalog() -> None:
    assert TEMPLATE_ID in CATALOG
    template = CATALOG[TEMPLATE_ID]
    assert template.category == "data"
    assert template.display_name == "PostgreSQL"


# ---------------------------------------------------------------------------
# Pydantic-validator roundtrip
# ---------------------------------------------------------------------------
def test_postgres_template_renders_to_valid_model() -> None:
    payload = _render(project_id="proj-pg-1", name="pg-prod")
    model = MCPServerConfigModel.model_validate(payload)
    assert model.transport == "stdio"
    assert model.command == "mcp-postgres"
    assert model.auth_ref is not None
    assert model.auth_ref.startswith("vault:")


# ---------------------------------------------------------------------------
# Single-secret invariant — the URI IS the secret, no host/user split
# ---------------------------------------------------------------------------
def test_postgres_secret_is_single_uri() -> None:
    """Splitting the URI across multiple Vault fields would let the
    operator leak partial credentials. Pin the single-URI shape so
    later edits don't drift."""
    template = CATALOG[TEMPLATE_ID]
    assert template.secret_keys == ("DATABASE_URI",)
    assert template.vault_path_template == "vault:secret/data/mcp/postgres/{project_id}"


# ---------------------------------------------------------------------------
# Timeout matches the platform default — DB queries don't get a free pass
# ---------------------------------------------------------------------------
def test_postgres_timeout_matches_platform_default() -> None:
    """Plan 05 'Decisiones clave': 'Timeouts agresivos por tool
    (default 30s) configurables'. postgres-mcp should NOT bump the
    default unconditionally — long-running queries are a footgun the
    operator opts into per project."""
    assert CATALOG[TEMPLATE_ID].default_timeout_s == 30.0


# ---------------------------------------------------------------------------
# Vault injection roundtrip
# ---------------------------------------------------------------------------
def test_postgres_vault_injection_lands_uri_in_env() -> None:
    template = CATALOG[TEMPLATE_ID]
    payload = _render(project_id="proj-pg-vault", name="pg")
    model = MCPServerConfigModel.model_validate(payload)
    runtime = MCPServerConfig(
        name=model.name,
        transport=model.transport,
        command=model.command,
        args=tuple(model.args),
        env=dict(model.env),
        url=model.url,
        headers=dict(model.headers),
        auth_ref=model.auth_ref,
        timeout_s=model.timeout_s,
    )

    fake_uri = "postgresql://user:hidden@db:5432/proj_pg_vault"
    resolver = StaticVaultResolver(values={runtime.auth_ref or "": {"DATABASE_URI": fake_uri}})
    injected = apply_vault_auth(runtime, resolver=resolver)

    assert injected.env["DATABASE_URI"] == fake_uri
    # The static env (none for postgres) stays untouched — the URI
    # alone is enough, no extra config slots to drift.
    assert set(injected.env.keys()) == {"DATABASE_URI"}.union(template.static_env.keys())
