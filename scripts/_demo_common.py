"""Utilidades compartidas por los scripts demo_human_02_*.

No es un test automático: son ayudas para ejecutar a mano los tests
humanos del Plan 02 (`human_02_01`..`human_02_05`). Cada script demo
importa de aquí las conexiones, el resolvedor de tenant y el sembrado
del escenario.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Any
from uuid import UUID, uuid4

# La consola de Windows usa cp1252; forzamos UTF-8 para no romper al
# imprimir acentos y caracteres de caja.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Conexiones del stack de desarrollo (sobrescribibles por entorno).
DB_URL = os.environ.get(
    "DEMO_DATABASE_URL",
    "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
    "@localhost:15432/agentic_platform",
)
REDIS_URL = os.environ.get("DEMO_REDIS_URL", "redis://localhost:6379/0")
# Tenant donde se crean los escenarios — uno que YA existe, para que
# aparezcan en tu admin-panel. Acepta slug o UUID.
TENANT_REF = os.environ.get("DEMO_TENANT", "tenant-a")

_PASS = "[  OK  ]"
_FAIL = "[ FALLO]"


def banner(title: str) -> None:
    """Imprime una cabecera de sección."""
    line = "=" * 72
    print(line)
    print(f"  {title}")
    print(line)


def check(label: str, ok: bool, detail: str = "") -> bool:
    """Imprime una línea de checklist (OK / FALLO) y devuelve `ok`."""
    mark = _PASS if ok else _FAIL
    print(f"  {mark}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


async def resolve_tenant(session: Any, ref: str = TENANT_REF) -> Any:
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


async def seed_scenario(
    session: Any,
    tenant: Any,
    *,
    model_spec: dict[str, Any],
    project_name: str,
    task_title: str,
    task_status: str = "in_progress",
    approval_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crea proyecto + agente Writer + tarea dentro de `tenant`.

    Devuelve un dict con los ids (`project`, `agent`, `task`). El caller
    posee la transacción.
    """
    from api_server.db.domain import Agent, Project, Task

    ids: dict[str, Any] = {"project": uuid4(), "agent": uuid4(), "task": uuid4()}
    suffix = ids["task"].hex[:8]
    session.add(
        Project(
            id=ids["project"],
            tenant_id=tenant.id,
            name=f"{project_name} ({suffix})",
            status="active",
            is_template=False,
            human_approval_policy=approval_policy,
        )
    )
    await session.flush()
    session.add(
        Agent(
            id=ids["agent"],
            tenant_id=tenant.id,
            name="Writer",
            role="writer",
            system_prompt="Eres un agente de prueba del Plan 02.",
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
            title=task_title,
            description="Escenario de un test humano del Plan 02.",
            status=task_status,
            priority="medium",
            assigned_agent_id=ids["agent"],
        )
    )
    return ids
