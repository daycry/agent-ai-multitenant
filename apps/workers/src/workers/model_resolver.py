"""Resolución del model_config a spec ejecutable — en el WORKER (ADR 0057 F1).

El dispatch reenvía el ``model_config`` del agente con la clave ``provider``
(un *kind* del catálogo cerrado, ADR 0021/0055) y SIN endpoint/credencial. El
sandbox del agent-runtime no tiene BD/Vault (principio #2), así que leía
``spec["kind"]`` (ausente) y caía en silencio al cliente ``scripted`` — un
agente con modelo real nunca usaba su proveedor (hallazgo crítico del ADR
0057).

Este módulo cierra ese hueco en el único sitio con BD+Vault antes del
contenedor: el worker. ``resolve_model_spec`` convierte el ``model_config`` en
un spec **concreto** para ``AGENT_TASK_SPEC``:

  * ``kind``      — el kind del proveedor (lo que el runtime consume),
  * ``model``     — el nombre nativo del proveedor (``to_provider_model_name``),
  * ``base_url`` / campos de credencial — de la fila ACTIVA más nueva del kind
    (``resolve_provider_config``) + su secreto en Vault, con el MISMO mapeo
    por kind que ``agent_runtime.providers._overlay_resolved`` (duplicado a
    propósito: el worker no importa el paquete del sandbox).

Un spec que ya trae ``kind`` (el ``scripted`` de los tests, o uno pre-resuelto)
pasa intacto. Un spec sin proveedor activo NO degrada a scripted: lanza
``ModelResolutionError`` y el worker finaliza la ejecución como fallida con un
motivo explícito (sin fallos silenciosos).

El spec resuelto lleva la credencial EN MEMORIA y dentro del env del contenedor
efímero; nunca debe loguearse (usar ``safe_spec_summary`` para logs).
"""

from __future__ import annotations

from typing import Any

from api_server.assistant.model_config import to_provider_model_name
from api_server.llm_providers.factory_resolver import resolve_provider_config
from api_server.llm_providers.vault import LLMProviderVaultStore
from sqlalchemy.ext.asyncio import AsyncSession


class ModelResolutionError(RuntimeError):
    """El model_config no se pudo resolver a un proveedor ejecutable.

    El worker la captura y finaliza la ejecución como ``failed`` con
    ``abort_code='model_unresolved'`` — nunca lanza el contenedor con un spec
    que caería a scripted o petaría dentro del sandbox.
    """


def _overlay_provider_fields(
    spec: dict[str, Any], kind: str, *, base_url: str | None, secret: dict[str, str]
) -> dict[str, Any]:
    """Aplica base_url+secreto al spec con el mapeo por kind del runtime.

    Espejo de ``agent_runtime.providers._overlay_resolved`` (mismas claves por
    kind); duplicado consciente — el worker no importa el paquete del sandbox.
    """
    merged = dict(spec)
    if kind == "azure_foundry":
        if base_url:
            merged["apim_base_url"] = base_url
        if secret.get("api_key"):
            merged["subscription_key"] = secret["api_key"]
    elif kind == "copilot":
        if secret.get("oauth_token"):
            merged["github_token"] = secret["oauth_token"]
    elif kind in ("claude_sdk", "claude"):
        # Claude SDK usa auth ambiental de suscripción — nada que inyectar.
        pass
    elif kind == "ollama":
        if base_url:
            merged["base_url"] = base_url
        if secret.get("bearer_token"):
            merged["api_key"] = secret["bearer_token"]
    return merged


async def resolve_model_spec(
    session: AsyncSession,
    model_spec: dict[str, Any],
    *,
    vault: LLMProviderVaultStore | None,
) -> dict[str, Any]:
    """Resuelve el ``model_config`` del dispatch a un spec ejecutable.

    * Con ``kind`` ya presente (``scripted`` de tests, o pre-resuelto): se
      devuelve INTACTO — el runtime ya sabe construirlo.
    * Con ``provider`` (kind del catálogo): se busca el proveedor ACTIVO más
      nuevo de ese kind (la misma semántica que el resto del dispatch), se lee
      su credencial de Vault y se inyectan ``kind`` + ``model`` nativo +
      endpoint/credencial. ``provider`` se conserva (trazabilidad).
    * Sin ``provider`` ni ``kind``, o sin proveedor activo del kind:
      :class:`ModelResolutionError` — fallo explícito, nunca scripted.
    """
    if model_spec.get("kind"):
        return dict(model_spec)

    provider_kind = model_spec.get("provider")
    model_id = model_spec.get("model")
    if not provider_kind or not model_id:
        raise ModelResolutionError(
            "model_config has neither a resolvable provider/model nor an explicit kind"
        )

    resolved = await resolve_provider_config(session, str(provider_kind), vault=vault)
    if resolved is None:
        raise ModelResolutionError(
            f"no active llm_providers row of kind {provider_kind!r} — "
            "activate one (or fix the agent's model_config) and retry"
        )

    spec = dict(model_spec)
    spec["kind"] = str(provider_kind)
    # El catálogo guarda ids estilo LiteLLM (p.ej. `ollama/llama3.1`); la API
    # del proveedor quiere el nombre pelado. Mismo transform que el asistente.
    spec["model"] = to_provider_model_name(str(provider_kind), str(model_id))
    return _overlay_provider_fields(
        spec, str(provider_kind), base_url=resolved.base_url, secret=resolved.secret
    )


def safe_spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """Resumen logueable del spec resuelto — SIN credenciales.

    Solo claves no sensibles; nunca incluir api_key/subscription_key/
    github_token ni ningún valor de Vault.
    """
    return {
        "kind": spec.get("kind"),
        "provider": spec.get("provider"),
        "model": spec.get("model"),
        "base_url": spec.get("base_url") or spec.get("apim_base_url"),
        "has_credential": bool(
            spec.get("api_key") or spec.get("subscription_key") or spec.get("github_token")
        ),
    }


__all__ = ["ModelResolutionError", "resolve_model_spec", "safe_spec_summary"]
