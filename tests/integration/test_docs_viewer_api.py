"""Integration tests for the docs-viewer API (Plan 07 task_07_D_api / 07_18).

Drives the three project-scoped endpoints end-to-end through the real app +
Postgres schema:

  * ``GET /projects/{id}/docs/tree``    — canonical folders → ``.md`` files,
    read from a tmp docs root (the ``get_docs_root_resolver`` dependency is
    overridden so no real worktree is needed);
  * ``GET /projects/{id}/docs/content`` — RAW markdown by repo-relative path,
    + path-traversal rejection (``..``, absolute);
  * ``GET /projects/{id}/docs/search``  — full-text over the project's
    internal-docs KB chunks (seeded via ``sync_project_docs`` + the
    deterministic ``HashEmbedder`` — no Ollama), ranked with snippets.

RBAC (task_07_18): a non-member / cross-tenant caller gets 404 for every
surface and search never leaks another tenant's docs
(``@pytest.mark.cross_tenant``).
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
            "Tenant DV",
            "tenant-dv",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-dv",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'member@dv.test', 'h'), ($2, 'outsider@dv.test', 'h')",
            member_id,
            outsider_id,
        )
        # Only `member_id` is a member of the tenant; `outsider_id` has no
        # membership row at all.
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
            "Project DV",
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
            "tenant-a-dv",
            b_tenant,
            "Tenant B",
            "tenant-b-dv",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-dv-xt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@dv.xt', 'h'), ($2, 'b@dv.xt', 'h')",
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
    """Lay down a small canonical ``docs/`` tree under ``root``."""
    docs = root / "docs"
    (docs / "01-overview").mkdir(parents=True)
    (docs / "03-guides").mkdir(parents=True)
    # A non-canonical top-level dir + a non-markdown file are dropped by the
    # tree walk — assert that below.
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


async def _sync_internal_docs(
    admin_database_url: str, *, project_id: UUID, tenant_id: UUID, docs_root: Path
) -> None:
    """Populate the project's internal-docs KB chunks (so full-text search
    has something to find), using the deterministic fake embedder."""
    from api_server.docs_structure.kb_sync import sync_project_docs
    from api_server.ingestion.embeddings import HashEmbedder
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await sync_project_docs(
            session,
            project_id=project_id,
            tenant_id=tenant_id,
            docs_root=docs_root,
            embedder=HashEmbedder(),
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


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

    # Map each (tenant, project) to its own tmp docs root. Tests register the
    # roots they seed; an unseeded project resolves to a missing dir → empty
    # tree, which is the production "worktree not materialised yet" behaviour.
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
# Tree + content happy path
# ===========================================================================
@pytest.mark.asyncio
async def test_tree_lists_canonical_folders_and_markdown(
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
        resp = await client.get(f"/projects/{seeded['project_id']}/docs/tree", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == str(seeded["project_id"])

        # Two canonical folders surfaced (assets/ is NOT canonical → dropped).
        folder_names = {f["name"] for f in body["folders"]}
        assert folder_names == {"01-overview", "03-guides"}
        # Top-level index.md surfaced.
        assert {f["relpath"] for f in body["files"]} == {"index.md"}

        # The non-markdown asset never appears anywhere in the tree.
        flat = resp.text
        assert "diagram.png" not in flat
        assert "assets" not in flat

        # Folder files carry the repo-relative path.
        guides = next(f for f in body["folders"] if f["name"] == "03-guides")
        assert {f["relpath"] for f in guides["files"]} == {"03-guides/setup.md"}
        assert set(files)  # sanity: fixture wrote files


@pytest.mark.asyncio
async def test_content_returns_raw_markdown(configured_app, migrations_pg_dsn: str) -> None:
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
            f"/projects/{seeded['project_id']}/docs/content",
            params={"path": "03-guides/setup.md"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["relpath"] == "03-guides/setup.md"
        assert body["content"] == files["03-guides/setup.md"]
        assert body["size_bytes"] == len(files["03-guides/setup.md"].encode("utf-8"))

        # A windows-style separator normalises to the same doc.
        win = await client.get(
            f"/projects/{seeded['project_id']}/docs/content",
            params={"path": "03-guides\\setup.md"},
            headers=headers,
        )
        assert win.status_code == 200, win.text
        assert win.json()["content"] == files["03-guides/setup.md"]


@pytest.mark.asyncio
async def test_content_missing_doc_404(configured_app, migrations_pg_dsn: str) -> None:
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
            f"/projects/{seeded['project_id']}/docs/content",
            params={"path": "04-reference/nope.md"},
            headers=headers,
        )
        assert resp.status_code == 404, resp.text


# ===========================================================================
# Path traversal blocked
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil_path",
    [
        "../../../../etc/passwd",
        "../secret.md",
        "03-guides/../../escape.md",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
        "..\\..\\windows.md",
    ],
)
async def test_content_path_traversal_blocked(
    configured_app, migrations_pg_dsn: str, evil_path: str
) -> None:
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"
    # Drop a real secret OUTSIDE the docs root to prove it cannot be reached.
    (proj_root / "secret.md").write_text("# TOP SECRET\n", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/content",
            params={"path": evil_path},
            headers=headers,
        )
        # A traversal attempt is a 400 (or 404 for a non-.md target); never
        # 200, and the secret content never leaks.
        assert resp.status_code in (400, 404), resp.text
        assert "TOP SECRET" not in resp.text


# ===========================================================================
# Full-text search returns ranked snippets
# ===========================================================================
@pytest.mark.asyncio
async def test_search_returns_ranked_snippets(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    # Build the internal-docs KB chunks so BM25 has rows to rank.
    await _sync_internal_docs(
        admin_database_url,
        project_id=seeded["project_id"],
        tenant_id=seeded["tenant_id"],
        docs_root=proj_root,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/search",
            params={"q": "Docker"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == "Docker"
        hits = body["hits"]
        assert len(hits) >= 1
        # Ranked 1..n in order.
        assert [h["rank"] for h in hits] == list(range(1, len(hits) + 1))
        # The hit points at the setup doc and carries a snippet mentioning
        # the matched term.
        top = hits[0]
        assert top["relpath"] == "03-guides/setup.md"
        assert "Docker" in top["snippet"]
        assert top["chunk_id"]
        assert top["document_id"]

        # A query with no match returns an empty hit list (not an error).
        empty = await client.get(
            f"/projects/{seeded['project_id']}/docs/search",
            params={"q": "kuberneteszzz"},
            headers=headers,
        )
        assert empty.status_code == 200, empty.text
        assert empty.json()["hits"] == []


# ===========================================================================
# RBAC — non-member of the tenant is denied
# ===========================================================================
@pytest.mark.asyncio
async def test_non_member_is_denied(configured_app, migrations_pg_dsn: str) -> None:
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    # The outsider has NO membership in the tenant → require_tenant_member 403.
    token = await _mint_token(seeded["outsider_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    roots[(seeded["tenant_id"], seeded["project_id"])] = proj_root / "docs"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tree = await client.get(f"/projects/{seeded['project_id']}/docs/tree", headers=headers)
        assert tree.status_code == 403, tree.text
        content = await client.get(
            f"/projects/{seeded['project_id']}/docs/content",
            params={"path": "index.md"},
            headers=headers,
        )
        assert content.status_code == 403, content.text
        search = await client.get(
            f"/projects/{seeded['project_id']}/docs/search",
            params={"q": "Docker"},
            headers=headers,
        )
        assert search.status_code == 403, search.text


# ===========================================================================
# RBAC — cross-tenant denial (@cross_tenant)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_docs_are_not_visible(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """A member of tenant B cannot read or search tenant A's project docs."""
    app, roots, tmp_path = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])

    # Tenant A's project gets a docs tree + a synced internal-docs KB.
    root_a = tmp_path / "a"
    _write_docs_tree(root_a)
    roots[(a["tenant_id"], a["project_id"])] = root_a / "docs"
    await _sync_internal_docs(
        admin_database_url,
        project_id=a["project_id"],
        tenant_id=a["tenant_id"],
        docs_root=root_a,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Sanity: tenant A's member CAN read its own tree.
        own = await client.get(
            f"/projects/{a['project_id']}/docs/tree",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert own.status_code == 200, own.text

        # Tenant B addressing tenant A's project id → RLS hides it → 404.
        for suffix, params in (
            ("/tree", None),
            ("/content", {"path": "index.md"}),
            ("/search", {"q": "Docker"}),
        ):
            foreign = await client.get(
                f"/projects/{a['project_id']}/docs{suffix}",
                params=params,
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert foreign.status_code == 404, (suffix, foreign.text)

        # And searching B's OWN project (which has no synced docs) returns no
        # hits from A even though A's chunks match the query — BM25's KB
        # visibility filter scopes it to B's grants.
        b_search = await client.get(
            f"/projects/{b['project_id']}/docs/search",
            params={"q": "Docker"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_search.status_code == 200, b_search.text
        assert b_search.json()["hits"] == []
