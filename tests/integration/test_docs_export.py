"""Integration tests for docs export — ZIP / PDF (Plan 07 task_07_17).

Drives the two export endpoints end-to-end through the real app + Postgres
schema, with the on-disk docs root pointed at a tmp dir (the
``get_docs_root_resolver`` dependency is overridden — no real worktree):

  * ``GET /projects/{id}/docs/export/zip`` — a deterministic stdlib ``zipfile``
    bundle of the project's canonical ``docs/`` ``.md`` tree. The test opens
    the returned archive and asserts it contains exactly the expected ``.md``
    entries (and never the non-markdown asset / non-canonical dir).
  * ``GET /projects/{id}/docs/export/pdf`` — PDF rendering is deliberately
    **not configured** offline (no markdown→PDF toolchain in the runtime; we
    do not pull a heavy / native dependency just for this), so the endpoint
    returns a documented ``501 Not Implemented`` pointing at the ZIP export.
    The test asserts the 501 contract (the path the implementation delivers).

RBAC (task_07_18 contract reused): a cross-tenant caller gets ``404`` for
both export surfaces — a member of tenant B can never download tenant A's docs
(``@pytest.mark.cross_tenant``).
"""

from __future__ import annotations

import asyncio
import io
import zipfile
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
# Seed helpers
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
            "Tenant EX",
            "tenant-ex",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ex",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'member@ex.test', 'h'), ($2, 'outsider@ex.test', 'h')",
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
            "Project EX",
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
            "tenant-a-ex",
            b_tenant,
            "Tenant B",
            "tenant-b-ex",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ex-xt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@ex.xt', 'h'), ($2, 'b@ex.xt', 'h')",
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


def _write_docs_tree(root: Path) -> dict[str, str]:
    """Lay down a small canonical ``docs/`` tree under ``root``.

    Mirrors the viewer-API fixture: two canonical folders + a top-level
    ``index.md``, plus a non-canonical ``assets/`` dir and a ``.png`` that the
    canonical tree walk (and therefore the export bundle) must NOT include.
    """
    docs = root / "docs"
    (docs / "01-overview").mkdir(parents=True)
    (docs / "03-guides").mkdir(parents=True)
    (docs / "assets").mkdir(parents=True)
    (docs / "assets" / "diagram.png").write_text("not markdown", encoding="utf-8")
    files = {
        "01-overview/README.md": "# Overview\n\nWhat this project is about.\n",
        "03-guides/setup.md": (
            "# Setup\n\nInstall the platform.\n\n## Prereqs\n\nDocker and Python.\n"
        ),
        "index.md": "# Index\n\nThe top-level landing page.\n",
    }
    for relpath, content in files.items():
        (docs / relpath).write_text(content, encoding="utf-8")
    return files


# ---------------------------------------------------------------------------
# App fixture — overrides the docs-root resolver to point at a tmp dir
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
    from api_server.routers.docs_viewer import get_docs_root_resolver

    app = create_app()

    roots: dict[tuple[UUID, UUID], Path] = {}

    def _resolver():
        def _resolve(tenant_id: UUID, project_id: UUID) -> Path:
            return roots.get(
                (tenant_id, project_id),
                tmp_path / "missing" / str(project_id) / "docs",
            )

        return _resolve

    app.dependency_overrides[get_docs_root_resolver] = _resolver
    try:
        yield app, roots, tmp_path
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


# ===========================================================================
# ZIP export — happy path
# ===========================================================================
@pytest.mark.asyncio
async def test_export_zip_contains_expected_markdown_entries(
    configured_app, migrations_pg_dsn: str
) -> None:
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    files = _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/zip", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
        assert str(seeded["project_id"]) in resp.headers["content-disposition"]

        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(archive.namelist())
        expected = {f"docs/{rel}" for rel in files}
        assert names == expected

        # The non-markdown asset + non-canonical dir never make it in.
        assert not any("diagram.png" in n for n in names)
        assert not any("assets" in n for n in names)

        # A bundled file's bytes match the on-disk source verbatim (read the
        # actual file bytes — on Windows ``write_text`` translates ``\n`` to
        # ``\r\n``, so compare against what is really on disk, not the source
        # string, to prove the export is a byte-faithful copy).
        on_disk = (proj_root / "docs" / "03-guides" / "setup.md").read_bytes()
        assert archive.read("docs/03-guides/setup.md") == on_disk

        # The archive is valid (no corrupt entries).
        assert archive.testzip() is None


