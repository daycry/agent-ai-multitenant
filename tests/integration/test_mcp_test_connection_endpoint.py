"""Unit-flavoured integration tests for `POST /projects/{id}/mcp/test-connection`
(Plan 05 task_05_07).

The endpoint's logic is small — verify the project exists, hand the
candidate config to `discover_tools()`, fold any error into a typed
`McpTestConnectionError` payload. We don't want to drag in the full
docker stack to exercise that, so we wire a minimal FastAPI app with
the mcp router only, override every dependency with a stub, and
monkeypatch `discover_tools` to control the outcome.

What's verified:

* 404 when the project is not visible to the caller.
* 200 success path: response shape mirrors `DiscoveryResult`.
* MCPAuthError → 401 with `error_code="AUTH_ERROR"`.
* MCPTransportError → 502 with `error_code="TRANSPORT_ERROR"`.
* Generic Exception → 502 with `error_code="UNKNOWN_ERROR"`.
* The body's Pydantic schema rejects an `auth_ref` without `vault:`
  before the endpoint code even runs (422).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from api_server.auth.deps import (
    AuthPrincipal,
    get_principal,
    get_tenant_session,
)
from api_server.routers import mcp as mcp_router_module
from api_server.routers.mcp import get_vault_resolver
from api_server.routers.mcp import router as mcp_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from shared_mcp import (
    DiscoveryResult,
    MCPAuthError,
    MCPServerConfig,
    MCPTool,
    MCPTransportError,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fakes for the dependency overrides
# ---------------------------------------------------------------------------
_TENANT_ID = uuid4()
_USER_ID = uuid4()
_PROJECT_ID = uuid4()


class _FakeSessionFactory:
    """Pretend `get_tenant_session` — its only role in the endpoint is
    to answer `SELECT id FROM projects WHERE id=... AND deleted_at IS NULL`.
    We mimic the query result via a tiny stub session that returns a
    canned scalar for ``execute(...).scalar_one_or_none()``."""

    def __init__(self, *, project_visible: bool) -> None:
        self.project_visible = project_visible

    async def __call__(self) -> AsyncGenerator[Any, None]:
        session = _FakeSession(project_visible=self.project_visible)
        yield session


class _FakeSession:
    def __init__(self, *, project_visible: bool) -> None:
        self._project_visible = project_visible

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._project_visible)


class _FakeResult:
    def __init__(self, project_visible: bool) -> None:
        self._project_visible = project_visible

    def scalar_one_or_none(self) -> UUID | None:
        return _PROJECT_ID if self._project_visible else None


def _fake_principal() -> AuthPrincipal:
    """A minimal principal — the endpoint never reads its fields beyond
    "did the dependency succeed". Same shape FastAPI builds for real."""
    return AuthPrincipal(user_id=_USER_ID, tenant_id=_TENANT_ID, session_id=uuid4())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def app_with_visible_project() -> Iterator[FastAPI]:
    """Project-visible app: scalar_one_or_none returns the project id."""
    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_principal] = _fake_principal
    app.dependency_overrides[get_tenant_session] = _FakeSessionFactory(project_visible=True)
    app.dependency_overrides[get_vault_resolver] = lambda: None
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def app_with_invisible_project() -> Iterator[FastAPI]:
    """Project-not-visible app: scalar_one_or_none returns None."""
    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_principal] = _fake_principal
    app.dependency_overrides[get_tenant_session] = _FakeSessionFactory(project_visible=False)
    app.dependency_overrides[get_vault_resolver] = lambda: None
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_visible_project: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_visible_project), base_url="http://test"
    ) as c:
        yield c


def _minimal_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "toy",
        "transport": "stdio",
        "command": "toy-mcp",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 404 when project not visible
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_returns_404_when_project_not_visible(
    app_with_invisible_project: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_invisible_project), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{_PROJECT_ID}/mcp/test-connection",
            json=_minimal_payload(),
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project not found"


# ---------------------------------------------------------------------------
# 200 — success path with mocked discover_tools
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_returns_200_with_discovered_tools(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(
        _config: MCPServerConfig, *, vault_resolver: Any = None
    ) -> DiscoveryResult:
        return DiscoveryResult(
            tools=[
                MCPTool(name="echo", description="Echo input.", input_schema={}),
                MCPTool(
                    name="add",
                    description="Add two ints.",
                    input_schema={"type": "object"},
                ),
            ],
            server_name="toy-mcp-server",
            server_version="1.0.0",
            server_instructions="Use echo for tests.",
            capabilities={"tools": {}},
        )

    monkeypatch.setattr(mcp_router_module, "discover_tools", fake_discover)

    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json=_minimal_payload(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_name"] == "toy-mcp-server"
    assert body["server_version"] == "1.0.0"
    assert body["server_instructions"] == "Use echo for tests."
    names = [tool["name"] for tool in body["tools"]]
    assert names == ["echo", "add"]
    # input_schema is preserved verbatim.
    assert body["tools"][1]["input_schema"] == {"type": "object"}


# ---------------------------------------------------------------------------
# Auth error → 401 with typed code
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_auth_error_maps_to_401_with_typed_code(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(*_a: Any, **_kw: Any) -> DiscoveryResult:
        raise MCPAuthError("server rejected credentials")

    monkeypatch.setattr(mcp_router_module, "discover_tools", fake_discover)

    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json=_minimal_payload(),
    )
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["error_code"] == "AUTH_ERROR"
    assert "rejected credentials" in detail["message"]


# ---------------------------------------------------------------------------
# Transport error → 502 with typed code
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_transport_error_maps_to_502_with_typed_code(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(*_a: Any, **_kw: Any) -> DiscoveryResult:
        raise MCPTransportError("connection refused")

    monkeypatch.setattr(mcp_router_module, "discover_tools", fake_discover)

    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json=_minimal_payload(),
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error_code"] == "TRANSPORT_ERROR"
    assert "connection refused" in detail["message"]


# ---------------------------------------------------------------------------
# Unknown exception → 502 with UNKNOWN_ERROR
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_error_maps_to_502_with_unknown_code(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(*_a: Any, **_kw: Any) -> DiscoveryResult:
        raise RuntimeError("some unexpected crash")

    monkeypatch.setattr(mcp_router_module, "discover_tools", fake_discover)

    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json=_minimal_payload(),
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error_code"] == "UNKNOWN_ERROR"
    assert "RuntimeError" in detail["message"]
    assert "some unexpected crash" in detail["message"]


# ---------------------------------------------------------------------------
# Body validation — bad auth_ref shape is rejected before discover_tools fires
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_body_rejects_raw_token_in_auth_ref_with_422(client: AsyncClient) -> None:
    """The Pydantic validator already enforces `auth_ref` starts with
    `vault:` — this test pins that the endpoint inherits that rule and
    rejects raw tokens before any MCP roundtrip."""
    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json=_minimal_payload(auth_ref="ghp_raw_token_not_in_vault"),
    )
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI's 422 puts every offending field under `detail`.
    assert any("auth_ref" in str(err) for err in body.get("detail", []))


@pytest.mark.asyncio
async def test_body_rejects_stdio_without_command_with_422(client: AsyncClient) -> None:
    resp = await client.post(
        f"/projects/{_PROJECT_ID}/mcp/test-connection",
        json={"name": "bad", "transport": "stdio"},  # missing command
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Vault resolver wiring — when set, the endpoint forwards it to discover
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Vault wiring (task_05_17) — get_vault_resolver builds an HvacVaultResolver
# when API_SERVER_VAULT_TOKEN is set, returns None otherwise.
# ---------------------------------------------------------------------------
def test_get_vault_resolver_returns_none_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default state — no env var set, resolver is None and the
    endpoint will surface AUTH_ERROR for configs that need Vault."""
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)
    from api_server.config import get_settings
    from api_server.routers.mcp import get_vault_resolver, reset_vault_resolver_cache

    get_settings.cache_clear()
    reset_vault_resolver_cache()
    assert get_vault_resolver() is None


