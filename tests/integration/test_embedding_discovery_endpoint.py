"""GET /admin/embeddings/available-models — discovery endpoint (ADR 0056).

Light wiring: builds the real app via ``create_app`` but overrides the
System-Admin gate and the Ollama-probe client, so the endpoint is exercised
end to end WITHOUT a DB, Redis, or a live Ollama (the probe is a MockTransport).

Asserts:
  * a System Admin gets the installed embedders filtered to the curated catalog,
    with the active model marked and the recommended (768) list present;
  * an unreachable Ollama degrades to ``ollama_reachable=false`` (200, not 500);
  * the surface is System-Admin gated (no token → 401).
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


def _tags_client(models: list[str] | None = None, *, raise_conn: bool = False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        if raise_conn:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"models": [{"name": m} for m in (models or [])]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _build_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SERVER_EMBEDDING_MODEL", "nomic-embed-text")
    from api_server.config import get_settings

    get_settings.cache_clear()
    from api_server.main import create_app

    return create_app()


def _as_system_admin(app) -> None:
    """Override the System-Admin gate with a dummy principal (no JWT/Redis)."""
    from api_server.auth.deps import AuthPrincipal, require_system_admin

    app.dependency_overrides[require_system_admin] = lambda: AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=None, is_system_admin=True
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_lists_compatible_and_marks_active(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    from api_server.routers.embeddings import get_ollama_probe_client

    probe = _tags_client(["nomic-embed-text:latest", "mxbai-embed-large:latest", "llama3.1:8b"])
    app.dependency_overrides[get_ollama_probe_client] = lambda: probe
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/embeddings/available-models")
    finally:
        await probe.aclose()
        app.dependency_overrides.clear()
        from api_server.config import get_settings

        get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ollama_reachable"] is True
    assert body["active_model"] == "nomic-embed-text"
    assert body["required_dim"] == 768
    by_name = {e["name"]: e for e in body["installed"]}
    assert "llama3.1:8b" not in by_name  # chat model excluded
    assert by_name["nomic-embed-text:latest"]["compatible"] is True
    assert by_name["nomic-embed-text:latest"]["active"] is True
    assert by_name["mxbai-embed-large:latest"]["compatible"] is False
    assert "nomic-embed-text" in body["recommended"]


@pytest.mark.asyncio
async def test_unreachable_ollama_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    _as_system_admin(app)
    from api_server.routers.embeddings import get_ollama_probe_client

    probe = _tags_client(raise_conn=True)
    app.dependency_overrides[get_ollama_probe_client] = lambda: probe
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/embeddings/available-models")
    finally:
        await probe.aclose()
        app.dependency_overrides.clear()
        from api_server.config import get_settings

        get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ollama_reachable"] is False
    assert body["installed"] == []
    assert "nomic-embed-text" in body["recommended"]


@pytest.mark.asyncio
async def test_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch)
    # No auth override: an unauthenticated request must not reach the data.
    try:
        async with _client(app) as client:
            resp = await client.get("/admin/embeddings/available-models")
    finally:
        from api_server.config import get_settings

        get_settings.cache_clear()
    assert resp.status_code in (401, 403)
