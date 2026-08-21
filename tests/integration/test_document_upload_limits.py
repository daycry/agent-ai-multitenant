"""Las dos válvulas de `POST /knowledge-bases/{kb_id}/documents` (prod-13
task_prod13_04 / api-2): tamaño y FORMATO.

El test que el plan nombra. La mitad de tamaño ya estaba entregada (lectura
acotada por trozos, `routers/_uploads.py`); la de formato es la que faltaba, y
lo que la desbloqueó fue dejar de intentar escribir la lista a mano: se le
pregunta a docling-serve al arrancar y se cachea
(`api_server/ingestion/formats.py`).

Lo que estos tests protegen, además del rechazo:

  * que un rechazo por formato NO deje rastro — ni blob en MinIO ni fila en
    `documents`. Un 415 que ya ha guardado el fichero no es una válvula;
  * que un `.txt` con `text/plain` **se siga aceptando**. Es lo que sube la
    suite de KBs de hoy y lo que un allowlist ingenuo rompe en silencio: el
    modo de fallo caro de esta tarea no es aceptar de más, es rechazar de más.
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Upload",
            f"tenant-upload-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "uploader@kb.test",
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
    from api_server.ingestion.formats import reset_supported_formats_cache
    from api_server.storage import (
        InMemoryObjectStorage,
        get_object_storage,
        reset_in_memory_storage,
    )

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    reset_in_memory_storage()
    reset_supported_formats_cache()

    from api_server.main import create_app

    app = create_app()
    storage = InMemoryObjectStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        yield app, storage
    finally:
        app.dependency_overrides.clear()
        reset_supported_formats_cache()
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


async def _kb(client: AsyncClient, headers: dict[str, str]) -> str:
    created = await client.post("/knowledge-bases", json={"name": "KB Upload"}, headers=headers)
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _count_documents(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval("SELECT count(*) FROM documents"))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unparseable_format_is_rejected_before_anything_is_stored(
    configured_app, migrations_pg_dsn: str
) -> None:
    app, storage = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = await _kb(client, headers)
        response = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("backup.zip", b"PK\x03\x04 not a document", "application/zip")},
            headers=headers,
        )

    assert response.status_code == 415, response.text
    assert "zip" in response.json()["detail"]
    # El rechazo llega ANTES del almacenamiento: sin esto el usuario se enteraba
    # minutos después, con el fichero ya en MinIO y la fila creada.
    assert await storage.list_objects(prefix="") == []
    assert await _count_documents(migrations_pg_dsn) == 0


@pytest.mark.asyncio
async def test_plain_text_and_pdf_are_still_accepted(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El contorno que un allowlist ingenuo rompe: `.txt` no es un `InputFormat`
    de Docling con nombre propio, pero el texto plano ES markdown y hoy se sube
    sin problema. Rechazarlo sería la regresión, no el arreglo."""
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = await _kb(client, headers)
        for filename, content_type in (
            ("notas.txt", "text/plain"),
            ("informe.pdf", "application/pdf"),
            # El navegador no reconoció la extensión y mandó el tipo opaco: la
            # extensión decide, y decide que sí.
            ("hoja.xlsx", "application/octet-stream"),
        ):
            response = await client.post(
                f"/knowledge-bases/{kb_id}/documents",
                files={"file": (filename, b"contenido", content_type)},
                headers=headers,
            )
            assert response.status_code == 201, f"{filename}: {response.text}"


@pytest.mark.asyncio
async def test_the_endpoint_honours_what_docling_serve_answered(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La lista no está escrita en el router: viene de la caché que el arranque
    primea contra docling-serve. Con un servicio que sólo declara `pdf`, el
    `.docx` que el respaldo aceptaba pasa a rechazarse."""
    import httpx
    from api_server.ingestion.formats import refresh_supported_formats

    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }

    openapi = {"components": {"schemas": {"InputFormat": {"enum": ["pdf"]}}}}
    await refresh_supported_formats(
        base_url="http://docling-serve:5001",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=openapi))
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = await _kb(client, headers)
        docx = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("contrato.docx", b"x", "application/octet-stream")},
            headers=headers,
        )
        pdf = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("contrato.pdf", b"x", "application/pdf")},
            headers=headers,
        )

    assert docx.status_code == 415, docx.text
    assert pdf.status_code == 201, pdf.text


# ---------------------------------------------------------------------------
# Tamaño
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_oversized_upload_is_a_413_and_stores_nothing(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el tope bajado a 1 KiB para no mover 50 MiB en un test. Lo que se
    comprueba es la propiedad, no la constante."""
    from api_server.routers import knowledge_bases as kb_router

    monkeypatch.setattr(kb_router, "MAX_UPLOAD_BYTES", 1024)

    app, storage = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = await _kb(client, headers)
        response = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("grande.txt", b"x" * (4 * 1024 * 1024), "text/plain")},
            headers=headers,
        )

    assert response.status_code == 413, response.text
    assert await storage.list_objects(prefix="") == []
    assert await _count_documents(migrations_pg_dsn) == 0


@pytest.mark.asyncio
async def test_a_file_exactly_at_the_cap_is_accepted(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El tope es INCLUSIVO, y el margen de multipart existe justo para que un
    fichero de exactamente el tope no se lleve un 413 por las boundaries."""
    from api_server.routers import knowledge_bases as kb_router

    monkeypatch.setattr(kb_router, "MAX_UPLOAD_BYTES", 1024)

    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_id = await _kb(client, headers)
        response = await client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("justo.txt", b"x" * 1024, "text/plain")},
            headers=headers,
        )

    assert response.status_code == 201, response.text
    assert response.json()["source_size_bytes"] == 1024