def test_get_vault_resolver_builds_hvac_resolver_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVER_VAULT_TOKEN", "dev-root-token")
    monkeypatch.setenv("API_SERVER_VAULT_URL", "http://vault.example:8200")
    from api_server.config import get_settings
    from api_server.routers.mcp import get_vault_resolver, reset_vault_resolver_cache

    get_settings.cache_clear()
    reset_vault_resolver_cache()
    resolver = get_vault_resolver()
    assert resolver is not None
    # Class name check avoids importing HvacVaultResolver here.
    assert type(resolver).__name__ == "HvacVaultResolver"


def test_get_vault_resolver_is_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVER_VAULT_TOKEN", "dev-root-token")
    from api_server.config import get_settings
    from api_server.routers.mcp import get_vault_resolver, reset_vault_resolver_cache

    get_settings.cache_clear()
    reset_vault_resolver_cache()
    a = get_vault_resolver()
    b = get_vault_resolver()
    assert a is b


@pytest.mark.asyncio
async def test_resolver_is_forwarded_to_discover_tools(
    app_with_visible_project: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the wiring: if the dependency provides a resolver, it must
    reach `discover_tools` via the `vault_resolver` kwarg."""
    sentinel = object()
    seen: dict[str, Any] = {}

    async def fake_discover(
        config: MCPServerConfig, *, vault_resolver: Any = None
    ) -> DiscoveryResult:
        seen["config"] = config
        seen["vault_resolver"] = vault_resolver
        return DiscoveryResult(tools=[], server_name="x", server_version="")

    monkeypatch.setattr(mcp_router_module, "discover_tools", fake_discover)
    app_with_visible_project.dependency_overrides[get_vault_resolver] = lambda: sentinel

    async with AsyncClient(
        transport=ASGITransport(app=app_with_visible_project), base_url="http://test"
    ) as c:
        resp = await c.post(
            f"/projects/{_PROJECT_ID}/mcp/test-connection",
            json=_minimal_payload(),
        )
    assert resp.status_code == 200
    assert seen["vault_resolver"] is sentinel
    assert isinstance(seen["config"], MCPServerConfig)
    assert seen["config"].name == "toy"
    assert seen["config"].transport == "stdio"
