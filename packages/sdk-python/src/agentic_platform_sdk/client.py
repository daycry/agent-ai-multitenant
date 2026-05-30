"""Thin, typed Python client for the Agentic Platform public v1 API.

Hand-written (NOT generated) layer that wires the generated Pydantic models
(:mod:`agentic_platform_sdk.models`) to the ``/api/v1`` endpoints described
by the committed OpenAPI 3.1 spec. It is deliberately small and stable; the
MODELS are regenerated from the spec (``scripts/generate.py``), the wiring is
maintained by hand against them.

Design:

  * :class:`ApiClient` is constructed with a ``base_url`` and an
    ``X-API-Token`` (the per-tenant credential minted by a Tenant Admin,
    Plan 13 Fase A). The token is sent verbatim in the ``X-API-Token`` HEADER
    on every request — never a query parameter (Plan 13 Decisiones Clave).
  * Methods mirror the v1 endpoints (projects / plans / tasks /
    conversations / kbs), returning the generated typed models. List
    endpoints take ``limit`` / ``offset`` (the spec's pagination bounds).
  * It is sync (``httpx.Client``) for ergonomics in scripts/notebooks. The
    transport is injectable so tests can drive it against a mock without a
    running server.

The lib raises :class:`ApiError` for non-2xx responses, carrying the status
code + parsed body, so a caller can branch on 401 (bad/absent token), 403
(scope), 404 (cross-tenant / missing) or 429 (rate limit) without re-parsing.
"""

from __future__ import annotations

import warnings
from types import TracebackType
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel

from agentic_platform_sdk.models import (
    ConversationResponse,
    KnowledgeBaseResponse,
    PlanResponse,
    ProjectResponse,
    TaskResponse,
    V1ConversationCreateRequest,
    V1KnowledgeBaseCreateRequest,
    V1PlanCreateRequest,
    V1ProjectCreateRequest,
    V1TaskCreateRequest,
)

# The header carrying the per-tenant credential — must match the server's
# Fase A ``X-API-Token`` dependency and the ApiTokenAuth security scheme in
# the OpenAPI document exactly.
API_TOKEN_HEADER = "X-API-Token"

# Default user agent so server-side audit can attribute calls to the SDK.
_USER_AGENT = "agentic-platform-python-sdk/0.1.0"

__all__ = ["API_TOKEN_HEADER", "ApiClient", "ApiError"]


class ApiError(RuntimeError):
    """A non-2xx response from the public v1 API.

    Carries the HTTP ``status_code`` and the parsed JSON ``body`` (or the
    raw text if it was not JSON) so callers can branch on 401/403/404/429
    without re-reading the response.
    """

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        detail = body.get("detail") if isinstance(body, dict) else body
        super().__init__(f"v1 API error {status_code}: {detail!r}")


