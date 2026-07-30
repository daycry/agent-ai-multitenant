"""Tabla ÚNICA kind → campos de credencial (prod-07 task_prod07_08, llm-13).

El problema que resuelve
------------------------
El mismo mapeo —«el campo X del secreto de Vault se llama Y en los kwargs del
provider»— estaba escrito TRES veces:

  1. ``workers/model_resolver._overlay_provider_fields`` — camino de dispatch;
  2. ``agent_runtime/providers._overlay_resolved`` — dentro del sandbox;
  3. los ``_build_*`` de ``api_server/llm_providers/factory.py`` — asistente y
     córtex.

Y ya habían divergido. El factory acepta el ``bearer_token`` de Azure (el caso de
APIM validando un JWT de AAD, sin ``subscription_key``); las otras dos copias no
lo mapeaban. Consecuencia medible: un proveedor azure bearer-only se crea desde
la UI, pasa el test de conexión y funciona en el asistente… y es IRRESOLUBLE por
dispatch — el agente arranca sin credencial y el run muere con un 401 dentro del
sandbox, un error que misatribuye por completo la causa raíz.

Por qué la tabla vive aquí
--------------------------
``shared_llm`` es el único paquete que los tres consumidores YA importan: el
worker, el runtime del sandbox y el api-server. Ponerla en cualquiera de los tres
obligaría a los otros dos a depender de él.

Esto es DATO, no política: no valida, no lee Vault, no decide si falta una
credencial obligatoria (eso es del resolver). Solo dice dónde aterriza cada campo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CredentialMapping:
    """Cómo aterriza la config resuelta de un kind en los kwargs del provider.

    ``base_url_field`` es el kwarg donde va el endpoint (``None`` para los kinds
    que no lo tienen: claude_sdk usa la suscripción y copilot su endpoint fijo).

    ``secret_fields`` son pares ``(campo_en_vault, campo_en_el_spec)``. Son pares
    y no un simple nombre porque los nombres NO coinciden: la ``api_key`` de
    Vault es la ``subscription_key`` de Azure, y el ``bearer_token`` de una fila
    Ollama es la ``api_key`` del provider. Ese desajuste es justo el detalle que
    cada copia tenía que recordar, y por el que divergieron.
    """

    base_url_field: str | None
    secret_fields: tuple[tuple[str, str], ...]


# Los cuatro kinds del catálogo CERRADO (ADR 0021) + el alias histórico `claude`.
# Añadir un quinto proveedor pide un ADR explícito, así que esta tabla se extiende
# deliberadamente, nunca en silencio.
CREDENTIAL_FIELDS: dict[str, CredentialMapping] = {
    # APIM admite DOS formas de auth y el provider acepta cualquiera:
    # `Ocp-Apim-Subscription-Key` (cuotas/facturación) o `Bearer` (APIM valida un
    # JWT). El bearer es el que faltaba en el worker y en el runtime.
    "azure_foundry": CredentialMapping(
        base_url_field="apim_base_url",
        secret_fields=(("api_key", "subscription_key"), ("bearer_token", "bearer_token")),
    ),
    # Copilot: el token OAuth largo del Device Flow; el JWT corto lo mint el
    # propio provider. Endpoint fijo (api.githubcopilot.com), sin base_url.
    "copilot": CredentialMapping(
        base_url_field=None,
        secret_fields=(("oauth_token", "github_token"),),
    ),
    # claude_sdk: DOS modos de auth sobre el MISMO kind (ADR 0063) — api_key de
    # Anthropic (→ ANTHROPIC_API_KEY) o token de suscripción Pro/Max de
    # `claude setup-token` (→ CLAUDE_CODE_OAUTH_TOKEN). Aquí viajan los dos y el
    # provider elige; ambos nombres coinciden con los del secreto.
    "claude_sdk": CredentialMapping(
        base_url_field=None,
        secret_fields=(("api_key", "api_key"), ("oauth_token", "oauth_token")),
    ),
    "claude": CredentialMapping(
        base_url_field=None,
        secret_fields=(("api_key", "api_key"), ("oauth_token", "oauth_token")),
    ),
    # Ollama: local sin credencial, cloud con bearer. El provider llama `api_key`
    # a lo que la fila guarda como `bearer_token`.
    "ollama": CredentialMapping(
        base_url_field="base_url",
        secret_fields=(("bearer_token", "api_key"),),
    ),
}


def overlay_credentials(
    spec: dict[str, Any],
    kind: str,
    *,
    base_url: str | None,
    secret: dict[str, str],
) -> dict[str, Any]:
    """Copia de ``spec`` con el endpoint + la credencial resueltos aplicados.

    Precedencia **fila de BD > env** (la de ``factory_resolver``): un valor
    resuelto NO VACÍO sobrescribe lo que trajera el spec del env/instalador; un
    campo ausente —o presente pero vacío— lo deja INTACTO. Esa asimetría importa:
    un ``""`` en Vault no es una credencial, y sobrescribir con él dejaría al
    provider sin auth creyendo que la tiene.

    El ``spec`` de entrada nunca se muta. Un kind desconocido devuelve una copia
    sin cambios: rechazarlo es trabajo de la validación, no de una tabla de datos.
    """
    merged = dict(spec)
    mapping = CREDENTIAL_FIELDS.get(kind)
    if mapping is None:
        return merged
    if mapping.base_url_field and base_url:
        merged[mapping.base_url_field] = base_url
    for vault_field, spec_field in mapping.secret_fields:
        value = secret.get(vault_field)
        if value:
            merged[spec_field] = value
    return merged


def credential_vault_fields(kind: str) -> tuple[str, ...]:
    """Los campos de Vault que ``kind`` puede usar como credencial.

    Para el resolver y el probe de conexión: «¿este kind necesita credencial y
    cuáles valen?». Vacío para un kind sin credencial (ollama local) o desconocido.
    """
    mapping = CREDENTIAL_FIELDS.get(kind)
    if mapping is None:
        return ()
    return tuple(vault_field for vault_field, _ in mapping.secret_fields)


__all__ = [
    "CREDENTIAL_FIELDS",
    "CredentialMapping",
    "credential_vault_fields",
    "overlay_credentials",
]
