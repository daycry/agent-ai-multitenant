"""Demo: ver a un agente ejecutar una tarea end-to-end (human_02_01).

Crea un escenario mínimo en la base de datos de desarrollo —tenant,
proyecto, agente Writer y tarea— y conduce una ejecución real: lanza el
contenedor `agent-runtime`, corre el agent loop LangGraph y persiste la
fila `Execution`. Es el pipeline de la Fase G ejecutado de una sola vez,
sin necesidad de levantar el orchestrator ni un worker Celery.

Uso (desde la raíz del repo, con el venv):

    .venv/Scripts/python scripts/demo_human_02_01.py

Requisitos:
  - El stack de desarrollo levantado: Postgres en :15432, Redis en :6379.
  - La imagen `agent-runtime:v1` construida:
        docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/

El proyecto se crea dentro de un tenant que YA existe — `tenant-a` por
defecto, cambiable con la variable DEMO_TENANT — para que aparezca en
tu admin-panel.

Por defecto usa un modelo determinista: NO necesita credenciales, pero
el "poema" es un texto prefijado — sirve para ver el pipeline funcionar.
Para un poema escrito por un LLM real, ver el final de este fichero.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

# Tenant donde se crea el proyecto demo — un tenant que YA existe, para
# que el proyecto aparezca en tu admin-panel. Acepta slug o UUID.
_DEMO_TENANT = os.environ.get("DEMO_TENANT", "tenant-a")

# The Windows console defaults to cp1252; force UTF-8 so the report
# (accents, box characters) prints instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# --- Conexiones (sobrescribibles por entorno) ------------------------------
_DB_URL = os.environ.get(
    "DEMO_DATABASE_URL",
    "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
    "@localhost:15432/agentic_platform",
)
_REDIS_URL = os.environ.get("DEMO_REDIS_URL", "redis://localhost:6379/0")

# --- El poema del modelo determinista --------------------------------------
_SEA_POEM = (
    "El mar repite su nombre en la orilla,\n"
    "una sílaba de sal y de espuma.\n"
    "Cada ola escribe y borra su renglón;\n"
    "el horizonte guarda lo que la arena olvida."
)

# Modelo determinista: un borrador y el poema final. No usa credenciales.
_SCRIPTED_MODEL: dict[str, Any] = {
    "kind": "scripted",
    "decisions": [
        {
            "kind": "act",
            "tool": "echo",
            "tool_args": {"text": "borrador del poema sobre el mar"},
            "tokens_in": 140,
            "tokens_out": 35,
            "cost_usd": 0.0018,
        },
        {
            "kind": "finish",
            "output": _SEA_POEM,
            "tokens_in": 90,
            "tokens_out": 70,
            "cost_usd": 0.0026,
        },
    ],
}


def _model_spec() -> tuple[dict[str, Any], str]:
    """El spec del ModelClient — determinista por defecto, o un proveedor
    real si DEMO_MODEL_KIND está definido. Devuelve (spec, etiqueta)."""
    kind = os.environ.get("DEMO_MODEL_KIND")
    if not kind:
        return _SCRIPTED_MODEL, "determinista (sin credenciales)"
    model = os.environ.get("DEMO_MODEL", "")
    if kind == "litellm":
        return {
            "kind": "litellm",
            "model": model or "gpt-4o",
            "base_url": os.environ.get("DEMO_BASE_URL", "http://localhost:4000"),
            "api_key": os.environ.get("DEMO_API_KEY"),
        }, f"LiteLLM ({model or 'gpt-4o'})"
    if kind in ("claude_sdk", "claude"):
        return {"kind": "claude_sdk", "model": model or "claude-opus-4-7"}, (
            f"Claude Agent SDK ({model or 'claude-opus-4-7'})"
        )
    if kind == "copilot":
        return {
            "kind": "copilot",
            "model": model or "gpt-4o",
            "oauth_token": os.environ.get("DEMO_GITHUB_TOKEN", ""),
        }, f"GitHub Copilot ({model or 'gpt-4o'})"
    raise SystemExit(f"DEMO_MODEL_KIND no reconocido: {kind!r}")


async def _resolve_tenant(session: Any, ref: str) -> Any:
    """Localiza un tenant que YA existe, por slug o por UUID."""
    from api_server.db.models import Organization
    from sqlalchemy import select

    stmt = select(Organization)
    try:
        stmt = stmt.where(Organization.id == UUID(ref))
    except ValueError:
        stmt = stmt.where(Organization.slug == ref)
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        available = (await session.execute(select(Organization.slug))).scalars().all()
        raise SystemExit(
            f"No existe el tenant «{ref}». Define DEMO_TENANT con uno de: "
            + ", ".join(sorted(available))
        )
    return org


async def _seed(sm: Any, model_spec: dict[str, Any]) -> dict[str, Any]:
    """Crea un proyecto + agente Writer + tarea dentro de un tenant que YA
    existe (`DEMO_TENANT`), para que el proyecto aparezca en tu admin-panel."""
    from api_server.db.domain import Agent, Project, Task

    ids: dict[str, Any] = {"project": uuid4(), "agent": uuid4(), "task": uuid4()}
    suffix = ids["task"].hex[:8]
    async with sm() as session, session.begin():
        tenant = await _resolve_tenant(session, _DEMO_TENANT)
        ids["tenant"] = tenant.id
        ids["tenant_label"] = f"{tenant.name} ({tenant.slug})"
        session.add(
            Project(
                id=ids["project"],
                tenant_id=tenant.id,
                name=f"Demo poema del mar ({suffix})",
                status="active",
                is_template=False,
            )
        )
        await session.flush()
        session.add(
            Agent(
                id=ids["agent"],
                tenant_id=tenant.id,
                name="Writer",
                role="writer",
                system_prompt="Escribes poemas breves y cuidados.",
                agent_type="ai",
                scope="project_local",
                project_id=ids["project"],
                model_config=model_spec,
            )
        )
        await session.flush()
        session.add(
            Task(
                id=ids["task"],
                tenant_id=tenant.id,
                project_id=ids["project"],
                title="Escribe un poema sobre el mar",
                description="Test humano human_02_01 — ejecución end-to-end.",
                status="in_progress",
                priority="medium",
                assigned_agent_id=ids["agent"],
            )
        )
    return ids


async def _complete_task(sm: Any, task_id: Any, execution_status: str) -> None:
    """Tras la ejecución, avanza la tarea a `done` (lo que hará el worker
    de forma automática en una iteración posterior de la Fase G)."""
    from api_server.db.domain import Task

    async with sm() as session, session.begin():
        task = await session.get(Task, task_id)
        if task is not None and execution_status == "done":
            task.status = "done"
            task.completed_at = datetime.now(UTC)


async def _report(sm: Any, ids: dict[str, Any], outcome: Any) -> None:
    """Imprime, paso a paso, lo que hizo el agente."""
    from api_server.db.execution_repo import list_executions_for_task

    async with sm() as session:
        execution = (await list_executions_for_task(session, ids["task"]))[0]

    print()
    print("-" * 66)
    print(f"  Ejecución {execution.id}  ·  estado: {execution.status}")
    print("-" * 66)
    print("  Lo que hizo el agente, paso a paso:")
    for step in execution.steps_log:
        print(
            f"    [{step['index']:>2}] {step['kind']:<11} "
            f"{step['node']:<12} {step.get('summary', '')}"
        )
    print()
    print("  Resultado — el poema:")
    for line in (execution.output or "").splitlines():
        print(f"    | {line}")
    print()
    print(
        f"  Iteraciones: {execution.iterations}   "
        f"Tokens: {execution.total_tokens}   "
        f"Coste: ${execution.total_cost_usd}"
    )
    final = "done" if outcome.status == "done" else outcome.status
    print(f"  Tarea {ids['task']}  ->  estado: {final}")
    print()
    print("  En el admin-panel: entra en el tenant del demo y abre el")
    print(f"  proyecto {ids['project']} — verás la tarea y el Timeline.")


async def main() -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.config import Settings
    from workers.execution import ExecutionRequest, conduct_execution

    model_spec, model_label = _model_spec()
    print("=" * 66)
    print("  Demo human_02_01 — un agente ejecuta una tarea end-to-end")
    print("=" * 66)
    print(f"  Modelo del agente: {model_label}")
    print()

    engine = create_async_engine(_DB_URL)
    redis: Any = Redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, model_spec)
        print("  Escenario creado en la base de datos de desarrollo:")
        print(f"    tenant    {ids['tenant_label']}")
        print(f"    proyecto  {ids['project']}")
        print(f"    agente    {ids['agent']}  «Writer»")
        print(f"    tarea     {ids['task']}  «Escribe un poema sobre el mar»")
        print()
        print("  Ejecutando el agente — lanzando el contenedor agent-runtime...")

        request = ExecutionRequest(
            tenant_id=str(ids["tenant"]),
            task_id=str(ids["task"]),
            agent_id=str(ids["agent"]),
            task={
                "id": str(ids["task"]),
                "title": "Escribe un poema sobre el mar",
                "description": "Un poema breve sobre el mar.",
            },
            model=model_spec,
            budgets=None,
        )
        outcome = await conduct_execution(
            request, settings=Settings(), sessionmaker=sm, redis=redis
        )
        await _complete_task(sm, ids["task"], outcome.status)
        await _report(sm, ids, outcome)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)
    except Exception as exc:  # - demo script: friendly errors
        print(f"\n  ERROR: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        print("  Comprueba que:", file=sys.stderr)
        print("   - el stack está levantado (Postgres :15432, Redis :6379)", file=sys.stderr)
        print(
            "   - la imagen existe:  docker build -t agent-runtime:v1"
            " docker/agent-runtimes/agent-runtime/",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Para un poema escrito por un LLM real (human_02_01 "de verdad")
# ---------------------------------------------------------------------------
# Define variables de entorno antes de ejecutar y elige UN proveedor:
#
#   LiteLLM (gateway con API key):
#     DEMO_MODEL_KIND=litellm  DEMO_MODEL=gpt-4o
#     DEMO_BASE_URL=http://localhost:4000  DEMO_API_KEY=sk-...
#
#   Claude Agent SDK (suscripción Pro/Max — requiere el CLI de Claude
#   Code y el paquete `claude-agent-sdk` instalados en el entorno):
#     DEMO_MODEL_KIND=claude_sdk  DEMO_MODEL=claude-opus-4-7
#
#   GitHub Copilot (token OAuth de GitHub):
#     DEMO_MODEL_KIND=copilot  DEMO_MODEL=gpt-4o  DEMO_GITHUB_TOKEN=gho_...