@pytest.mark.asyncio
async def test_export_zip_is_deterministic(configured_app, migrations_pg_dsn: str) -> None:
    """Same docs tree → byte-identical archive across calls (reproducible)."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/zip", headers=headers
        )
        second = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/zip", headers=headers
        )
        assert first.status_code == second.status_code == 200
        assert first.content == second.content


@pytest.mark.asyncio
async def test_export_zip_empty_tree_is_valid_empty_archive(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A project whose worktree is not materialised → a valid empty ZIP."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    # No docs root registered → resolver points at a missing dir → empty tree.

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/zip", headers=headers
        )
        assert resp.status_code == 200, resp.text
        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        assert archive.namelist() == []


# ===========================================================================
# PDF export — documented 501 deferral
# ===========================================================================
@pytest.mark.asyncio
async def test_export_pdf_returns_501_not_configured(
    configured_app, migrations_pg_dsn: str
) -> None:
    """PDF rendering is not configured offline → a clear 501 (no heavy dep)."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/pdf",
            params={"path": "03-guides/setup.md"},
            headers=headers,
        )
        assert resp.status_code == 501, resp.text
        assert "not configured" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil_path",
    [
        "../../../../etc/passwd",
        "03-guides/../../escape.md",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
    ],
)
async def test_export_pdf_path_traversal_blocked_before_501(
    configured_app, migrations_pg_dsn: str, evil_path: str
) -> None:
    """A hostile ``path`` is a 400 (validated) — the 501 deferral never weakens
    path safety, and a real secret outside the docs root is never reached."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"
    (proj_root / "secret.md").write_text("# TOP SECRET\n", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/pdf",
            params={"path": evil_path},
            headers=headers,
        )
        assert resp.status_code in (400, 404), resp.text
        assert "TOP SECRET" not in resp.text


# ===========================================================================
# RBAC — non-member of the tenant is denied
# ===========================================================================
@pytest.mark.asyncio
async def test_export_non_member_is_denied(configured_app, migrations_pg_dsn: str) -> None:
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["outsider_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        zip_resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/zip", headers=headers
        )
        assert zip_resp.status_code == 403, zip_resp.text
        pdf_resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/export/pdf",
            params={"path": "index.md"},
            headers=headers,
        )
        assert pdf_resp.status_code == 403, pdf_resp.text


# ===========================================================================
# RBAC — cross-tenant denial (@cross_tenant)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_export_is_not_visible(configured_app, migrations_pg_dsn: str) -> None:
    """A member of tenant B cannot export tenant A's project docs."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])

    root_a = tmp_path / "a"
    _write_docs_tree(root_a)
    roots[(a["tenant_id"], a["project_id"])] = root_a / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Sanity: tenant A's member CAN export its own docs.
        own = await client.get(
            f"/projects/{a['project_id']}/docs/export/zip",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert own.status_code == 200, own.text
        assert zipfile.ZipFile(io.BytesIO(own.content)).namelist()

        # Tenant B addressing tenant A's project id → RLS hides it → 404 for
        # both export surfaces, and no archive bytes leak.
        foreign_zip = await client.get(
            f"/projects/{a['project_id']}/docs/export/zip",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert foreign_zip.status_code == 404, foreign_zip.text
        assert b"PK" not in foreign_zip.content  # not a ZIP payload

        foreign_pdf = await client.get(
            f"/projects/{a['project_id']}/docs/export/pdf",
            params={"path": "index.md"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert foreign_pdf.status_code == 404, foreign_pdf.text
