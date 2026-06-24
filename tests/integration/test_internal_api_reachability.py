"""The agent-runtime can reach the api-server's internal API (Plan prod-01
task_11 / sandbox-4).

The sandbox calls ``/internal/agent/*`` to get its assigned agent + to run the
knowledge/memory tool families. Two things had to be true and were not:

  * (a) the httpx client must NOT inherit ``HTTP(S)_PROXY`` from the env — those
    calls must NOT egress through the deny-by-default egress-proxy (which has no
    ``api-server`` allow entry). So the client is built with ``trust_env=False``.
  * (b) there must be a network route: the api-server joins the internal
    ``agentic-agents`` network (ADR 0060 option B1) so the sandbox reaches it.
  * (c) when an internal token IS injected (a production run with an assigned
    agent) but the API does not answer, the boot must FAIL LOUDLY instead of
    silently degrading (the old behaviour).

These are unit-ish (httpx MockTransport + the pure compose generator); no live
stack needed.
"""

from __future__ import annotations

import httpx
import pytest
from agent_runtime.internal_api import InternalAgentAPI, InternalAPIUnreachableError
from installer_backend.compose_generator import generate_compose
from installer_backend.config import (
    Environment,
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)

pytestmark = pytest.mark.integration


def _api(handler: object) -> InternalAgentAPI:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return InternalAgentAPI(
        base_url="http://api-server:8000",
        bearer_token="e2e-token",
        client=httpx.Client(transport=transport),
    )


def test_internal_api_client_ignores_proxy_env() -> None:
    # A default client (no injected one) must NOT trust HTTP(S)_PROXY: the
    # internal API is reached directly on agentic-agents, never via the egress
    # allowlist proxy.
    api = InternalAgentAPI(base_url="http://api-server:8000", bearer_token="t")
    assert api.client is not None
    assert api.client.trust_env is False


def test_ensure_reachable_is_ok_when_the_api_answers() -> None:
    api = _api(lambda request: httpx.Response(200, json={"status": "ok"}))
    api.ensure_reachable()  # must not raise


def test_ensure_reachable_fails_loudly_when_unreachable() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to api-server", request=request)

    api = _api(_boom)
    with pytest.raises(InternalAPIUnreachableError):
        api.ensure_reachable()


def _prod_compose() -> dict:
    cfg = InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=Environment.PRODUCTION),
        resources=ResourceConfig(
            worker_replicas=1,
            worker_memory_gib=4,
            gpu_enabled=False,
            ollama_mode=None,
            embedding_model="nomic-embed-text",
        ),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=False)),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )
    return generate_compose(cfg)


def test_api_server_joins_agents_network_so_the_sandbox_can_reach_it() -> None:
    nets = _prod_compose()["services"]["api-server"]["networks"]
    assert "agentic-agents" in nets, "api-server must be on agentic-agents for the internal API"
