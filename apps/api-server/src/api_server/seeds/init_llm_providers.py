"""Siembra el proveedor LLM que el instalador configuró — y el modelo por defecto.

Plan `remediacion-instalador-runs-de-serie-2026-09-09`, `task_inst_03` (G1) y
`task_inst_04` (G2).

## Por qué existe

El instalador validaba «≥ 1 proveedor habilitado» en el ``install.yaml`` y después
**no lo materializaba en ningún sitio**: emitía ``LLM_OLLAMA_ENABLED`` al ``.env``
—una variable que nadie leía— e ``init_tenant`` creaba tenant, admin y membresía,
pero ninguna fila de ``llm_providers``. Una instalación limpia arrancaba con la
tabla vacía y **todo run moría ``model_unresolved``**. Y aunque la fila hubiera
existido, los agentes sembrados heredan ``DEFAULT_MODEL_CONFIG``
(``claude_sdk``/``claude-sonnet-5``, hardcodeado): en una instalación solo-Ollama
seguían sin poder resolver.

## Qué hace

Con ``settings.llm_ollama_enabled``:

1. Crea (idempotente por slug ``ollama``) la fila de ``llm_providers`` de kind
   ``ollama`` con el ``base_url`` normalizado a ``/v1`` — el contrato del cliente
   (``shared_llm.providers.ollama``) que hasta hoy tenía que conocer el operador.
2. Si no hay modelo por defecto de plataforma configurado, lo fija a ese proveedor y
   a ``settings.llm_ollama_chat_model``. NO pisa uno ya puesto: si el operador lo
   cambió desde el panel, manda el operador.

Sólo Ollama: es el único kind sembrable sin credencial. Los otros tres necesitan un
secreto que el instalador no escribe en Vault, y una fila sin credencial mentiría
igual que la variable huérfana. Se configuran desde ``/admin/llm-providers``.

Corre dentro de la MISMA transacción que ``init_tenant`` (lo llama su ``_amain``),
con el admin recién creado como actor: ``set_platform_setting`` exige un System
Admin y el primer usuario de una BD limpia lo es.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.config import Settings
from api_server.db.llm_providers import LlmProvider, LLMProviderKind, get_llm_provider_by_slug
from api_server.db.models import User
from api_server.db.platform_settings import (
    MODEL_DEFAULT_CONFIG_KEY,
    get_platform_setting,
    set_platform_setting,
)
from api_server.schemas.llm_providers import normalize_ollama_base_url

#: Slug estable de la fila sembrada. Idempotencia = «existe una fila con este slug».
SEEDED_OLLAMA_SLUG = "ollama"
SEEDED_OLLAMA_DISPLAY_NAME = "Ollama"

_log = structlog.get_logger("init-llm-providers")


@dataclass(frozen=True)
class LlmProviderSeedResult:
    """Qué hizo la siembra, para el log y para los tests."""

    created_provider: bool
    provider_id: UUID | None
    set_default_model: bool


async def ensure_llm_providers_from_settings(
    session: AsyncSession,
    *,
    settings: Settings,
    actor_user_id: UUID,
) -> LlmProviderSeedResult:
    """Siembra (idempotente) el proveedor Ollama del instalador y el default de plataforma."""
    if not settings.llm_ollama_enabled:
        return LlmProviderSeedResult(
            created_provider=False, provider_id=None, set_default_model=False
        )

    if not settings.llm_ollama_endpoint:
        # Habilitado sin host es un `install.yaml` a medias: mejor decirlo que sembrar
        # una fila que el factory rechazaría («ollama requires base_url»).
        _log.error("init_llm_providers.ollama_endpoint_missing")
        raise ValueError(
            "API_SERVER_LLM_OLLAMA_ENABLED=true exige API_SERVER_LLM_OLLAMA_ENDPOINT "
            "(el host de Ollama, p. ej. http://ollama:11434)"
        )

    created = False
    provider = await get_llm_provider_by_slug(session, SEEDED_OLLAMA_SLUG)
    if provider is None:
        provider = LlmProvider(
            kind=LLMProviderKind.OLLAMA.value,
            slug=SEEDED_OLLAMA_SLUG,
            display_name=SEEDED_OLLAMA_DISPLAY_NAME,
            base_url=normalize_ollama_base_url(settings.llm_ollama_endpoint),
            is_active=True,
            config={},
        )
        session.add(provider)
        await session.flush()
        created = True
        _log.info(
            "init_llm_providers.ollama_created",
            provider_id=str(provider.id),
            base_url=provider.base_url,
        )

    set_default = False
    current_default = await get_platform_setting(session, MODEL_DEFAULT_CONFIG_KEY, default=None)
    if current_default is None:
        actor = await session.scalar(select(User).where(User.id == actor_user_id))
        if actor is None:
            raise ValueError(f"actor user {actor_user_id} not found")
        await set_platform_setting(
            session,
            MODEL_DEFAULT_CONFIG_KEY,
            {
                "provider": LLMProviderKind.OLLAMA.value,
                "model": settings.llm_ollama_chat_model,
                "temperature": 0.1,
            },
            actor=actor,
        )
        set_default = True
        _log.info("init_llm_providers.default_model_set", model=settings.llm_ollama_chat_model)

    return LlmProviderSeedResult(
        created_provider=created, provider_id=provider.id, set_default_model=set_default
    )