class ApiClient:
    """Sync client for the Agentic Platform public v1 API.

    Example::

        from agentic_platform_sdk import ApiClient

        with ApiClient("https://platform.example.com", "tkn_...") as api:
            for project in api.list_projects():
                print(project.id, project.name)
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not api_token:
            raise ValueError("api_token is required")
        self._api_token = api_token
        # ``transport`` is injectable so tests can drive the client against a
        # mock (httpx.MockTransport) without a running server.
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                API_TOKEN_HEADER: api_token,
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    # -- context manager -------------------------------------------------
    def __enter__(self) -> ApiClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    # -- request plumbing ------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        response = self._http.request(method, path, params=params, json=json)
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            raise ApiError(response.status_code, body)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _page_params(limit: int | None, offset: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return params

    @staticmethod
    def _body(payload: BaseModel) -> dict[str, Any]:
        """JSON-mode dump of a request model.

        The GENERATED create models declare an enum field with a *string*
        default (e.g. ``status: ProjectStatus | None = 'active'``), so an
        unset default stays a plain ``str`` and Pydantic emits a cosmetic
        ``PydanticSerializationUnexpectedValue`` warning on dump even though
        the serialized value is correct. We suppress just that warning here —
        the wire value (``"active"``) is exactly what the server expects.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return payload.model_dump(mode="json")

    # ===================================================================
    # Projects
    # ===================================================================
    def list_projects(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[ProjectResponse]:
        """GET /api/v1/projects — the token tenant's projects."""
        data = self._request("GET", "/api/v1/projects", params=self._page_params(limit, offset))
        return [ProjectResponse.model_validate(item) for item in data]

    def get_project(self, project_id: UUID | str) -> ProjectResponse:
        """GET /api/v1/projects/{project_id}."""
        data = self._request("GET", f"/api/v1/projects/{project_id}")
        return ProjectResponse.model_validate(data)

    def create_project(self, payload: V1ProjectCreateRequest) -> ProjectResponse:
        """POST /api/v1/projects (requires a ``write`` scope token)."""
        data = self._request("POST", "/api/v1/projects", json=self._body(payload))
        return ProjectResponse.model_validate(data)

    # ===================================================================
    # Plans
    # ===================================================================
    def list_plans(
        self, project_id: UUID | str, *, limit: int | None = None, offset: int | None = None
    ) -> list[PlanResponse]:
        """GET /api/v1/projects/{project_id}/plans."""
        data = self._request(
            "GET",
            f"/api/v1/projects/{project_id}/plans",
            params=self._page_params(limit, offset),
        )
        return [PlanResponse.model_validate(item) for item in data]

    def get_plan(self, plan_id: UUID | str) -> PlanResponse:
        """GET /api/v1/plans/{plan_id}."""
        data = self._request("GET", f"/api/v1/plans/{plan_id}")
        return PlanResponse.model_validate(data)

    def create_plan(self, project_id: UUID | str, payload: V1PlanCreateRequest) -> PlanResponse:
        """POST /api/v1/projects/{project_id}/plans (requires ``write``)."""
        data = self._request(
            "POST",
            f"/api/v1/projects/{project_id}/plans",
            json=self._body(payload),
        )
        return PlanResponse.model_validate(data)

    # ===================================================================
    # Tasks
    # ===================================================================
    def list_tasks(
        self, project_id: UUID | str, *, limit: int | None = None, offset: int | None = None
    ) -> list[TaskResponse]:
        """GET /api/v1/projects/{project_id}/tasks."""
        data = self._request(
            "GET",
            f"/api/v1/projects/{project_id}/tasks",
            params=self._page_params(limit, offset),
        )
        return [TaskResponse.model_validate(item) for item in data]

    def get_task(self, project_id: UUID | str, task_id: UUID | str) -> TaskResponse:
        """GET /api/v1/projects/{project_id}/tasks/{task_id}."""
        data = self._request("GET", f"/api/v1/projects/{project_id}/tasks/{task_id}")
        return TaskResponse.model_validate(data)

    def create_task(self, project_id: UUID | str, payload: V1TaskCreateRequest) -> TaskResponse:
        """POST /api/v1/projects/{project_id}/tasks (requires ``write``)."""
        data = self._request(
            "POST",
            f"/api/v1/projects/{project_id}/tasks",
            json=self._body(payload),
        )
        return TaskResponse.model_validate(data)

    # ===================================================================
    # Conversations
    # ===================================================================
    def list_conversations(
        self, project_id: UUID | str, *, limit: int | None = None, offset: int | None = None
    ) -> list[ConversationResponse]:
        """GET /api/v1/projects/{project_id}/conversations."""
        data = self._request(
            "GET",
            f"/api/v1/projects/{project_id}/conversations",
            params=self._page_params(limit, offset),
        )
        return [ConversationResponse.model_validate(item) for item in data]

    def get_conversation(self, conversation_id: UUID | str) -> ConversationResponse:
        """GET /api/v1/conversations/{conversation_id}."""
        data = self._request("GET", f"/api/v1/conversations/{conversation_id}")
        return ConversationResponse.model_validate(data)

    def create_conversation(
        self, project_id: UUID | str, payload: V1ConversationCreateRequest
    ) -> ConversationResponse:
        """POST /api/v1/projects/{project_id}/conversations (``write``)."""
        data = self._request(
            "POST",
            f"/api/v1/projects/{project_id}/conversations",
            json=self._body(payload),
        )
        return ConversationResponse.model_validate(data)

    # ===================================================================
    # Knowledge bases
    # ===================================================================
    def list_kbs(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[KnowledgeBaseResponse]:
        """GET /api/v1/kbs — the token tenant's knowledge bases."""
        data = self._request("GET", "/api/v1/kbs", params=self._page_params(limit, offset))
        return [KnowledgeBaseResponse.model_validate(item) for item in data]

    def get_kb(self, kb_id: UUID | str) -> KnowledgeBaseResponse:
        """GET /api/v1/kbs/{kb_id}."""
        data = self._request("GET", f"/api/v1/kbs/{kb_id}")
        return KnowledgeBaseResponse.model_validate(data)

    def create_kb(self, payload: V1KnowledgeBaseCreateRequest) -> KnowledgeBaseResponse:
        """POST /api/v1/kbs (requires a ``write`` scope token)."""
        data = self._request("POST", "/api/v1/kbs", json=self._body(payload))
        return KnowledgeBaseResponse.model_validate(data)
