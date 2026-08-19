"""Utilidades compartidas por los scripts demo_human_02_*.

No es un test automático: son ayudas para ejecutar a mano los tests
humanos del Plan 02 (`human_02_01`..`human_02_05`). Cada script demo
importa de aquí las conexiones, el resolvedor de tenant y el sembrado
del escenario.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# La consola de Windows usa cp1252; forzamos UTF-8 para no romper al
# imprimir acentos y caracteres de caja.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Silencia el "pending deprecation" de langgraph/langchain que ensucia
# la salida de los demos — es informativo, no bloquea nada, y los demos
# están pensados para leerse cómodamente desde la terminal.
for _category in (DeprecationWarning, PendingDeprecationWarning):
    warnings.filterwarnings("ignore", message=r".*allowed_objects.*", category=_category)

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

# Estado compartido entre los 5 demos: si existe, todos reutilizan el
# proyecto y el agente que creó `setup_demo_project.py` y sólo añaden
# una tarea nueva por ejecución (en vez de crear un proyecto cada vez).
STATE_FILE = Path(__file__).parent / ".demo_state.json"


def load_demo_state() -> dict[str, Any] | None:
    """Lee el estado compartido si existe. None si nadie corrió setup."""
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_demo_state(state: dict[str, Any]) -> None:
    """Persiste el estado compartido en disco (JSON)."""
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


# Pausa entre fases del demo — para que se lea cómodamente. DEMO_NO_PAUSE
# las desactiva (útil para verificar el script sin esperar).
_DEFAULT_PAUSE_S = float(os.environ.get("DEMO_PAUSE_S", "5"))


def pause(seconds: float | None = None, *, note: str = "") -> None:
    """Espera un rato para que el flujo sea legible (versión sync)."""
    if os.environ.get("DEMO_NO_PAUSE"):
        return
    secs = _DEFAULT_PAUSE_S if seconds is None else seconds
    if note:
        print(f"  ... {note}  (pausa {secs:g}s)")
    time.sleep(secs)


async def apause(seconds: float | None = None, *, note: str = "") -> None:
    """Versión async de `pause` — para usar dentro de un loop asyncio."""
    if os.environ.get("DEMO_NO_PAUSE"):
        return
    secs = _DEFAULT_PAUSE_S if seconds is None else seconds
    if note:
        print(f"  ... {note}  (pausa {secs:g}s)")
    await asyncio.sleep(secs)


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


def _as_uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


async def seed_scenario(
    session: Any,
    tenant: Any,
    *,
    model_spec: dict[str, Any],
    project_name: str,
    task_title: str,
    task_status: str = "in_progress",
    approval_policy: dict[str, Any] | None = None,
    project_id: Any | None = None,
    agent_id: Any | None = None,
) -> dict[str, Any]:
    """Añade el escenario de un test humano del Plan 02.

    Si `project_id`/`agent_id` se pasan (típicamente desde el estado
    compartido que escribe `setup_demo_project.py`), se reutilizan y
    sólo se crea la tarea. Si no, se crea proyecto + agente + tarea —
    el comportamiento original cuando un demo se ejecuta suelto.
    """
    from api_server.db.domain import Agent, Project, Task

    ids: dict[str, Any] = {"task": uuid4()}

    if project_id is None:
        ids["project"] = uuid4()
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
    else:
        ids["project"] = _as_uuid(project_id)

    if agent_id is None:
        ids["agent"] = uuid4()
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
    else:
        ids["agent"] = _as_uuid(agent_id)

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
