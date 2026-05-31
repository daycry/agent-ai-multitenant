"""Integration tests for the docs-viewer diff endpoint (Plan 07 task_07_16be).

Backend half of task_07_16 (the frontend diff *viewer* is a later task). Drives
``GET /projects/{id}/docs/diff`` end-to-end through the real app + Postgres
schema against a **throwaway git repo** (built per-test under ``tmp_path`` —
the ``get_docs_repo_resolver`` dependency is overridden so no real worktree is
needed):

  * happy path — a ``docs/x.md`` edited across two commits returns the
    expected added / removed lines (structured) + a raw unified diff;
  * an unchanged file across two refs returns ``unchanged=True`` with no
    add/remove lines;
  * path-traversal (``..`` / absolute) and option-like git refs are rejected
    with a 400, never leaking content outside the docs root;
  * RBAC — a non-member is denied (403) and a cross-tenant caller addressing
    another tenant's project gets a 404 (RLS hides the row,
    ``@pytest.mark.cross_tenant``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# DB seed helpers (mirror test_docs_viewer_api.py)
# ---------------------------------------------------------------------------
async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_single_tenant(dsn: str) -> dict[str, UUID]:
    """One tenant with a member + a non-member user, and one project."""
    tenant_id = uuid4()
    member_id = uuid4()
    outsider_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant DD",
            "tenant-dd",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-dd",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'member@dd.test', 'h'), ($2, 'outsider@dd.test', 'h')",
            member_id,
            outsider_id,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_user')",
            uuid4(),
            tenant_id,
            member_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Project DD",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "member_id": member_id,
        "outsider_id": outsider_id,
        "project_id": project_id,
    }


async def _seed_two_tenants(dsn: str) -> dict[str, dict[str, UUID]]:
    """Two tenants, each with a member + a project. Cross-tenant denial."""
    a_tenant, a_user, a_project = uuid4(), uuid4(), uuid4()
    b_tenant, b_user, b_project = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            a_tenant,
            "Tenant A",
            "tenant-a-dd",
            b_tenant,
            "Tenant B",
            "tenant-b-dd",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-dd-xt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@dd.xt', 'h'), ($2, 'b@dd.xt', 'h')",
            a_user,
            b_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_user'), ($4, $5, $6, 'tenant_user')",
            uuid4(),
            a_tenant,
            a_user,
            uuid4(),
            b_tenant,
            b_user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            a_project,
            a_tenant,
            "Project A",
            b_project,
            b_tenant,
            "Project B",
        )
    finally:
        await conn.close()
    return {
        "a": {"tenant_id": a_tenant, "user_id": a_user, "project_id": a_project},
        "b": {"tenant_id": b_tenant, "user_id": b_user, "project_id": b_project},
    }


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Throwaway git repo: docs/x.md committed, then edited in a second commit
# ---------------------------------------------------------------------------
_DOC_RELPATH = "03-guides/setup.md"

_V1 = "# Setup\n\nInstall the platform.\n\nUse Docker.\n"
# v2: keeps the title + first line, drops "Use Docker.", adds two new lines.
_V2 = "# Setup\n\nInstall the platform.\n\nRun the installer.\nThen log in.\n"


def _build_doc_repo(repo_root: Path) -> dict[str, str]:
    """Create a git repo at ``repo_root`` with ``docs/<_DOC_RELPATH>`` edited
    across two commits. Returns the two commit shas keyed ``base`` / ``head``.
    """
    from workers.git_repos import _run_git

    repo_root.mkdir(parents=True, exist_ok=True)
    _run_git("init", str(repo_root))
    # Deterministic, network-free identity so commits succeed in CI.
    _run_git("config", "user.email", "diff@test.local", cwd=repo_root)
    _run_git("config", "user.name", "Diff Test", cwd=repo_root)
    _run_git("config", "commit.gpgsign", "false", cwd=repo_root)

    doc = repo_root / "docs" / _DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)

    doc.write_text(_V1, encoding="utf-8")
    _run_git("add", "-A", cwd=repo_root)
    _run_git("commit", "-m", "docs: add setup guide", cwd=repo_root)
    base_sha = _run_git("rev-parse", "HEAD", cwd=repo_root).strip()

    doc.write_text(_V2, encoding="utf-8")
    _run_git("add", "-A", cwd=repo_root)
    _run_git("commit", "-m", "docs: revise setup guide", cwd=repo_root)
    head_sha = _run_git("rev-parse", "HEAD", cwd=repo_root).strip()

    return {"base": base_sha, "head": head_sha}


# ---------------------------------------------------------------------------
# App fixture — overrides the repo-root resolver to point at a tmp git repo
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app
    from api_server.routers.docs_viewer import get_docs_repo_resolver

    app = create_app()

    # Map each (tenant, project) to its own tmp git repo root. An unseeded
    # project resolves to a missing dir → git diff fails → 400 (production
    # "repo not materialised yet" behaviour, surfaced as a client error).
    repos: dict[tuple[UUID, UUID], Path] = {}

    def _resolver():
        def _resolve(tenant_id: UUID, project_id: UUID) -> Path:
            return repos.get(
                (tenant_id, project_id),
                tmp_path / "missing" / str(project_id),
            )

        return _resolve

    app.dependency_overrides[get_docs_repo_resolver] = _resolver
    try:
        yield app, repos, tmp_path
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


# ===========================================================================
# Happy path — added / removed lines across two commits
# ===========================================================================
@pytest.mark.asyncio
async def test_diff_returns_added_and_removed_lines(configured_app, migrations_pg_dsn: str) -> None:
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    shas = _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": shas["base"], "head": shas["head"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["relpath"] == _DOC_RELPATH
        assert body["base_ref"] == shas["base"]
        assert body["head_ref"] == shas["head"]
        assert body["unchanged"] is False

        added = {ln["content"] for ln in body["lines"] if ln["kind"] == "added"}
        removed = {ln["content"] for ln in body["lines"] if ln["kind"] == "removed"}
        # The revision dropped "Use Docker." and added two new lines.
        assert "Use Docker." in removed
        assert "Run the installer." in added
        assert "Then log in." in added
        assert body["removed"] == 1
        assert body["added"] == 2

        # The raw unified diff is present and consistent with the counts.
        assert body["raw"].count("\n+") >= 2
        assert "-Use Docker." in body["raw"]
        # At least one hunk header is parsed.
        assert any(ln["kind"] == "hunk" for ln in body["lines"])


@pytest.mark.asyncio
async def test_diff_unchanged_file_is_empty(configured_app, migrations_pg_dsn: str) -> None:
    """Same ref on both sides → no changes, ``unchanged=True``, no lines."""
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    shas = _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": shas["head"], "head": shas["head"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unchanged"] is True
        assert body["added"] == 0
        assert body["removed"] == 0
        assert body["lines"] == []


# ===========================================================================
# Bad input — refs / paths rejected with a 400
# ===========================================================================
@pytest.mark.asyncio
async def test_diff_unknown_ref_is_400(configured_app, migrations_pg_dsn: str) -> None:
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": "deadbeefdeadbeef", "head": "HEAD"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil_path",
    [
        "../../../../etc/passwd",
        "../secret.md",
        "03-guides/../../escape.md",
        "/etc/passwd",
        "..\\..\\windows.md",
    ],
)
async def test_diff_path_traversal_blocked(
    configured_app, migrations_pg_dsn: str, evil_path: str
) -> None:
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    shas = _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root
    # A secret committed OUTSIDE docs/ must never leak via the diff.
    secret = repo_root / "secret.md"
    secret.write_text("# TOP SECRET\n", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": evil_path, "base": shas["base"], "head": shas["head"]},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert "TOP SECRET" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("evil_ref", ["--output=/tmp/x", "-O/tmp/x", "HEAD HEAD"])
async def test_diff_option_like_ref_rejected(
    configured_app, migrations_pg_dsn: str, evil_ref: str
) -> None:
    """A ref that would be parsed as a git option (or carries whitespace) is a
    400, never executed."""
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    shas = _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": evil_ref, "head": shas["head"]},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text


# ===========================================================================
# RBAC — non-member is denied
# ===========================================================================
@pytest.mark.asyncio
async def test_diff_non_member_is_denied(configured_app, migrations_pg_dsn: str) -> None:
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    # The outsider has NO membership → require_tenant_member 403.
    token = await _mint_token(seeded["outsider_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    repo_root = tmp_path / "proj"
    shas = _build_doc_repo(repo_root)
    repos[(seeded["tenant_id"], seeded["project_id"])] = repo_root

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": shas["base"], "head": shas["head"]},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text


# ===========================================================================
# RBAC — cross-tenant denial (@cross_tenant)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_diff_cross_tenant_is_404(configured_app, migrations_pg_dsn: str) -> None:
    """A member of tenant B cannot diff tenant A's project docs."""
    app, repos, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])

    repo_a = tmp_path / "a"
    shas = _build_doc_repo(repo_a)
    repos[(a["tenant_id"], a["project_id"])] = repo_a

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Sanity: tenant A's member CAN diff its own doc.
        own = await client.get(
            f"/projects/{a['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": shas["base"], "head": shas["head"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert own.status_code == 200, own.text

        # Tenant B addressing tenant A's project id → RLS hides it → 404,
        # and tenant A's content never leaks.
        foreign = await client.get(
            f"/projects/{a['project_id']}/docs/diff",
            params={"path": _DOC_RELPATH, "base": shas["base"], "head": shas["head"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert foreign.status_code == 404, foreign.text
        assert "Use Docker." not in foreign.text
