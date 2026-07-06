"""Resolución + builder del modelo LLM del córtex del System Owner (F1, ADR 0074).

Clona el patrón de :mod:`api_server.assistant.model_config` pero **sin override por
tenant**: el córtex es un singleton del owner, así que su modelo se resuelve SOLO
desde un platform-default propio (clave ``cortex.default_model`` en
``platform_settings``), nunca desde ``tenant_settings``.

Forma almacenada idéntica a ``assistant.default_model``:
``{"provider_id", "model_id", "reasoning_effort"?}``. A diferencia del asistente,
el córtex razona en profundidad por diseño (ADR 0074): cuando la selección no fija
``reasoning_effort``, se resuelve a un esfuerzo **alto** por defecto.

Deliberación (ADR 0021/0076): el camino primario es ``claude_sdk`` con ``effort``
modulado; el razonamiento profundo + la web nativa (WebSearch/WebFetch) viven ahí.
Sin el Claude Agent SDK instalado (build sin WITH_CLAUDE) un modelo ``claude_sdk``
daría un ``ImportError`` aguas abajo → lo convertimos en una **degradación limpia**:
si hay otro provider del catálogo configurable, se usa el loop clásico con
``reasoning_effort``; si no hay nada construible, el builder señaliza la
indisponibilidad con :class:`CortexModelUnavailableError`, que el router (Bloque E)
traduce a un **503 honesto** (NUNCA un 500).

La web (Tarea 6, ADR 0076) se delega EXCLUSIVAMENTE al Claude Agent SDK: Anthropic
gestiona el fetch (anti-SSRF, sin egress propio), así que las web tools sólo se
activan en el camino ``claude_sdk``. Con cualquier otro kind, el córtex razona pero
NO busca en Internet (no hay web propia en F1).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from shared_llm.base import LLMProvider
from shared_llm.reasoning import reasoning_call_kwargs
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.graph import AssistantModelClient
from api_server.assistant.llm import LLMAssistantModel

if TYPE_CHECKING:
    from api_server.cortex.affect_policy import EffortDecision
from api_server.assistant.model_config import (
    AssistantModelSelection,
    ResolvedAssistantModel,
    _selection_from_value,
    to_provider_model_name,
)
from api_server.db.llm_providers import get_llm_provider
from api_server.db.models import User
from api_server.db.platform_settings import (
    get_platform_setting,
    set_platform_setting,
)

# Settings coordinate (platform_settings — escrito sólo por un System Admin).
CORTEX_DEFAULT_MODEL_KEY = "cortex.default_model"

# Esfuerzo de razonamiento por defecto del córtex cuando la selección no fija uno
# (ADR 0074: el córtex delibera en profundidad por diseño, no en "modo chat").
CORTEX_DEFAULT_REASONING_EFFORT = "high"

# Tools web NATIVAS del Claude Agent SDK (ADR 0076). Anthropic gestiona el fetch
# (anti-SSRF) — no se implementa navegador/egress propio. Sólo se pasan en el
# camino claude_sdk.
CORTEX_WEB_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")


class CortexModelUnavailableError(RuntimeError):
    """No hay un modelo del córtex utilizable (nada configurado, o un modelo
    ``claude_sdk`` sin el Claude Agent SDK y sin alternativa construible).

    El router la traduce a un **503 honesto** — jamás un 500."""


# ---------------------------------------------------------------------------
# Platform default (platform_settings: key 'cortex.default_model')
# ---------------------------------------------------------------------------
async def get_cortex_default_model(session: AsyncSession) -> AssistantModelSelection | None:
    """La selección de modelo por defecto del córtex, o ``None`` cuando no se ha
    configurado. Tolerante a un valor ausente/corrupto (devuelve ``None``)."""
    value = await get_platform_setting(session, CORTEX_DEFAULT_MODEL_KEY, default=None)
    return _selection_from_value(value)


async def set_cortex_default_model(
    session: AsyncSession,
    selection: AssistantModelSelection,
    *,
    actor: User,
) -> None:
    """Fija el modelo por defecto del córtex (System Admin only — ``set_platform_setting``
    re-verifica que el actor es System Admin)."""
    await set_platform_setting(session, CORTEX_DEFAULT_MODEL_KEY, selection.to_value(), actor=actor)


async def clear_cortex_default_model(session: AsyncSession, *, actor: User) -> None:
    """Desconfigura el modelo por defecto del córtex (System Admin only)."""
    await set_platform_setting(session, CORTEX_DEFAULT_MODEL_KEY, None, actor=actor)


# ---------------------------------------------------------------------------
# Resolución (SOLO platform-default: el córtex es singleton, sin override tenant)
# ---------------------------------------------------------------------------
async def resolve_cortex_model(admin_session: AsyncSession) -> ResolvedAssistantModel | None:
    """Resuelve el modelo efectivo del córtex, o ``None``.

    Sólo el platform-default ``cortex.default_model`` (sin override por tenant: el
    córtex es un singleton del owner). El nivel es utilizable cuando su provider
    sigue existiendo y está ACTIVO — el ``model_id`` NO se re-valida aquí (igual que
    el asistente: eso pasó al guardar, y re-chequear el catálogo en cada turno
    añadiría una ronda de red al hot-path). ``reasoning_effort`` cae al default ALTO
    del córtex (ADR 0074) cuando la selección no fija uno. ``None`` significa que no
    hay nada configurado → el router lo expone como 503."""
    selection = await get_cortex_default_model(admin_session)
    if selection is None:
        return None
    provider = await get_llm_provider(admin_session, selection.provider_id)
    if provider is None or not provider.is_active:
        return None
    return ResolvedAssistantModel(
        provider_id=selection.provider_id,
        model_id=selection.model_id,
        source="platform_default",
        provider_kind=provider.kind,
        provider_display_name=provider.display_name,
        reasoning_effort=selection.reasoning_effort or CORTEX_DEFAULT_REASONING_EFFORT,
    )


# ---------------------------------------------------------------------------
# Builder del modelo (reutilizable por el router en el Bloque E)
# ---------------------------------------------------------------------------
def cortex_call_kwargs(
    kind: str | None,
    reasoning_effort: str | None,
    *,
    web_enabled: bool = False,
) -> dict[str, object]:
    """Los ``extra_call_kwargs`` que se vuelcan al ``complete()`` del provider.

    Combina:
      * el ``reasoning_effort`` traducido al kwarg nativo del kind (ADR 0070,
        reutiliza :func:`shared_llm.reasoning.reasoning_call_kwargs`: ``effort`` para
        claude_sdk, ``reasoning_effort`` para el resto);
      * cuando ``web_enabled`` y el kind es ``claude_sdk``, las web tools nativas del
        SDK en ``allowed_tools`` (ADR 0076). Con cualquier otro kind NO se añade web
        (no hay navegador propio en F1).
    """
    kwargs: dict[str, object] = dict(reasoning_call_kwargs(kind, reasoning_effort))
    if web_enabled and kind == "claude_sdk":
        kwargs["allowed_tools"] = list(CORTEX_WEB_TOOLS)
    return kwargs


def build_cortex_model(
    resolved: ResolvedAssistantModel,
    *,
    provider: LLMProvider | None,
    claude_sdk_available: bool,
    web_enabled: bool = False,
) -> LLMAssistantModel:
    """Construye el modelo del córtex a partir de la selección resuelta.

    ``provider`` es el cliente concreto ya construido (vía
    :func:`api_server.llm_providers.factory.build_llm_provider`) o ``None`` cuando no
    se pudo construir (credencial/endpoint ausentes, SDK opcional no instalado).
    ``claude_sdk_available`` indica si el Claude Agent SDK está presente en ESTE
    proceso (el córtex corre EN el api-server).

    Degradación limpia (ADR 0074):
      * un modelo ``claude_sdk`` sin el SDK instalado NO puede deliberar aquí — el
        caller debe haber resuelto una alternativa del catálogo o, si no la hay,
        levantamos :class:`CortexModelUnavailableError` (→ 503 honesto, nunca 500);
      * un ``provider`` ``None`` (no construible) también señaliza indisponibilidad.

    El ``effort`` resuelto se propaga vía ``extra_call_kwargs`` igual que el asistente
    (y, como ``run_agent``/``complete`` ya propagan ``effort`` al SDK, el razonamiento
    profundo no se ignora). Las web tools (ADR 0076) se añaden SOLO en claude_sdk."""
    if resolved.provider_kind == "claude_sdk" and not claude_sdk_available:
        raise CortexModelUnavailableError(
            "el modelo del córtex usa Claude (claude_sdk) pero este proceso no incluye "
            "el Claude Agent SDK (build con WITH_CLAUDE=1); configura otro proveedor "
            "del catálogo para el córtex o redespliega con el SDK"
        )
    if provider is None:
        raise CortexModelUnavailableError(
            "el proveedor LLM configurado para el córtex no está disponible"
        )
    api_model = to_provider_model_name(resolved.provider_kind, resolved.model_id)
    extra = cortex_call_kwargs(
        resolved.provider_kind,
        resolved.reasoning_effort,
        web_enabled=web_enabled,
    )
    return LLMAssistantModel(
        provider=provider,
        model=api_model,
        extra_call_kwargs=extra,
        reasoning_effort=resolved.reasoning_effort,
        provider_kind=resolved.provider_kind,
    )


def apply_effort_decision(
    model: AssistantModelClient, decision: EffortDecision
) -> AssistantModelClient:
    """Reconstruye el modelo con el effort EFECTIVO de la política afectiva.

    Solo reconstruye cuando hay un cambio real y el modelo es el
    :class:`LLMAssistantModel` de producción (copia por-request vía
    ``dataclasses.replace`` — sin estado compartido): regenera el kwarg de
    razonamiento del kind preservando el resto (``allowed_tools`` incluido).
    Un doble de test (no-dataclass) queda intacto — la decisión se audita
    igualmente en la metadata del turno."""
    if decision.effective is None or decision.effective == decision.base:
        return model
    if not isinstance(model, LLMAssistantModel):
        return model
    kwargs = {
        k: v for k, v in model.extra_call_kwargs.items() if k not in ("effort", "reasoning_effort")
    }
    kwargs.update(reasoning_call_kwargs(model.provider_kind, decision.effective))
    return dataclasses.replace(model, extra_call_kwargs=kwargs, reasoning_effort=decision.effective)


__all__ = [
    "CORTEX_DEFAULT_MODEL_KEY",
    "CORTEX_DEFAULT_REASONING_EFFORT",
    "CORTEX_WEB_TOOLS",
    "CortexModelUnavailableError",
    "apply_effort_decision",
    "build_cortex_model",
    "clear_cortex_default_model",
    "cortex_call_kwargs",
    "get_cortex_default_model",
    "resolve_cortex_model",
    "set_cortex_default_model",
]
