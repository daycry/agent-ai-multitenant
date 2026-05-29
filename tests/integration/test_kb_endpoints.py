"""Integration tests for the /knowledge-bases endpoints (Plan 04 task_04_09).

Drives the three resource families end-to-end:

  - KB CRUD,
  - KB ↔ Project grants (M:N),
  - Document upload (multipart) + list + delete.

The `ObjectStorage` dependency is overridden with the in-memory
implementation so the tests don't need MinIO up. The contract that
the real `MinIOObjectStorage` honours is identical (same `put_object`
/ `get_object` / `delete_object`).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    other_project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant KB",
            "tenant-kb",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-kb-endpoints",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@kb.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            project_id,
            tenant_id,
            "Project A",
            other_project_id,
            tenant_id,
            "Project B",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "project_id": project_id,
        "other_project_id": other_project_id,
    }


# ---------------------------------------------------------------------------
# App fixture — overrides ObjectStorage with the in-memory impl
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
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
    from api_server.storage import (
        InMemoryObjectStorage,
        get_object_storage,
        reset_in_memory_storage,
    )

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    reset_in_memory_storage()

    from api_server.main import create_app

    app = create_app()
    storage = InMemoryObjectStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        yield app, storage
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# KB CRUD
# ===========================================================================
@pytest.mark.asyncio
async def test_kb_crud_round_trip(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create.
        create = await client.post(
            "/knowledge-bases",
            json={"name": "KB One", "description": "first"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        kb_id = create.json()["id"]
        assert create.json()["embedding_model_id"] == "nomic-embed-text-v1.5"

        # List.
        listed = await client.get("/knowledge-bases", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["id"] == kb_id

        # Get single.
        single = await client.get(f"/knowledge-bases/{kb_id}", headers=headers)
        assert single.status_code == 200
        assert single.json()["name"] == "KB One"

        # Update.
        updated = await client.put(
            f"/knowledge-bases/{kb_id}",
            json={"description": "first (renamed)"},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "first (renamed)"

        # Delete (soft).
        deleted = await client.delete(f"/knowledge-bases/{kb_id}", headers=headers)
        assert deleted.status_code == 204

        # Listing again is empty.
        empty = await client.get("/knowledge-bases", headers=headers)
        assert empty.json() == []


@pytest.mark.asyncio
async def test_kb_name_unique_per_tenant(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/knowledge-bases", json={"name": "KB Dup"}, headers=headers)
        assert first.status_code == 201
        second = await client.post("/knowledge-bases", json={"name": "KB Dup"}, headers=headers)
        assert second.status_code == 409


# ===========================================================================
# KB ↔ Project grants
# ===========================================================================
@pytest.mark.asyncio
async def test_grant_and_revoke_kb_for_project(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = await client.post("/knowledge-bases", json={"name": "KB Grant"}, headers=headers)
        kb_id = kb.json()["id"]

        # Before granting → project sees nothing.
        before = await client.get(
            f"/projects/{seeded['project_id']}/knowledge-bases", headers=headers
        )
        assert before.json() == []

        # Grant.
        grant = await client.post(
            f"/knowledge-bases/{kb_id}/projects",
            json={"project_id": str(seeded["project_id"])},
            headers=headers,
        )
        assert grant.status_code == 201

        # Project now sees it.
        after = await client.get(
            f"/projects/{seeded['project_id']}/knowledge-bases", headers=headers
        )
        assert [r["id"] for r in after.json()] == [kb_id]
        # The other project still sees nothing.
        other = await client.get(
            f"/projects/{seeded['other_project_id']}/knowledge-bases", headers=headers
        )
        assert other.json() == []

        # Re-grant is idempotent.
        regrant = await client.post(
            f"/knowledge-bases/{kb_id}/projects",
            json={"project_id": str(seeded["project_id"])},
            headers=headers,
        )
        assert regrant.status_code == 201

        # Revoke.
        revoke = await client.delete(
            f"/knowledge-bases/{kb_id}/projects/{seeded['project_id']}", headers=headers
        )
        assert revoke.status_code == 204
        after_revoke = await client.get(
            f"/projects/{seeded['project_id']}/knowledge-bases", headers=headers
        )
        assert after_revoke.json() == []


# ===========================================================================
# Document upload (MinIO via InMemoryObjectStorage)
# ===========================================================================
@pytest.mark.asyncio
async def test_upload_document_writes_to_storage_and_persists_row(
    configured_app, migrations_pg_dsn: str
) -> None:
    app, storage = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = await client.post("/knowledge-bases", json={"name": "KB Upload"}, headers=headers)
        kb_id = kb.json()["id"]

        upload = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("hello.txt", b"Hello, RAG world.", "text/plain")},
            data={"title": "Greeting"},
            headers=headers,
        )
        assert upload.status_code == 201, upload.text
        body = upload.json()
        assert body["status"] == "pending"
        assert body["source_filename"] == "hello.txt"
        assert body["source_mime_type"] == "text/plain"
        assert body["source_size_bytes"] == len(b"Hello, RAG world.")
        assert body["title"] == "Greeting"
        # Key is the canonical tenant-prefixed path.
        assert body["source_storage_key"].startswith(f"kb/{seeded['tenant_id']}/{kb_id}/")
        assert body["source_storage_key"].endswith("/hello.txt")

        # The bytes really landed in storage.
        assert await storage.object_exists(key=body["source_storage_key"]) is True
        retrieved = await storage.get_object(key=body["source_storage_key"])
        assert retrieved == b"Hello, RAG world."


@pytest.mark.asyncio
async def test_upload_enqueues_ingestion(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan 06.11: a successful upload must hand the document to the
    ingestion worker (best-effort send_task by Celery name)."""
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    enqueued: list[str] = []

    async def _fake_enqueue(document_id: object) -> bool:
        enqueued.append(str(document_id))
        return True

    import api_server.routers.knowledge_bases as kb_router

    monkeypatch.setattr(kb_router, "enqueue_ingestion", _fake_enqueue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = await client.post("/knowledge-bases", json={"name": "KB Enq"}, headers=headers)
        kb_id = kb.json()["id"]
        upload = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("a.txt", b"hi", "text/plain")},
            headers=headers,
        )
        assert upload.status_code == 201, upload.text
        doc_id = upload.json()["id"]

    assert enqueued == [doc_id]


