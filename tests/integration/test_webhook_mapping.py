"""Integration tests for webhook -> system action mapping (task_13_10).

The INBOUND pipeline's last hop: task_13_08 VERIFIES the per-origin HMAC,
task_13_09 NORMALISES the payload, and this phase MAPS a normalised event to a
configurable SYSTEM action in the target project — create a task, comment on a
task, or escalate — per the project's ``action_mappings``, with title/body
templating from the event, and EXECUTES it under the config's own tenant/project
(RLS-scoped). This suite proves, end-to-end over the public endpoint:

  * a GitHub PR-review event creates a task in the mapped project (title/body
    rendered from the event templates);
  * a Jira issue created event creates a task;
  * a configured ``comment`` action appends a comment audit event on the target
    task (no duplicate task);
  * a configured ``escalate`` action escalates the target task (status ->
    ``blocked`` + an escalation audit event);
  * a redelivery (same delivery id) is idempotent — NO duplicate task / action;
  * cross-tenant (@pytest.mark.cross_tenant): the action lands ONLY in the
    target tenant/project — a comment/escalate can never touch another tenant's
    task, and a create_task lands in the config's own project.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the ``configured_app`` fixture migrates a throwaway DB and flushes Redis DB 15.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_GITHUB_SIG_HEADER = "X-Hub-Signature-256"
_GENERIC_SIG_HEADER = "X-Signature-256"
_SECRET = "s3cret-signing-key-acme"  # - test fixture, not a real secret


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_project(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_task(
    dsn: str, *, tenant_id: UUID, project_id: UUID, title: str = "existing task"
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority) "
            "VALUES ($1, $2, $3, $4, 'in_progress', 'medium')",
            task_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_config(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    origin: str = "github",
    secret: str = _SECRET,
    action_mappings: list[dict] | None = None,
) -> UUID:
    """Seed an ``incoming_webhook_configs`` row (secret Fernet-encrypted)."""
    from api_server.webhooks.secrets import encrypt_signing_secret

    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_configs "
            "(id, tenant_id, project_id, origin, name, signing_secret_encrypted, "
            " enabled, action_mappings) "
            "VALUES ($1, $2, $3, $4, $5, $6, true, $7)",
            config_id,
            tenant_id,
            project_id,
            origin,
            f"{origin}-config",
            encrypt_signing_secret(secret),
            json.dumps(action_mappings or []),
        )
    finally:
        await conn.close()
    return config_id


async def _count_tasks(dsn: str, *, project_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval("SELECT count(*) FROM tasks WHERE project_id = $1", project_id)
    finally:
        await conn.close()
    return int(row)


async def _fetch_task(dsn: str, *, project_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, tenant_id, project_id, title, description, status "
            "FROM tasks WHERE project_id = $1",
            project_id,
        )
    finally:
        await conn.close()
    return row


async def _fetch_task_by_id(dsn: str, *, task_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT id, status FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()
    return row


async def _audit_events(dsn: str, *, task_id: UUID, kind: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM task_audit_events WHERE task_id = $1 AND kind = $2",
            task_id,
            kind,
        )
    finally:
        await conn.close()
    return int(row)


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE incoming_webhook_events, incoming_webhook_configs, "
            "task_audit_events, tasks, projects, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


def _github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _generic_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


# Real-shaped payloads (mirroring task_13_09's template suite).
_PR_REVIEW_PAYLOAD = json.dumps(
    {
        "action": "submitted",
        "review": {
            "state": "approved",
            "body": "LGTM, ship it",
            "html_url": "https://github.com/acme/api/pull/42#pullrequestreview-1",
            "user": {"login": "reviewer-jane"},
        },
        "pull_request": {"number": 42, "title": "Add retry logic"},
        "repository": {"full_name": "acme/api"},
    }
).encode("utf-8")

_JIRA_PAYLOAD = json.dumps(
    {
        "webhookEvent": "jira:issue_created",
        "user": {"displayName": "Alice Reporter"},
        "issue": {
            "key": "PROJ-123",
            "self": "https://acme.atlassian.net/rest/api/2/issue/10001",
            "fields": {
                "summary": "Login button is broken",
                "description": "Steps to reproduce ...",
                "project": {"key": "PROJ"},
                "issuetype": {"name": "Bug"},
                "reporter": {"displayName": "Alice Reporter"},
            },
        },
    }
).encode("utf-8")


# ===========================================================================
# GitHub PR-review event -> creates a task in the mapped project
# ===========================================================================
@pytest.mark.asyncio
async def test_github_pr_review_creates_task(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[
            {
                "event_type": "github.pull_request_review",
                "action": "create_task",
                "title_template": "Review: {title}",
                "body_template": "{body}\n\nby {actor} ({review_state})",
            }
        ],
    )

    body = _PR_REVIEW_PAYLOAD
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Event": "pull_request_review",
        "X-GitHub-Delivery": "pr-review-1",
    }
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["action"] == "create_task"
    assert _SECRET not in resp.text

    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1
    task = await _fetch_task(migrations_pg_dsn, project_id=project)
    assert task["title"] == "Review: Add retry logic"
    assert task["description"] == "LGTM, ship it\n\nby reviewer-jane (approved)"
    assert task["tenant_id"] == tenant
    assert task["project_id"] == project
    assert task["status"] == "backlog"


# ===========================================================================
# Jira issue created -> creates a task
# ===========================================================================
@pytest.mark.asyncio
async def test_jira_issue_creates_task(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="jira",
        action_mappings=[{"event_type": "jira.issue_created", "action": "create_task"}],
    )

    body = _JIRA_PAYLOAD
    headers = {_GENERIC_SIG_HEADER: _generic_signature(_SECRET, body)}
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/jira/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["action"] == "create_task"

    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1
    task = await _fetch_task(migrations_pg_dsn, project_id=project)
    # No templates -> falls back to the normalised title/body.
    assert task["title"] == "[PROJ-123] Login button is broken"
    assert task["description"] == "Steps to reproduce ..."


# ===========================================================================
# A configured comment action comments on the target task
# ===========================================================================
@pytest.mark.asyncio
async def test_comment_action_comments(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    target_task = await _seed_task(migrations_pg_dsn, tenant_id=tenant, project_id=project)
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[
            {
                "event_type": "github.pull_request_review",
                "action": "comment",
                "target_task_id": str(target_task),
                "body_template": "Review {review_state} by {actor}",
            }
        ],
    )

    body = _PR_REVIEW_PAYLOAD
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Event": "pull_request_review",
        "X-GitHub-Delivery": "comment-1",
    }
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["action"] == "comment"
    assert resp.json()["task_id"] == str(target_task)
    # No NEW task — the target task is the only one.
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1
    assert await _audit_events(migrations_pg_dsn, task_id=target_task, kind="comment") == 1
    # The task is not escalated by a comment.
    row = await _fetch_task_by_id(migrations_pg_dsn, task_id=target_task)
    assert row["status"] == "in_progress"


# ===========================================================================
# A configured escalate action escalates the target task
# ===========================================================================
@pytest.mark.asyncio
async def test_escalate_action_escalates(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    target_task = await _seed_task(migrations_pg_dsn, tenant_id=tenant, project_id=project)
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="sentry",
        action_mappings=[
            {
                "event_type": "sentry.error",
                "action": "escalate",
                "target_task_id": str(target_task),
            }
        ],
    )

    body = json.dumps(
        {
            "action": "created",
            "project": "backend-prod",
            "data": {
                "event": {
                    "event_id": "ev-9f8e7d",
                    "title": "TypeError: undefined",
                    "culprit": "app/handlers/payment",
                    "web_url": "https://sentry.io/.../ev-9f8e7d/",
                    "issue_id": "555",
                }
            },
        }
    ).encode("utf-8")
    headers = {
        _GENERIC_SIG_HEADER: _generic_signature(_SECRET, body),
        "X-Request-Id": "escalate-1",
    }
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/sentry/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["action"] == "escalate"
    row = await _fetch_task_by_id(migrations_pg_dsn, task_id=target_task)
    assert row["status"] == "blocked"
    assert await _audit_events(migrations_pg_dsn, task_id=target_task, kind="escalation") == 1


# ===========================================================================
# Redelivery is idempotent — no duplicate task / action
# ===========================================================================
@pytest.mark.asyncio
async def test_redelivery_does_not_duplicate_task(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[{"event_type": "github.pull_request_review", "action": "create_task"}],
    )

    body = _PR_REVIEW_PAYLOAD
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Event": "pull_request_review",
        "X-GitHub-Delivery": "dup-delivery-1",
    }
    async with _client(configured_app) as client:
        first = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
        second = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    # Exactly ONE task despite two deliveries (idempotent on delivery id).
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1


# ===========================================================================
# Cross-tenant: the action lands ONLY in the target tenant/project
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_action_lands_only_in_target_tenant(configured_app, migrations_pg_dsn: str) -> None:
    """A config's action only ever writes into its OWN tenant/project.

    Two tenants. Tenant A's config maps PR-review -> create_task. We also try a
    comment action whose target_task_id belongs to TENANT B: it must be a no-op
    (RLS hides B's task), the event is recorded but B's task is never touched.
    """
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="acme")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="globex")
    project_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="proj-a")
    project_b = await _seed_project(migrations_pg_dsn, tenant_id=tenant_b, name="proj-b")
    # A task in tenant B that tenant A's webhook will (illegitimately) target.
    task_b = await _seed_task(migrations_pg_dsn, tenant_id=tenant_b, project_id=project_b)

    # --- create_task lands in tenant A's project, never B's ---
    config_create = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        project_id=project_a,
        origin="github",
        action_mappings=[{"event_type": "github.pull_request_review", "action": "create_task"}],
    )
    body = _PR_REVIEW_PAYLOAD
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Event": "pull_request_review",
        "X-GitHub-Delivery": "xt-create-1",
    }
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_create}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert await _count_tasks(migrations_pg_dsn, project_id=project_a) == 1
    # Tenant B's project got NOTHING.
    assert await _count_tasks(migrations_pg_dsn, project_id=project_b) == 1  # only the seeded task

    # --- a comment action targeting TENANT B's task is a no-op (RLS-fenced) ---
    config_comment = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        project_id=project_a,
        origin="jira",
        action_mappings=[
            {
                "event_type": "jira.issue_created",
                "action": "comment",
                "target_task_id": str(task_b),  # belongs to tenant B!
            }
        ],
    )
    jira_body = _JIRA_PAYLOAD
    jira_headers = {_GENERIC_SIG_HEADER: _generic_signature(_SECRET, jira_body)}
    async with _client(configured_app) as client:
        resp2 = await client.post(
            f"/webhooks/incoming/jira/{config_comment}", content=jira_body, headers=jira_headers
        )
    # The event is accepted + recorded, but NO comment landed on tenant B's task.
    assert resp2.status_code == 202, resp2.text
    assert "action" not in resp2.json()  # the cross-tenant target was a no-op
    assert await _audit_events(migrations_pg_dsn, task_id=task_b, kind="comment") == 0
    # Tenant B's task is untouched.
    row_b = await _fetch_task_by_id(migrations_pg_dsn, task_id=task_b)
    assert row_b["status"] == "in_progress"
