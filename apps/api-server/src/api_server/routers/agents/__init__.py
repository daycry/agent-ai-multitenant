"""`/agents` endpoints — CRUD tenant-scoped con filtros de scope.

Modelo de auth
--------------
- Todos los endpoints exigen un JWT válido (`get_tenant_session` -> AsyncSession
  con RLS). Los usuarios de un tenant ven sus propias plantillas + los
  `project_local`, más todos los agentes `global_builtin` (estos últimos vía la
  policy `agents_global_builtin_read` de la migración 0004).
- Las escrituras están restringidas por scope:
    * `project_local` y `global_tenant_template` -> cualquier usuario del tenant.
    * `global_builtin` -> bloqueado aquí (403). El System Admin los crea/actualiza
      con los scripts de seed o con futuros endpoints /admin/agents; los built-in
      no se editan desde la API de tenant.

Semántica del borrado suave
---------------------------
`DELETE /agents/{id}` sella `deleted_at`. La fila se queda para auditoría pero
queda fuera de las queries de list/get (`deleted_at IS NULL` en cada lectura).
Reutilizar el mismo nombre después está permitido porque hoy no hay restricciones
de unicidad sobre agents.

## Por qué esto es un paquete (plan prod-16, `task_prod16_12`)

Era un solo `routers/agents.py` de **1462 líneas** con seis responsabilidades
distintas. Repartido:

  * :mod:`.common` — lo que comparten varios módulos. **No tiene rutas.**
  * :mod:`.crud`   — list / provider-options / get / create / update / delete.
  * :mod:`.forks`  — fork, diff contra el origen y merge selectivo (ADR 0006).
  * :mod:`.knowledge_bases` — los grants agente↔KB del Plan 06.9.
  * :mod:`.prompt_versions` — el historial del `system_prompt` (`task_gov_02`).
  * :mod:`.tools`  — asignación de tools (06.15) y el set efectivo (06.18).
  * :mod:`.skills` — asignación de skills (ADR 0050).
  * :mod:`.capabilities` — el Hub de Capacidad del Plan 06.17.

El montaje de abajo es lo que hace que las rutas sean **las mismas**: los
sub-routers no llevan prefijo propio y cuelgan del `/agents` de siempre.

**El orden de montaje no es cosmético en ESTE router.** `GET /agents/provider-options`
y `GET /agents/{agent_id}` solapan —la segunda es paramétrica de un solo
segmento—, y FastAPI casa por ORDEN DE REGISTRO. Las dos viven en :mod:`.crud`, en
el orden correcto, y `crud` se monta el PRIMERO; si `provider-options` quedara
detrás del comodín, el endpoint desaparecería en silencio y la petición la
serviría `get_agent`, devolviendo 422 al intentar parsear ``"provider-options"``
como UUID. Ni un import roto, ni un tipo mal, ni una ruta perdida del conjunto:
nada lo delataría salvo el test de ORDEN de
``tests/unit/test_agents_router_package.py``.

**Y ojo con `route_paths`**: desde FastAPI 0.141 `include_router` ya no aplana,
así que un router compuesto de sub-routers como éste presenta `_IncludedRouter`
sin `.path`. Cualquier introspección sobre él —empezando por
``main._is_admin_surface``, que decide si un router lleva la guarda de System
Admin— tiene que usar :func:`api_server.routing_introspection.route_paths` y no
`router.routes` a pelo. Este es el segundo paquete del repo que anida
sub-routers, tras `routers/sso/`.
"""

from __future__ import annotations

from fastapi import APIRouter

from api_server.routers.agents import (
    capabilities,
    common,
    crud,
    forks,
    knowledge_bases,
    prompt_versions,
    skills,
    tools,
)
from api_server.routers.agents.common import (
    _agent_capability_ids,
    _clone_agent_capabilities,
    _teams_by_agent,
)
from api_server.routers.agents.tools import EffectiveToolEntry, EffectiveToolsResponse

# El prefijo `/agents` lo lleva CADA sub-router, no este contenedor, y es una
# obligación de FastAPI, no una preferencia: `GET /agents` y `POST /agents` se
# declaran con ruta vacía, e `include_router` rechaza —con `FastAPIError:
# Prefix and path cannot be both empty`— incluir un router que tenga una ruta
# vacía sin darle prefijo en la llamada. Con el prefijo en el hijo, sus rutas ya
# nacen como `/agents`, y el contenedor solo las agrupa.
router = APIRouter()
# `crud` PRIMERO y no por gusto: trae `/provider-options`, que tiene que
# registrarse antes que su `/{agent_id}` (ver el docstring de arriba).
router.include_router(crud.router)
router.include_router(forks.router)
router.include_router(knowledge_bases.router)
# `prompt-versions` tiene DOS segmentos, así que no solapa con `/{agent_id}` y no
# necesita el cuidado de orden que sí exige `provider-options` (ver arriba).
router.include_router(prompt_versions.router)
router.include_router(tools.router)
router.include_router(skills.router)
router.include_router(capabilities.router)

__all__ = [
    "EffectiveToolEntry",
    "EffectiveToolsResponse",
    "_agent_capability_ids",
    "_clone_agent_capabilities",
    "_teams_by_agent",
    "capabilities",
    "common",
    "crud",
    "forks",
    "knowledge_bases",
    "prompt_versions",
    "router",
    "skills",
    "tools",
]
