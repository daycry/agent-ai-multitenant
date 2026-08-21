"""Guardas de visibilidad compartidas por los routers (plan prod-14, quality-8).

Un *guard* aquí es una función que convierte «RLS no me devolvió filas» en un
error HTTP explícito. Suena trivial y no lo es: el predicado exacto de esos
lookups (¿filtra `deleted_at`?, ¿404 o 403?, ¿qué `detail` devuelve?) ES la
frontera de aislamiento. Cuando el mismo guard vive copiado en cuatro routers,
cada copia puede divergir por separado — y una divergencia en la copia de un
router es un hueco de tenancy que no se ve en el diff de los otros tres.

`verify_project_visible` estaba cuadruplicado en `tasks.py`, `plans.py`,
`conversations.py` e `incoming_webhook_configs.py`, ya con tres docstrings
distintas y dos firmas distintas (tres devolvían el `Project`, una devolvía
`None`). Este módulo es la definición canónica.

Este módulo NO importa nada de los routers: solo modelos, SQLAlchemy y FastAPI.
Así puede usarse desde cualquiera de ellos sin ciclos de import.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Project

__all__ = ["PROJECT_NOT_FOUND_DETAIL", "verify_project_visible", "verify_project_visible_id"]

# Un único `detail` para las cuatro rutas. Que sea idéntico es parte de la
# guarda: un mensaje distinto por router es un canal lateral que confirma en qué
# subsistema existe un id que no deberías poder ver.
PROJECT_NOT_FOUND_DETAIL = "project not found"


async def verify_project_visible(session: AsyncSession, project_id: UUID) -> Project:
    """Devuelve el proyecto de la ruta, o levanta 404.

    La RLS ya esconde los proyectos de otros tenants: esta función traduce ese
    «0 filas» a un 404 EXPLÍCITO en vez de dejar que el fallo aparezca más
    abajo como un error de clave ajena (que además filtraría, por el mensaje de
    PostgreSQL, que el id existe en algún sitio).

    Incluye `deleted_at IS NULL` a propósito: un proyecto borrado (soft) no debe
    aceptar tareas, planes, conversaciones ni webhooks nuevos. Este predicado
    —tenant por RLS **y** no borrado— es la referencia canónica; si algún día el
    dispatch del orchestrator añade el filtro `deleted_at` (hallazgo db-5), debe
    coincidir con este.

    404 y no 403 igualmente a propósito: distinguirlos revelaría la existencia
    del proyecto en otro tenant.
    """
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL)
    return project


async def verify_project_visible_id(session: AsyncSession, project_id: UUID) -> None:
    """Igual que :func:`verify_project_visible` pero sin traer la fila entera.

    Para los llamantes que solo necesitan la comprobación (el caso de
    `incoming_webhook_configs`, que no usa ninguna columna del proyecto). Mismo
    predicado y mismo `detail`: si divergen, el 404 deja de ser indistinguible.
    """
    result = await session.execute(
        select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL)
