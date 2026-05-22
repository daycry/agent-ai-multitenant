"""human_02_04 — la validación humana pausa la ejecución.

Crea un proyecto con una política de aprobación que exige decisión
humana para las acciones de código, y ejecuta una tarea cuyo agente
intenta una acción sensible (`shell_exec`). La ejecución queda aparcada
en `awaiting_human_approval` y se persiste una solicitud de aprobación
pendiente — el resto del test (aprobar / rechazar) lo hace el revisor
desde el admin-panel.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/demo_human_02_04.py

Requiere el stack de desarrollo (Postgres + Redis) y la imagen
`agent-runtime:v1` construida.
"""

from __future__ import annotations

import asyncio
import sys

from _demo_common import DB_URL, REDIS_URL, banner, resolve_tenant, seed_scenario

# El agente intenta una acción de categoría sensible: shell_exec mapea a
# `code_execution`, que la política marca como human_required.
_SENSITIVE_MODEL = {
    "kind": "scripted",
    "decisions": [{"kind": "act", "tool": "shell_exec", "tool_args": {"cmd": "deploy --prod"}}],
}
_POLICY = {"categories": {"code_execution": "human_required"}}


async def main() -> int:
    from api_server.db.domain import ApprovalRequest
    from api_server.db.execution_repo import list_executions_for_task
    from redis.asyncio import Redis
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.config import Settings
    from workers.execution import ExecutionRequest, conduct_execution

    banner("human_02_04 — la validación humana pausa la ejecución")
    engine = create_async_engine(DB_URL)
    redis: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await resolve_tenant(session)
            ids = await seed_scenario(
                session,
                tenant,
                model_spec=_SENSITIVE_MODEL,
                project_name="Demo aprobación humana",
                task_title="Desplegar a producción",
                approval_policy=_POLICY,
            )
        print(f"  Tenant   : {tenant.name} ({tenant.slug})")
        print(f"  Proyecto : {ids['project']}")
        print(f"  Tarea    : {ids['task']}  «Desplegar a producción»")
        print("  Política : code_execution → human_required")
        print()
        print("  Ejecutando — el agente intentará una acción sensible (shell_exec)...")

        outcome = await conduct_execution(
            ExecutionRequest(
                tenant_id=str(tenant.id),
                task_id=str(ids["task"]),
                agent_id=str(ids["agent"]),
                task={
                    "id": str(ids["task"]),
                    "title": "Desplegar a producción",
                    "description": "Despliegue que requiere aprobación.",
                },
                model=_SENSITIVE_MODEL,
                budgets=None,
            ),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        async with sm() as session:
            execution = (await list_executions_for_task(session, ids["task"]))[0]
            requests = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(ApprovalRequest.execution_id == execution.id)
                    )
                )
                .scalars()
                .all()
            )

        print()
        print(f"  Ejecución {execution.id}  ·  estado: {execution.status}")
        ok = outcome.status == "awaiting_human_approval" and len(requests) == 1
        if requests:
            req = requests[0]
            print(f"  Solicitud de aprobación {req.id}")
            print(f"    estado    : {req.status}")
            print(f"    categoría : {req.category}")
            print(f"    acción    : {req.action}")
        print()
        if ok:
            print("  ✓ La ejecución quedó APARCADA esperando decisión humana — la")
            print("    acción sensible NO se ejecutó.")
            print()
            print("  Ahora, en el admin-panel, completa el test humano:")
            print("    1. Entra en Aprobaciones — verás la solicitud pendiente.")
            print("    2. Al APROBAR, la tarea continúa con la acción aplicada.")
            print("    3. Al RECHAZAR, la tarea vuelve a in_progress con feedback.")
            print("    4. Sin respuesta en 24 h, la tarea pasa a blocked.")
        else:
            print("  REVISAR — la ejecución no quedó aparcada como se esperaba.")
        return 0 if ok else 1
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
