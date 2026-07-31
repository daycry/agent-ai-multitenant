"""El orden de `delete_document`: primero la fila, el blob después (prod-04 task_prod_04_11).

El defecto (hallazgo db-3)
--------------------------
`DELETE /knowledge-bases/{kb}/documents/{doc}` borraba el objeto de MinIO ANTES
del `soft_delete`, y el commit ocurre al cerrar el request. La ventana es
pequeña pero el daño es permanente y asimétrico:

* si el commit falla → queda un documento **vivo** en la base de datos cuyo
  binario ya no existe. La UI lo ofrece, el `reindex` no puede reconstruirlo, y
  la fuente no está en ningún sitio. Irrecuperable.
* si el commit va bien → exactamente el mismo resultado que borrando el blob
  después, solo que 200 ms antes.

O sea: el orden antiguo no compraba nada y podía destruir el dato. Y contradecía
la promesa explícita del modelo («soft-deletable so a destructive UI action can
be reverted before the cleanup job kicks in», `db/knowledge.py`): revertir un
soft-delete cuyo blob ya no está no revierte nada.

El arreglo
----------
Quitar el borrado ansioso. El binario lo reclama
`workers.collect_knowledge_garbage` (G-03) cuando vence la gracia
(`knowledge_gc_retention_days`, 30 d por defecto), junto con los chunks y la
fila. Ese barrido YA existía — el plan proponía crear una tarea Celery nueva,
pero era innecesaria: lo único que hacía falta era dejar de adelantarse.

Este módulo prueba la cadena entera, no media: borrar deja el blob, y el GC se
lo lleva cuando toca. Sin la segunda mitad, «no borrar el blob» sería una fuga.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
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
            "Tenant Del",
            "tenant-del-order",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-del-order",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "ordering@kb.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id}


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.fixture()
def kb_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):  # type: ignore[no-untyped-def]
    """La app real + un `ObjectStorage` en memoria (mismo patrón que
    `test_kb_endpoints.py`: el contrato de `MinIOObjectStorage` es idéntico)."""
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


@pytest.mark.asyncio
async def test_deleting_a_document_keeps_the_source_blob_until_the_gc_reclaims_it(
    kb_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    app, storage = kb_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = (
            await client.post("/knowledge-bases", json={"name": "KB Orden"}, headers=headers)
        ).json()["id"]
        upload = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("fuente.txt", b"la unica copia de la fuente", "text/plain")},
            headers=headers,
        )
        assert upload.status_code == 201, upload.text
        doc_id = upload.json()["id"]
        storage_key = upload.json()["source_storage_key"]
        assert await storage.object_exists(key=storage_key) is True

        deleted = await client.delete(
            f"/knowledge-bases/{kb_id}/documents/{doc_id}", headers=headers
        )
        assert deleted.status_code == 204

        # 1. La fila está soft-borrada: el listado ya no la ofrece.
        listed = await client.get(f"/knowledge-bases/{kb_id}/documents", headers=headers)
        assert doc_id not in [d["id"] for d in listed.json()]

    # 2. …pero LA FUENTE SIGUE AHÍ. Éste es el assert que habría fallado antes
    #    de prod-04, y el que impide que un commit fallido destruya el binario.
    assert await storage.object_exists(key=storage_key) is True, (
        "el borrado se llevó el blob por delante del commit: si la transacción "
        "falla, queda un documento vivo sin fuente y el reindex es imposible"
    )
    body = await storage.get_object(key=storage_key)
    assert body == b"la unica copia de la fuente"

    # 3. Y no es una fuga: el GC lo reclama al vencer la gracia. Sin esta mitad,
    #    «no borrar el blob» sería cambiar un bug por otro.
    from workers.maintenance.knowledge_gc import collect_knowledge_garbage

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        result = await collect_knowledge_garbage(sm, storage, retention_days=0)
        assert result["documents_purged"] >= 1
        async with sm() as session:
            rows = (
                await session.execute(
                    text("SELECT count(*) FROM documents WHERE id = :d"), {"d": UUID(doc_id)}
                )
            ).scalar_one()
        assert int(rows) == 0
    finally:
        await engine.dispose()

    assert (
        await storage.object_exists(key=storage_key) is False
    ), "el GC no reclamó el blob del documento vencido: ahora la fuga es al revés"


@pytest.mark.asyncio
async def test_the_endpoint_no_longer_touches_object_storage_at_all() -> None:
    """Guarda estructural, por si alguien «optimiza» reintroduciendo el borrado.

    Un test de comportamiento no distingue «no borra» de «borra y falla en
    silencio»; éste mira el código del endpoint. No es vacuo: si la función
    desapareciera o cambiara de nombre, `_endpoint_body` no la encontraría y el
    assert de longitud rompería.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api-server"
        / "src"
        / "api_server"
        / "routers"
        / "knowledge_bases.py"
    ).read_text(encoding="utf-8")
    marker = "async def delete_document("
    start = source.index(marker)
    end = source.index("\n@router.", start)
    body = source[start:end]
    assert len(body) > 200, "no se localizó el cuerpo del endpoint"
    assert "delete_object" not in body, (
        "delete_document volvió a borrar el blob dentro del request: el binario "
        "muere antes de que el commit garantice que la fila quedó borrada"
    )
    assert "soft_delete(" in body
