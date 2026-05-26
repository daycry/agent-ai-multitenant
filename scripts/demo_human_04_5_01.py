"""Demo: Memory replay end-to-end (human_04_5_01).

Replica el contrato del test humano `human_04_5_01` del Plan 04.5:
una Execution `done` debe alimentar la memoria, y un `memory_recall`
posterior debe encontrarla.

Como el wire-up worker→sandbox (mint del token + register de las
tools en el contenedor) no entró en las 6 tareas de Plan 04.5, el
demo simula la cara del agente llamando **directamente** al
``/internal/agent/*`` con un agent token minteado por el script.
Esto ejerce exactamente el mismo código que un sandbox real ejecutaría
— sólo cambia quién dispara la llamada.

Pasos en pantalla:

  1. Insertar una `Execution` done en el proyecto compartido.
  2. Disparar la pieza pura del Memorizer (`_memorize_execution_async`)
     con un LLM fake que produce 2 candidatos. Verás los rows en
     `memory_entries` con `agent_id` + `source_execution_id`.
  3. Mintear un agent token y llamar `POST /internal/agent/memory-recall`
     contra el api-server local. Ver los hits ordenados por RRF.
  4. Llamar `POST /internal/agent/memory-store` para grabar otra
     memoria a mano (lado escritura del agente). Comprobar
     `memory_recall` la encuentra también.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/setup_demo_project.py     # Plan 02
    .venv/Scripts/python scripts/setup_demo_04_5.py         # Plan 04.5
    .venv/Scripts/python scripts/demo_human_04_5_01.py

Requisitos:
  - Postgres :15432 + Redis :6379.
  - api-server local sirviendo en http://localhost:8001
    (`cd apps/api-server && .venv/Scripts/uvicorn api_server.main:app --reload --port 8001`).
  - Los dos setup scripts ejecutados (estado en `scripts/.demo_state.json`).

El api-server expone `/internal/agent/*` con el mismo JWT_SECRET que
mintamos aquí — por eso ambos lados leen `API_SERVER_JWT_SECRET`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import httpx
from _demo_common import (
    DB_URL,
    apause,
    banner,
    check,
    load_demo_state,
)

# api-server endpoint — uvicorn por defecto en :8001 en dev.
_API_URL = os.environ.get("DEMO_API_URL", "http://localhost:8001")


# ---------------------------------------------------------------------------
# Fake LLM para el Memorizer
# ---------------------------------------------------------------------------
class _FakeLLM:
    """Devuelve un JSON con 2 candidatos para que el demo sea
    determinista — un episodic + un semantic, ambos sobre asyncpg."""

    name = "fake-demo-llm"

    def __init__(self) -> None:
        self._content = (
            "["
            '{"content": "El proyecto usa asyncpg como único driver de Postgres.",'
            ' "type": "semantic", "tags": ["asyncpg", "postgres"]},'
            '{"content": "Hoy el agente Writer arregló un import de asyncpg que'
            ' faltaba en db/session.py.",'
            ' "type": "episodic", "tags": ["asyncpg", "incident"]}'
            "]"
        )

    async def complete(self, messages: Sequence[Any], **kwargs: Any) -> Any:
        from shared_llm.types import CompletionResponse, Usage

        return CompletionResponse(
            content=self._content,
            model="fake-model",
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    async def stream(
        self, messages: Sequence[Any], **kwargs: Any
    ) -> AsyncIterator[Any]:  # pragma: no cover - demo
        from shared_llm.types import StreamChunk

        yield StreamChunk(delta="", usage=None, raw={})

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Pasos del demo
# ---------------------------------------------------------------------------
async def _seed_done_execution(
    sm: Any,
    *,
    tenant_id: UUID,
    project_id: UUID,
    agent_id: UUID,
) -> dict[str, UUID]:
    """Inserta una `Execution` done para que el Memorizer tenga algo
    que destilar. También añade la Task asociada.

    Los `steps_log` siguen el shape canónico (index + node + status +
    summary + kind) que la UI de `/admin/executions/<id>` espera para
    pintar la Timeline. Si los seedeas como dicts sueltos sin `index`,
    la página queda vacía con "Esta ejecución todavía no tiene pasos
    registrados"."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from api_server.db.domain import Execution, Task

    task_id = uuid4()
    execution_id = uuid4()
    now_iso = datetime.now(UTC).isoformat()
    # Steps simulando un run real de 5 pasos del agent loop. Cada uno
    # con index ascendente, node, status, summary, started_at, ended_at
    # — la shape que `agent_runtime.steps` produce y que la UI consume.
    steps_log: list[dict[str, Any]] = [
        {
            "index": 0,
            "kind": "node",
            "node": "perceive",
            "status": "ok",
            "summary": "Perceived task: Arreglar import de asyncpg",
            "started_at": now_iso,
            "ended_at": now_iso,
        },
        {
            "index": 1,
            "kind": "model_call",
            "node": "plan",
            "status": "ok",
            "summary": "decision: act (shell_exec)",
            "model": "fake-demo-llm",
            "tokens_in": 120,
            "tokens_out": 28,
            "cost_usd": 0.0015,
            "started_at": now_iso,
            "ended_at": now_iso,
        },
        {
            "index": 2,
            "kind": "tool_call",
            "node": "act",
            "status": "ok",
            "summary": "Tool 'shell_exec' -> ok",
            "tool": "shell_exec",
            "args": {"cmd": "pytest tests/unit/test_session.py"},
            "result": {"ok": True, "output": "tests pass"},
            "started_at": now_iso,
            "ended_at": now_iso,
        },
        {
            "index": 3,
            "kind": "node",
            "node": "observe",
            "status": "ok",
            "summary": "Observed result of 'shell_exec'",
            "started_at": now_iso,
            "ended_at": now_iso,
        },
        {
            "index": 4,
            "kind": "node",
            "node": "finalize",
            "status": "ok",
            "summary": "Finalized output",
            "started_at": now_iso,
            "ended_at": now_iso,
        },
    ]
    async with sm() as session, session.begin():
        session.add(
            Task(
                id=task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="Arreglar import de asyncpg en db/session.py",
                description="Tarea del demo human_04_5_01.",
                status="done",
                priority="medium",
                assigned_agent_id=agent_id,
            )
        )
        await session.flush()
        session.add(
            Execution(
                id=execution_id,
                tenant_id=tenant_id,
                task_id=task_id,
                agent_id=agent_id,
                status="done",
                output="Importado asyncpg, los tests pasan.",
                steps_log=steps_log,
                iterations=1,
                total_tokens=148,
                total_cost_usd=Decimal("0.0015"),
                model_call_count=1,
                tool_call_count=1,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
    return {"task_id": task_id, "execution_id": execution_id}


async def _run_memorizer(execution_id: UUID) -> dict[str, Any]:
    """Llama a la pieza pura del Memorizer con el FakeLLM.

    El Memorizer lee su DSN de ``WORKERS_DATABASE_URL`` (default
    apunta al puerto 5432 — wrong para dev). Aquí construimos un
    ``Settings`` que reusa el DSN del demo (`DB_URL`, puerto 15432)
    para que la pieza pura se conecte al mismo Postgres.

    Pre-carga el módulo `api_server.db.models` (donde vive `User`)
    para que el FK ``memory_entries.user_id`` resuelva en el
    metadata cuando `persist_memory_candidates` haga el flush."""
    import api_server.db.models  # noqa: F401  - registra User en el metadata
    from workers.config import Settings
    from workers.memorizer import _memorize_execution_async

    fake = _FakeLLM()
    settings = Settings(database_url=DB_URL)
    return await _memorize_execution_async(
        execution_id, settings=settings, llm_factory=lambda _s: fake
    )


async def _count_memories_for_agent(sm: Any, agent_id: UUID) -> int:
    from api_server.db.memory import MemoryEntry
    from sqlalchemy import func, select

    async with sm() as session:
        result = await session.execute(
            select(func.count()).select_from(MemoryEntry).where(MemoryEntry.agent_id == agent_id)
        )
        return int(result.scalar_one())


def _mint_agent_token(*, agent_id: UUID, tenant_id: UUID) -> str:
    """Mintea un token kind=agent con la clave del api-server.

    Importa la función real para no duplicar lógica de firma; eso
    significa que el demo lee el mismo `API_SERVER_JWT_SECRET` que el
    api-server. Si el api-server lo cambia, el demo debe leer el
    mismo (env compartido).
    """
    from api_server.auth.internal_agent import mint_agent_token

    return mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)


def _recall(token: str, *, query: str, limit: int = 5) -> list[dict[str, Any]]:
    response = httpx.post(
        f"{_API_URL}/internal/agent/memory-recall",
        json={"query": query, "limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    hits: list[dict[str, Any]] = response.json().get("hits") or []
    return hits


def _store(token: str, *, content: str, type_: str, tags: list[str]) -> dict[str, Any]:
    response = httpx.post(
        f"{_API_URL}/internal/agent/memory-store",
        json={"content": content, "type": type_, "tags": tags},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def _print_hits(hits: list[dict[str, Any]]) -> None:
    if not hits:
        print("    (sin resultados)")
        return
    for h in hits:
        score = h["rrf_score"]
        bm25 = h["bm25_rank"]
        vec = h["vector_rank"]
        preview = h["content"][:90].replace("\n", " ")
        print(f"    · score={score:.4f}  bm25={bm25}  vec={vec}  scope={h['scope']}")
        print(f"      “{preview}{'…' if len(h['content']) > 90 else ''}”")


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("demo human_04_5_01 — Memory replay end-to-end")

    shared = load_demo_state()
    if shared is None or "kb_id" not in shared:
        raise SystemExit(
            "Falta el estado del demo. Ejecuta antes:\n"
            "    .venv/Scripts/python scripts/setup_demo_project.py\n"
            "    .venv/Scripts/python scripts/setup_demo_04_5.py"
        )
    tenant_id = UUID(shared["tenant_id"])
    project_id = UUID(shared["project_id"])
    agent_id = UUID(shared["agent_id"])

    print(f"  Tenant   : {shared['tenant_slug']}")
    print(f"  Proyecto : {project_id}")
    print(f"  Agente   : {agent_id}")
    print(f"  api-server: {_API_URL}")
    print()

    # Sanity check: el api-server responde.
    try:
        ok = httpx.get(f"{_API_URL}/healthz", timeout=2.0)
        ok.raise_for_status()
    except Exception as exc:
        raise SystemExit(
            f"\n  No alcanzo el api-server en {_API_URL}: {exc}\n"
            "  Arráncalo en otra terminal:\n"
            "    cd apps/api-server\n"
            "    .venv/Scripts/uvicorn api_server.main:app --reload --port 8001"
        ) from None

    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)

        # ----- Paso 1: ejecución done -----
        print("─" * 66)
        print("  Paso 1/4 — Sembrar una Execution `done`")
        print("─" * 66)
        before = await _count_memories_for_agent(sm, agent_id)
        ids = await _seed_done_execution(
            sm, tenant_id=tenant_id, project_id=project_id, agent_id=agent_id
        )
        check(
            "Execution + Task creadas",
            True,
            f"execution_id={ids['execution_id']}",
        )
        await apause(2, note="dando tiempo al admin-panel para verla")

        # ----- Paso 2: Memorizer destilando -----
        print()
        print("─" * 66)
        print("  Paso 2/4 — Memorizer destila con LLM fake (2 candidatos)")
        print("─" * 66)
        outcome = await _run_memorizer(ids["execution_id"])
        check(
            f"Memorizer persistio {outcome['persisted']} entradas",
            outcome["persisted"] > 0,
            f"reason={outcome['reason']}",
        )
        after = await _count_memories_for_agent(sm, agent_id)
        check(
            f"memory_entries del agente {before} → {after}",
            after > before,
        )
        await apause(2, note="puedes mirar /admin/memories antes de seguir")

        # ----- Paso 3: recall HTTP -----
        print()
        print("─" * 66)
        print("  Paso 3/4 — `memory_recall` HTTP (lado lectura del agente)")
        print("─" * 66)
        token = _mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)
        print(f"  Token minteado (kind=agent), llamando {_API_URL}/internal/agent/memory-recall")
        hits = _recall(token, query="asyncpg driver", limit=5)
        check(f"hits devueltos: {len(hits)}", len(hits) > 0)
        _print_hits(hits)
        await apause(2)

        # ----- Paso 4: store + recall again -----
        print()
        print("─" * 66)
        print("  Paso 4/4 — `memory_store` HTTP (lado escritura del agente)")
        print("─" * 66)
        new_phrase = (
            "Recordatorio: la pila vectorial usa pgvector con HNSW;"
            " ningún índice externo, todo en el mismo Postgres."
        )
        stored = _store(token, content=new_phrase, type_="semantic", tags=["pgvector"])
        check("memory_store 201", True, f"memory_id={stored['memory_id']}")
        await apause(2, note="ahora buscamos lo recién guardado")
        hits2 = _recall(token, query="pgvector HNSW Postgres", limit=5)
        found = any(h["memory_id"] == stored["memory_id"] for h in hits2)
        check("recall encuentra la nueva memoria", found)
        _print_hits(hits2)

        print()
        print("  En el admin-panel (haz Ctrl+click si tu terminal lo soporta):")
        print("    · http://localhost:3000/admin/memories")
        print(f"        — verás las {after} entradas del agente.")
        print("        Filtra scope=team_shared para ver las 3 que dejó este demo:")
        print("          2 del Memorizer (con agent_id + source_execution_id)")
        print("          1 del memory_store del paso 4")
        print(f"    · http://localhost:3000/admin/executions/{ids['execution_id']}")
        print("        — Timeline de la Execution sembrada (5 pasos del agent loop).")
        print()
        print("  El test humano human_04_5_01 queda demostrado en vivo.")
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
