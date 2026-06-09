"""/admin/ollama/* — native Ollama model management (ADR 0056, option U-B).

Light wiring like test_embedding_discovery_endpoint: real app via ``create_app``
with the System-Admin gate and the Ollama client overridden (MockTransport), so
the endpoints are exercised without a DB, Redis, or a live Ollama.

Asserts: list parses installed models (and degrades on an unreachable Ollama),
pull/delete succeed and surface Ollama-side failures (502 / 404), and the
surface is System-Admin gated.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


def _ollama_client(
    *,
    tags: list[dict] | None = None,
    pull_status: int = 200,
    delete_status: int = 200,
    raise_conn: bool = False,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_conn:
            raise httpx.ConnectError("connection refused")
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": tags or []})
        if path == "/api/pull":
            if pull_status >= 400:
                return httpx.Response(pull_status, json={"error": "pull boom"})
            return httpx.Response(200, json={"status": "success"})
        if path == "/api/delete":
            if delete_status >= 400:
                return httpx.Response(delete_status, json={"error": "delete boom"})
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"unexpected {path}"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _build_app(monkeypatch: pytest.MonkeyPatch):
    from api_server.config import get_settings

    get_settings.cache_clear()
    from api_server.main import create_app

    return create_app()


def _as_system_admin(app) -> None:
    from api_server.auth.deps import AuthPrincipal, require_system_admin

    app.dependency_overrides[require_system_admin] = lambda: AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=None, is_system_admin=True
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _override_client(app, probe: httpx.AsyncClient) -> None:
    from api_server.routers.ollama import get_ollama_client

    app.dependency_overrides[get_ollama_client] = lambda: probe


def _cleanup(app) -> None:
    app.dependency_overrides.clear()
    from api_server.config import get_settings

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_models_parses_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    probe = _ollama_client(
        tags=[
            {
                "name": "nomic-embed-text:latest",
                "size": 274301056,
                "modified_at": "2026-06-01T00:00:00Z",
            },
            {"name": "llama3.1:8b", "size": 4700000000},
        ]
    )
    _override_client(app, probe)
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/ollama/models")
    finally:
        await probe.aclose()
        _cleanup(app)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ollama_reachable"] is True
    by_name = {m["name"]: m for m in body["models"]}
    assert by_name["nomic-embed-text:latest"]["size_bytes"] == 274301056
    assert by_name["nomic-embed-text:latest"]["modified_at"] == "2026-06-01T00:00:00Z"
    assert by_name["llama3.1:8b"]["modified_at"] is None


@pytest.mark.asyncio
async def test_list_models_degrades_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    probe = _ollama_client(raise_conn=True)
    _override_client(app, probe)
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/ollama/models")
    finally:
        await probe.aclose()
        _cleanup(app)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ollama_reachable"] is False
    assert body["models"] == []


@pytest.mark.asyncio
async def test_pull_model_success(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    probe = _ollama_client()
    _override_client(app, probe)
    try:
        async with _client(app) as client:
            resp = await client.post("/admin/ollama/models/pull", json={"name": "nomic-embed-text"})
    finally:
        await probe.aclose()
        _cleanup(app)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_pull_model_ollama_error_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    probe = _ollama_client(pull_status=500)
    _override_client(app, probe)
    try:
        async with _client(app) as client:
            resp = await client.post("/admin/ollama/models/pull", json={"name": "bogus"})
    finally:
        await probe.aclose()
        _cleanup(app)

    assert resp.status_code == 502, resp.text


@pytest.mark.asyncio
async def test_delete_model_success_and_404(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    ok = _ollama_client()
    _override_client(app, ok)
    try:
        async with _client(app) as client:
            good = await client.request(
                "DELETE", "/admin/ollama/models", json={"name": "nomic-embed-text"}
            )
    finally:
        await ok.aclose()
        _cleanup(app)
    assert good.status_code == 200, good.text
    assert good.json()["ok"] is True

    app = _build_app(monkeypatch)
    _as_system_admin(app)
    missing = _ollama_client(delete_status=404)
    _override_client(app, missing)
    try:
        async with _client(app) as client:
            gone = await client.request("DELETE", "/admin/ollama/models", json={"name": "ghost"})
    finally:
        await missing.aclose()
        _cleanup(app)
    assert gone.status_code == 404, gone.text


@pytest.mark.asyncio
async def test_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/ollama/models")
    finally:
        _cleanup(app)
    assert resp.status_code in (401, 403)
