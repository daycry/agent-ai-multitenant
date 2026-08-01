"""CRUD de las capas de guardrails (prod-03 task_prod03_08, guardrails-4).

Tres capas —plataforma → tenant → proyecto— y una regla que las hace útiles:
**lo que la plataforma bloquea no se relaja abajo**. Ese `strict=True` es lo que
convierte `LockedFieldOverrideError` (que existía desde el Plan 11 sin un solo
llamante fuera de tests) en un **422 con mensaje**, en vez de un override que se
ignora en silencio y deja al tenant creyendo que apagó el check.

Superficie:

* ``GET  /guardrails/config``                          config EFECTIVA + recibo
  de procedencia (qué capa ganó cada check y cuáles están bloqueados);
* ``GET  /guardrails/config/layers/{scope}``           una capa, tal cual;
* ``PUT  /guardrails/config/layers/tenant``            escribe la capa tenant;
* ``PUT  /guardrails/config/layers/project/{id}``      escribe la de un proyecto;
* ``DELETE`` de las dos anteriores.

La capa de PLATAFORMA se lee pero no se escribe por aquí: la siembra el baseline
y la edita un System Admin. La RLS de la migración 0132 lo respalda —su
``WITH CHECK`` no admite la rama ``tenant_id IS NULL``—, así que aunque este
router se equivocara, PostgreSQL diría que no.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from shared_guardrails.exceptions import GuardrailConfigError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.guardrail_config import (
    LockedFieldOverrideError,
    delete_layer_config,
    get_layer_config,
    resolve_effective_layers,
    set_layer_config,
)
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.guardrail_configs import (
    GuardrailEffectiveConfigResponse,
    GuardrailLayerResponse,
    GuardrailLayerUpdate,
    GuardrailProvenanceEntry,
    GuardrailRejectedOverride,
    to_layer_response,
)

router = APIRouter(prefix="/guardrails/config", tags=["guardrails"])

_Scope = Literal["platform", "tenant", "project"]


def _locked_override_422(exc: LockedFieldOverrideError) -> HTTPException:
    """El 422 que el operador necesita leer: qué check, en qué hook, y por qué.

    Un 422 con «validation error» no serviría de nada aquí: el tenant admin está
    intentando apagar un guardrail y tiene que entender que no puede y cuál.
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "locked_guardrail_override",
            "hook": exc.hook,
            "key": exc.key,
            "layer": exc.layer,
            "message": (
                f"El guardrail '{exc.key}' del hook '{exc.hook}' está bloqueado por la "
                f"plataforma: la capa '{exc.layer}' no puede sobrescribirlo ni eliminarlo."
            ),
        },
    )


@router.get("", response_model=GuardrailEffectiveConfigResponse)
async def get_effective_config(
    project_id: UUID | None = None,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailEffectiveConfigResponse:
    """La config que se aplicaría de verdad, con su procedencia por check."""
    tenant_id = require_tenant_id(principal)
    resolved = await resolve_effective_layers(session, tenant_id=tenant_id, project_id=project_id)
    return GuardrailEffectiveConfigResponse(
        config=resolved.config.to_dict(),
        provenance=[
            GuardrailProvenanceEntry(
                hook=p.hook,
                key=p.key,
                type=p.type,
                winning_layer=p.winning_layer,
                locked=p.locked,
            )
            for provs in resolved.provenance.values()
            for p in provs
        ],
        rejected_overrides=[
            GuardrailRejectedOverride(
                hook=r.hook, key=r.key, attempted_by=r.attempted_by, reason=r.reason
            )
            for r in resolved.rejected_overrides
        ],
        locked_keys={hook: keys for hook, keys in resolved.locked_keys.items() if keys},
    )


@router.get("/layers/{scope}", response_model=GuardrailLayerResponse)
async def read_layer(
    scope: _Scope,
    project_id: UUID | None = None,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailLayerResponse:
    tenant_id = require_tenant_id(principal)
    if scope == "project" and project_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id required for scope='project'",
        )
    row = await get_layer_config(
        session,
        scope,
        tenant_id=None if scope == "platform" else tenant_id,
        project_id=project_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="layer not configured")
    return to_layer_response(row)


@router.put("/layers/tenant", response_model=GuardrailLayerResponse)
async def put_tenant_layer(
    payload: GuardrailLayerUpdate,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailLayerResponse:
    tenant_id = require_tenant_id(principal)
    try:
        row = await set_layer_config(
            session,
            "tenant",
            payload.config,
            tenant_id=tenant_id,
            actor_id=principal.user_id,
        )
    except LockedFieldOverrideError as exc:
        raise _locked_override_422(exc) from exc
    except GuardrailConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return to_layer_response(row)


@router.put("/layers/project/{project_id}", response_model=GuardrailLayerResponse)
async def put_project_layer(
    project_id: UUID,
    payload: GuardrailLayerUpdate,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailLayerResponse:
    tenant_id = require_tenant_id(principal)
    try:
        row = await set_layer_config(
            session,
            "project",
            payload.config,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=principal.user_id,
        )
    except LockedFieldOverrideError as exc:
        raise _locked_override_422(exc) from exc
    except GuardrailConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return to_layer_response(row)


@router.delete("/layers/tenant", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant_layer(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    tenant_id = require_tenant_id(principal)
    if not await delete_layer_config(session, "tenant", tenant_id=tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="layer not configured")


@router.delete("/layers/project/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_layer(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    tenant_id = require_tenant_id(principal)
    if not await delete_layer_config(
        session, "project", tenant_id=tenant_id, project_id=project_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="layer not configured")
