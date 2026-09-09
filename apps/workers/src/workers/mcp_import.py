"""Import automático de las tools de un servidor MCP en la lane `marketplace`.

ADR 0166 D3, `task_mk_01`.

Un despliegue del marketplace que escribe un servidor en `projects.mcp_servers`
no puede descubrirlo en línea: el handler tiene la transacción del request
abierta y el discovery es una red de hasta 300 s (perf-2/db-2). Encola este
mensaje tras el commit; lo mismo hace el final del «Conectar» de OAuth (D5).
Aquí, fuera de cualquier request, se llama a la MISMA función que usa el botón
manual —`api_server.mcp.import_tools.import_server_tools`— con `tool_names=None`
(todas las anunciadas, con reconciliación R3 y tope L1) y sin `security_level`
(R2: el automatismo no pisa la elección del operador).

Tres cosas que este proceso cumple y que el ADR deja escritas porque es donde D5
se rompería si no fuesen ciertas: la imagen de workers se construye SOBRE la del
api-server (importa `api_server` tal cual, como `marketplace_gates`); corre con
rol **BYPASSRLS**, así que cada query lleva su `tenant_id` explícito y ese id es
el del mensaje; y no tiene `HTTP_PROXY`, por lo que el descubrimiento sale por el
egress-proxy con la misma factoría que el api-server (ADR 0165 D9).

**Estado derivado (D4).** No hay `importing` que se pueda quedar atascado: si
esta lane no está levantada, la tarjeta sigue diciendo «sin importar» y el botón
manual sigue funcionando. Por eso la task nunca propaga: deja rastro y termina.
La única excepción es el `IntegrityError` de una carrera por `(tenant, name)`
—dos despliegues del mismo listing en dos proyectos del tenant—: en la ruta
asíncrona no hay a quién devolverle un 409, así que se REINTENTA con backoff
acotado (R5) y al segundo intento el upsert converge.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from celery import Task
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_session

_log = structlog.get_logger("workers.mcp_import")

#: Públicas: el productor (`api_server.celery_client`) declara las suyas y un test
#: compara las dos parejas. Un nombre distinto en cada lado deja el mensaje en el
#: broker para siempre mientras el productor devuelve `True`.
TASK_NAME = "workers.mcp_import_server_tools"
QUEUE = "marketplace"
MAX_RETRIES = 3

__all__ = ["MAX_RETRIES", "QUEUE", "TASK_NAME", "import_server_tools_task"]


@app.task(name=TASK_NAME, bind=True, max_retries=MAX_RETRIES)  # type: ignore[untyped-decorator]
def import_server_tools_task(
    self: Task,
    *,
    tenant_id: str,
    project_id: str,
    server_name: str,
    installation_id: str | None = None,
    listing_id: str | None = None,
    version: str | None = None,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    """Entry point Celery. Nunca propaga salvo para reintentar una carrera (R5)."""
    try:
        return asyncio.run(
            _import_async(
                tenant_id=UUID(tenant_id),
                project_id=UUID(project_id),
                server_name=server_name,
                installation_id=UUID(installation_id) if installation_id else None,
                listing_id=UUID(listing_id) if listing_id else None,
                version=version,
                roles=list(roles or []),
            )
        )
    except IntegrityError as exc:
        # Carrera por `(tenant, name)`: al segundo intento las filas ya existen y el
        # upsert converge. Backoff acotado: 2, 4, 8 s.
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc
    except Exception as exc:  # defensivo: la task no puede morir sin dejar rastro
        _log.exception(
            "mcp.import.task_failed",
            project_id=project_id,
            server=server_name,
            error=str(exc),
        )
        return {"server": server_name, "status": f"error:{type(exc).__name__}"}


def _vault_resolver(settings: Settings) -> Any:
    """El resolver de Vault del worker, o None si no está configurado (entonces un
    servidor con `auth_ref` u OAuth falla tipado, no a ciegas). Pasa por la
    fábrica del worker: es la que mantiene el token renovándose (prod-10)."""
    from shared_mcp import HvacVaultResolver

    from workers.vault_client import build_worker_vault_client

    client = build_worker_vault_client(settings)
    return None if client is None else HvacVaultResolver(client=client)


async def _import_async(
    *,
    tenant_id: UUID,
    project_id: UUID,
    server_name: str,
    installation_id: UUID | None,
    listing_id: UUID | None,
    version: str | None,
    roles: list[str],
) -> dict[str, Any]:
    from api_server.db.domain import Project
    from api_server.mcp.import_tools import (
        ConfigInvalidError,
        DiscoveryFailedError,
        ImportAbstainedError,
        Provenance,
        ServerNotDeclaredError,
        import_server_tools,
    )

    settings = get_settings()
    async with worker_session(settings) as session:
        project = (
            await session.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if project is None:
            _log.warning("mcp.import.project_not_found", project_id=str(project_id))
            return {"server": server_name, "status": "project_not_found"}

        provenance = (
            Provenance(listing_id=listing_id, installation_id=installation_id, version=version)
            if installation_id is not None and listing_id is not None
            else None
        )
        try:
            outcome = await import_server_tools(
                session,
                tenant_id=tenant_id,
                project=project,
                server_name=server_name,
                tool_names=None,
                security_level=None,
                resolver=_vault_resolver(settings),
                proxy_url=settings.egress_proxy_url,
                provenance=provenance,
            )
        except (ServerNotDeclaredError, ConfigInvalidError, ImportAbstainedError) as exc:
            await session.rollback()
            _log.warning(
                "mcp.import.skipped",
                project_id=str(project_id),
                server=server_name,
                reason=str(exc),
            )
            return {
                "server": server_name,
                "status": f"skipped:{type(exc).__name__}",
                "reason": str(exc),
            }
        except DiscoveryFailedError as exc:
            await session.rollback()
            _log.warning(
                "mcp.import.discovery_failed",
                project_id=str(project_id),
                server=server_name,
                error_code=exc.failure.error_code,
                reason=exc.failure.message,
            )
            return {
                "server": server_name,
                "status": f"discovery_failed:{exc.failure.error_code}",
                "reason": exc.failure.message,
            }

        # El role_map del despliegue, aplicado ahora que las filas existen (ADR
        # 0128): sólo sobre las tools sin política previa — la que había se respeta.
        applied: list[str] = []
        if roles and outcome.tools:
            policy = {
                str(k): list(v)
                for k, v in (project.mcp_tool_roles or {}).items()
                if isinstance(v, list)
            }
            for row in outcome.tools:
                if row.name not in policy:
                    policy[row.name] = list(roles)
                    applied.append(row.name)
            if applied:
                project.mcp_tool_roles = policy

        await session.commit()
        result = {
            "server": server_name,
            "status": "ok",
            "created": len(outcome.created),
            "refreshed": len(outcome.refreshed),
            "retired": len(outcome.retired),
            "omitted": len(outcome.omitted),
            "roles_applied": len(applied),
            "warnings": outcome.warnings,
        }
        _log.info("mcp.import.done", project_id=str(project_id), **result)
        return result
