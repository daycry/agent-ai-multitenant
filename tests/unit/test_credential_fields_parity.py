"""El mapeo kind→credencial, en UNA tabla (prod-07 task_prod07_08, llm-13).

Había TRES copias del mismo mapeo «qué campo del secreto de Vault va a qué
kwarg del provider»:

  1. ``workers/model_resolver._overlay_provider_fields`` — el camino de dispatch;
  2. ``agent_runtime/providers._overlay_resolved`` — dentro del sandbox;
  3. los ``_build_*`` de ``api_server/llm_providers/factory.py`` — asistente y
     córtex.

Y ya habían divergido: el factory acepta el ``bearer_token`` de Azure (APIM
validando un JWT, sin subscription_key) y las otras dos NO lo mapeaban. O sea, un
proveedor azure bearer-only se puede crear y probar desde la UI, funciona en el
asistente… y es IRRESOLUBLE por dispatch: el agente arranca sin credencial y
muere con un 401 dentro del sandbox.

La tabla vive ahora en ``shared_llm.credential_fields``, que es el paquete que
los tres consumidores YA importan. Estos tests son la guarda de que ninguna copia
se vuelve a desviar de ella.
"""

from __future__ import annotations

import pytest
from shared_llm.credential_fields import CREDENTIAL_FIELDS, overlay_credentials

pytestmark = pytest.mark.unit

# Los cuatro kinds del catálogo cerrado (ADR 0021) + el alias histórico `claude`.
_KINDS = ("azure_foundry", "copilot", "claude_sdk", "claude", "ollama")


def test_the_table_covers_the_closed_catalogue() -> None:
    """§4 de verificar-antes-de-implementar: si la tabla se quedara vacía, todos
    los tests de paridad de abajo pasarían vacíamente."""
    assert set(_KINDS) <= set(CREDENTIAL_FIELDS), sorted(CREDENTIAL_FIELDS)
    total_fields = sum(len(m.secret_fields) for m in CREDENTIAL_FIELDS.values())
    assert total_fields >= 7, f"la tabla perdió mapeos (vio {total_fields})"


# ---------------------------------------------------------------------------
# 1. El runtime (dentro del sandbox) usa la tabla
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", _KINDS)
def test_runtime_overlay_matches_the_table(kind: str) -> None:
    """``_overlay_resolved`` del agent-runtime tiene que producir exactamente lo
    que dice la tabla, para TODOS los campos de secreto que declara."""
    from agent_runtime.providers import ResolvedProviderConfig, _overlay_resolved

    mapping = CREDENTIAL_FIELDS[kind]
    secret = {vault_field: f"secreto-{vault_field}" for vault_field, _ in mapping.secret_fields}
    resolved = ResolvedProviderConfig(base_url="https://endpoint.example", secret=secret)

    from_runtime = _overlay_resolved({"kind": kind}, kind, resolved)
    from_table = overlay_credentials(
        {"kind": kind}, kind, base_url="https://endpoint.example", secret=secret
    )
    assert from_runtime == from_table


def test_azure_bearer_only_reaches_the_sandbox() -> None:
    """El caso concreto de la divergencia: un azure SOLO con bearer_token tiene
    que llegar al spec del contenedor. Antes se descartaba y el agente arrancaba
    sin credencial para morir con un 401 que misatribuía la causa."""
    from agent_runtime.providers import ResolvedProviderConfig, _overlay_resolved

    resolved = ResolvedProviderConfig(
        base_url="https://apim.example", secret={"bearer_token": "jwt-aad"}
    )
    merged = _overlay_resolved({"kind": "azure_foundry"}, "azure_foundry", resolved)
    assert merged["bearer_token"] == "jwt-aad"
    assert merged["apim_base_url"] == "https://apim.example"
    # Y sin subscription_key inventada: no había.
    assert "subscription_key" not in merged


def test_azure_bearer_only_builds_a_runtime_client() -> None:
    """De punta a punta (§5 de verificar-antes-de-implementar): que el campo esté
    en el spec no vale si el constructor no lo usa."""
    from agent_runtime.providers import build_provider_client

    client = build_provider_client(
        {
            "kind": "azure_foundry",
            "name": "gpt-4o",
            "apim_base_url": "https://apim.example",
            "bearer_token": "jwt-aad",
        }
    )
    assert client is not None


