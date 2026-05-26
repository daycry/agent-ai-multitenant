"""Demo: RAG con citas end-to-end (human_04_5_02).

Replica el contrato del test humano `human_04_5_02` del Plan 04.5: el
agente busca en una KB granted a su proyecto y debe recibir hits con
``document_id`` que apunta al PDF/MD subido (en este demo, el doc que
sembró `setup_demo_04_5.py`).

Pasos en pantalla:

  1. Mintea un agent token y comprueba que el api-server responde.
  2. `POST /internal/agent/rag-search` con tres queries reales sobre
     la KB del demo. Por cada query pinta los hits con scores y la
     cita (kb_id + document_id + ordinal + bbox).
  3. `POST /internal/agent/document-convert` para el document_id que
     ha aparecido en los hits — muestra los chunks completos del
     documento (la vista "Open document" desde la cita).
  4. `POST /internal/agent/promote-to-kb` — crea una nueva KB de
     destino y promueve el documento, demostrando el flujo de
     "guardar este doc en otra KB del proyecto".

Como el wire-up worker→sandbox (mint del token + register de las
tools en el contenedor) no entró en las 6 tareas de Plan 04.5, el
demo dispara las llamadas desde el script — la lógica del servidor
es exactamente la misma que vería el sandbox real.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/setup_demo_project.py     # Plan 02
    .venv/Scripts/python scripts/setup_demo_04_5.py         # Plan 04.5
    .venv/Scripts/python scripts/demo_human_04_5_02.py

Requisitos: idénticos a `demo_human_04_5_01.py` — Postgres :15432,
api-server local en :8001, los dos setups ejecutados antes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any
from uuid import UUID, uuid4

import httpx
from _demo_common import (
    DB_URL,
    apause,
    banner,
    check,
    load_demo_state,
    save_demo_state,
)

_API_URL = os.environ.get("DEMO_API_URL", "http://localhost:8001")

# Las tres queries — palabras clave que coinciden con los tres temas
# que el setup sembró en el Document de la KB. El parser FTS es
# `simple` (sin stemming), así que keywords directos rinden mejor que
# frases en lenguaje natural.
_QUERIES: list[str] = [
    "tenant_id RLS",
    "sandbox agent-runtime",
    "asyncpg",
]


def _mint_agent_token(*, agent_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.internal_agent import mint_agent_token

    return mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)


def _rag_search(token: str, *, query: str, limit: int = 3) -> list[dict[str, Any]]:
    response = httpx.post(
        f"{_API_URL}/internal/agent/rag-search",
        json={"query": query, "limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    hits: list[dict[str, Any]] = response.json().get("hits") or []
    return hits


def _document_convert(token: str, *, document_id: str) -> dict[str, Any]:
    response = httpx.post(
        f"{_API_URL}/internal/agent/document-convert",
        json={"document_id": document_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def _promote_to_kb(
    token: str,
    *,
    document_id: str,
    target_kb_id: str,
    title: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_id": document_id,
        "target_kb_id": target_kb_id,
    }
    if title is not None:
        body["title"] = title
    response = httpx.post(
        f"{_API_URL}/internal/agent/promote-to-kb",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


async def _create_secondary_kb(sm: Any, *, tenant_id: UUID, project_id: UUID, name: str) -> UUID:
    """Crea (idempotente) una KB destino para el demo de promote_to_kb
    y la otorga al proyecto del agente."""
    import api_server.db.models  # noqa: F401 - registra User para los FK
    from api_server.db.knowledge import KnowledgeBase, KnowledgeBaseProject
    from sqlalchemy import select

    async with sm() as session, session.begin():
        existing = await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.name == name,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        kb = existing.scalar_one_or_none()
        if kb is None:
            kb = KnowledgeBase(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                description="KB destino sembrada por demo_human_04_5_02.py",
            )
            session.add(kb)
            await session.flush()
        grant = await session.execute(
            select(KnowledgeBaseProject).where(
                KnowledgeBaseProject.kb_id == kb.id,
                KnowledgeBaseProject.project_id == project_id,
            )
        )
        if grant.scalar_one_or_none() is None:
            session.add(
                KnowledgeBaseProject(kb_id=kb.id, project_id=project_id, tenant_id=tenant_id)
            )
    return kb.id


def _print_hits(query: str, hits: list[dict[str, Any]]) -> None:
    print(f"  Query: «{query}»")
    if not hits:
        print("    (sin hits)")
        return
    for idx, h in enumerate(hits, start=1):
        rrf = h["rrf_score"]
        bm25 = h["bm25_rank"]
        vec = h["vector_rank"]
        rer = h.get("rerank_score")
        preview = h["content"][:100].replace("\n", " ")
        print(
            f"    {idx}. rrf={rrf:.4f}  bm25={bm25}  vec={vec}"
            + (f"  rerank={rer:.3f}" if rer is not None else "")
        )
        print(f"       “{preview}{'…' if len(h['content']) > 100 else ''}”")
        print(
            f"       cita → kb_id={h['kb_id']}  document_id={h['document_id']}"
            f"  ordinal={h['ordinal']}"
        )


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("demo human_04_5_02 — RAG con citas end-to-end")

    shared = load_demo_state()
    if shared is None or "kb_id" not in shared or "document_id" not in shared:
        raise SystemExit(
            "Falta el estado del demo Plan 04.5. Ejecuta antes:\n"
            "    .venv/Scripts/python scripts/setup_demo_project.py\n"
            "    .venv/Scripts/python scripts/setup_demo_04_5.py"
        )
    tenant_id = UUID(shared["tenant_id"])
    project_id = UUID(shared["project_id"])
    agent_id = UUID(shared["agent_id"])
    kb_id = UUID(shared["kb_id"])
    document_id = UUID(shared["document_id"])

    print(f"  Tenant     : {shared['tenant_slug']}")
    print(f"  Proyecto   : {project_id}")
    print(f"  Agente     : {agent_id}")
    print(f"  KB origen  : {kb_id}")
    print(f"  Document   : {document_id}")
    print(f"  api-server : {_API_URL}")
    print()

    try:
        httpx.get(f"{_API_URL}/healthz", timeout=2.0).raise_for_status()
    except Exception as exc:
        raise SystemExit(
            f"\n  No alcanzo el api-server en {_API_URL}: {exc}\n"
            "  Arráncalo en otra terminal:\n"
            "    cd apps/api-server\n"
            "    .venv/Scripts/uvicorn api_server.main:app --reload --port 8001"
        ) from None

    token = _mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)

    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)

        # ----- Paso 1: rag_search por las 3 queries -----
        print("─" * 66)
        print("  Paso 1/3 — `rag_search` HTTP x 3 queries")
        print("─" * 66)
        any_hits = False
        for q in _QUERIES:
            hits = _rag_search(token, query=q, limit=3)
            _print_hits(q, hits)
            if hits:
                any_hits = True
            print()
            await apause(1)
        check("Al menos una query devolvió hits", any_hits)
        await apause(
            2, note=f"Mira /admin/projects/{project_id}/knowledge-bases para ver la KB y el doc"
        )

        # ----- Paso 2: document_convert para ver chunks completos -----
        print()
        print("─" * 66)
        print("  Paso 2/3 — `document_convert` (vista «open document»)")
        print("─" * 66)
        out = _document_convert(token, document_id=str(document_id))
        check(
            f"Document devuelto: «{out['title']}»",
            True,
            f"{len(out['chunks'])} chunks · MIME={out['source_mime_type']}",
        )
        for chunk in out["chunks"]:
            preview = chunk["content"][:90].replace("\n", " ")
            print(f"    ord={chunk['ordinal']}  chunk_id={chunk['chunk_id']}")
            print(f"      “{preview}{'…' if len(chunk['content']) > 90 else ''}”")
        await apause(2)

        # ----- Paso 3: promote_to_kb a una KB destino nueva -----
        print()
        print("─" * 66)
        print("  Paso 3/3 — `promote_to_kb` (copiar doc a una KB destino)")
        print("─" * 66)
        target_name = "KB destino del demo (04.5)"
        target_kb_id = await _create_secondary_kb(
            sm, tenant_id=tenant_id, project_id=project_id, name=target_name
        )
        promoted = _promote_to_kb(
            token,
            document_id=str(document_id),
            target_kb_id=str(target_kb_id),
            title="Notas de arquitectura (promoted)",
        )
        check(
            "promote_to_kb 201",
            True,
            f"new doc {promoted['document_id']} · chunks={promoted['chunks_persisted']}",
        )

        # Persistir el target_kb_id para inspección manual (opcional).
        shared["target_kb_id"] = str(target_kb_id)
        shared["promoted_document_id"] = promoted["document_id"]
        save_demo_state(shared)

        print()
        print("  En el admin-panel:")
        print(f"    · /admin/projects/{project_id}/knowledge-bases")
        print(f"        — KB origen ({kb_id}) y KB destino ({target_kb_id})")
        print("          ambas concedidas a este proyecto.")
        print(f"    · /admin/documents/{document_id}/citations")
        print("        — chunks del Document original con bounding boxes.")
        print(f"    · /admin/documents/{document_id}/ingestion")
        print("        — estado del pipeline de ingestión (indexed).")
        print(f"    · /admin/documents/{promoted['document_id']}/citations")
        print("        — chunks del Document promovido a la KB destino.")
        print("    · Cada hit del paso 1 lleva su cita kb_id + document_id;")
        print("      la UI de revisión usa ese par para abrir el viewer.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:  # script de demo — errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        sys.exit(1)
