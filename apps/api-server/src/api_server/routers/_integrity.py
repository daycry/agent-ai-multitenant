"""Traducción de `IntegrityError` a un 409 de dominio (prod-13 task_prod13_23).

Hallazgo api-5. Seis routers hacían esto:

    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc.orig)) from exc

y `str(exc.orig)` es el mensaje CRUDO de PostgreSQL, que el cliente recibe tal
cual. No es solo feo: es una **fuga de información**. Un ejemplo real de lo que
salía por la API a cualquiera con una sesión de tenant:

    duplicate key value violates unique constraint "uq_projects_tenant_slug_live"
    DETAIL:  Key (tenant_id, slug)=(3f2a…, api-v1) already exists.

Eso entrega tres cosas gratis: los nombres internos de tablas/constraints (mapa
del esquema para preparar otros ataques), los nombres de columna reales, y —lo
peor— **el valor de la clave en conflicto, incluido el `tenant_id`**. En una
plataforma multi-tenant, filtrar el UUID de un tenant en un mensaje de error es
exactamente lo que el aislamiento existe para impedir. Y el mensaje del
`DETAIL:` puede llevar datos de OTRA fila que el llamante no tiene permiso para
ver, porque la comprobación de unicidad la hace el índice, no la RLS.

## El contrato de aquí

:func:`integrity_conflict` devuelve la `HTTPException` 409 que el router debe
lanzar. El `detail` es un dict estable — `{"error": <código>, "message": <es>}` —
con el mismo aspecto que el resto de los 409 de dominio del proyecto
(`invalid_plan_transition`, `project_not_active`). El código sale del **nombre de
la constraint** que PostgreSQL reporta, no del texto del mensaje: los nombres son
parte del esquema y los controlamos nosotros; el texto cambia con la versión de
PostgreSQL y con el idioma del servidor.

Una constraint desconocida cae en el genérico `conflict`. Eso es a propósito y es
fail-closed: preferimos un mensaje pobre a filtrar el crudo. El nombre de la
constraint sí se registra en el log del servidor (`structlog`), que es donde un
operador puede verlo, para que el diagnóstico no se pierda.
"""

from __future__ import annotations

import re
from typing import Final

import structlog
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

#: `constraint` → `(código estable, mensaje para el usuario en castellano)`.
#:
#: Solo constraints que un cliente puede provocar con una petición válida. Las
#: que solo se rompen por un bug (FKs internas, CHECKs de enum que el schema
#: Pydantic ya valida) no hacen falta aquí: caen en el genérico y el nombre queda
#: en el log.
_CONSTRAINT_MESSAGES: Final[dict[str, tuple[str, str]]] = {
    # Unicidad de nombre/slug por tenant.
    "uq_projects_tenant_slug_live": (
        "duplicate_project_slug",
        "Ya existe un proyecto con ese nombre en este tenant.",
    ),
    "ix_knowledge_bases_tenant_name": (
        "duplicate_kb_name",
        "Ya existe una base de conocimiento con ese nombre.",
    ),
    "uq_tools_tenant_name": (
        "duplicate_tool_name",
        "Ya existe una tool con ese nombre.",
    ),
    "uq_teams_tenant_name_live": (
        "duplicate_team_name",
        "Ya existe un equipo con ese nombre.",
    ),
    "uq_skills_tenant_name_live": (
        "duplicate_skill_name",
        "Ya existe una skill con ese nombre.",
    ),
    "uq_agents_tenant_name_global_live": (
        "duplicate_agent_name",
        "Ya existe un agente con ese nombre en este tenant.",
    ),
    "uq_agents_tenant_project_name_live": (
        "duplicate_agent_name",
        "Ya existe un agente con ese nombre en este proyecto.",
    ),
    "ix_kb_categories_tenant_slug": (
        "duplicate_category_slug",
        "Ya existe una categoría con ese identificador.",
    ),
    "ix_custom_chat_modes_tenant_name": (
        "duplicate_chat_mode_name",
        "Ya existe un modo de chat con ese nombre.",
    ),
    # Grafo de dependencias de tareas.
    "ck_task_dependencies_no_self_loop": (
        "task_depends_on_itself",
        "Una tarea no puede depender de sí misma.",
    ),
    "pk_task_dependencies": (
        "duplicate_task_dependency",
        "Esa dependencia entre tareas ya existe.",
    ),
}

#: Código genérico para lo que no está en el mapa. Deliberadamente vago.
_FALLBACK: Final[tuple[str, str]] = (
    "conflict",
    "La operación entra en conflicto con datos que ya existen.",
)

# PostgreSQL nombra la constraint en el propio mensaje. asyncpg además la expone
# como `exc.orig.__cause__.constraint_name`, pero solo en algunas rutas, así que
# se intenta el atributo y se cae al regex sobre el mensaje.
_CONSTRAINT_RE = re.compile(r'constraint "([^"]+)"')


def constraint_name(exc: IntegrityError) -> str | None:
    """El nombre de la constraint violada, o None si no se puede determinar."""
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
    match = _CONSTRAINT_RE.search(str(orig))
    return match.group(1) if match else None


def integrity_conflict(exc: IntegrityError, *, context: str) -> HTTPException:
    """El 409 de dominio que sustituye a `detail=str(exc.orig)`.

    `context` identifica la operación en el LOG del servidor (``plan.create``,
    ``task.dependencies``); no va en la respuesta. El llamante hace:

        except IntegrityError as exc:
            await session.rollback()
            raise integrity_conflict(exc, context="plan.create") from exc
    """
    name = constraint_name(exc)
    code, message = _CONSTRAINT_MESSAGES.get(name or "", _FALLBACK)
    # El crudo se queda AQUÍ, en el log del servidor, donde el operador lo ve y
    # el cliente no. Nunca en el `detail`.
    logger.warning(
        "integrity_error",
        context=context,
        constraint=name,
        mapped_code=code,
        db_error=str(exc.orig),
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": code, "message": message},
    )


async def flush_or_conflict(session: AsyncSession, *, context: str) -> None:
    """`session.flush()` que traduce la `IntegrityError` a un 409 de dominio.

    El caso que la motivó: los índices únicos parciales `(tenant_id, name)` que
    la migración 0126 puso sobre `teams`, `skills` y `agents` (prod-13
    task_prod13_13). Antes de ellos, un nombre repetido se colaba; con ellos, y
    sin nadie que atrapara la excepción, se convertía en un **500** — que para
    quien usa la UI es indistinguible de «la plataforma se ha roto», cuando lo
    que ha pasado es que ese nombre ya está cogido.

    Hace `rollback()` antes de levantar: tras una `IntegrityError` la sesión
    queda en estado abortado y cualquier consulta posterior fallaría con un
    error distinto que ya no dice nada del problema real.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise integrity_conflict(exc, context=context) from exc


__all__ = ["constraint_name", "flush_or_conflict", "integrity_conflict"]
