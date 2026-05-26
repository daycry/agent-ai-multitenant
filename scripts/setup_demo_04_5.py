"""Setup para los demos `demo_human_04_5_*` (Plan 04.5).

Extiende el proyecto compartido que crea `setup_demo_project.py`
(Plan 02) con la **infraestructura RAG**: una KnowledgeBase
"Arquitectura" granted al proyecto, un Document llamado
"Arquitectura del sistema" y 4 chunks con contenido típico del
producto. Sin esto, `demo_human_04_5_02.py` no tendría sobre qué
buscar.

También se asegura de que el agente del estado compartido tenga
``memory_scope=team_shared`` — la tabla `agents` por defecto deja
`private`, pero los dos tests humanos del Plan 04.5 necesitan un
agente con scope shared para que la Memorizer + recall produzcan
algo visible.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/setup_demo_project.py    # Plan 02
    .venv/Scripts/python scripts/setup_demo_04_5.py        # esto

Ejecutarlo de nuevo es idempotente: si ya existe la KB del demo
para este tenant, la reutiliza y resemilla los chunks. Requiere
el stack de desarrollo (Postgres en :15432).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from uuid import UUID, uuid4

from _demo_common import (
    DB_URL,
    STATE_FILE,
    banner,
    check,
    load_demo_state,
    resolve_tenant,
    save_demo_state,
)

# Nombre de la KB que crea / reutiliza este setup.
_KB_NAME = "Arquitectura del sistema (demo 04.5)"

# Document + chunks: textos realistas sobre la arquitectura del producto.
# El demo de RAG (04_5_02) busca palabras como "asyncpg", "tenant_id",
# "sandbox" y debe encontrar exactamente los chunks correspondientes.
#
# El Document se siembra como `application/pdf` con 2 páginas y bboxes
# normalizados (Docling-style, ver `apps/admin-panel/.../citations`)
# para que la vista de citas pinte los 4 chunks como rectángulos
# azules sobre las páginas A4 — igual que haría con un PDF real
# subido por la UI.
_DOCUMENT_TITLE = "Notas de arquitectura"
_DOCUMENT_FILENAME = "arch.pdf"
_DOCUMENT_MIME = "application/pdf"
_DOCUMENT_PAGES = 2
# (content, bbox) por chunk. Coords normalizadas top-left.
#   page=0 → primera página, page=1 → segunda.
#   2 chunks por página: top half (y=0.10) + bottom half (y=0.55).
_CHUNK_SPECS: list[tuple[str, dict[str, Any]]] = [
    (
        "Multi-tenancy desde el día uno: cada tabla lleva un tenant_id"
        " indexado y PostgreSQL RLS hace cumplir el aislamiento.",
        {"page": 0, "x": 0.10, "y": 0.10, "w": 0.80, "h": 0.35},
    ),
    (
        "Los workers nunca ejecutan código del usuario: lanzan contenedores"
        " agent-runtime sandbox con red restringida y sin socket Docker.",
        {"page": 0, "x": 0.10, "y": 0.55, "w": 0.80, "h": 0.35},
    ),
    (
        "El acceso a BD desde dev usa asyncpg + SQLAlchemy 2.x async; las"
        " migraciones corren bajo el rol migrations_user con BYPASSRLS.",
        {"page": 1, "x": 0.10, "y": 0.10, "w": 0.80, "h": 0.35},
    ),
    (
        "El sandbox habla con el api-server por /internal/agent/*, con un"
        " bearer JWT de vida corta que el worker mintea antes del launch.",
        {"page": 1, "x": 0.10, "y": 0.55, "w": 0.80, "h": 0.35},
    ),
]


async def _ensure_kb_and_document(sm: Any, *, tenant_id: UUID, project_id: UUID) -> dict[str, Any]:
    """Crea (o reutiliza) la KB + Document + chunks del demo. Idempotente."""
    import api_server.db.models  # noqa: F401 - registra User para los FK
    from api_server.db.knowledge import Chunk, Document, KnowledgeBase, KnowledgeBaseProject
    from sqlalchemy import delete, select

    async with sm() as session, session.begin():
        # 1) KB
        kb_row = await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.name == _KB_NAME,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        kb = kb_row.scalar_one_or_none()
        if kb is None:
            kb = KnowledgeBase(
                id=uuid4(),
                tenant_id=tenant_id,
                name=_KB_NAME,
                description="Knowledge Base sembrada por scripts/setup_demo_04_5.py.",
            )
            session.add(kb)
            await session.flush()
            kb_created = True
        else:
            kb_created = False

        # 2) Grant kb_projects (idempotente)
        grant_row = await session.execute(
            select(KnowledgeBaseProject).where(
                KnowledgeBaseProject.kb_id == kb.id,
                KnowledgeBaseProject.project_id == project_id,
            )
        )
        if grant_row.scalar_one_or_none() is None:
            session.add(
                KnowledgeBaseProject(kb_id=kb.id, project_id=project_id, tenant_id=tenant_id)
            )

        # 3) Document (un único document por KB del demo; si ya existe,
        #    resemillamos sus chunks).
        doc_row = await session.execute(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.kb_id == kb.id,
                Document.title == _DOCUMENT_TITLE,
                Document.deleted_at.is_(None),
            )
        )
        document = doc_row.scalar_one_or_none()
        if document is None:
            document = Document(
                id=uuid4(),
                tenant_id=tenant_id,
                kb_id=kb.id,
                title=_DOCUMENT_TITLE,
                source_filename=_DOCUMENT_FILENAME,
                source_mime_type=_DOCUMENT_MIME,
                source_storage_key=(f"kb/{tenant_id}/{kb.id}/seed-document/{_DOCUMENT_FILENAME}"),
                source_size_bytes=512,
                status="indexed",
                page_count=_DOCUMENT_PAGES,
            )
            session.add(document)
            await session.flush()
        else:
            # Limpia chunks antiguos antes de resemillar — así el demo
            # arranca siempre con los textos canónicos. Re-aplicamos
            # mime/page_count por si el setup anterior los dejó como
            # markdown / 0.
            document.source_mime_type = _DOCUMENT_MIME
            document.source_filename = _DOCUMENT_FILENAME
            document.page_count = _DOCUMENT_PAGES
            await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

        for ordinal, (content, bbox) in enumerate(_CHUNK_SPECS):
            session.add(
                Chunk(
                    tenant_id=tenant_id,
                    document_id=document.id,
                    ordinal=ordinal,
                    content=content,
                    bbox=bbox,
                )
            )

    return {
        "kb_id": kb.id,
        "kb_created": kb_created,
        "document_id": document.id,
        "chunks": len(_CHUNK_SPECS),
    }


async def _ensure_agent_team_shared(sm: Any, agent_id: UUID) -> bool:
    """El agente del estado compartido empieza con memory_scope=private
    (default del schema). Los demos 04.5 esperan team_shared para
    producir memorias visibles. Cambiamos el campo si es necesario.
    Devuelve True si hubo cambio, False si ya estaba bien."""
    from api_server.db.domain import Agent

    async with sm() as session, session.begin():
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise SystemExit(
                f"No encontré el agente {agent_id} del estado compartido. "
                "Vuelve a ejecutar scripts/setup_demo_project.py."
            )
        if agent.memory_scope == "team_shared":
            return False
        agent.memory_scope = "team_shared"
    return True


async def _ensure_project_has_team(sm: Any, *, tenant_id: UUID, project_id: UUID) -> UUID:
    """Las memorias `team_shared` necesitan que el Project tenga
    team_id (es el owner pointer). Si el proyecto no tiene team_id,
    creamos un Team y lo enlazamos."""
    from api_server.db.domain import Project, Team

    async with sm() as session, session.begin():
        project = await session.get(Project, project_id)
        if project is None:
            raise SystemExit(f"No encontré el proyecto {project_id}.")
        if project.team_id is not None:
            return project.team_id
        team = Team(
            id=uuid4(),
            tenant_id=tenant_id,
            name="Demo Team 04.5",
        )
        session.add(team)
        await session.flush()
        project.team_id = team.id
    return team.id


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("setup demo Plan 04.5 — KB + Document para human_04_5_01/02")

    shared = load_demo_state()
    if shared is None:
        raise SystemExit(
            "Falta el estado compartido. Ejecuta antes:\n"
            "    .venv/Scripts/python scripts/setup_demo_project.py"
        )

    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        tenant_id = UUID(shared["tenant_id"])
        project_id = UUID(shared["project_id"])
        agent_id = UUID(shared["agent_id"])

        async with sm() as session:
            tenant = await resolve_tenant(session, shared["tenant_slug"])

        team_id = await _ensure_project_has_team(sm, tenant_id=tenant_id, project_id=project_id)
        scope_changed = await _ensure_agent_team_shared(sm, agent_id)
        seeded = await _ensure_kb_and_document(sm, tenant_id=tenant_id, project_id=project_id)

        # Persistir las nuevas IDs en el estado compartido — los
        # demos posteriores las leen sin volver a buscar.
        shared.update(
            {
                "kb_id": str(seeded["kb_id"]),
                "document_id": str(seeded["document_id"]),
                "team_id": str(team_id),
            }
        )
        save_demo_state(shared)

        print()
        print(f"  Tenant         : {tenant.name} ({tenant.slug})")
        print(f"  Proyecto       : {project_id}")
        print(f"  Team           : {team_id}")
        print(f"  Agente Writer  : {agent_id}  (memory_scope=team_shared)")
        print(f"  KnowledgeBase  : {seeded['kb_id']}")
        print(f"  Document       : {seeded['document_id']}")
        print(f"  Chunks         : {seeded['chunks']} (resemillados)")
        print()
        check(
            "KB lista",
            True,
            "creada nueva" if seeded["kb_created"] else "ya existía, chunks resemillados",
        )
        check(
            "Agent memory_scope = team_shared",
            True,
            "ajustado ahora" if scope_changed else "ya estaba correcto",
        )
        check("Estado compartido actualizado", True, f"{STATE_FILE.name}")
        print()
        print("  Ahora puedes ejecutar:")
        print("    .venv/Scripts/python scripts/demo_human_04_5_01.py")
        print("    .venv/Scripts/python scripts/demo_human_04_5_02.py")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # script de demo — errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  ¿Está Postgres :15432 levantado?", file=sys.stderr)
        sys.exit(1)