@pytest.mark.asyncio
async def test_empty_upload_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = await client.post("/knowledge-bases", json={"name": "KB Empty"}, headers=headers)
        kb_id = kb.json()["id"]
        resp = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("empty.txt", b"", "text/plain")},
            headers=headers,
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_and_get_documents(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = (
            await client.post("/knowledge-bases", json={"name": "KB List"}, headers=headers)
        ).json()["id"]

        # Upload two documents.
        for filename in ("a.txt", "b.txt"):
            await client.post(
                f"/knowledge-bases/{kb_id}/documents",
                files={"file": (filename, b"x" * 10, "text/plain")},
                headers=headers,
            )

        listed = await client.get(f"/knowledge-bases/{kb_id}/documents", headers=headers)
        assert listed.status_code == 200
        assert {d["source_filename"] for d in listed.json()} == {"a.txt", "b.txt"}

        # GET single.
        doc_id = listed.json()[0]["id"]
        single = await client.get(f"/knowledge-bases/{kb_id}/documents/{doc_id}", headers=headers)
        assert single.status_code == 200
        assert single.json()["id"] == doc_id


@pytest.mark.asyncio
async def test_reindex_resets_document_and_reenqueues(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan 06.11: re-index resets an indexed/failed doc to `pending`,
    drops its stale chunks, clears the error, and re-enqueues."""
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    enqueued: list[str] = []

    async def _fake_enqueue(document_id: object) -> bool:
        enqueued.append(str(document_id))
        return True

    import api_server.routers.knowledge_bases as kb_router

    monkeypatch.setattr(kb_router, "enqueue_ingestion", _fake_enqueue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = (
            await client.post("/knowledge-bases", json={"name": "KB Reindex"}, headers=headers)
        ).json()["id"]
        doc_id = (
            await client.post(
                f"/knowledge-bases/{kb_id}/documents",
                files={"file": ("r.txt", b"reindex me", "text/plain")},
                headers=headers,
            )
        ).json()["id"]

    # Simulate a finished (failed) ingestion with stale chunks.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE documents SET status = 'failed', error_message = 'boom',"
            " indexed_at = now() WHERE id = $1",
            UUID(doc_id),
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'stale chunk')",
            uuid4(),
            seeded["tenant_id"],
            UUID(doc_id),
        )
    finally:
        await conn.close()

    enqueued.clear()  # drop the enqueue the upload itself fired
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/knowledge-bases/{kb_id}/documents/{doc_id}/reindex", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"

    assert enqueued == [doc_id]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, error_message FROM documents WHERE id = $1", UUID(doc_id)
        )
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", UUID(doc_id)
        )
    finally:
        await conn.close()
    assert row["status"] == "pending"
    assert row["error_message"] is None
    assert n_chunks == 0


@pytest.mark.asyncio
async def test_reindex_missing_document_404(configured_app, migrations_pg_dsn: str) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = (
            await client.post("/knowledge-bases", json={"name": "KB R404"}, headers=headers)
        ).json()["id"]
        resp = await client.post(
            f"/knowledge-bases/{kb_id}/documents/{uuid4()}/reindex", headers=headers
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_drops_blob_and_soft_deletes_row(
    configured_app, migrations_pg_dsn: str
) -> None:
    app, storage = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = (
            await client.post("/knowledge-bases", json={"name": "KB Del"}, headers=headers)
        ).json()["id"]
        upload = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("to-delete.txt", b"bye", "text/plain")},
            headers=headers,
        )
        doc_id = upload.json()["id"]
        storage_key = upload.json()["source_storage_key"]

        assert await storage.object_exists(key=storage_key) is True

        deleted = await client.delete(
            f"/knowledge-bases/{kb_id}/documents/{doc_id}", headers=headers
        )
        assert deleted.status_code == 204

        # Blob is gone.
        assert await storage.object_exists(key=storage_key) is False
        # Row is soft-deleted — listing skips it.
        listed = await client.get(f"/knowledge-bases/{kb_id}/documents", headers=headers)
        assert doc_id not in [d["id"] for d in listed.json()]
