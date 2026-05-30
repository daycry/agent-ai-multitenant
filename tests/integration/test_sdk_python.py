"""Integration tests for the official Python SDK (Plan 13 task_13_13).

The SDK (``packages/sdk-python``, importable as ``agentic_platform_sdk``) is a
typed client GENERATED from the public v1 OpenAPI 3.1 contract (Pydantic v2
models via datamodel-code-generator) plus a thin hand-written ``httpx``
client. This suite proves the SDK is REAL and usable WITHOUT a running
server:

  * the package imports and exposes the expected public surface;
  * its generated models MATCH the v1 schemas — a representative model
    (``ProjectResponse``) round-trips a payload shaped exactly like the
    component schema in the committed spec;
  * an :class:`ApiClient` constructs with a base URL + an ``X-API-Token``
    and validates its required args;
  * driven against a MOCK transport (no network), the client sends the
    ``X-API-Token`` header on every request, hits the right v1 path and
    decodes the response into the typed model;
  * a non-2xx response (e.g. 401 bad token, 429 rate-limited) surfaces as
    a typed :class:`ApiError` carrying the status code.

It needs neither PostgreSQL nor Redis — the transport is mocked — so it runs
fast and hermetically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.integration

# Repo-root-relative path to the committed spec the SDK was generated from.
_SPEC_PATH = Path(__file__).resolve().parents[2] / "packages" / "sdk-python" / "openapi-v1.json"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _project_payload() -> dict[str, object]:
    """A JSON body shaped exactly like the v1 ``ProjectResponse`` schema."""
    return {
        "id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "name": "Demo project",
        "description": None,
        "status": "active",
        "team_id": None,
        "mcp_servers": [],
        "rag_knowledge_bases": [],
        "worker_config": {},
        "repository_config": None,
        "human_approval_policy": None,
        "secrets_vault_id": None,
        "budget_amount": None,
        "budget_currency": None,
        "budget_period": None,
        "budget_period_start_day": None,
        "budget_period_length_days": None,
        "paused_by_budget": False,
        "is_template": False,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "deleted_at": None,
    }


# ---------------------------------------------------------------------------
# The SDK imports + exposes its public surface
# ---------------------------------------------------------------------------
def test_sdk_imports_and_public_surface() -> None:
    import agentic_platform_sdk as sdk

    assert sdk.__version__
    for name in (
        "ApiClient",
        "ApiError",
        "API_TOKEN_HEADER",
        "ProjectResponse",
        "PlanResponse",
        "TaskResponse",
        "ConversationResponse",
        "KnowledgeBaseResponse",
        "V1ProjectCreateRequest",
        "V1PlanCreateRequest",
        "V1TaskCreateRequest",
        "V1ConversationCreateRequest",
        "V1KnowledgeBaseCreateRequest",
    ):
        assert hasattr(sdk, name), f"SDK missing public symbol {name!r}"
    assert sdk.API_TOKEN_HEADER == "X-API-Token"


# ---------------------------------------------------------------------------
# The committed spec exists and the model matches the v1 schema
# ---------------------------------------------------------------------------
def test_committed_spec_is_the_v1_contract() -> None:
    assert _SPEC_PATH.is_file(), f"generated spec missing at {_SPEC_PATH}"
    spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    schemas = spec["components"]["schemas"]
    # The model the SDK exposes must line up with the spec component.
    schema_props = set(schemas["ProjectResponse"]["properties"])
    from agentic_platform_sdk import ProjectResponse

    model_props = set(ProjectResponse.model_fields)
    assert model_props == schema_props, model_props ^ schema_props
    # The auth scheme advertised by the contract is the one the client sends.
    assert spec["components"]["securitySchemes"]["ApiTokenAuth"]["name"] == "X-API-Token"


def test_representative_model_round_trips() -> None:
    from agentic_platform_sdk import ProjectResponse

    payload = _project_payload()
    model = ProjectResponse.model_validate(payload)
    assert model.name == "Demo project"
    assert model.status == "active"
    # round-trip back to JSON-mode dict and re-validate -> stable
    again = ProjectResponse.model_validate(model.model_dump(mode="json"))
    assert again.id == model.id


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------
def test_client_requires_base_url_and_token() -> None:
    from agentic_platform_sdk import ApiClient

    with pytest.raises(ValueError):
        ApiClient("", "tkn")
    with pytest.raises(ValueError):
        ApiClient("https://x.example", "")
    # happy path constructs + closes cleanly
    client = ApiClient("https://platform.example.com", "tkn_abc")
    client.close()


# ---------------------------------------------------------------------------
# Driven against a mock transport: sends X-API-Token + decodes typed model
# ---------------------------------------------------------------------------
def test_client_sends_api_token_header_and_decodes_model() -> None:
    from agentic_platform_sdk import ApiClient, ProjectResponse

    seen_headers: dict[str, str] = {}
    seen_path: dict[str, str] = {}
    body = _project_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["token"] = request.headers.get("X-API-Token", "")
        seen_path["path"] = request.url.path
        seen_path["query"] = request.url.query.decode()
        return httpx.Response(200, json=[body])

    transport = httpx.MockTransport(handler)
    with ApiClient("https://platform.example.com", "tkn_secret_123", transport=transport) as client:
        projects = client.list_projects(limit=10, offset=0)

    assert seen_headers["token"] == "tkn_secret_123"
    assert seen_path["path"] == "/api/v1/projects"
    assert "limit=10" in seen_path["query"]
    assert len(projects) == 1
    assert isinstance(projects[0], ProjectResponse)
    assert projects[0].name == "Demo project"


def test_client_post_sends_typed_body() -> None:
    from agentic_platform_sdk import ApiClient, V1ProjectCreateRequest

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(201, json=_project_payload())

    transport = httpx.MockTransport(handler)
    with ApiClient("https://platform.example.com", "tkn", transport=transport) as client:
        client.create_project(V1ProjectCreateRequest(name="New project"))

    assert captured["method"] == "POST"
    assert captured["json"]["name"] == "New project"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Non-2xx surfaces as a typed ApiError
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status_code", [401, 403, 404, 429])
def test_error_responses_raise_api_error(status_code: int) -> None:
    from agentic_platform_sdk import ApiClient, ApiError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "nope"})

    transport = httpx.MockTransport(handler)
    with (
        ApiClient("https://platform.example.com", "tkn", transport=transport) as client,
        pytest.raises(ApiError) as excinfo,
    ):
        client.list_projects()
    assert excinfo.value.status_code == status_code
    assert excinfo.value.body == {"detail": "nope"}
