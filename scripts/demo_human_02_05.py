"""human_02_05 — el tiempo real funciona.

Genera actividad observable en vivo: crea una tarea y la hace recorrer
el Kanban (backlog → ready → in_progress → done) publicando cada
transición en el bus de eventos, con pausas entre medias. Mientras,
ejecuta un agente de verdad — su stream por-ejecución alimenta el
Timeline.

El revisor abre el board del proyecto en varias pestañas y comprueba
que las transiciones aparecen sin refrescar y casi al instante.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/demo_human_02_05.py

Requiere el stack de desarrollo (Postgres + Redis), el api-server
sirviendo el admin-panel, y la imagen `agent-runtime:v1`. Define
DEMO_NO_WAIT=1 para no esperar a pulsar Enter.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from _demo_common import DB_URL, REDIS_URL, banner, resolve_tenant, seed_scenario

_MODEL = {
    "kind": "scripted",
    "decisions": [
        {"kind": "act", "tool": "echo", "tool_args": {"text": "trabajando"}},
        {"kind": "finish", "output": "tarea completada"},
    ],
}
# Pausa entre transiciones — da tiempo al revisor a verlas llegar.
_PAUSE_S = 4.0


def _wait_for_human(project_id: Any) -> None:
    print(f"  Abre el board del proyecto {project_id} en el admin-panel,")
    print("  preferiblemente en VARIAS pestañas a la vez.")
    if os.environ.get("DEMO_NO_WAIT") or not sys.stdin.isatty():
        print("  (DEMO_NO_WAIT activo — continúo sin esperar)")
        return
    input("  Pulsa Enter cuando lo tengas abierto y observa el board... ")


async def _publish_created(sm: Any, redis: Any, task_id: Any) -> None:
    from api_server.db.domain import Task
    from api_server.events import publish_task_created

    async with sm() as session:
        task = await session.get(Task, task_id)
    await publish_task_created(redis, task)


async def _transition(sm: Any, redis: Any, task_id: Any, old: str, new: str) -> None:
    """Mueve la tarea de `old` a `new` y publica el evento en el bus."""
    from api_server.db.domain import Task
    from api_server.events import publish_task_status_changed

    async with sm() as session, session.begin():
        task = await session.get(Task, task_id)
        task.status = new
    async with sm() as session:
        task = await session.get(Task, task_id)
    await publish_task_status_changed(redis, task, old_status=old, new_status=new)
    print(f"  · transición publicada: {old} → {new}")


async def main() -> int:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.config import Settings
    from workers.execution import ExecutionRequest, conduct_execution

    banner("human_02_05 — el tiempo real funciona")
    engine = create_async_engine(DB_URL)
    redis: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await resolve_tenant(session)
            ids = await seed_scenario(
                session,
                tenant,
                model_spec=_MODEL,
                project_name="Demo tiempo real",
                task_title="Tarea observable en vivo",
                task_status="backlog",
            )
        print(f"  Tenant   : {tenant.name} ({tenant.slug})")
        print(f"  Proyecto : {ids['project']}")
        print()
        _wait_for_human(ids["project"])
        print()

        # La tarea recorre el Kanban; cada transición se publica en vivo.
        await _publish_created(sm, redis, ids["task"])
        print("  · tarea creada (aparece en backlog)")
        await asyncio.sleep(_PAUSE_S)
        await _transition(sm, redis, ids["task"], "backlog", "ready")
        await asyncio.sleep(_PAUSE_S)
        await _transition(sm, redis, ids["task"], "ready", "in_progress")

        print("  · ejecutando el agente (su stream alimenta el Timeline)...")
        outcome = await conduct_execution(
            ExecutionRequest(
                tenant_id=str(tenant.id),
                task_id=str(ids["task"]),
                agent_id=str(ids["agent"]),
                task={
                    "id": str(ids["task"]),
                    "title": "Tarea observable en vivo",
                    "description": "Demostración de tiempo real.",
                },
                model=_MODEL,
                budgets=None,
            ),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )
        await asyncio.sleep(_PAUSE_S)
        await _transition(sm, redis, ids["task"], "in_progress", "done")

        print()
        print(f"  Ejecución {outcome.execution_id}  ·  estado: {outcome.status}")
        print()
        print("  Comprueba en el board (test humano human_02_05):")
        print("    - las transiciones aparecieron sin refrescar, casi al instante;")
        print("    - se vieron igual en todas las pestañas abiertas;")
        print("    - cerrar y reabrir una pestaña recupera el estado actual.")
        print(f"    - el Timeline de la ejecución {outcome.execution_id} muestra los pasos.")
        return 0
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # - script de demo: errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "  ¿Está el stack (Postgres :15432, Redis :6379) e imagen levantados?", file=sys.stderr
        )
        sys.exit(1)
