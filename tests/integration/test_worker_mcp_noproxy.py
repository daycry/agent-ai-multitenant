"""NO_PROXY para MCP servers internos (prueba Atlassian 2026-07-18).

El sandbox del run recibe ``HTTP_PROXY`` → egress-proxy (tinyproxy,
FilterDefaultDeny; ADR 0019). El transporte HTTP del cliente MCP (httpx,
``trust_env=True``) lo respeta, así que un MCP server INTERNO del compose
(``http://mcp-atlassian:9000/mcp``) moría con ``403 Filtered``: el proxy
solo permite su allowlist estática y los sidecars por-proyecto no están en
ella. Cazado en vivo: `mcp.server_failed` y el run degeneró a bucle.

El worker emite ahora ``NO_PROXY``/``no_proxy`` con los hostnames INTERNOS
(sin punto = nombre de servicio Docker, solo resoluble en la red de agentes)
de los ``mcp_servers`` declarados por el proyecto — la declaración es la
autorización (RBAC tenant_admin). Un MCP EXTERNO (FQDN con punto) sigue
saliendo por el proxy y exige su host en la allowlist: deny-by-default
intacto.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from api_server.config import get_settings as get_api_settings
from workers.execution import ExecutionRequest, _build_runtime_env

pytestmark = pytest.mark.integration

_API_URL = "http://api-server:8000"


@pytest.fixture(autouse=True)
def _api_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "mcp-noproxy-test-secret")
    get_api_settings.cache_clear()
    try:
        yield
    finally:
        get_api_settings.cache_clear()


def _request(mcp_servers: list[dict] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t", "title": "mcp noproxy", "description": "seam"},
        model={"kind": "scripted", "decisions": [{"kind": "finish", "output": "ok"}]},
        mcp_servers=mcp_servers,
    )


def test_internal_mcp_hosts_land_in_no_proxy() -> None:
    request = _request(
        [
            {
                "name": "atlassian",
                "transport": "streamable_http",
                "url": "http://mcp-atlassian-test:9000/mcp",
            },
            # externo (FQDN): DEBE seguir saliendo por el proxy.
            {
                "name": "context7",
                "transport": "streamable_http",
                "url": "https://mcp.context7.com/mcp",
            },
            # stdio: sin URL, nada que exceptuar.
            {"name": "toy", "transport": "stdio", "command": "toy-mcp"},
        ]
    )
    env = _build_runtime_env(request, None, agent_internal_api_url=_API_URL)
    assert env["NO_PROXY"] == "mcp-atlassian-test"
    assert env["no_proxy"] == "mcp-atlassian-test"


def test_no_mcp_servers_means_no_no_proxy() -> None:
    env = _build_runtime_env(_request(None), None, agent_internal_api_url=_API_URL)
    assert "NO_PROXY" not in env
    assert "no_proxy" not in env


def test_only_external_mcp_servers_means_no_no_proxy() -> None:
    env = _build_runtime_env(
        _request(
            [
                {
                    "name": "context7",
                    "transport": "streamable_http",
                    "url": "https://mcp.context7.com/mcp",
                }
            ]
        ),
        None,
        agent_internal_api_url=_API_URL,
    )
    assert "NO_PROXY" not in env
