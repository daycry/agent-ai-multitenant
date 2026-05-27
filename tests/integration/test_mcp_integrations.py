"""Structural validation for the remaining catalog templates
(Plan 05 task_05_11).

This file covers the seven "everything else" templates the roadmap
groups under task_05_11:

* filesystem-mcp   (category=files, no Vault)
* gdrive-mcp       (category=files, OAuth via Vault)
* gmail-mcp        (category=comms, OAuth via Vault)
* gcalendar-mcp    (category=comms, OAuth via Vault)
* slack-mcp        (category=comms, bot-token + team-id via Vault)
* jira-mcp         (category=issues, Atlassian basic-auth via Vault)
* linear-mcp       (category=issues, single API key via Vault)

The catalog already lived in shared_mcp from task_05_08; what we add
here is the "is this still consistent with the rest of the platform"
check. Same shape as test_github_mcp.py and test_postgres_mcp.py:

1. Catalog presence + category bucketing.
2. Render → MCPServerConfigModel (Pydantic, task_05_04).
3. Vault injection roundtrip when the template requires auth.
4. A handful of template-specific spot-checks for the highest-risk
   ones (gmail-send footgun, slack scopes hint, jira basic-auth
   shape) — they make sure a careless edit doesn't silently break
   a security-relevant invariant.
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


REMAINING_IDS = (
    "filesystem-mcp",
    "gdrive-mcp",
    "gmail-mcp",
    "gcalendar-mcp",
    "slack-mcp",
    "jira-mcp",
    "linear-mcp",
)


def _render(template: McpServerTemplate, *, project_id: str, name: str) -> dict[str, Any]:
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
# All seven templates exist + render through Pydantic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", REMAINING_IDS)
def test_template_exists_in_catalog(template_id: str) -> None:
    assert template_id in CATALOG


@pytest.mark.parametrize("template_id", REMAINING_IDS)
def test_template_renders_to_valid_model(template_id: str) -> None:
    template = CATALOG[template_id]
    payload = _render(template, project_id="proj-1", name=template_id)
    model = MCPServerConfigModel.model_validate(payload)
    # Sanity on transport-specific fields.
    if model.transport == "stdio":
        assert model.command, f"{template_id} stdio missing command"
        assert model.url is None
    else:
        assert model.url, f"{template_id} non-stdio missing url"
        assert model.command is None


# ---------------------------------------------------------------------------
# Vault injection for the six that need it (filesystem-mcp does not)
# ---------------------------------------------------------------------------
VAULT_BACKED_IDS = tuple(tid for tid in REMAINING_IDS if tid != "filesystem-mcp")


@pytest.mark.parametrize("template_id", VAULT_BACKED_IDS)
def test_vault_backed_templates_inject_secrets(template_id: str) -> None:
    template = CATALOG[template_id]
    payload = _render(template, project_id="proj-vault", name=template_id)
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

    fake_secret = {key: f"fake-{key.lower()}" for key in template.secret_keys}
    resolver = StaticVaultResolver(values={runtime.auth_ref or "": fake_secret})
    injected = apply_vault_auth(runtime, resolver=resolver)

    # Every declared secret key lands in env (all 6 are stdio).
    for key in template.secret_keys:
        assert injected.env[key] == fake_secret[key]


def test_filesystem_template_has_no_vault_auth() -> None:
    """filesystem-mcp is the exception in the set: the bind mount
    needs no credentials, just an `ALLOWED_DIRS` allowlist. We pin
    that explicitly so adding `auth_ref` to it later requires a
    deliberate edit (and a chat with security)."""
    template = CATALOG["filesystem-mcp"]
    assert template.vault_path_template is None
    assert template.secret_keys == ()
    assert "ALLOWED_DIRS" in template.static_env


# ---------------------------------------------------------------------------
# Spot checks on the riskier templates
# ---------------------------------------------------------------------------
def test_gmail_template_carries_send_footgun_warning() -> None:
    """gmail-mcp is the one template in the catalog with destructive
    side effects (send mail). The description must call this out so
    the operator picker shows the warning, not just a generic blurb."""
    description = CATALOG["gmail-mcp"].description.lower()
    assert "send" in description
    # OAuth shape: access + refresh tokens, same as gdrive/gcalendar.
    assert set(CATALOG["gmail-mcp"].secret_keys) == {
        "GMAIL_ACCESS_TOKEN",
        "GMAIL_REFRESH_TOKEN",
    }


def test_slack_template_hints_at_minimum_scopes() -> None:
    """Bot-token scope sprawl is the easiest way to over-provision
    slack-mcp. The description must name at least one minimal
    scope so the operator knows what to request from Slack."""
    description = CATALOG["slack-mcp"].description.lower()
    assert "chat:write" in description
    assert set(CATALOG["slack-mcp"].secret_keys) == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}


def test_jira_template_carries_three_secrets() -> None:
    """Atlassian's basic-auth needs three pieces: API token, the
    user's email, and the instance URL. All three live in Vault — no
    static fallback for any of them, otherwise a stale URL would
    survive a credential rotation."""
    assert set(CATALOG["jira-mcp"].secret_keys) == {
        "JIRA_API_TOKEN",
        "JIRA_EMAIL",
        "JIRA_INSTANCE_URL",
    }
    assert "atlassian" in CATALOG["jira-mcp"].display_name.lower()


def test_linear_template_is_single_key() -> None:
    """linear-mcp uses one API key, no email/instance dance. Pin so
    nobody splits it later (would be a regression in operator UX)."""
    assert CATALOG["linear-mcp"].secret_keys == ("LINEAR_API_KEY",)


# ---------------------------------------------------------------------------
# Categorisation drift guard
# ---------------------------------------------------------------------------
def test_category_buckets_are_stable() -> None:
    """The UI picker groups by `category`. If we silently move
    `slack-mcp` from "comms" to "other" the picker breaks for every
    project already using it. Pin the buckets."""
    expected = {
        "filesystem-mcp": "files",
        "gdrive-mcp": "files",
        "gmail-mcp": "comms",
        "gcalendar-mcp": "comms",
        "slack-mcp": "comms",
        "jira-mcp": "issues",
        "linear-mcp": "issues",
    }
    for template_id, category in expected.items():
        assert CATALOG[template_id].category == category
