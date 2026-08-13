"""Nombre de agente duplicado → 409 con sugerencia, nunca 500.

## De dónde sale esto

La migración 0126 (2026-07-30) puso dos índices únicos parciales sobre `agents`:

    uq_agents_tenant_project_name_live  (tenant_id, project_id, name)
                                        WHERE deleted_at IS NULL AND project_id IS NOT NULL
    uq_agents_tenant_name_global_live   (tenant_id, name)
                                        WHERE deleted_at IS NULL AND project_id IS NULL

Son la regla de negocio correcta —un espacio de nombres de agente por tenant y
por proyecto— y no se discuten aquí. Lo que faltaba era el otro lado del índice:
TRES endpoints crean un agente HEREDANDO el nombre del origen cuando no se les da
uno (`POST /agents/{id}/fork`, `POST /teams/{id}/adopt` y
`POST /human-agents/templates/{id}/clone`), y ninguno atrapaba la
`IntegrityError`. Forkear dos veces la misma plantilla al mismo destino salía por
la API como un **500**, que para quien usa la UI es indistinguible de «la
plataforma se ha roto» cuando lo que ha pasado es que ese nombre ya está cogido.

## Por qué un módulo y no un `except` en cada sitio

Porque los tres necesitan exactamente lo mismo y el matiz que hay que acertar es
el mismo: **cuál de los dos índices se violó**, que no es un detalle interno sino
la diferencia entre «ya hay un agente así en este proyecto» y «…en este tenant» —
dos acciones distintas para quien lo lee. Tres copias del `except` serían tres
sitios donde equivocarse de índice.

## Quién manda: el índice, no el pre-check

El `except IntegrityError` es obligatorio y es el que cierra la carrera: dos
peticiones simultáneas con el mismo nombre pasan las dos cualquier comprobación
previa, y una revienta igual. **El índice es la única autoridad.**

El SELECT previo existe por otra razón: para poder proponer un nombre LIBRE. Y va
antes del flush, no después, por una razón concreta que se comprobó a mano
(`Session._flush` con SQLAlchemy 2.0.49): cuando un flush falla, la transacción
exterior queda `DEACTIVE` **aunque el flush vaya dentro de un
`session.begin_nested()`**. Con la sesión de `auth/deps.open_tenant_session` —que
abre `async with session.begin()`— la consulta siguiente muere con
`InvalidRequestError: Can't operate on closed transaction inside context
manager`. Y si se hace el `rollback()` completo para desatascarla, se va con él el
`set_config('app.tenant_id', …, is_local := true)` que instala la RLS —es de
ámbito TRANSACCIÓN—, así que la consulta correría sin tenant y no vería NADA: la
sugerencia saldría siempre «X (copia)» aunque ese nombre también estuviese
cogido. Un fallo silencioso, del peor tipo. Preguntando ANTES, la respuesta es
correcta y el `except` sigue estando para lo que importa.

En la rama de carrera (el pre-check dijo «libre» y el índice dijo que no) la
sugerencia se calcula con lo que se leyó antes, que ya no está fresco. Es
best-effort a propósito: es un caso raro, y el 409 —que es lo que evita el 500—
sale igual de bien.

## Por qué se sugiere y no se renombra solo

Decisión del operador: **el nombre es identidad** — por él se eligen agentes en
los `role_map` y al montar equipos. Renombrar en silencio decide por el usuario
algo que después tiene que deshacer, y lo descubre tarde. La API rechaza con 409 y
devuelve `suggested_name` para que la UI lo ponga DELANTE del usuario, visible y
editable.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Agent
from api_server.routers._integrity import constraint_name, integrity_conflict

#: Los dos índices únicos parciales de la 0126. Cualquier otra constraint que
#: salte aquí NO es cosa nuestra y se delega en `_integrity` (que la traduce a un
#: 409 genérico sin filtrar el crudo de PostgreSQL).
PROJECT_NAME_CONSTRAINT: Final = "uq_agents_tenant_project_name_live"
TENANT_NAME_CONSTRAINT: Final = "uq_agents_tenant_name_global_live"
AGENT_NAME_CONSTRAINTS: Final[frozenset[str]] = frozenset(
    {PROJECT_NAME_CONSTRAINT, TENANT_NAME_CONSTRAINT}
)

#: `Agent.name` es `String(120)`: una sugerencia más larga que eso no es una
#: sugerencia, es el siguiente 500.
_NAME_MAX_LENGTH: Final[int] = 120

#: Cuántos «(copia N)» se prueban antes de rendirse y tirar de sufijo aleatorio.
#: Con 50 copias del mismo nombre en un proyecto, el problema ya no es el nombre.
_MAX_SUGGESTION_ATTEMPTS: Final[int] = 50


def _like_escape(value: str) -> str:
    """Neutraliza `%`, `_` y `\\` para que un nombre con comodines no ensanche el
    LIKE (un agente llamado `Analista_%` no debe traerse medio catálogo)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _candidate(base: str, attempt: int) -> str:
    """`base (copia)`, `base (copia 2)`, `base (copia 3)`… recortado a 120."""
    suffix = " (copia)" if attempt == 1 else f" (copia {attempt})"
    room = _NAME_MAX_LENGTH - len(suffix)
    return f"{base[:room].rstrip()}{suffix}"


