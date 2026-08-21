"""Refrescar los despliegues cuando la instalación cambia de versión (ADR 0142 D7).

Vive aparte de `deploy.py` —que ya sostiene el alta y la retirada en 950
líneas— porque es una operación distinta con su propio contrato: no crea ni
deshace nada en el proyecto, **re-encaja la configuración** de lo que ya está
desplegado en el `config_schema` de la versión nueva.

Tres resultados posibles por despliegue, y ninguno es «se aplica como se pueda»:

* **actualizado** — los valores caben en el esquema nuevo (campos nuevos toman
  su default, los retirados se van) y el despliegue avanza de `deployed_version`;
* **deshabilitado** — el esquema nuevo exige un campo que no existe ni tiene
  default. El despliegue queda `disabled` **con el motivo escrito**
  (`disabled_reason`, migración 0130) y su config intacta, para que el humano
  vea qué falta y lo rellene. Aplicarlo a medias dejaría al proyecto con una
  capacidad configurada a medias, que es peor que no tenerla;
* **intacto** — ya estaba en esa versión.

Y una regla que es la mitad del valor de este módulo: **el fallo de un
despliegue no arrastra a los demás**. Un proyecto cuyo esquema se rompe no
puede impedir que los otros nueve se actualicen; si lo hiciera, una sola config
huérfana congelaría el tenant entero en una versión vieja.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    DeploymentStatus,
    MarketplaceDeployment,
)
from api_server.marketplace.config_schema import apply_schema_migration, dropped_fields

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DeploymentRefresh:
    """Qué le pasó a UN despliegue."""

    deployment_id: UUID
    project_id: UUID
    #: ``updated`` / ``disabled`` / ``unchanged``
    outcome: str
    from_version: str
    to_version: str
    #: Campos que el esquema nuevo ya no declara y se descartaron. Se enseñan:
    #: perder ajustes en silencio es cómo se pierde la confianza en la función.
    dropped: tuple[str, ...] = ()
    #: Por qué quedó `disabled`. Vacío en los otros dos desenlaces.
    problems: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": str(self.deployment_id),
            "project_id": str(self.project_id),
            "outcome": self.outcome,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "dropped": list(self.dropped),
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class RefreshReport:
    """El resultado de refrescar TODOS los despliegues de una instalación."""

    results: tuple[DeploymentRefresh, ...] = field(default=())

    @property
    def updated(self) -> tuple[DeploymentRefresh, ...]:
        return tuple(r for r in self.results if r.outcome == "updated")

    @property
    def disabled(self) -> tuple[DeploymentRefresh, ...]:
        return tuple(r for r in self.results if r.outcome == "disabled")

    def as_dict(self) -> dict[str, Any]:
        return {
            "results": [r.as_dict() for r in self.results],
            "updated": len(self.updated),
            "disabled": len(self.disabled),
        }


def plan_refresh(
    *,
    current_config: dict[str, Any] | None,
    new_schema: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """`(valores_reencajados, problemas, descartados)`. Puro, sin BD.

    Delegado entero en `config_schema`: la aritmética de «campo nuevo toma su
    default, retirado se va, requerido-sin-default se señala» ya vive allí y
    tiene sus tests. Aquí solo se junta con la lista de descartados, que la UI
    necesita para decir «se perdieron estos ajustes».
    """
    values, problems = apply_schema_migration(current_config, new_schema)
    return values, problems, dropped_fields(current_config, new_schema)


async def refresh_installation_deployments(
    session: AsyncSession,
    *,
    installation_id: UUID,
    new_version: str,
    new_schema: dict[str, Any] | None,
) -> RefreshReport:
    """Re-encaja cada despliegue VIVO de la instalación en el esquema nuevo.

    «Vivo» = `active` o `disabled`. Los `retired` se quedan fuera a propósito:
    son historia, y actualizarles la versión reescribiría el registro de qué
    estuvo desplegado.

    Un despliegue previamente `disabled` que AHORA sí encaja vuelve a `active`
    con el motivo borrado — es el camino de vuelta, y sin él la única salida de
    un `disabled` sería retirar y volver a desplegar a mano.

    El caller es dueño de la transacción y del commit.
    """
    rows = (
        (
            await session.execute(
                select(MarketplaceDeployment).where(
                    MarketplaceDeployment.installation_id == installation_id,
                    MarketplaceDeployment.status != DeploymentStatus.RETIRED.value,
                )
            )
        )
        .scalars()
        .all()
    )

    results: list[DeploymentRefresh] = []
    for deployment in rows:
        from_version = deployment.deployed_version
        if from_version == new_version and deployment.status == DeploymentStatus.ACTIVE.value:
            results.append(
                DeploymentRefresh(
                    deployment_id=deployment.id,
                    project_id=deployment.project_id,
                    outcome="unchanged",
                    from_version=from_version,
                    to_version=new_version,
                )
            )
            continue

        values, problems, dropped = plan_refresh(
            current_config=dict(deployment.config or {}), new_schema=new_schema
        )

        if problems:
            # NO se aplica nada: ni los valores ni la versión. La config vieja se
            # conserva para que el humano la vea al rellenar lo que falta.
            deployment.status = DeploymentStatus.DISABLED.value
            deployment.disabled_reason = (
                f"La versión {new_version} cambió la configuración requerida: "
                + "; ".join(problems)
            )
            results.append(
                DeploymentRefresh(
                    deployment_id=deployment.id,
                    project_id=deployment.project_id,
                    outcome="disabled",
                    from_version=from_version,
                    to_version=new_version,
                    dropped=tuple(dropped),
                    problems=tuple(problems),
                )
            )
            continue

        deployment.config = values
        deployment.deployed_version = new_version
        deployment.status = DeploymentStatus.ACTIVE.value
        deployment.disabled_reason = None
        results.append(
            DeploymentRefresh(
                deployment_id=deployment.id,
                project_id=deployment.project_id,
                outcome="updated",
                from_version=from_version,
                to_version=new_version,
                dropped=tuple(dropped),
            )
        )

    await session.flush()

    report = RefreshReport(results=tuple(results))
    logger.info(
        "marketplace_deployments_refreshed",
        installation_id=str(installation_id),
        new_version=new_version,
        updated=len(report.updated),
        disabled=len(report.disabled),
    )
    return report


__all__ = [
    "DeploymentRefresh",
    "RefreshReport",
    "plan_refresh",
    "refresh_installation_deployments",
]
