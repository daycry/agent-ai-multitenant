"""OpenAPI 3.1 + Swagger UI for the public v1 API (Plan 13 task_13_06).

The published v1 contract lives at ``/api/v1/openapi.json`` (an OpenAPI
3.1 document scoped to the public ``/api/v1`` surface) with a Swagger UI at
``/api/v1/docs``. External tooling reads the document to learn the paths,
schemas and HOW to authenticate — the ``X-API-Token`` apiKey header scheme.

The live curl check (``auto_13_06_a``,
``curl -f http://api-server:8000/api/v1/openapi.json``) needs a running
server + the docker network, so it is recorded as a human/stack check.
Here we validate the SAME contract IN-PROCESS with a FastAPI TestClient —
no DB / Redis needed: the docs endpoints are unauthenticated (a developer
reads the contract before wiring a token) and the document is generated
from the static v1 route set, so building the app is enough.

What this proves:

  * ``GET /api/v1/openapi.json`` returns an OpenAPI **3.1.x** document;
  * it lists the public v1 paths (projects / plans / tasks / conversations
    / kbs);
  * it declares the ``X-API-Token`` **apiKey header** security scheme and
    applies it globally, so the docs show how to authenticate;
  * the Swagger UI route serves 200 HTML referencing the JSON document.
"""

from __future__ import annotations

import pytest
from api_server.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def client() -> TestClient:
    """In-process client over the real app (docs endpoints need no DB)."""
    return TestClient(create_app())


def test_openapi_json_is_3_1(client: TestClient) -> None:
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    # OpenAPI 3.1.x (FastAPI default, pinned explicitly in the builder).
    assert doc["openapi"].startswith("3.1."), doc["openapi"]


def test_openapi_lists_the_v1_paths(client: TestClient) -> None:
    doc = client.get("/api/v1/openapi.json").json()
    paths = set(doc["paths"])
    # The five public resources are represented (collection roots +
    # representative item/sub-collection paths).
    expected = {
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/plans",
        "/api/v1/plans/{plan_id}",
        "/api/v1/projects/{project_id}/tasks",
        "/api/v1/projects/{project_id}/conversations",
        "/api/v1/conversations/{conversation_id}",
        "/api/v1/kbs",
        "/api/v1/kbs/{kb_id}",
    }
    missing = expected - paths
    assert not missing, f"missing v1 paths in openapi: {missing}"
    # The published contract is the public surface only — no internal app
    # routes (e.g. the JWT-authed admin endpoints) leak into it.
    assert all(p.startswith("/api/v1/") for p in paths), paths


def test_openapi_declares_apikey_header_security_scheme(client: TestClient) -> None:
    doc = client.get("/api/v1/openapi.json").json()
    schemes = doc["components"]["securitySchemes"]
    # Exactly one scheme: an apiKey carried in the X-API-Token header.
    assert len(schemes) == 1, schemes
    scheme = next(iter(schemes.values()))
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "header"
    assert scheme["name"] == "X-API-Token"
    # Applied globally so every operation advertises the requirement.
    scheme_name = next(iter(schemes))
    assert {scheme_name: []} in doc["security"]


def test_openapi_response_schemas_present(client: TestClient) -> None:
    """The reused public response schemas are inlined as components."""
    doc = client.get("/api/v1/openapi.json").json()
    component_schemas = doc["components"]["schemas"]
    for name in ("ProjectResponse", "PlanResponse", "TaskResponse"):
        assert name in component_schemas, f"{name} missing from components"


def test_swagger_ui_serves_html(client: TestClient) -> None:
    resp = client.get("/api/v1/docs")
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "swagger-ui" in body.lower()
    # Swagger UI is bound to the v1 document, not the app-wide /openapi.json.
    assert "/api/v1/openapi.json" in body
