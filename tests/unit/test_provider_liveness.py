"""«Probar conexión» tiene que poder decir NO (prod-07 task_prod07_11, llm-12).

Dos mentiras del probe, medidas contra el factory que construye los providers de
verdad:

1. **Copilot con un token revocado lucía VERDE.** El probe solo comprobaba que el
   `oauth_token` EXISTE en Vault. Un token revocado en GitHub sigue existiendo en
   Vault, así que el operador veía verde y el run moría con un 401 después. El
   mint del JWT (`api.github.com/copilot_internal/v2/token`) es una llamada
   barata y ya implementada en `CopilotProvider._ensure_jwt`: eso es el probe.

2. **Azure bearer-only lucía ROJO.** El probe exigía `subscription_key`, pero el
   factory acepta APIM con `Authorization: Bearer` (APIM valida un JWT de AAD).
   Una config perfectamente válida se reportaba como CONFIG_ERROR.

Un probe que no puede decir NO no es un probe, y uno que dice NO a lo que sí
funciona es peor: enseña a ignorarlo.

Sin red: todas las llamadas van por un ``MockTransport``.
"""

from __future__ import annotations

import httpx
import pytest
from api_server.llm_providers.liveness import LivenessStatus, probe_provider

pytestmark = pytest.mark.unit


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Copilot: probe REAL contra el mint del JWT
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_copilot_with_a_revoked_token_is_red() -> None:
    """EL hallazgo: el token está en Vault pero GitHub ya no lo acepta."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with _client(handler) as client:
        result = await probe_provider(
            kind="copilot",
            base_url=None,
            secret={"oauth_token": "gho_revocado"},
            http_client=client,
        )

    assert result.ok is False
    assert result.status is LivenessStatus.AUTH_ERROR
    assert seen, "el probe no llegó a llamar a GitHub — sigue siendo un check de presencia"
    assert "copilot_internal/v2/token" in seen[0]


@pytest.mark.asyncio
async def test_copilot_with_a_live_token_is_green() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "token gho_vivo"
        return httpx.Response(200, json={"token": "tid=...;exp=1", "expires_at": 1})

    async with _client(handler) as client:
        result = await probe_provider(
            kind="copilot", base_url=None, secret={"oauth_token": "gho_vivo"}, http_client=client
        )

    assert result.ok is True
    assert result.status is LivenessStatus.OK


@pytest.mark.asyncio
async def test_copilot_without_a_token_is_a_config_error() -> None:
    """Sin credencial no se llama a nadie: es configuración, no autenticación."""
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await probe_provider(kind="copilot", base_url=None, secret={}, http_client=client)

    assert result.ok is False
    assert result.status is LivenessStatus.CONFIG_ERROR
    assert called is False


@pytest.mark.asyncio
async def test_copilot_never_echoes_the_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="gho_secretisimo leaked upstream")

    async with _client(handler) as client:
        result = await probe_provider(
            kind="copilot",
            base_url=None,
            secret={"oauth_token": "gho_secretisimo"},
            http_client=client,
        )

    assert "gho_secretisimo" not in result.detail
    assert result.status is LivenessStatus.UPSTREAM_ERROR


@pytest.mark.asyncio
async def test_copilot_unreachable_is_a_connection_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    async with _client(handler) as client:
        result = await probe_provider(
            kind="copilot", base_url=None, secret={"oauth_token": "gho_x"}, http_client=client
        )

    assert result.status is LivenessStatus.CONNECTION_ERROR


# ---------------------------------------------------------------------------
# Azure Foundry: bearer-only es una config VÁLIDA
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_azure_bearer_only_is_probed_with_a_bearer_header() -> None:
    """La otra mitad del hallazgo: el factory acepta bearer-only y el probe lo
    rechazaba de plano, así que el operador no podía validar una config buena."""
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(200, json={"data": []})

    async with _client(handler) as client:
        result = await probe_provider(
            kind="azure_foundry",
            base_url="https://apim.example",
            secret={"bearer_token": "jwt-aad"},
            http_client=client,
        )

    assert result.ok is True, result.detail
    assert headers[0]["Authorization"] == "Bearer jwt-aad"
    assert "Ocp-Apim-Subscription-Key" not in headers[0]


@pytest.mark.asyncio
async def test_azure_subscription_key_still_uses_the_apim_header() -> None:
    """No-regresión: el camino de subscription_key no cambia."""
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(200, json={"data": []})

    async with _client(handler) as client:
        result = await probe_provider(
            kind="azure_foundry",
            base_url="https://apim.example",
            secret={"api_key": "sub-key"},
            http_client=client,
        )

    assert result.ok is True
    assert headers[0]["Ocp-Apim-Subscription-Key"] == "sub-key"
    assert "Authorization" not in headers[0]


@pytest.mark.asyncio
async def test_azure_with_no_credential_at_all_is_a_config_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no debería llamarse sin credencial")

    async with _client(handler) as client:
        result = await probe_provider(
            kind="azure_foundry",
            base_url="https://apim.example",
            secret={},
            http_client=client,
        )

    assert result.ok is False
    assert result.status is LivenessStatus.CONFIG_ERROR


@pytest.mark.asyncio
async def test_azure_without_base_url_is_a_config_error() -> None:
    async with _client(lambda _r: httpx.Response(200)) as client:
        result = await probe_provider(
            kind="azure_foundry",
            base_url=None,
            secret={"bearer_token": "jwt-aad"},
            http_client=client,
        )
    assert result.status is LivenessStatus.CONFIG_ERROR


# ---------------------------------------------------------------------------
# El probe acepta EXACTAMENTE lo que el factory construye
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "vault_field"),
    [
        ("azure_foundry", "api_key"),
        ("azure_foundry", "bearer_token"),
        ("copilot", "oauth_token"),
        ("ollama", "bearer_token"),
    ],
)
async def test_probe_and_factory_agree_on_every_credential_field(
    kind: str, vault_field: str
) -> None:
    """§5 y §7: la tabla única declara qué campos valen por kind, y el factory los
    acepta (ya hay test de eso). Si el PROBE rechaza uno de ellos como
    CONFIG_ERROR, la UI está diciendo «mal configurado» de algo que funciona."""
    from shared_llm.credential_fields import credential_vault_fields

    assert vault_field in credential_vault_fields(kind), "el caso salió de la tabla"

    async with _client(lambda _r: httpx.Response(200, json={"data": []})) as client:
        result = await probe_provider(
            kind=kind,
            base_url="https://endpoint.example/v1",
            secret={vault_field: "credencial"},
            http_client=client,
        )

    assert result.status is not LivenessStatus.CONFIG_ERROR, result.detail


# ---------------------------------------------------------------------------
# claude_sdk: el límite del probe, documentado en el propio detail
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_claude_sdk_says_out_loud_that_it_did_not_call_anything() -> None:
    """No hay llamada barata para la suscripción, y el plan pide que el límite se
    diga en la UI en vez de fingir un verde con el mismo peso que los demás."""
    async with _client(lambda _r: httpx.Response(200)) as client:
        result = await probe_provider(
            kind="claude_sdk", base_url=None, secret={"oauth_token": "tok"}, http_client=client
        )

    assert result.ok is True
    assert "no live call" in result.detail.lower()