# ---------------------------------------------------------------------------
# 1.bis El WORKER (camino de dispatch) usa la tabla
#
# La copia nº1 de la lista de arriba — ``workers/model_resolver`` — era la que
# faltaba en este fichero, y era justo la que seguía divergiendo: mantuvo su
# propio mapeo (sin el bearer_token de Azure) mientras el runtime y el factory ya
# estaban alineados. Un test de paridad que no cubre la copia que diverge da
# confianza injustificada, que es peor que no tenerlo.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", _KINDS)
def test_worker_overlay_matches_the_table(kind: str) -> None:
    from workers.model_resolver import _overlay_provider_fields

    mapping = CREDENTIAL_FIELDS[kind]
    secret = {vault_field: f"secreto-{vault_field}" for vault_field, _ in mapping.secret_fields}

    from_worker = _overlay_provider_fields(
        {"kind": kind}, kind, base_url="https://endpoint.example", secret=secret
    )
    from_table = overlay_credentials(
        {"kind": kind}, kind, base_url="https://endpoint.example", secret=secret
    )
    assert from_worker == from_table


def test_azure_bearer_only_survives_the_dispatch_path() -> None:
    """El caso concreto de la divergencia, en el camino del worker: el spec que
    viaja al contenedor tiene que llevar el bearer. Antes se descartaba aquí, así
    que un azure bearer-only era configurable, probable y utilizable por el
    asistente… e IRRESOLUBLE por dispatch."""
    from workers.model_resolver import _overlay_provider_fields

    merged = _overlay_provider_fields(
        {"kind": "azure_foundry"},
        "azure_foundry",
        base_url="https://apim.example",
        secret={"bearer_token": "jwt-aad"},
    )
    assert merged["bearer_token"] == "jwt-aad"
    assert merged["apim_base_url"] == "https://apim.example"
    assert "subscription_key" not in merged


# ---------------------------------------------------------------------------
# 2. El factory (asistente / córtex) acepta los mismos campos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ("azure_foundry", "copilot", "ollama"))
def test_factory_accepts_every_vault_field_the_table_declares(kind: str) -> None:
    """Para cada campo de secreto de la tabla, el factory tiene que construir un
    provider con SOLO ese campo. Un campo que la tabla promete y el factory
    ignora es un proveedor configurable e inutilizable."""
    from api_server.llm_providers.factory import build_provider_from_kind

    mapping = CREDENTIAL_FIELDS[kind]
    for vault_field, _spec_field in mapping.secret_fields:
        provider = build_provider_from_kind(
            kind,
            base_url="https://endpoint.example",
            secret={vault_field: "secreto"},
            model="gpt-4o",
        )
        assert provider is not None, (
            f"el factory no construye '{kind}' con solo '{vault_field}', "
            f"pero la tabla lo declara como credencial válida"
        )


# ---------------------------------------------------------------------------
# 3. Semántica del overlay: DB row > env, y nunca mutar la entrada
# ---------------------------------------------------------------------------
def test_absent_fields_leave_the_spec_untouched() -> None:
    """Precedencia «DB row > env»: un campo AUSENTE en el secreto no borra el
    valor que traía el spec del env/instalador."""
    spec = {"kind": "ollama", "base_url": "http://env:11434/v1", "api_key": "de-env"}
    merged = overlay_credentials(spec, "ollama", base_url=None, secret={})
    assert merged["base_url"] == "http://env:11434/v1"
    assert merged["api_key"] == "de-env"


def test_empty_string_does_not_overwrite() -> None:
    """Un campo presente pero VACÍO en Vault no es una credencial: sobrescribir
    con "" dejaría al provider sin auth creyendo que la tiene."""
    spec = {"kind": "ollama", "api_key": "de-env"}
    merged = overlay_credentials(spec, "ollama", base_url=None, secret={"bearer_token": ""})
    assert merged["api_key"] == "de-env"


def test_the_input_spec_is_never_mutated() -> None:
    spec = {"kind": "copilot"}
    overlay_credentials(spec, "copilot", base_url=None, secret={"oauth_token": "gho_x"})
    assert spec == {"kind": "copilot"}


def test_an_unknown_kind_is_a_no_op() -> None:
    """Un kind fuera del catálogo no revienta: devuelve el spec tal cual (el
    rechazo del kind es de la validación, no de esta tabla)."""
    spec = {"kind": "inventado"}
    assert overlay_credentials(spec, "inventado", base_url="x", secret={"api_key": "y"}) == spec
