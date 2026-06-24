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

from importlib import import_module
from typing import Any
from uuid import UUID

from api_server.assistant.model_config import to_provider_model_name
from api_server.llm_providers.factory_resolver import resolve_provider_config
from api_server.llm_providers.vault import LLMProviderVaultError, LLMProviderVaultStore
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
        # El Claude Agent SDK admite DOS modos de auth, ambos sobre el mismo
        # kind (ADR 0021/0063): API key (`secret['api_key']` → ANTHROPIC_API_KEY)
        # y suscripción Pro/Max (`secret['oauth_token']`, de `claude
        # setup-token` → CLAUDE_CODE_OAUTH_TOKEN). El runtime mapea la clave del
        # spec al env var correcto dentro del contenedor; aquí solo trasladamos
        # la credencial de Vault al spec (antes se descartaba → el agente nunca
        # se autenticaba en el sandbox).
        if secret.get("api_key"):
            merged["api_key"] = secret["api_key"]
        if secret.get("oauth_token"):
            merged["oauth_token"] = secret["oauth_token"]
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

    # Concrete provider pinned by provider_id (Feature B / "todo a proveedores
    # concretos"): resolve THAT exact row + its credential, instead of the newest
    # active of the kind. provider (kind) rides along for traceability/fallback.
    provider_id = model_spec.get("provider_id")
    model_id = model_spec.get("model")
    if provider_id and model_id:
        resolved_spec = await _resolve_by_provider_id(
            session, model_spec, str(provider_id), str(model_id), vault
        )
        if resolved_spec is not None:
            return resolved_spec

    provider_kind = model_spec.get("provider")
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


async def _resolve_by_provider_id(
    session: AsyncSession,
    model_spec: dict[str, Any],
    provider_id: str,
    model_id: str,
    vault: LLMProviderVaultStore | None,
) -> dict[str, Any] | None:
    """Overlay endpoint + Vault credential from the EXACT ``llm_providers`` row
    pinned by ``provider_id`` (not the newest-active-of-kind). Returns ``None`` when
    the id is malformed / row missing / inactive, so the caller falls back to the
    kind path (backward-compat)."""
    try:
        pid = UUID(provider_id)
    except (ValueError, TypeError):
        return None
    # Lazy import: the worker's mypy context can't statically resolve this
    # api_server submodule (same quirk handled elsewhere with import_module).
    get_llm_provider = import_module("api_server.db.llm_providers").get_llm_provider
    row = await get_llm_provider(session, pid)
    if row is None or not row.is_active:
        return None
    secret: dict[str, str] = {}
    if row.secret_vault_path and vault is not None:
        try:
            secret = vault.read_secret(row.secret_vault_path)
        except LLMProviderVaultError:
            secret = {}
    spec = dict(model_spec)
    spec["kind"] = row.kind
    spec["model"] = to_provider_model_name(row.kind, model_id)
    return _overlay_provider_fields(spec, row.kind, base_url=row.base_url, secret=secret)


def safe_spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """Resumen logueable del spec resuelto — SIN credenciales.

    Solo claves no sensibles; nunca incluir api_key/subscription_key/
    github_token ni ningún valor de Vault.
    """
    return {
        "kind": spec.get("kind"),
        "provider": spec.get("provider"),
        "model": spec.get("model"),
        "reasoning_effort": spec.get("reasoning_effort"),  # ADR 0070 (no sensible)
        "base_url": spec.get("base_url") or spec.get("apim_base_url"),
        "has_credential": bool(
            spec.get("api_key")
            or spec.get("subscription_key")
            or spec.get("github_token")
            or spec.get("oauth_token")
        ),
    }


__all__ = ["ModelResolutionError", "resolve_model_spec", "safe_spec_summary"]
