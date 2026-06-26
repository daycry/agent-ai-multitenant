"""Agente global ve el contexto de proyecto task-scoped (Plan 06.17 task_06_17_13 / ADR 0054).

Antes de esta tarea existía una asimetría real entre escritura y lectura para un
agente **global** (sin ``project_id`` propio) que ejecuta una tarea **de un
proyecto**:

  * RAG: ``rag_search_endpoint`` devolvía ``hits=[]`` cuando ``agent.project_id``
    era ``None`` (``internal_agent.py:426``), de modo que un agente global no veía
    NUNCA los chunks del proyecto de la tarea;
  * Memoria: ``memory_recall`` resolvía ``project_id = agent.project_id`` (None),
    así que el scope ``project_shared`` no recuperaba nada del proyecto de la
    tarea.

El Memorizer en cambio ESCRIBE bajo ``task.project_id`` — write ≠ read.

El ADR 0054 (opción B) resuelve el ``project_id`` de LECTURA como el de la tarea
en curso (``task.project_id``) cuando el agente es global, **estrictamente
tenant-safe** (el ``task_id`` lo porta el token del runtime; el proyecto se
resuelve server-side validando ``task.tenant_id == principal.tenant_id``) y
acotado a ese único proyecto. Activable por ``platform_settings``
(``memory.global_agent_uses_task_project``, default ON).

Este módulo verifica, contra Postgres real con RLS:

  1. con el flag ON, un agente global en una tarea de proyecto recibe ``hits>0``
     en ``rag-search`` y lee memoria ``project_shared`` del proyecto de la tarea;
  2. **aislamiento cross-tenant**: un token de tenant B cuyo ``task_id`` apunta a
     la tarea de tenant A NO ve el proyecto de A (el proyecto efectivo no se
     resuelve porque la tarea no pertenece al tenant del token);
  3. **aislamiento cross-project**: el agente global solo ve el ÚNICO proyecto de
     la tarea, no otro proyecto del mismo tenant;
  4. con el flag OFF, el comportamiento estricto antiguo: el agente global no ve
     conocimiento ni memoria de proyecto (``hits=[]``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion.embeddings import HashEmbedder
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Chunks del proyecto de tenant A (KB granted a su proyecto).
_A_PROJECT_CHUNKS = [
    "El proyecto Alpha usa asyncpg y pgvector para el acceso async.",
    "Reciprocal Rank Fusion mezcla las dos listas ordenadas.",
    "Las migraciones corren bajo el rol migrations_user con BYPASSRLS.",
]
# Memoria project_shared del proyecto de tenant A.
_A_PROJECT_MEMORY = "Decisión de arquitectura: el orquestador asigna tareas por load-balanced."
# Chunks de OTRO proyecto del mismo tenant A (no debe verlos el agente global de
# la tarea del primer proyecto).
_A_OTHER_CHUNKS = [
    "El proyecto Gamma usa una pila completamente distinta y secreta.",
]


async def _seed_two_tenants(dsn: str) -> dict[str, Any]:
    """Siembra dos tenants aislados:

      * tenant A: proyecto P1 (con KB+chunks+memoria project_shared) + proyecto
        P2 (otro proyecto con sus propios chunks) + un agente GLOBAL
        (``global_tenant_template``, ``project_id=NULL``) + una tarea de P1
        asignada a ese agente;
      * tenant B: proyecto Q + agente global + tarea de Q.

    Todo el seed usa el rol BYPASSRLS (migrations_user) para poder insertar
    cross-tenant en una sola pasada."""
    embedder = HashEmbedder()

    a = {
        "tenant_id": uuid4(),
        "p1_id": uuid4(),
        "p2_id": uuid4(),
        "kb_id": uuid4(),
        "kb_other_id": uuid4(),
        "doc_id": uuid4(),
        "doc_other_id": uuid4(),
        "agent_id": uuid4(),
        "task_id": uuid4(),
    }
    b = {
        "tenant_id": uuid4(),
        "q_id": uuid4(),
        "agent_id": uuid4(),
        "task_id": uuid4(),
    }

    a_vecs = await embedder.embed(_A_PROJECT_CHUNKS)
    a_other_vecs = await embedder.embed(_A_OTHER_CHUNKS)
    mem_vec = (await embedder.embed([_A_PROJECT_MEMORY]))[0]

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, tasks, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)" " VALUES ($1,$2,$3),($4,$5,$6),($7,$8,$9)",
            a["tenant_id"],
            "Tenant A",
            "tenant-a-gctx",
            b["tenant_id"],
            "Tenant B",
            "tenant-b-gctx",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-gctx",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name)" " VALUES ($1,$2,$3),($4,$5,$6),($7,$8,$9)",
            a["p1_id"],
            a["tenant_id"],
            "Alpha P1",
            a["p2_id"],
            a["tenant_id"],
            "Alpha P2",
            b["q_id"],
            b["tenant_id"],
            "Beta Q",
        )
        # KBs + grants de tenant A.
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name)" " VALUES ($1,$2,$3),($4,$5,$6)",
            a["kb_id"],
            a["tenant_id"],
            "Alpha KB",
            a["kb_other_id"],
            a["tenant_id"],
            "Gamma KB",
        )
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id)"
            " VALUES ($1,$2,$3),($4,$5,$6)",
            a["kb_id"],
            a["p1_id"],
            a["tenant_id"],
            a["kb_other_id"],
            a["p2_id"],
            a["tenant_id"],
        )
        for doc_id, kb_id in ((a["doc_id"], a["kb_id"]), (a["doc_other_id"], a["kb_other_id"])):
            await conn.execute(
                "INSERT INTO documents"
                " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
                "  source_storage_key, source_size_bytes, status)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'indexed')",
                doc_id,
                a["tenant_id"],
                kb_id,
                "doc",
                "doc.md",
                "text/markdown",
                f"kb/{a['tenant_id']}/{kb_id}/{doc_id}/source.md",
                1000,
            )

        def _vec(v: Any) -> str:
            return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

        for ordinal, (text_c, vec) in enumerate(zip(_A_PROJECT_CHUNKS, a_vecs, strict=True)):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
                " VALUES ($1,$2,$3,$4,$5,$6::vector)",
                uuid4(),
                a["tenant_id"],
                a["doc_id"],
                ordinal,
                text_c,
                _vec(vec),
            )
        for ordinal, (text_c, vec) in enumerate(zip(_A_OTHER_CHUNKS, a_other_vecs, strict=True)):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
                " VALUES ($1,$2,$3,$4,$5,$6::vector)",
                uuid4(),
                a["tenant_id"],
                a["doc_other_id"],
                ordinal,
                text_c,
                _vec(vec),
            )
        # Memoria project_shared de P1.
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, project_id, type, content, embedding)"
            " VALUES ($1,$2,'project_shared',$3,'semantic',$4,$5::vector)",
            uuid4(),
            a["tenant_id"],
            a["p1_id"],
            _A_PROJECT_MEMORY,
            _vec(mem_vec),
        )

        # Agentes GLOBALES (project_id NULL, scope global_tenant_template).
        for d in (a, b):
            await conn.execute(
                "INSERT INTO agents"
                " (id, tenant_id, project_id, name, role, system_prompt,"
                "  memory_scope, scope)"
                " VALUES ($1,$2,NULL,$3,'backend_dev',$4,'project_shared',"
                "         'global_tenant_template')",
                d["agent_id"],
                d["tenant_id"],
                "Global Agent",
                "You are a global agent.",
            )
        # Tareas de proyecto, asignadas a los agentes globales.
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, assigned_agent_id)"
            " VALUES ($1,$2,$3,$4,'in_progress',$5),($6,$7,$8,$9,'in_progress',$10)",
            a["task_id"],
            a["tenant_id"],
            a["p1_id"],
            "Tarea de P1",
            a["agent_id"],
            b["task_id"],
            b["tenant_id"],
            b["q_id"],
            "Tarea de Q",
            b["agent_id"],
        )
    finally:
        await conn.close()

    return {"a": a, "b": b}


async def _set_flag(dsn: str, *, enabled: bool) -> None:
    from api_server.db.platform_settings import GLOBAL_AGENT_USES_TASK_PROJECT_KEY

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value)"
            " VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            GLOBAL_AGENT_USES_TASK_PROJECT_KEY,
            "true" if enabled else "false",
        )
    finally:
        await conn.close()


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

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app
    from api_server.routers.docs_viewer import get_query_embedder

    app = create_app()

    # Mismo HashEmbedder determinista que sembró los vectores → ranking
    # reproducible sin Ollama.
    async def _yield_hash():
        yield HashEmbedder()

    app.dependency_overrides[get_query_embedder] = _yield_hash
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _post(app: Any, path: str, token: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# 1. Flag ON: el agente global ve el contexto del proyecto de la TAREA
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_global_agent_sees_task_project_rag(configured_app, migrations_pg_dsn: str) -> None:
    """rag-search devuelve hits>0 para un agente global cuyo token porta el
    ``task_id`` de una tarea de proyecto (project_id efectivo = task.project_id)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/rag-search",
        token,
        {"query": "Reciprocal Rank Fusion mezcla las dos listas ordenadas.", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert len(hits) >= 1, hits
    assert any("Reciprocal Rank Fusion" in h["content"] for h in hits), hits


@pytest.mark.asyncio
async def test_global_agent_sees_task_project_memory(
    configured_app, migrations_pg_dsn: str
) -> None:
    """memory-recall scope project_shared recupera la memoria del proyecto de la
    tarea para el agente global (project_id efectivo = task.project_id)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/memory-recall",
        token,
        {"query": _A_PROJECT_MEMORY, "scopes": ["project_shared"], "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert len(hits) >= 1, hits
    assert any(_A_PROJECT_MEMORY in h["content"] for h in hits), hits


# ---------------------------------------------------------------------------
# 1b. Flag ON: el agente global ESCRIBE project_shared en el proyecto de la
# tarea (simetría store=recall, ADR 0054 / M2). Antes: memory-store daba 400.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_global_agent_stores_project_shared_into_task_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    """memory-store scope project_shared persiste en el proyecto de la TAREA para
    el agente global (project_id efectivo = task.project_id), y es recuperable —
    cierra la asimetría read=write del recall."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    learned = "El agente global aprendió que P1 despliega los martes."
    stored = await _post(
        configured_app,
        "/internal/agent/memory-store",
        token,
        {"content": learned, "type": "semantic", "scope": "project_shared"},
    )
    assert stored.status_code == 201, stored.text

    recalled = await _post(
        configured_app,
        "/internal/agent/memory-recall",
        token,
        {"query": learned, "scopes": ["project_shared"], "limit": 5},
    )
    assert recalled.status_code == 200, recalled.text
    hits = recalled.json()["hits"]
    assert any("despliega los martes" in h["content"] for h in hits), hits


@pytest.mark.asyncio
async def test_flag_off_global_agent_cannot_store_project_shared(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Con el flag OFF no hay proyecto efectivo → el store project_shared del
    agente global falla cerrado (400), simétrico con el recall vacío."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=False)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    stored = await _post(
        configured_app,
        "/internal/agent/memory-store",
        token,
        {"content": "no debería persistir", "type": "semantic", "scope": "project_shared"},
    )
    assert stored.status_code == 400, stored.text


# ---------------------------------------------------------------------------
# 2. Aislamiento cross-tenant: un token de B con task_id de A NO ve A
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_tenant_task_id_leaks_nothing(configured_app, migrations_pg_dsn: str) -> None:
    """Un token de tenant B cuyo ``task_id`` apunta a la tarea de tenant A no
    resuelve el proyecto de A: rag-search no devuelve los chunks de A (la tarea
    no pertenece al tenant del token, así que no hay project_id efectivo)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    # tenant B, pero task_id de A — intento de fuga cross-tenant.
    token = mint_agent_token(agent_id=b["agent_id"], tenant_id=b["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/rag-search",
        token,
        {"query": "Reciprocal Rank Fusion mezcla las dos listas ordenadas.", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert hits == [], f"un token de B no debe ver el proyecto de A: {hits}"


@pytest.mark.asyncio
async def test_cross_tenant_memory_leaks_nothing(configured_app, migrations_pg_dsn: str) -> None:
    """Idéntico para memoria: B con task_id de A no recupera la memoria
    project_shared de A."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    token = mint_agent_token(agent_id=b["agent_id"], tenant_id=b["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/memory-recall",
        token,
        {"query": _A_PROJECT_MEMORY, "scopes": ["project_shared"], "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert all(_A_PROJECT_MEMORY not in h["content"] for h in hits), hits


# ---------------------------------------------------------------------------
# 3. Aislamiento cross-project: solo el ÚNICO proyecto de la tarea
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_project_only_task_project(configured_app, migrations_pg_dsn: str) -> None:
    """El agente global solo ve el proyecto de su tarea (P1); los chunks del OTRO
    proyecto del mismo tenant (P2/Gamma) NO aparecen."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=True)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/rag-search",
        token,
        {"query": "proyecto Gamma pila secreta distinta", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert all("Gamma" not in h["content"] for h in hits), hits


# ---------------------------------------------------------------------------
# 4. Flag OFF: comportamiento estricto antiguo (sin contexto de proyecto)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_off_global_agent_sees_nothing(configured_app, migrations_pg_dsn: str) -> None:
    """Con el flag OFF, un agente global no ve conocimiento de proyecto: rag-search
    devuelve ``[]`` aunque el token porte el task_id (comportamiento antiguo)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    await _set_flag(migrations_pg_dsn, enabled=False)
    token = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"], task_id=a["task_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/rag-search",
        token,
        {"query": "Reciprocal Rank Fusion mezcla las dos listas ordenadas.", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hits"] == [], resp.text