def first_free_name(base: str, taken: set[str]) -> str:
    """El primer `base (copia N)` que no esté en `taken`."""
    for attempt in range(1, _MAX_SUGGESTION_ATTEMPTS + 1):
        candidate = _candidate(base, attempt)
        if candidate not in taken:
            return candidate
    # Salida de emergencia: un sufijo corto y aleatorio. Feo, pero libre.
    return _candidate(base, 1)[: _NAME_MAX_LENGTH - 7] + f" {uuid4().hex[:6]}"


async def taken_agent_names(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    prefix: str,
) -> set[str]:
    """Los nombres VIVOS que empiezan por `prefix` en el mismo espacio de nombres.

    `project_id` no es un adorno: define en cuál de los dos índices estamos, y
    mirar el espacio equivocado devolvería un nombre igual de ocupado.

    Va con `no_autoflush` a propósito: el agente que estamos intentando crear ya
    está PENDIENTE en la sesión, y un autoflush lo insertaría aquí dentro — es
    decir, la misma `IntegrityError` que venimos a evitar, ahora desde el sitio
    que venía a explicarla.
    """
    stmt = select(Agent.name).where(
        Agent.tenant_id == tenant_id,
        Agent.deleted_at.is_(None),
        Agent.name.like(f"{_like_escape(prefix)}%", escape="\\"),
        # El índice es parcial por `project_id IS NULL` / `IS NOT NULL`: el filtro
        # se parte igual. `== None` generaría un `= NULL` que nunca casa.
        Agent.project_id.is_(None) if project_id is None else Agent.project_id == project_id,
    )
    with session.no_autoflush:
        return set((await session.execute(stmt)).scalars().all())


def agent_name_conflict(
    *,
    constraint: str,
    name: str,
    suggestion: str,
    hint: str | None = None,
) -> HTTPException:
    """El 409 de dominio, con el mensaje escrito para quien lo va a leer.

    El `detail` mantiene la forma que ya usan los 409 del proyecto —`{"error",
    "message"}` (ver `_integrity`)— y añade los campos que la UI necesita para
    cumplir la parte (b) de la decisión: `conflicting_name` y `suggested_name`.
    El nombre del ÍNDICE no sale nunca al cliente; se traduce a `namespace`.
    """
    in_project = constraint == PROJECT_NAME_CONSTRAINT
    where = "en este proyecto" if in_project else "en este tenant"
    message = (
        f"Ya existe un agente llamado «{name}» {where}. Elige otro nombre"
        f" —por ejemplo «{suggestion}»— o renombra/borra el que ya está."
    )
    if hint:
        message = f"{message} {hint}"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "duplicate_agent_name",
            "message": message,
            "conflicting_name": name,
            "suggested_name": suggestion,
            "namespace": "project" if in_project else "tenant",
        },
    )


async def flush_agent_or_conflict(
    session: AsyncSession,
    *,
    context: str,
    name: str,
    tenant_id: UUID,
    project_id: UUID | None,
    hint: str | None = None,
) -> None:
    """`session.flush()` que convierte el nombre de agente repetido en un 409.

    Sustituye al `session.flush()` desnudo en todo sitio que inserte un `Agent`
    cuyo nombre puede venir heredado del origen. `context` solo viaja al log del
    servidor (vía `_integrity.integrity_conflict` para el caso no-nuestro).

    Deja la sesión con `rollback()` hecho antes de levantar por la vía del
    `except`, igual que `flush_or_conflict`: tras una `IntegrityError` la sesión
    queda abortada y cualquier consulta posterior fallaría con un error que ya no
    dice nada del problema real.
    """
    taken = await taken_agent_names(
        session, tenant_id=tenant_id, project_id=project_id, prefix=name
    )
    if name in taken:
        raise agent_name_conflict(
            # Sin `IntegrityError` que lo diga, el índice se deduce del sitio
            # donde vive el agente, que es la misma regla que aplica PostgreSQL.
            constraint=(TENANT_NAME_CONSTRAINT if project_id is None else PROJECT_NAME_CONSTRAINT),
            name=name,
            suggestion=first_free_name(name, taken),
            hint=hint,
        )

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        violated = constraint_name(exc)
        if violated is None or violated not in AGENT_NAME_CONSTRAINTS:
            # No es un choque de nombre de agente: que lo traduzca el genérico,
            # que como mínimo garantiza que el crudo de PostgreSQL no sale.
            raise integrity_conflict(exc, context=context) from exc
        # Carrera: entre el SELECT y el INSERT alguien cogió el nombre. La
        # sugerencia sale de lo leído antes (best-effort, ver el docstring del
        # módulo); el 409 —que es lo que evita el 500— es igual de correcto.
        raise agent_name_conflict(
            constraint=violated,
            name=name,
            suggestion=first_free_name(name, taken | {name}),
            hint=hint,
        ) from exc


__all__ = [
    "AGENT_NAME_CONSTRAINTS",
    "PROJECT_NAME_CONSTRAINT",
    "TENANT_NAME_CONSTRAINT",
    "agent_name_conflict",
    "first_free_name",
    "flush_agent_or_conflict",
    "taken_agent_names",
]
