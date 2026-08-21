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
    (``resolve_provider_config``) + su secreto en Vault, aplicados con la tabla
    ÚNICA ``shared_llm.credential_fields`` (prod-07 task_prod07_08). Este módulo
    tenía su propia copia del mapeo, «duplicada a propósito», y la copia
    divergió: se le quedó sin mapear el ``bearer_token`` de Azure, así que un
    proveedor azure bearer-only era configurable e irresoluble por dispatch.

Un spec que ya trae ``kind`` (el ``scripted`` de los tests, o uno pre-resuelto)
pasa intacto. Un spec sin proveedor activo NO degrada a scripted: lanza
``ModelResolutionError`` y el worker finaliza la ejecución como fallida con un
motivo explícito (sin fallos silenciosos). Un fallo de VAULT tampoco degrada:
se distingue con ``abort_code='vault_unavailable'`` (task_prod07_07) para que el
operador sepa dónde mirar.

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
from shared_llm.credential_fields import credential_vault_fields, overlay_credentials
from sqlalchemy.ext.asyncio import AsyncSession


class ModelResolutionError(RuntimeError):
    """El model_config no se pudo resolver a un proveedor ejecutable.

    El worker la captura y finaliza la ejecución como ``failed`` SIN lanzar el
    contenedor, con el ``abort_code`` que trae la excepción:

      * ``model_unresolved`` (por defecto) — el catálogo no da un proveedor:
        model_config incompleto, o ninguna fila activa del kind. Se arregla en
        la configuración.
      * ``vault_unavailable`` (prod-07 task_prod07_07) — el proveedor EXISTE y
        tiene credencial, pero Vault no la sirve. Se arregla en Vault, y el
        código lo dice para que nadie vaya a mirar el catálogo.

    La distinción no es cosmética: antes ambos casos acababan en un 401 dentro
    del sandbox que misatribuía la causa al proveedor (llm-9 / workers-8).
    """

    def __init__(self, message: str, *, abort_code: str = "model_unresolved") -> None:
        super().__init__(message)
        self.abort_code = abort_code


def _overlay_provider_fields(
    spec: dict[str, Any], kind: str, *, base_url: str | None, secret: dict[str, str]
) -> dict[str, Any]:
    """Aplica base_url+secreto al spec con el mapeo por kind del catálogo.

    Delega en ``shared_llm.credential_fields.overlay_credentials``, la tabla
    ÚNICA (prod-07 task_prod07_08). Esta función tenía su propia copia del mapeo
    —la copia nº1 de las tres que cita el módulo de la tabla— y era la que
    DIVERGÍA: no trasladaba el ``bearer_token`` de Azure, así que un proveedor
    azure bearer-only se creaba desde la UI, pasaba el test de conexión, servía
    al asistente… y era irresoluble por dispatch. Se queda como envoltorio con
    nombre propio porque es la costura que el resto del módulo llama.
    """
    return overlay_credentials(spec, kind, base_url=base_url, secret=secret)


def _read_secret_or_abort(vault: LLMProviderVaultStore, path: str, *, kind: str) -> dict[str, str]:
    """Lee la credencial de Vault reintentando UNA vez; si no, aborta.

    El reintento existe porque un blip de red no debe tumbar un run de 30
    iteraciones; el abort existe porque en el sandbox no hay fallback de env que
    recoja el testigo (ver ``resolve_provider_config(strict_vault=...)``).
    """
    for attempt in (1, 2):
        try:
            return vault.read_secret(path)
        except LLMProviderVaultError as exc:
            if attempt == 2:
                raise ModelResolutionError(
                    f"Vault transport error reading the credential of provider "
                    f"kind {kind!r} (2 attempts): {exc}. La fila TIENE credencial "
                    f"configurada — revisa Vault, no el catálogo.",
                    abort_code="vault_unavailable",
                ) from exc
    raise AssertionError("unreachable")  # pragma: no cover


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

    # En el dispatch un fallo de Vault NO puede degradar a «sin credencial»
    # (task_prod07_07): `strict_vault=True` para los kinds que USAN credencial,
    # con un reintento de la resolución completa —la consulta de la fila es
    # idempotente y barata— antes de abortar con `vault_unavailable`. Un kind sin
    # credencial en la tabla conserva la degradación: no hay nada que perder.
    strict = bool(credential_vault_fields(str(provider_kind)))
    resolved = None
    for attempt in (1, 2):
        try:
            resolved = await resolve_provider_config(
                session, str(provider_kind), vault=vault, strict_vault=strict
            )
            break
        except LLMProviderVaultError as exc:
            if attempt == 2:
                raise ModelResolutionError(
                    f"Vault transport error reading the credential of the active "
                    f"{provider_kind!r} provider (2 attempts): {exc}. La fila TIENE "
                    f"credencial configurada — revisa Vault, no el catálogo.",
                    abort_code="vault_unavailable",
                ) from exc
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
        if credential_vault_fields(row.kind):
            # El kind usa credencial y la fila apunta a una: si Vault no la
            # sirve, abortar (task_prod07_07). Antes se degradaba a `{}` y el
            # contenedor arrancaba sin auth para morir con un 401.
            secret = _read_secret_or_abort(vault, row.secret_vault_path, kind=row.kind)
        else:
            # Kind sin credencial en la tabla: no hay nada que la ausencia del
            # secreto rompa, así que se mantiene la degradación silenciosa.
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
