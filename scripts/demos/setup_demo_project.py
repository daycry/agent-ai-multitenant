"""Prepara el proyecto y el agente compartidos por los 5 demos.

Crea, en el tenant configurado (`DEMO_TENANT`, por defecto `tenant-a`),
un proyecto y un agente Writer; persiste sus ids en
`scripts/.demo_state.json`. A partir de ese momento, los cinco scripts
`demo_human_02_0*.py` añaden sus tareas a ese proyecto en vez de crear
uno nuevo cada vez — así puedes verlos todos juntos en el mismo board
del admin-panel.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/setup_demo_project.py

Ejecutarlo de nuevo crea un proyecto distinto y sobrescribe el estado.
Requiere el stack de desarrollo (Postgres en :15432).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from _demo_common import (
    DB_URL,
    STATE_FILE,
    banner,
    resolve_tenant,
    save_demo_state,
)

# La política deja activada la categoría que `demo_human_02_04.py`
# necesita para que la ejecución se aparque en `awaiting_human_approval`.
# Para los otros demos es inerte (no intentan acciones de esa categoría).
_POLICY: dict[str, Any] = {"categories": {"code_execution": "human_required"}}


async def main() -> int:
    from uuid import uuid4

    from api_server.db.domain import Agent, Project
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("setup demo — proyecto compartido para los 5 tests humanos")
    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await resolve_tenant(session)
            project_id = uuid4()
            agent_id = uuid4()
            suffix = project_id.hex[:8]
            session.add(
                Project(
                    id=project_id,
                    tenant_id=tenant.id,
                    name=f"Demo tests humanos ({suffix})",
                    status="active",
                    is_template=False,
                    human_approval_policy=_POLICY,
                )
            )
            await session.flush()
            session.add(
                Agent(
                    id=agent_id,
                    tenant_id=tenant.id,
                    name="Writer",
                    role="writer",
                    system_prompt="Eres un agente de prueba del Plan 02.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=project_id,
                    # Marcador — cada demo pasa su propio `model` en la
                    # ExecutionRequest, así que el del agente no se usa.
                    model_config={"kind": "scripted", "decisions": []},
                )
            )

        state = {
            "tenant_slug": tenant.slug,
            "tenant_id": str(tenant.id),
            "project_id": str(project_id),
            "agent_id": str(agent_id),
        }
        save_demo_state(state)

        print(f"  Tenant       : {tenant.name} ({tenant.slug})")
        print(f"  Proyecto     : {project_id}")
        print(f"  Agente Writer: {agent_id}")
        print("  Política     : code_execution -> human_required (para human_02_04)")
        print()
        print(f"  Estado guardado en {STATE_FILE.name}")
        print()
        print("  Ahora puedes ejecutar los 5 demos en el orden que prefieras —")
        print("  todos añadirán sus tareas a ESTE proyecto, no crearán uno nuevo.")
        print("  Para empezar de cero más adelante, vuelve a ejecutar este setup.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # script de demo: errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  ¿Está Postgres :15432 levantado?", file=sys.stderr)
        sys.exit(1)
