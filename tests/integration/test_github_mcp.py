"""Structural validation for the SCM family of MCP catalog templates
(Plan 05 task_05_09).

The roadmap names this file `test_github_mcp.py` because github-mcp is
the canonical example, but in practice we share the same shape across
github, gitlab and azure-devops — they're all PAT-authenticated SCM
servers. The test exercises every entry in the family the same way:

1. Catalog entry is a well-formed `McpServerTemplate`.
2. Calling `_render(template, ...)` yields a dict that
   `MCPServerConfigModel` (the Pydantic validator from task_05_04)
   accepts without errors.
3. Wrapping the result in `MCPServerConfig` and passing through
   `apply_vault_auth` with a `StaticVaultResolver` (task_05_05)
   produces a runtime config whose `env` carries the right secret
   keys — proving the template + Vault wiring + dataclass are all
   in sync.

We deliberately don't try to spawn github-mcp or hit GitHub's API —
those depend on real PATs and would flake on every CI run. The
structural assertion is enough: when the template, the Pydantic
schema and the Vault injector all agree on the shape, the runtime
roundtrip is mechanical and tested elsewhere
(`tests/integration/test_mcp_auth_injection.py`).
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.mcp.config import MCPServerConfigModel
from shared_mcp import (
    CATALOG,
    MCPServerConfig,
    McpServerTemplate,
    StaticVaultResolver,
    apply_vault_auth,
)
from shared_mcp.catalog import render_vault_path

pytestmark = pytest.mark.integration


SCM_IDS = ("github-mcp", "gitlab-mcp", "azure-devops-mcp", "bitbucket-mcp")


def _render(template: McpServerTemplate, *, project_id: str, name: str) -> dict[str, Any]:
    """Translate a catalog template into the dict shape that
    `MCPServerConfigModel` accepts. The api-server's catalog endpoint
    (a follow-up of task_05_15) will do the same — keep this in
    lock-step with that future implementation."""
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
# Catalog presence + identity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", SCM_IDS)
def test_scm_template_exists_in_catalog(template_id: str) -> None:
    assert template_id in CATALOG
    assert CATALOG[template_id].category == "scm"


def test_scm_family_matches_declared_set() -> None:
    """If we drift the SCM family (drop one, rename one) we want the
    test to surface — the family is a load-bearing contract for the
    UI picker grouping. Adding a new SCM template requires bumping
    SCM_IDS above so the parametrized tests pick it up too."""
    # SCM_IDS is the stdio family (the parametrized stdio/env tests use it).
    # `github-remote` is the HTTP replacement (ADR 0117) — same scm category
    # for picker grouping, but not stdio, so it's tracked separately here.
    scm_members = {tid for tid, t in CATALOG.items() if t.category == "scm"}
    assert scm_members == set(SCM_IDS) | {"github-remote"}


# ---------------------------------------------------------------------------
# Pydantic-validator roundtrip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", SCM_IDS)
def test_scm_template_renders_to_valid_model(template_id: str) -> None:
    template = CATALOG[template_id]
    payload = _render(template, project_id="proj-test-1", name=f"{template_id}-test")
    model = MCPServerConfigModel.model_validate(payload)
    # Sanity: the Pydantic model preserves transport-specific fields.
    assert model.transport == template.transport
    if template.transport == "stdio":
        assert model.command == template.command
    else:
        assert model.url == template.url


# ---------------------------------------------------------------------------
# Auth shape: every SCM template requires Vault auth
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", SCM_IDS)
def test_scm_template_declares_vault_auth(template_id: str) -> None:
    """SCM access is always credentialled — no anonymous read-only
    template makes sense in this family. Drift here means we'd be
    declaring a server without auth, which CLAUDE.md flat-out
    forbids for production credentials."""
    template = CATALOG[template_id]
    assert template.vault_path_template is not None
    assert template.vault_path_template.startswith("vault:")
    assert "{project_id}" in template.vault_path_template
    assert len(template.secret_keys) >= 1


# ---------------------------------------------------------------------------
# Vault injection roundtrip — secret lands in env (stdio family is stdio)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", SCM_IDS)
def test_scm_template_injects_secrets_into_env(template_id: str) -> None:
    template = CATALOG[template_id]
    payload = _render(template, project_id="proj-vault-test", name=template_id)
    model = MCPServerConfigModel.model_validate(payload)

    # Build the runtime dataclass the SDK consumes.
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

    # Wire a static resolver that returns one fake secret per declared key.
    fake_secret = {key: f"fake-{key.lower()}-value" for key in template.secret_keys}
    resolver = StaticVaultResolver(values={runtime.auth_ref or "": fake_secret})

    injected = apply_vault_auth(runtime, resolver=resolver)
    # stdio family → env carries the secret keys; static env stays.
    for key in template.secret_keys:
        assert injected.env[key] == fake_secret[key]
    for static_key, static_val in template.static_env.items():
        assert injected.env[static_key] == static_val


# ---------------------------------------------------------------------------
# github-mcp specifics — pin the exact knobs the canonical entry exposes
# ---------------------------------------------------------------------------
def test_github_template_exposes_canonical_knobs() -> None:
    """If someone bumps github-mcp's auth env var or URL upstream, this
    test points at exactly which knob to edit. Acts as the human-
    readable changelog for that one entry."""
    gh = CATALOG["github-mcp"]
    assert gh.transport == "stdio"
    assert gh.command == "github-mcp"
    assert gh.secret_keys == ("GITHUB_TOKEN",)
    assert gh.static_env.get("GITHUB_HOST", "").startswith("https://")
    # The vault path is project-scoped, not global — multi-tenant
    # invariant: one project's token never reaches another's session.
    assert "{project_id}" in (gh.vault_path_template or "")
